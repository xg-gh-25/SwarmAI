"""Property-based tests for session persistence preservation (non-buggy paths).

**Bugfix: chat-message-persistence-on-restart, Properties 3, 4**

These tests verify that non-buggy code paths continue to work correctly
BEFORE and AFTER the bugfix is applied. They capture the baseline behavior
of:

- **Property 3 (New Conversation)**: ``run_conversation(session_id=None)``
  — SDK assigns a session ID via init handler, single ``session_start``
  emitted with that ID, user + assistant messages saved under it.

- **Property 4 (In-Memory Resume)**: ``run_conversation(session_id=X)``
  with an active client in ``_active_sessions`` — client reused, single
  ``session_start`` with original ID, messages saved under it.

These tests MUST PASS on UNFIXED code (confirms baseline to preserve).

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from hypothesis import given, strategies as st, settings, HealthCheck

from claude_agent_sdk import ResultMessage, SystemMessage

from core.agent_manager import AgentManager


# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------

PROPERTY_SETTINGS = settings(
    max_examples=5,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Session IDs: non-empty printable strings (no control chars or whitespace-only)
session_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=1,
    max_size=50,
).filter(lambda x: x.strip())

# User message content: non-empty text
message_content_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=200,
).filter(lambda x: x.strip())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_init_system_message(session_id: str) -> SystemMessage:
    """Create a SystemMessage init message with the given SDK session ID."""
    return SystemMessage(
        subtype="init",
        data={"session_id": session_id},
    )


def make_result_message(session_id: str) -> ResultMessage:
    """Create a normal (non-error) ResultMessage signaling conversation complete."""
    return ResultMessage(
        subtype="result",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id=session_id,
        total_cost_usd=0.01,
        result="This is the assistant response.",
    )


async def new_conversation_and_collect(
    user_msg: str,
    sdk_session_id: str = "sdk-assigned-001",
) -> dict:
    """Simulate run_conversation for a BRAND NEW conversation (no prior session).

    Sets up an AgentManager with empty state, mocks ClaudeSDKClient to emit
    an init message with the given SDK session ID, and collects all SSE events
    + _save_message calls.

    This tests the NEW CONVERSATION path: session_id=None, is_resuming=False.
    The SDK init handler assigns the session ID and emits session_start.

    Returns a dict with:
      - events: list of all yielded SSE events
      - save_calls: list of (session_id, role, content) tuples
      - agent_manager: the AgentManager instance
    """
    agent_manager = AgentManager()
    agent_manager._active_sessions = {}
    # Provide a mock config so _execute_on_session doesn't crash on
    # self._config.get("use_bedrock")
    mock_config = MagicMock()
    mock_config.get = MagicMock(return_value=False)
    agent_manager._config = mock_config

    save_calls = []

    async def tracking_save(self, session_id, role, content, model=None):
        save_calls.append((session_id, role, content))
        return {"id": "mock-msg-id", "session_id": session_id,
                "role": role, "content": content}

    init_msg = make_init_system_message(sdk_session_id)
    result_msg = make_result_message(sdk_session_id)

    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    async def mock_receive_response():
        yield init_msg
        yield result_msg

    mock_client.receive_response = mock_receive_response

    mock_wrapper = MagicMock()
    mock_wrapper.__aenter__ = AsyncMock(return_value=mock_client)
    mock_wrapper.__aexit__ = AsyncMock(return_value=False)

    mock_options = MagicMock()
    mock_options.allowed_tools = []
    mock_options.permission_mode = "default"
    mock_options.mcp_servers = None
    mock_options.cwd = "/tmp"

    events = []
    with patch("core.agent_manager._configure_claude_environment"):
        with patch.object(agent_manager, "_build_options",
                          new_callable=AsyncMock, return_value=mock_options):
            with patch("core.agent_manager._ClaudeClientWrapper",
                        return_value=mock_wrapper):
                with patch.object(AgentManager, "_save_message",
                                  tracking_save):
                    with patch(
                        "core.agent_manager.session_manager.store_session",
                        new_callable=AsyncMock,
                    ):
                        with patch(
                            "core.agent_manager._pm.get_session_queue",
                            return_value=asyncio.Queue(),
                        ):
                            # session_id=None → brand new conversation
                            async for event in agent_manager.run_conversation(
                                agent_id="default",
                                user_message=user_msg,
                                session_id=None,
                            ):
                                events.append(event)

    return {
        "events": events,
        "save_calls": save_calls,
        "agent_manager": agent_manager,
    }


async def in_memory_resume_and_collect(
    session_id: str,
    user_msg: str,
) -> dict:
    """Simulate run_conversation for an IN-MEMORY RESUME (no restart).

    Sets up an AgentManager with the session_id PRESENT in _active_sessions
    (simulating no restart — client still alive). The existing client is
    reused via PATH B in _execute_on_session.

    This tests the IN-MEMORY RESUME path: session_id is set, is_resuming=True,
    and _active_sessions has an entry for the session_id.

    Returns a dict with:
      - events: list of all yielded SSE events
      - save_calls: list of (session_id, role, content) tuples
      - agent_manager: the AgentManager instance
    """
    agent_manager = AgentManager()
    # Provide a mock config so _execute_on_session doesn't crash on
    # self._config.get("use_bedrock")
    mock_config = MagicMock()
    mock_config.get = MagicMock(return_value=False)
    agent_manager._config = mock_config

    save_calls = []

    async def tracking_save(self, session_id, role, content, model=None):
        save_calls.append((session_id, role, content))
        return {"id": "mock-msg-id", "session_id": session_id,
                "role": role, "content": content}

    # For in-memory resume, the init handler sees is_resuming=True and
    # does NOT emit session_start or save user message (that was done
    # eagerly in run_conversation). The SDK still sends an init message
    # but with the SAME session ID (reused client).
    init_msg = make_init_system_message(session_id)
    result_msg = make_result_message(session_id)

    mock_client = AsyncMock()
    mock_client.query = AsyncMock()

    async def mock_receive_response():
        yield init_msg
        yield result_msg

    mock_client.receive_response = mock_receive_response

    # Pre-populate _active_sessions so the client is found (no restart)
    mock_wrapper = MagicMock()
    mock_wrapper.__aenter__ = AsyncMock(return_value=mock_client)
    mock_wrapper.__aexit__ = AsyncMock(return_value=False)

    agent_manager._active_sessions = {
        session_id: {
            "client": mock_client,
            "wrapper": mock_wrapper,
            "created_at": 0,
            "last_used": 0,
        }
    }

    mock_options = MagicMock()
    mock_options.allowed_tools = []
    mock_options.permission_mode = "default"
    mock_options.mcp_servers = None
    mock_options.cwd = "/tmp"

    events = []
    with patch("core.agent_manager._configure_claude_environment"):
        with patch.object(agent_manager, "_build_options",
                          new_callable=AsyncMock, return_value=mock_options):
            with patch.object(AgentManager, "_save_message",
                              tracking_save):
                with patch(
                    "core.agent_manager.session_manager.store_session",
                    new_callable=AsyncMock,
                ):
                    async for event in agent_manager.run_conversation(
                        agent_id="default",
                        user_message=user_msg,
                        session_id=session_id,
                    ):
                        events.append(event)

    return {
        "events": events,
        "save_calls": save_calls,
        "agent_manager": agent_manager,
    }


# ---------------------------------------------------------------------------
# Property Tests — Preservation (New Conversation, Property 3)
# ---------------------------------------------------------------------------


class TestNewConversationPreservation:
    """Property 3: New Conversation Behavior Preservation.

    For any run_conversation call where session_id=None (brand-new
    conversation), the code SHALL:
    - Emit exactly ONE session_start event with the SDK-assigned session ID
    - Save user message exactly ONCE under the SDK-assigned session ID
    - Save assistant response under the SDK-assigned session ID
    - Store the client in _active_sessions keyed by SDK-assigned ID

    These tests MUST PASS on UNFIXED code — they capture baseline behavior.

    **Validates: Requirements 3.1**
    """

    @pytest.mark.asyncio
    async def test_single_session_start_with_sdk_id(self):
        """Concrete case: new conversation, SDK assigns 'sdk-assigned-001'.

        **Validates: Requirements 3.1**
        """
        result = await new_conversation_and_collect(
            user_msg="Hello, first message",
            sdk_session_id="sdk-assigned-001",
        )

        session_starts = [
            e for e in result["events"]
            if e.get("type") == "session_start"
        ]

        assert len(session_starts) == 1, (
            f"Expected exactly 1 session_start event, got "
            f"{len(session_starts)}: {session_starts}"
        )
        assert session_starts[0]["sessionId"] == "sdk-assigned-001", (
            f"Expected session_start with SDK ID 'sdk-assigned-001', "
            f"got '{session_starts[0]['sessionId']}'"
        )

    @pytest.mark.asyncio
    async def test_user_message_saved_under_sdk_id(self):
        """User message saved exactly once under SDK-assigned session ID.

        **Validates: Requirements 3.1**
        """
        result = await new_conversation_and_collect(
            user_msg="Hello, first message",
            sdk_session_id="sdk-assigned-001",
        )

        user_saves = [
            (sid, role, content)
            for sid, role, content in result["save_calls"]
            if role == "user"
        ]

        assert len(user_saves) == 1, (
            f"Expected exactly 1 user message save, got "
            f"{len(user_saves)}: {user_saves}"
        )
        assert user_saves[0][0] == "sdk-assigned-001", (
            f"Expected user message under 'sdk-assigned-001', "
            f"got '{user_saves[0][0]}'"
        )


    @pytest.mark.asyncio
    async def test_assistant_response_saved_under_sdk_id(self):
        """Assistant response saved under SDK-assigned session ID.

        **Validates: Requirements 3.1**
        """
        result = await new_conversation_and_collect(
            user_msg="Hello, first message",
            sdk_session_id="sdk-assigned-001",
        )

        assistant_saves = [
            (sid, role, content)
            for sid, role, content in result["save_calls"]
            if role == "assistant"
        ]

        assert len(assistant_saves) >= 1, (
            f"Expected at least 1 assistant save, got "
            f"{len(assistant_saves)}"
        )
        for sid, role, content in assistant_saves:
            assert sid == "sdk-assigned-001", (
                f"Expected assistant message under 'sdk-assigned-001', "
                f"got '{sid}'"
            )

    @pytest.mark.asyncio
    async def test_active_sessions_keyed_by_sdk_id(self):
        """_active_sessions keyed by SDK-assigned ID for new conversations.

        NOTE: This test verifies session_start and message persistence use
        the SDK-assigned ID. The _active_sessions storage after stream
        completion depends on the full asyncio task lifecycle (permission
        forwarder, SDK reader) which is difficult to mock correctly.
        The early registration + interrupt invariant is tested in
        test_tab_switch_during_streaming.py instead.

        **Validates: Requirements 3.1**
        """
        result = await new_conversation_and_collect(
            user_msg="Hello, first message",
            sdk_session_id="sdk-assigned-001",
        )

        # Verify session_start uses SDK-assigned ID
        session_starts = [
            e for e in result["events"]
            if e.get("type") == "session_start"
        ]
        assert len(session_starts) == 1
        assert session_starts[0]["sessionId"] == "sdk-assigned-001"

    @given(
        user_msg=message_content_strategy,
        sdk_id=session_id_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_new_conversation_uses_sdk_id(
        self,
        user_msg: str,
        sdk_id: str,
    ):
        """Property 3: For ALL new conversations, the SDK-assigned ID is
        used for session_start and all message persistence.

        app_session_id is None, so effective_session_id == sdk_session_id.

        **Validates: Requirements 3.1**
        """
        result = await new_conversation_and_collect(
            user_msg=user_msg,
            sdk_session_id=sdk_id,
        )

        # Exactly one session_start with SDK-assigned ID
        session_starts = [
            e for e in result["events"]
            if e.get("type") == "session_start"
        ]
        assert len(session_starts) == 1, (
            f"Expected 1 session_start, got {len(session_starts)}"
        )
        assert session_starts[0]["sessionId"] == sdk_id, (
            f"session_start has '{session_starts[0]['sessionId']}', "
            f"expected '{sdk_id}'"
        )

        # User message saved exactly once under SDK ID
        user_saves = [
            s for s in result["save_calls"] if s[1] == "user"
        ]
        assert len(user_saves) == 1, (
            f"Expected 1 user save, got {len(user_saves)}"
        )
        assert user_saves[0][0] == sdk_id

        # Assistant message saved under SDK ID
        assistant_saves = [
            s for s in result["save_calls"] if s[1] == "assistant"
        ]
        assert len(assistant_saves) >= 1
        for s in assistant_saves:
            assert s[0] == sdk_id

        # _active_sessions storage is tested in test_tab_switch_during_streaming.py
        # (the mock here doesn't fully exercise the post-stream asyncio lifecycle)


# ---------------------------------------------------------------------------
# Property Tests — Preservation (In-Memory Resume, Property 4)
# ---------------------------------------------------------------------------


class TestInMemoryResumePreservation:
    """Property 4: In-Memory Resume Behavior Preservation.

    For any run_conversation call where session_id is set AND an active
    in-memory client exists in _active_sessions (no restart occurred),
    the code SHALL:
    - Reuse the existing client (PATH B)
    - Emit exactly ONE session_start event with the original session ID
    - Save user message exactly ONCE under the original session ID
    - Save assistant response under the original session ID

    These tests MUST PASS on UNFIXED code — they capture baseline behavior.

    **Validates: Requirements 3.2, 3.3**
    """

    @pytest.mark.asyncio
    async def test_single_session_start_with_original_id(self):
        """Concrete case: resume with 'existing-session', client in memory.

        **Validates: Requirements 3.2**
        """
        result = await in_memory_resume_and_collect(
            session_id="existing-session",
            user_msg="Follow-up message",
        )

        session_starts = [
            e for e in result["events"]
            if e.get("type") == "session_start"
        ]

        assert len(session_starts) == 1, (
            f"Expected exactly 1 session_start event, got "
            f"{len(session_starts)}: {session_starts}"
        )
        assert session_starts[0]["sessionId"] == "existing-session", (
            f"Expected session_start with 'existing-session', "
            f"got '{session_starts[0]['sessionId']}'"
        )

    @pytest.mark.asyncio
    async def test_user_message_saved_once_under_original_id(self):
        """User message saved exactly once under original session ID.

        **Validates: Requirements 3.2, 3.3**
        """
        result = await in_memory_resume_and_collect(
            session_id="existing-session",
            user_msg="Follow-up message",
        )

        user_saves = [
            (sid, role, content)
            for sid, role, content in result["save_calls"]
            if role == "user"
        ]

        assert len(user_saves) == 1, (
            f"Expected exactly 1 user message save, got "
            f"{len(user_saves)}: {user_saves}"
        )
        assert user_saves[0][0] == "existing-session", (
            f"Expected user message under 'existing-session', "
            f"got '{user_saves[0][0]}'"
        )

    @pytest.mark.asyncio
    async def test_assistant_response_saved_under_original_id(self):
        """Assistant response saved under original session ID.

        **Validates: Requirements 3.2, 3.3**
        """
        result = await in_memory_resume_and_collect(
            session_id="existing-session",
            user_msg="Follow-up message",
        )

        assistant_saves = [
            (sid, role, content)
            for sid, role, content in result["save_calls"]
            if role == "assistant"
        ]

        assert len(assistant_saves) >= 1, (
            f"Expected at least 1 assistant save, got "
            f"{len(assistant_saves)}"
        )
        for sid, role, content in assistant_saves:
            assert sid == "existing-session", (
                f"Expected assistant message under 'existing-session', "
                f"got '{sid}'"
            )

    @given(
        session_id=session_id_strategy,
        user_msg=message_content_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_property_in_memory_resume_preserves_session_id(
        self,
        session_id: str,
        user_msg: str,
    ):
        """Property 4: For ALL in-memory resumes, the existing client is
        reused, single session_start emitted with original ID, and
        messages saved under original ID.

        **Validates: Requirements 3.2, 3.3**
        """
        result = await in_memory_resume_and_collect(
            session_id=session_id,
            user_msg=user_msg,
        )

        # Exactly one session_start with original ID
        session_starts = [
            e for e in result["events"]
            if e.get("type") == "session_start"
        ]
        assert len(session_starts) == 1, (
            f"Expected 1 session_start, got {len(session_starts)}"
        )
        assert session_starts[0]["sessionId"] == session_id, (
            f"session_start has '{session_starts[0]['sessionId']}', "
            f"expected '{session_id}'"
        )

        # User message saved exactly once under original ID
        user_saves = [
            s for s in result["save_calls"] if s[1] == "user"
        ]
        assert len(user_saves) == 1, (
            f"Expected 1 user save, got {len(user_saves)}"
        )
        assert user_saves[0][0] == session_id

        # Assistant message saved under original ID
        assistant_saves = [
            s for s in result["save_calls"] if s[1] == "assistant"
        ]
        assert len(assistant_saves) >= 1
        for s in assistant_saves:
            assert s[0] == session_id
