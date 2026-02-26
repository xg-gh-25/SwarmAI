"""Unit tests for AgentManager TSCC telemetry integration.

Tests that the ``_run_query_on_client`` method in ``agent_manager.py``
correctly yields TSCC telemetry events alongside normal SSE events,
handles lifecycle state transitions, triggers snapshots at the right
points, and degrades gracefully when telemetry emission fails.

Testing methodology: unit tests with mocked Claude SDK client.
Key invariants verified:
- Telemetry events are yielded for assistant messages and tool use
- Lifecycle transitions: active on init, paused on ask_user, idle on complete, failed on error
- Snapshot creation at conversation completion and user-input pause
- Telemetry failures never interrupt the SSE stream
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from core.agent_manager import AgentManager


# ---------------------------------------------------------------------------
# Helpers — fake SDK message types
# ---------------------------------------------------------------------------

class FakeTextBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeToolUseBlock:
    def __init__(self, name: str, tool_id: str = "tu_1", tool_input: dict | None = None):
        self.type = "tool_use"
        self.name = name
        self.id = tool_id
        self.input = tool_input or {}


class FakeToolResultBlock:
    def __init__(self, tool_use_id: str = "tu_1", content: str = "ok", is_error: bool = False):
        self.type = "tool_result"
        self.tool_use_id = tool_use_id
        self.content = content
        self.is_error = is_error


class FakeAssistantMessage:
    """Mimics claude_agent_sdk.AssistantMessage."""
    def __init__(self, content: list, model: str = "claude-sonnet-4-20250514"):
        self.content = content
        self.model = model


class FakeSystemMessage:
    """Mimics claude_agent_sdk.SystemMessage."""
    def __init__(self, subtype: str, data: dict | None = None):
        self.subtype = subtype
        self.data = data or {}


class FakeResultMessage:
    """Mimics claude_agent_sdk.ResultMessage."""
    def __init__(self, result: str | None = None, is_error: bool = False,
                 subtype: str = "result"):
        self.result = result
        self.is_error = is_error
        self.subtype = subtype
        self.duration_ms = 100
        self.total_cost_usd = 0.01
        self.num_turns = 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agent_manager():
    """Create an AgentManager instance for testing."""
    return AgentManager()


@pytest.fixture
def base_agent_config():
    return {"model": "claude-sonnet-4-20250514"}


@pytest.fixture
def base_session_context():
    return {"sdk_session_id": None}


async def collect_events(async_iter) -> list[dict]:
    """Drain an async iterator into a list."""
    events = []
    async for event in async_iter:
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Helper: build a mock SDK client that yields a sequence of messages
# ---------------------------------------------------------------------------

def make_mock_client(messages: list):
    """Create a mock ClaudeSDKClient whose receive_response yields *messages*.

    The mock also stubs ``query()`` as a no-op coroutine.
    """
    client = AsyncMock()

    async def _receive():
        for m in messages:
            yield m

    client.receive_response = _receive
    client.query = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Test 1: Telemetry events yielded alongside normal SSE events
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_telemetry_events_yielded_with_sse(agent_manager, base_agent_config):
    """Verify that _run_query_on_client yields TSCC telemetry events
    interleaved with normal SSE events when processing assistant messages."""

    session_id = "test-session-telemetry-001"
    session_ctx = {"sdk_session_id": None}

    # SDK message sequence: init → assistant with text + tool_use → result
    sdk_messages = [
        FakeSystemMessage("init", {"session_id": session_id}),
        FakeAssistantMessage([
            FakeTextBlock("Analyzing your code..."),
            FakeToolUseBlock("read_file", "tu_1", {"path": "src/main.py"}),
        ]),
        FakeResultMessage(result=None, is_error=False, subtype="result"),
    ]
    client = make_mock_client(sdk_messages)

    acc = MagicMock()
    acc.blocks = []
    acc.__bool__ = lambda self: False
    acc.add = MagicMock()
    acc.extend = MagicMock()

    with patch("core.agent_manager._tscc_state_manager") as mock_sm, \
         patch("core.agent_manager._tscc_snapshot_manager") as mock_snap, \
         patch("core.agent_manager.session_manager") as mock_sess, \
         patch("core.agent_manager._permission_request_queue", new=asyncio.Queue()), \
         patch("core.agent_manager.ResultMessage", FakeResultMessage), \
         patch("core.agent_manager.SystemMessage", FakeSystemMessage):

        mock_sm.get_or_create_state = AsyncMock()
        mock_sm.set_lifecycle_state = AsyncMock()
        mock_sm.apply_event = AsyncMock()
        mock_sm.get_state = AsyncMock(return_value=MagicMock())
        mock_sess.store_session = AsyncMock()
        agent_manager._save_message = AsyncMock()
        agent_manager._format_message = AsyncMock(return_value={
            "type": "assistant",
            "content": [
                {"type": "text", "text": "Analyzing your code..."},
                {"type": "tool_use", "name": "read_file", "id": "tu_1", "input": {"path": "src/main.py"}},
            ],
            "model": "claude-sonnet-4-20250514",
        })

        events = await collect_events(
            agent_manager._run_query_on_client(
                client=client,
                query_content="Analyze my code",
                display_text="Analyze my code",
                agent_config=base_agent_config,
                session_context=session_ctx,
                assistant_content=acc,
                is_resuming=False,
                content=None,
                user_message="Analyze my code",
                agent_id="agent-1",
            )
        )

    # Collect event types
    event_types = [e.get("type") for e in events]

    # Should contain normal SSE events
    assert "session_start" in event_types, f"Missing session_start in {event_types}"
    assert "result" in event_types, f"Missing result in {event_types}"

    # Should contain TSCC telemetry events
    telemetry_types = {"agent_activity", "tool_invocation", "sources_updated"}
    found_telemetry = {e["type"] for e in events if e.get("type") in telemetry_types}
    assert len(found_telemetry) > 0, (
        f"Expected telemetry events, got types: {event_types}"
    )

    # agent_activity should appear (from init + text block)
    assert "agent_activity" in found_telemetry

    # tool_invocation should appear (from tool_use block)
    assert "tool_invocation" in found_telemetry

    # sources_updated should appear (read_file has path in input)
    assert "sources_updated" in found_telemetry


# ---------------------------------------------------------------------------
# Test 2: Telemetry failure does not interrupt agent execution
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_telemetry_failure_does_not_interrupt_sse(agent_manager, base_agent_config):
    """Verify that if _tscc_state_manager.apply_event raises, the normal
    SSE stream continues uninterrupted (graceful degradation)."""

    session_id = "test-session-graceful-001"
    session_ctx = {"sdk_session_id": None}

    sdk_messages = [
        FakeSystemMessage("init", {"session_id": session_id}),
        FakeAssistantMessage([FakeTextBlock("Hello world")]),
        FakeResultMessage(result=None, is_error=False, subtype="result"),
    ]
    client = make_mock_client(sdk_messages)

    acc = MagicMock()
    acc.blocks = []
    acc.__bool__ = lambda self: False
    acc.add = MagicMock()
    acc.extend = MagicMock()

    with patch("core.agent_manager._tscc_state_manager") as mock_sm, \
         patch("core.agent_manager._tscc_snapshot_manager", None), \
         patch("core.agent_manager.session_manager") as mock_sess, \
         patch("core.agent_manager._permission_request_queue", new=asyncio.Queue()), \
         patch("core.agent_manager.ResultMessage", FakeResultMessage), \
         patch("core.agent_manager.SystemMessage", FakeSystemMessage):

        # Make the state manager raise on every call after init
        call_count = 0

        async def failing_apply(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Telemetry DB exploded")

        mock_sm.get_or_create_state = AsyncMock()
        mock_sm.set_lifecycle_state = AsyncMock()
        mock_sm.apply_event = AsyncMock(side_effect=failing_apply)
        mock_sm.get_state = AsyncMock(return_value=MagicMock())
        mock_sess.store_session = AsyncMock()
        agent_manager._save_message = AsyncMock()
        agent_manager._format_message = AsyncMock(return_value={
            "type": "assistant",
            "content": [{"type": "text", "text": "Hello world"}],
            "model": "claude-sonnet-4-20250514",
        })

        events = await collect_events(
            agent_manager._run_query_on_client(
                client=client,
                query_content="Hello",
                display_text="Hello",
                agent_config=base_agent_config,
                session_context=session_ctx,
                assistant_content=acc,
                is_resuming=False,
                content=None,
                user_message="Hello",
                agent_id="agent-1",
            )
        )

    event_types = [e.get("type") for e in events]

    # Normal SSE events must still flow despite telemetry failures
    assert "session_start" in event_types, f"session_start missing: {event_types}"
    assert "assistant" in event_types, f"assistant missing: {event_types}"
    assert "result" in event_types, f"result missing: {event_types}"

    # The apply_event was called (and failed), but execution continued
    assert call_count > 0, "apply_event should have been called at least once"


# ---------------------------------------------------------------------------
# Test 3: Lifecycle state transitions at correct points
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lifecycle_transitions_normal_flow(agent_manager, base_agent_config):
    """Verify lifecycle: active on init, idle on successful completion."""

    session_id = "test-session-lifecycle-001"
    session_ctx = {"sdk_session_id": None}

    sdk_messages = [
        FakeSystemMessage("init", {"session_id": session_id}),
        FakeAssistantMessage([FakeTextBlock("Done")]),
        FakeResultMessage(result=None, is_error=False, subtype="result"),
    ]
    client = make_mock_client(sdk_messages)

    acc = MagicMock()
    acc.blocks = []
    acc.__bool__ = lambda self: False
    acc.add = MagicMock()
    acc.extend = MagicMock()

    lifecycle_calls = []

    with patch("core.agent_manager._tscc_state_manager") as mock_sm, \
         patch("core.agent_manager._tscc_snapshot_manager") as mock_snap, \
         patch("core.agent_manager.session_manager") as mock_sess, \
         patch("core.agent_manager._permission_request_queue", new=asyncio.Queue()), \
         patch("core.agent_manager.ResultMessage", FakeResultMessage), \
         patch("core.agent_manager.SystemMessage", FakeSystemMessage):

        async def track_lifecycle(tid, state):
            lifecycle_calls.append((tid, state))

        mock_sm.get_or_create_state = AsyncMock()
        mock_sm.set_lifecycle_state = AsyncMock(side_effect=track_lifecycle)
        mock_sm.apply_event = AsyncMock()
        mock_sm.get_state = AsyncMock(return_value=MagicMock())
        mock_sess.store_session = AsyncMock()
        agent_manager._save_message = AsyncMock()
        agent_manager._format_message = AsyncMock(return_value={
            "type": "assistant",
            "content": [{"type": "text", "text": "Done"}],
            "model": "claude-sonnet-4-20250514",
        })

        events = await collect_events(
            agent_manager._run_query_on_client(
                client=client,
                query_content="Do something",
                display_text="Do something",
                agent_config=base_agent_config,
                session_context=session_ctx,
                assistant_content=acc,
                is_resuming=False,
                content=None,
                user_message="Do something",
                agent_id="agent-1",
            )
        )

    # Extract just the state values
    states = [s for (_, s) in lifecycle_calls]

    # First transition should be "active" (on init)
    assert states[0] == "active", f"First lifecycle should be active, got {states}"

    # Last transition should be "idle" (on successful completion)
    assert states[-1] == "idle", f"Last lifecycle should be idle, got {states}"


# ---------------------------------------------------------------------------
# Test 4: Lifecycle transitions to "failed" on error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lifecycle_failed_on_error(agent_manager, base_agent_config):
    """Verify lifecycle transitions to 'failed' when SDK returns an error."""

    session_id = "test-session-error-001"
    session_ctx = {"sdk_session_id": None}

    sdk_messages = [
        FakeSystemMessage("init", {"session_id": session_id}),
        FakeResultMessage(
            result="Something went wrong",
            is_error=True,
            subtype="error_during_execution",
        ),
    ]
    client = make_mock_client(sdk_messages)

    acc = MagicMock()
    acc.blocks = []
    acc.__bool__ = lambda self: False
    acc.add = MagicMock()
    acc.extend = MagicMock()

    lifecycle_calls = []

    with patch("core.agent_manager._tscc_state_manager") as mock_sm, \
         patch("core.agent_manager._tscc_snapshot_manager", None), \
         patch("core.agent_manager.session_manager") as mock_sess, \
         patch("core.agent_manager._permission_request_queue", new=asyncio.Queue()), \
         patch("core.agent_manager.ResultMessage", FakeResultMessage), \
         patch("core.agent_manager.SystemMessage", FakeSystemMessage):

        async def track_lifecycle(tid, state):
            lifecycle_calls.append((tid, state))

        mock_sm.get_or_create_state = AsyncMock()
        mock_sm.set_lifecycle_state = AsyncMock(side_effect=track_lifecycle)
        mock_sm.apply_event = AsyncMock()
        mock_sm.get_state = AsyncMock(return_value=MagicMock())
        mock_sess.store_session = AsyncMock()
        agent_manager._save_message = AsyncMock()
        agent_manager._format_message = AsyncMock(return_value=None)
        agent_manager._cleanup_session = AsyncMock()

        events = await collect_events(
            agent_manager._run_query_on_client(
                client=client,
                query_content="Fail please",
                display_text="Fail please",
                agent_config=base_agent_config,
                session_context=session_ctx,
                assistant_content=acc,
                is_resuming=False,
                content=None,
                user_message="Fail please",
                agent_id="agent-1",
            )
        )

    states = [s for (_, s) in lifecycle_calls]

    # Should have "active" from init, then "failed" from error
    assert "active" in states, f"Expected active in {states}"
    assert "failed" in states, f"Expected failed in {states}"

    # "failed" should come after "active"
    active_idx = states.index("active")
    failed_idx = states.index("failed")
    assert failed_idx > active_idx, "failed should come after active"

    # Normal error event should still be yielded
    error_events = [e for e in events if e.get("type") == "error"]
    assert len(error_events) > 0, "Error SSE event should still be yielded"


# ---------------------------------------------------------------------------
# Test 5: Snapshot triggers at conversation completion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_created_on_completion(agent_manager, base_agent_config):
    """Verify a snapshot is created when conversation completes (idle)."""

    session_id = "test-session-snap-complete-001"
    session_ctx = {"sdk_session_id": None}

    sdk_messages = [
        FakeSystemMessage("init", {"session_id": session_id}),
        FakeAssistantMessage([FakeTextBlock("All done")]),
        FakeResultMessage(result=None, is_error=False, subtype="result"),
    ]
    client = make_mock_client(sdk_messages)

    acc = MagicMock()
    acc.blocks = []
    acc.__bool__ = lambda self: False
    acc.add = MagicMock()
    acc.extend = MagicMock()

    mock_state = MagicMock()
    snapshot_calls = []

    with patch("core.agent_manager._tscc_state_manager") as mock_sm, \
         patch("core.agent_manager._tscc_snapshot_manager") as mock_snap, \
         patch("core.agent_manager.session_manager") as mock_sess, \
         patch("core.agent_manager._permission_request_queue", new=asyncio.Queue()), \
         patch("core.agent_manager.ResultMessage", FakeResultMessage), \
         patch("core.agent_manager.SystemMessage", FakeSystemMessage):

        mock_sm.get_or_create_state = AsyncMock()
        mock_sm.set_lifecycle_state = AsyncMock()
        mock_sm.apply_event = AsyncMock()
        mock_sm.get_state = AsyncMock(return_value=mock_state)
        mock_sess.store_session = AsyncMock()
        agent_manager._save_message = AsyncMock()
        agent_manager._format_message = AsyncMock(return_value={
            "type": "assistant",
            "content": [{"type": "text", "text": "All done"}],
            "model": "claude-sonnet-4-20250514",
        })

        def track_snapshot(tid, state, reason):
            snapshot_calls.append((tid, reason))
            return MagicMock()

        mock_snap.create_snapshot = MagicMock(side_effect=track_snapshot)

        events = await collect_events(
            agent_manager._run_query_on_client(
                client=client,
                query_content="Finish up",
                display_text="Finish up",
                agent_config=base_agent_config,
                session_context=session_ctx,
                assistant_content=acc,
                is_resuming=False,
                content=None,
                user_message="Finish up",
                agent_id="agent-1",
            )
        )

    # Snapshot should have been created with "Conversation turn completed"
    assert len(snapshot_calls) > 0, "Expected at least one snapshot call"
    reasons = [r for (_, r) in snapshot_calls]
    assert "Conversation turn completed" in reasons, (
        f"Expected 'Conversation turn completed' in {reasons}"
    )

    # Snapshot should be for the correct session
    tids = [t for (t, _) in snapshot_calls]
    assert session_id in tids


# ---------------------------------------------------------------------------
# Test 6: Snapshot triggers when pausing for user input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_snapshot_created_on_ask_user(agent_manager, base_agent_config):
    """Verify a snapshot is created when agent pauses for user input."""

    session_id = "test-session-snap-pause-001"
    session_ctx = {"sdk_session_id": None}

    # AskUserQuestion is a ToolUseBlock with name="AskUserQuestion"
    ask_block = FakeToolUseBlock(
        "AskUserQuestion", "tu_ask_1",
        {"questions": ["What file should I edit?"]}
    )

    sdk_messages = [
        FakeSystemMessage("init", {"session_id": session_id}),
        FakeAssistantMessage([ask_block]),
    ]
    client = make_mock_client(sdk_messages)

    acc = MagicMock()
    acc.blocks = []
    acc.__bool__ = lambda self: False
    acc.add = MagicMock()
    acc.extend = MagicMock()

    mock_state = MagicMock()
    snapshot_calls = []
    lifecycle_calls = []

    with patch("core.agent_manager._tscc_state_manager") as mock_sm, \
         patch("core.agent_manager._tscc_snapshot_manager") as mock_snap, \
         patch("core.agent_manager.session_manager") as mock_sess, \
         patch("core.agent_manager._permission_request_queue", new=asyncio.Queue()), \
         patch("core.agent_manager.ResultMessage", FakeResultMessage), \
         patch("core.agent_manager.SystemMessage", FakeSystemMessage):

        async def track_lifecycle(tid, state):
            lifecycle_calls.append((tid, state))

        mock_sm.get_or_create_state = AsyncMock()
        mock_sm.set_lifecycle_state = AsyncMock(side_effect=track_lifecycle)
        mock_sm.apply_event = AsyncMock()
        mock_sm.get_state = AsyncMock(return_value=mock_state)
        mock_sess.store_session = AsyncMock()
        agent_manager._save_message = AsyncMock()
        agent_manager._format_message = AsyncMock(return_value={
            "type": "ask_user_question",
            "toolUseId": "tu_ask_1",
            "questions": ["What file should I edit?"],
            "sessionId": "test-session-snap-pause-001",
        })

        def track_snapshot(tid, state, reason):
            snapshot_calls.append((tid, reason))
            return MagicMock()

        mock_snap.create_snapshot = MagicMock(side_effect=track_snapshot)

        events = await collect_events(
            agent_manager._run_query_on_client(
                client=client,
                query_content="Help me",
                display_text="Help me",
                agent_config=base_agent_config,
                session_context=session_ctx,
                assistant_content=acc,
                is_resuming=False,
                content=None,
                user_message="Help me",
                agent_id="agent-1",
            )
        )

    # Lifecycle should transition to "paused"
    states = [s for (_, s) in lifecycle_calls]
    assert "paused" in states, f"Expected 'paused' in lifecycle states: {states}"

    # Snapshot should have been created with "Waiting for user input"
    reasons = [r for (_, r) in snapshot_calls]
    assert "Waiting for user input" in reasons, (
        f"Expected 'Waiting for user input' in {reasons}"
    )

    # ask_user_question event should be yielded
    event_types = [e.get("type") for e in events]
    assert "ask_user_question" in event_types


# ---------------------------------------------------------------------------
# Test 7: No snapshot on error (had_error flag prevents idle transition)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_completion_snapshot_on_error(agent_manager, base_agent_config):
    """Verify no 'Conversation turn completed' snapshot when there's an error."""

    session_id = "test-session-no-snap-err-001"
    session_ctx = {"sdk_session_id": None}

    sdk_messages = [
        FakeSystemMessage("init", {"session_id": session_id}),
        FakeResultMessage(
            result="Auth failed",
            is_error=True,
            subtype="error_during_execution",
        ),
    ]
    client = make_mock_client(sdk_messages)

    acc = MagicMock()
    acc.blocks = []
    acc.__bool__ = lambda self: False
    acc.add = MagicMock()
    acc.extend = MagicMock()

    snapshot_calls = []

    with patch("core.agent_manager._tscc_state_manager") as mock_sm, \
         patch("core.agent_manager._tscc_snapshot_manager") as mock_snap, \
         patch("core.agent_manager.session_manager") as mock_sess, \
         patch("core.agent_manager._permission_request_queue", new=asyncio.Queue()), \
         patch("core.agent_manager.ResultMessage", FakeResultMessage), \
         patch("core.agent_manager.SystemMessage", FakeSystemMessage):

        mock_sm.get_or_create_state = AsyncMock()
        mock_sm.set_lifecycle_state = AsyncMock()
        mock_sm.apply_event = AsyncMock()
        mock_sm.get_state = AsyncMock(return_value=MagicMock())
        mock_sess.store_session = AsyncMock()
        agent_manager._save_message = AsyncMock()
        agent_manager._format_message = AsyncMock(return_value=None)
        agent_manager._cleanup_session = AsyncMock()

        def track_snapshot(tid, state, reason):
            snapshot_calls.append((tid, reason))
            return MagicMock()

        mock_snap.create_snapshot = MagicMock(side_effect=track_snapshot)

        events = await collect_events(
            agent_manager._run_query_on_client(
                client=client,
                query_content="Break",
                display_text="Break",
                agent_config=base_agent_config,
                session_context=session_ctx,
                assistant_content=acc,
                is_resuming=False,
                content=None,
                user_message="Break",
                agent_id="agent-1",
            )
        )

    # "Conversation turn completed" snapshot should NOT be created on error
    completion_reasons = [r for (_, r) in snapshot_calls
                         if r == "Conversation turn completed"]
    assert len(completion_reasons) == 0, (
        f"Should not create completion snapshot on error, got: {snapshot_calls}"
    )
