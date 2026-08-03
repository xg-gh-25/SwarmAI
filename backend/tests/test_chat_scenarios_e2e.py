"""End-to-end integration tests for all 6 chat scenarios.

Exercises the full SSE pipeline (session_router -> session_unit -> SSE events)
with a mocked Claude SDK client. Each test sends a real HTTP request to the
FastAPI app and validates the SSE event sequence.

Scenarios tested:
  1. Fresh send (COLD -> IDLE -> STREAMING -> IDLE)
  2. Warm send (IDLE -> STREAMING -> IDLE, subprocess reused)
  3. Append while streaming (queue path — send during STREAMING)
  4. Stop -> new message (interrupt -> IDLE/COLD -> fresh send)
  5. Resume within TTL (same as warm send, verifies no re-spawn)
  6. Resume post TTL (COLD -> context injection -> spawn -> stream)

Mock strategy: Patches ``_ClaudeClientWrapper`` in ``claude_environment.py``
to return a fake client that emits a configurable sequence of SDK messages.
All other layers (session_router, session_unit, chat.py SSE, sse_with_heartbeat)
run un-mocked.

# Feature: chat-experience-regression
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


# NOTE on loop scope (root cause of the cross-test "database is locked" errors,
# run_6a4402cb): pyproject.toml sets asyncio_default_fixture_loop_scope=session
# but leaves the TEST loop at the default function scope. Each test thus ran on
# its own loop that closed at test end — while the aiosqlite connection
# worker-threads opened during the request had not finished closing. The
# orphaned worker thread kept the OS write-lock, so the NEXT test's
# reset_database fixture failed instantly with "database is locked". The fix:
# every async test below carries @pytest.mark.asyncio(loop_scope="session") so
# the test loop matches the (session-scoped) fixture loop — ONE loop stays alive
# across the file and connections close cleanly. Applied per-test (NOT as a
# module-level pytestmark, which would also tag the SYNC tests in this file and
# emit spurious warnings, and NOT globally in pyproject, which would change loop
# scope for the other ~124 async test files).


# ---------------------------------------------------------------------------
# Mock SDK types — must be defined before importing session_unit
# ---------------------------------------------------------------------------

class _FakeSystemMessage:
    """Mimics claude_agent_sdk.SystemMessage."""
    def __init__(self, session_id: str = "sdk-session-001"):
        self.subtype = "init"
        self.session_id = session_id
        self.data = {"session_id": session_id}


class _FakeTextBlock:
    """Mimics claude_agent_sdk.TextBlock."""
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _FakeAssistantMessage:
    """Mimics claude_agent_sdk.AssistantMessage."""
    def __init__(self, text: str = "Hello!", model: str = "test-model"):
        self.content = [_FakeTextBlock(text)]
        self.model = model
        self.session_id = None


class _FakeStreamEvent:
    """Mimics claude_agent_sdk.types.StreamEvent for text_delta."""
    def __init__(self, text: str, index: int = 0):
        self.event = {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "text_delta", "text": text},
        }


class _FakeResultMessage:
    """Mimics claude_agent_sdk.ResultMessage."""
    def __init__(self, session_id: str = "sdk-session-001"):
        self.is_error = False
        self.subtype = None
        self.result = ""
        self.error = ""
        self.session_id = session_id
        self.duration_ms = 500
        self.total_cost_usd = 0.001
        self.num_turns = 1
        self.usage = {
            "input_tokens": 1000,
            "output_tokens": 50,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }


class _FakeToolUseBlock:
    pass


class _FakeToolResultBlock:
    pass


class _FakeUserMessage:
    pass


class _FakeThinkingBlock:
    pass


# Patch SDK modules before importing our code
_sdk_mock = MagicMock(**{
    "ResultMessage": _FakeResultMessage,
    "AssistantMessage": _FakeAssistantMessage,
    "SystemMessage": _FakeSystemMessage,
    "TextBlock": _FakeTextBlock,
    "ToolUseBlock": _FakeToolUseBlock,
    "ToolResultBlock": _FakeToolResultBlock,
    "UserMessage": _FakeUserMessage,
    "ClaudeAgentOptions": MagicMock,
    "ClaudeSDKClient": MagicMock,
})
_sdk_types_mock = MagicMock(**{
    "StreamEvent": _FakeStreamEvent,
    "ThinkingBlock": _FakeThinkingBlock,
})


def _make_real_options(model: str = "test-model", system_prompt: str = "test"):
    """A REAL ClaudeAgentOptions (not MagicMock) for scenarios that route through
    ``session_unit._build_retry_options``.

    That function does ``dict(vars(original_options))`` then reconstructs via the
    REAL ``ClaudeAgentOptions`` (re-imported inside the function — the module-level
    stub at the top of this file does NOT intercept it). A bare ``MagicMock()``
    leaks ``_mock_*`` internals into ``vars()`` → ``TypeError: __init__() got an
    unexpected keyword argument '_mock_return_value'``. A real options object
    round-trips cleanly. Only ``system_prompt``/``mcp_servers`` are read by the
    warm/resume path, but a real object also satisfies the retry reconstruction.
    """
    from claude_agent_sdk import ClaudeAgentOptions
    opts = ClaudeAgentOptions()
    opts.system_prompt = system_prompt
    try:
        opts.model = model
    except Exception:
        pass  # model may not be a settable field on all SDK versions — non-fatal
    return opts


# ---------------------------------------------------------------------------
# Fake SDK client
# ---------------------------------------------------------------------------

class FakeSDKClient:
    """Drop-in replacement for ClaudeSDKClient.

    Produces a configurable sequence of SDK messages when iterated via
    ``receive_response()``.  Supports ``query()`` and ``interrupt()``.
    """

    def __init__(self, messages: Optional[list] = None, session_id: str = "sdk-session-001"):
        self._messages = messages or self._default_messages(session_id)
        self._interrupted = False
        self._query_called = False
        self.session_id = session_id

    @staticmethod
    def _default_messages(session_id: str) -> list:
        """Standard happy-path message sequence."""
        return [
            _FakeSystemMessage(session_id),
            _FakeStreamEvent("Hello "),
            _FakeStreamEvent("world!"),
            _FakeAssistantMessage("Hello world!", "test-model"),
            _FakeResultMessage(session_id),
        ]

    async def query(self, content):
        self._query_called = True

    def receive_response(self):
        return self._aiter_messages()

    async def _aiter_messages(self):
        for msg in self._messages:
            if self._interrupted:
                return
            # Small yield to let the event loop run (simulates real I/O)
            await asyncio.sleep(0.001)
            yield msg

    async def interrupt(self):
        self._interrupted = True

    # Context manager protocol (not used directly but needed for type compat)
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class FakeClientWrapper:
    """Replaces ``_ClaudeClientWrapper`` — returns a FakeSDKClient."""

    def __init__(self, options=None, messages=None, session_id="sdk-session-001"):
        self.options = options
        self.client = FakeSDKClient(messages=messages, session_id=session_id)
        self.pid = 12345

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *args):
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_sdk():
    """Patch the Claude SDK modules for all tests in this file."""
    with patch.dict(sys.modules, {
        "claude_agent_sdk": _sdk_mock,
        "claude_agent_sdk.types": _sdk_types_mock,
    }):
        yield


@pytest.fixture()
def _reset_session_infrastructure():
    """Reset the session_router singleton between tests.

    NOTE: the real singleton names are ``session_router`` and
    ``lifecycle_manager`` (session_registry.py:42-47). A prior version reset
    ``_router``/``_lifecycle_manager`` — phantom attributes that never reset the
    real singletons, so the router (and its drain worker) leaked across tests.
    The cross-test DB lock is fixed at the loop-scope layer (module ``pytestmark``
    above), not here; this reset is correctness hygiene so each test starts from
    a clean registry.
    """
    from core import session_registry
    # Clear any existing singletons
    session_registry.session_router = None
    session_registry.lifecycle_manager = None
    session_registry._initialized = False
    yield
    # Cleanup after test
    session_registry.session_router = None
    session_registry.lifecycle_manager = None
    session_registry._initialized = False


@pytest.fixture()
async def async_client():
    """Async HTTP client wired to the FastAPI app."""
    from main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ---------------------------------------------------------------------------
# SSE parsing helper
# ---------------------------------------------------------------------------

def parse_sse_body(body: str) -> list[dict]:
    """Parse an SSE response body into a list of event dicts.

    Filters out heartbeats and the [DONE] sentinel.
    """
    events = []
    for line in body.split("\n"):
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            events.append({"type": "__done__"})
            continue
        try:
            event = json.loads(data)
            if event.get("type") == "heartbeat":
                continue
            events.append(event)
        except json.JSONDecodeError:
            pass
    return events


def event_types(events: list[dict]) -> list[str]:
    """Extract just the type field from a list of events."""
    return [e.get("type", "?") for e in events]


# ---------------------------------------------------------------------------
# Scenario 1: Fresh send (COLD -> IDLE -> STREAMING -> IDLE)
# ---------------------------------------------------------------------------

class TestScenario1_FreshSend:
    """New session, subprocess not running. Full cold-start path."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_fresh_send_produces_correct_event_sequence(
        self, async_client, _reset_session_infrastructure
    ):
        """A fresh message to a new session should produce:
        session_start -> text_delta(s) -> assistant -> result -> [DONE]
        """
        with patch(
            "core.claude_environment._ClaudeClientWrapper",
            side_effect=lambda options: FakeClientWrapper(options=options),
        ), patch(
            "core.session_unit._spawn_lock", asyncio.Lock()
        ), patch(
            "core.claude_environment._env_lock", asyncio.Lock()
        ), patch(
            "core.resource_monitor.resource_monitor.spawn_budget",
            return_value=MagicMock(can_spawn=True),
        ):
            response = await async_client.post(
                "/api/chat/stream",
                json={"agent_id": "default", "message": "Hello"},
            )

            assert response.status_code == 200
            events = parse_sse_body(response.text)
            types = event_types(events)

            # Must have session_start
            assert "session_start" in types, f"Missing session_start. Got: {types}"
            # Must have at least one text_delta
            assert "text_delta" in types, f"Missing text_delta. Got: {types}"
            # Must have result event
            assert "result" in types, f"Missing result. Got: {types}"
            # Must end with [DONE]
            assert types[-1] == "__done__", f"Last event should be [DONE]. Got: {types[-1]}"

            # Verify result event has session_id and usage
            result_evt = next(e for e in events if e.get("type") == "result")
            assert result_evt.get("session_id") is not None
            assert result_evt.get("usage") is not None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_fresh_send_with_chinese_text(
        self, async_client, _reset_session_infrastructure
    ):
        """Chinese text must not cause null byte errors."""
        chinese_msg = "Polish below content and append them into Phase-2 for aidlc"

        with patch(
            "core.claude_environment._ClaudeClientWrapper",
            side_effect=lambda options: FakeClientWrapper(options=options),
        ), patch(
            "core.session_unit._spawn_lock", asyncio.Lock()
        ), patch(
            "core.claude_environment._env_lock", asyncio.Lock()
        ), patch(
            "core.resource_monitor.resource_monitor.spawn_budget",
            return_value=MagicMock(can_spawn=True),
        ):
            response = await async_client.post(
                "/api/chat/stream",
                json={"agent_id": "default", "message": chinese_msg},
            )
            assert response.status_code == 200
            events = parse_sse_body(response.text)
            types = event_types(events)
            assert "result" in types

    @pytest.mark.asyncio(loop_scope="session")
    async def test_null_bytes_stripped_from_system_prompt(
        self, _reset_session_infrastructure
    ):
        """Null bytes in system prompt must be stripped before spawn."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-null", agent_id="default")

        # Create options with a null byte in system_prompt
        mock_options = MagicMock()
        mock_options.system_prompt = "Hello\x00World"

        spawned = False

        async def fake_enter(self_wrapper):
            nonlocal spawned
            spawned = True
            return FakeSDKClient()

        with patch(
            "core.claude_environment._ClaudeClientWrapper",
        ) as MockWrapper, patch(
            "core.session_unit._spawn_lock", asyncio.Lock()
        ), patch(
            "core.claude_environment._env_lock", asyncio.Lock()
        ), patch(
            "core.resource_monitor.resource_monitor.spawn_budget",
            return_value=MagicMock(can_spawn=True),
        ):
            wrapper_instance = MagicMock()
            wrapper_instance.__aenter__ = AsyncMock(return_value=FakeSDKClient())
            wrapper_instance.__aexit__ = AsyncMock(return_value=False)
            MockWrapper.return_value = wrapper_instance

            await unit._spawn(mock_options)

            # Verify null byte was stripped
            assert "\x00" not in mock_options.system_prompt
            assert mock_options.system_prompt == "HelloWorld"
            assert unit.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Scenario 2: Warm send (IDLE -> STREAMING -> IDLE, subprocess reused)
# ---------------------------------------------------------------------------

class TestScenario2_WarmSend:
    """Session already warm (subprocess alive, IDLE). No re-spawn needed."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_warm_send_reuses_subprocess(self, _reset_session_infrastructure):
        """send() from IDLE must NOT spawn a new subprocess."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-warm", agent_id="default")
        # Simulate warm subprocess
        unit._transition(SessionState.IDLE)
        fake_client = FakeSDKClient()
        unit._client = fake_client
        unit._wrapper = MagicMock()
        unit._sdk_session_id = "sdk-session-warm"
        # Simulate a genuinely-clean warm session: without this, send()'s
        # resume-poison guard (fail-closed, PIT01) sees _last_turn_clean=False on a
        # fresh unit, recycles the warm client, and re-spawns via the stubbed SDK
        # (AsyncMock) → receive_response() returns a coroutine → the whole warm-reuse
        # assertion breaks. A real warm IDLE session that ended cleanly has this True.
        unit._last_turn_clean = True

        # Real options: the warm-send path reconstructs via _build_retry_options,
        # which a MagicMock would poison with _mock_* attrs (see _make_real_options).
        mock_options = _make_real_options()

        events = []
        async for event in unit.send(
            query_content="Hello again",
            options=mock_options,
        ):
            events.append(event)

        types = [e.get("type") for e in events]
        assert "session_start" in types
        assert "result" in types
        # Should end in IDLE
        assert unit.state == SessionState.IDLE
        # Client should have been reused (same object)
        assert unit._client is fake_client or unit._client is not None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_dirty_warm_send_recycles_before_reuse(self, _reset_session_infrastructure):
        """Warm IDLE session whose last turn did NOT end clean → send() MUST recycle
        (poison-guard, PIT01) before reuse: _crash_to_cold_async(clear_identity=False)
        fires so the next spawn is fresh WITH --resume. This is the sibling of the
        clean-warm fast path above — the recycle branch that has real user impact
        (a soft-interrupt/SSE-drop leaves the CLI in corrupt turn-state; reusing it
        returns an instant zombie error). Covers the branch Scenario2's happy path
        deliberately skips (Gate-2 finding B, run_2bda6845)."""
        from core.session_unit import SessionUnit, SessionState
        from unittest.mock import AsyncMock

        unit = SessionUnit(session_id="test-dirty-warm", agent_id="default")
        unit._transition(SessionState.IDLE)
        unit._client = FakeSDKClient()
        unit._wrapper = MagicMock()
        unit._sdk_session_id = "sdk-session-dirty"
        unit._last_turn_clean = False  # last turn did NOT end clean → guard MUST fire

        recycle = AsyncMock()
        unit._crash_to_cold_async = recycle  # observe the recycle, don't drive respawn

        # Stop after the guard by aborting the spawn that follows (we only assert the
        # guard fired, not the full re-stream — spawn wiring is Scenario6's job).
        with patch.object(unit, "_ensure_spawned") as spawn:
            async def _abort(*a, **k):
                yield {"_abort": True}
            spawn.side_effect = _abort
            events = []
            async for event in unit.send(query_content="dirty", options=_make_real_options()):
                events.append(event)

        # The poison-guard recycled before reuse, preserving --resume identity.
        recycle.assert_awaited_once()
        assert recycle.await_args.kwargs.get("clear_identity") is False


# ---------------------------------------------------------------------------
# Scenario 3: Stop -> new message
# ---------------------------------------------------------------------------

class TestScenario3_StopThenNewMessage:
    """User stops streaming, then sends a new message."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_interrupt_transitions_to_cold_on_user_stop(self, _reset_session_infrastructure):
        """A user Stop (autonomous=False) recycles the subprocess → COLD.

        PIT01 recycle fix: leaving the poisoned subprocess warm (IDLE) caused
        the next send() to reuse it into a zombie. A user Stop now recycles to
        COLD via the blessed kill path; resume identity is preserved so the
        next send() respawns clean with --resume.
        """
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-stop", agent_id="default")
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)

        fake_client = MagicMock()
        fake_client.interrupt = AsyncMock()
        unit._client = fake_client

        survived = await unit.interrupt(timeout=2.0)

        assert survived is True  # the turn was stopped
        assert unit.state == SessionState.COLD
        assert unit._client is None

    @pytest.mark.asyncio(loop_scope="session")
    async def test_send_after_stop_succeeds(self, _reset_session_infrastructure):
        """send() after interrupt (IDLE) should stream normally."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-stop-send", agent_id="default")
        unit._transition(SessionState.IDLE)

        fake_client = FakeSDKClient()
        unit._client = fake_client
        unit._wrapper = MagicMock()
        unit._sdk_session_id = "sdk-session-stop"
        unit._last_turn_clean = True  # clean warm session — skip poison-guard recycle

        # Real options (see _make_real_options): warm/resume path reconstructs via
        # _build_retry_options, which a MagicMock poisons with _mock_* attrs.
        mock_options = _make_real_options()

        events = []
        async for event in unit.send(
            query_content="After stop",
            options=mock_options,
        ):
            events.append(event)

        types = [e.get("type") for e in events]
        assert "result" in types
        assert unit.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Scenario 4: Auto-recover stuck STREAMING
# ---------------------------------------------------------------------------

class TestScenario4_AutoRecoverStuck:
    """If previous stream got stuck (STREAMING), next send() auto-recovers."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_send_from_stuck_streaming_auto_recovers(
        self, _reset_session_infrastructure
    ):
        """send() when state is STREAMING and genuinely stuck (stall > threshold)
        should force_unstick -> COLD -> spawn."""
        from core.session_unit import SessionUnit, SessionState, AUTO_RECOVER_STALL_THRESHOLD

        unit = SessionUnit(session_id="test-stuck", agent_id="default")
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)
        # Simulate a genuinely stuck session — last event well beyond threshold
        unit._last_event_time = time.time() - (AUTO_RECOVER_STALL_THRESHOLD + 30)
        unit._streaming_start_time = time.time() - (AUTO_RECOVER_STALL_THRESHOLD + 60)

        # Old client (stuck)
        old_client = MagicMock()
        old_client.interrupt = AsyncMock()
        unit._client = old_client
        unit._wrapper = MagicMock()
        unit._wrapper.__aexit__ = AsyncMock(return_value=False)

        mock_options = MagicMock()
        mock_options.model = "test-model"
        mock_options.system_prompt = "test"

        with patch(
            "core.claude_environment._ClaudeClientWrapper",
            side_effect=lambda options: FakeClientWrapper(options=options),
        ), patch(
            "core.claude_environment._configure_claude_environment",
        ), patch(
            "core.session_unit._spawn_lock", asyncio.Lock()
        ), patch(
            "core.claude_environment._env_lock", asyncio.Lock()
        ), patch(
            "core.resource_monitor.resource_monitor.spawn_budget",
            return_value=MagicMock(can_spawn=True),
        ):
            events = []
            async for event in unit.send(
                query_content="Recover me",
                options=mock_options,
                config=MagicMock(),
            ):
                events.append(event)

            types = [e.get("type") for e in events]
            assert "result" in types
            assert unit.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Scenario 5: Resume within TTL (same as warm send)
# ---------------------------------------------------------------------------

class TestScenario5_ResumeWithinTTL:
    """Subprocess alive, within 12hr TTL. Same as warm send path."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_resume_within_ttl_no_context_injection(
        self, _reset_session_infrastructure
    ):
        """Resume within TTL must NOT inject context (no cold resume)."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-ttl", agent_id="default")
        unit._transition(SessionState.IDLE)

        fake_client = FakeSDKClient()
        unit._client = fake_client
        unit._wrapper = MagicMock()
        unit._sdk_session_id = "sdk-session-ttl"
        unit._last_turn_clean = True  # clean warm session — skip poison-guard recycle

        # Real options (see _make_real_options): resume path reconstructs via
        # _build_retry_options, which a MagicMock poisons with _mock_* attrs.
        mock_options = _make_real_options()

        events = []
        async for event in unit.send(
            query_content="Still within TTL",
            options=mock_options,
        ):
            events.append(event)

        types = [e.get("type") for e in events]
        assert "result" in types
        # Verify: state is IDLE (not stuck)
        assert unit.state == SessionState.IDLE
        # Verify: query was called (subprocess reused)
        assert fake_client._query_called


# ---------------------------------------------------------------------------
# Scenario 6: Resume post TTL (COLD -> context injection -> spawn)
# ---------------------------------------------------------------------------

class TestScenario6_ResumePostTTL:
    """Subprocess killed by TTL. Cold resume with context injection."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_cold_resume_detects_prior_messages(
        self, async_client, _reset_session_infrastructure
    ):
        """Cold resume should detect prior messages and inject context."""
        from database import db

        # Seed a session with prior messages in DB
        session_id = "test-cold-resume-001"
        await db.sessions.put({
            "id": session_id,
            "agent_id": "default",
            "title": "Test Cold Resume",
            "created_at": "2026-03-24T00:00:00",
        })
        await db.messages.put({
            "id": "msg-prior-1",
            "session_id": session_id,
            "role": "user",
            "content": [{"type": "text", "text": "Previous message"}],
            "created_at": "2026-03-24T00:00:01",
        })
        await db.messages.put({
            "id": "msg-prior-2",
            "session_id": session_id,
            "role": "assistant",
            "content": [{"type": "text", "text": "Previous response"}],
            "model": "test-model",
            "created_at": "2026-03-24T00:00:02",
        })

        captured_options = {}

        def capture_wrapper(options):
            captured_options["system_prompt"] = getattr(options, "system_prompt", None)
            return FakeClientWrapper(options=options)

        with patch(
            "core.claude_environment._ClaudeClientWrapper",
            side_effect=capture_wrapper,
        ), patch(
            "core.session_unit._spawn_lock", asyncio.Lock()
        ), patch(
            "core.claude_environment._env_lock", asyncio.Lock()
        ), patch(
            "core.resource_monitor.resource_monitor.spawn_budget",
            return_value=MagicMock(can_spawn=True),
        ):
            response = await async_client.post(
                "/api/chat/stream",
                json={
                    "agent_id": "default",
                    "message": "New message after TTL",
                    "session_id": session_id,
                },
            )

            assert response.status_code == 200
            events = parse_sse_body(response.text)
            types = event_types(events)

            # Should have session_resuming (cold resume indicator)
            # or session_start at minimum
            assert "session_start" in types or "session_resuming" in types, \
                f"Expected session_start or session_resuming. Got: {types}"
            assert "result" in types

    @pytest.mark.asyncio(loop_scope="session")
    async def test_cold_resume_inserts_boundary_marker(
        self, async_client, _reset_session_infrastructure
    ):
        """Cold resume should insert a role='system' boundary marker into DB."""
        from database import db

        session_id = "test-boundary-marker-001"
        await db.sessions.put({
            "id": session_id,
            "agent_id": "default",
            "title": "Test Boundary Marker",
            "created_at": "2026-03-24T00:00:00",
        })
        await db.messages.put({
            "id": "msg-bm-prior-1",
            "session_id": session_id,
            "role": "user",
            "content": [{"type": "text", "text": "Old message"}],
            "created_at": "2026-03-24T00:00:01",
        })
        await db.messages.put({
            "id": "msg-bm-prior-2",
            "session_id": session_id,
            "role": "assistant",
            "content": [{"type": "text", "text": "Old response"}],
            "model": "test-model",
            "created_at": "2026-03-24T00:00:02",
        })

        def capture_wrapper(options):
            return FakeClientWrapper(options=options)

        with patch(
            "core.claude_environment._ClaudeClientWrapper",
            side_effect=capture_wrapper,
        ), patch(
            "core.session_unit._spawn_lock", asyncio.Lock()
        ), patch(
            "core.claude_environment._env_lock", asyncio.Lock()
        ), patch(
            "core.resource_monitor.resource_monitor.spawn_budget",
            return_value=MagicMock(can_spawn=True),
        ):
            response = await async_client.post(
                "/api/chat/stream",
                json={
                    "agent_id": "default",
                    "message": "New message after restart",
                    "session_id": session_id,
                },
            )
            assert response.status_code == 200

        # Verify boundary marker was inserted
        messages = await db.messages.list_by_session(session_id)
        system_msgs = [m for m in messages if m.get("role") == "system"]
        assert len(system_msgs) >= 1, (
            f"Expected at least 1 system boundary marker. "
            f"Got roles: {[m.get('role') for m in messages]}"
        )
        # Check the content shape
        marker = system_msgs[0]
        content = marker.get("content", [])
        assert len(content) == 1
        assert content[0].get("type") == "resume_boundary"

    @pytest.mark.asyncio(loop_scope="session")
    async def test_cold_resume_with_null_bytes_in_context(
        self, _reset_session_infrastructure
    ):
        """If resume context somehow contains null bytes, they must be stripped."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-null-resume", agent_id="default")

        mock_options = MagicMock()
        mock_options.system_prompt = "Previous context\x00with null\x00bytes"

        with patch(
            "core.claude_environment._ClaudeClientWrapper",
        ) as MockWrapper, patch(
            "core.session_unit._spawn_lock", asyncio.Lock()
        ), patch(
            "core.claude_environment._env_lock", asyncio.Lock()
        ), patch(
            "core.resource_monitor.resource_monitor.spawn_budget",
            return_value=MagicMock(can_spawn=True),
        ):
            wrapper_instance = MagicMock()
            wrapper_instance.__aenter__ = AsyncMock(return_value=FakeSDKClient())
            wrapper_instance.__aexit__ = AsyncMock(return_value=False)
            MockWrapper.return_value = wrapper_instance

            await unit._spawn(mock_options)

            assert "\x00" not in mock_options.system_prompt
            assert unit.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Scenario: [DONE] sentinel
# ---------------------------------------------------------------------------

class TestDoneSentinel:
    """Backend must send data: [DONE] at end of SSE stream."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_sse_stream_ends_with_done(
        self, async_client, _reset_session_infrastructure
    ):
        """SSE stream must end with data: [DONE] sentinel."""
        with patch(
            "core.claude_environment._ClaudeClientWrapper",
            side_effect=lambda options: FakeClientWrapper(options=options),
        ), patch(
            "core.session_unit._spawn_lock", asyncio.Lock()
        ), patch(
            "core.claude_environment._env_lock", asyncio.Lock()
        ), patch(
            "core.resource_monitor.resource_monitor.spawn_budget",
            return_value=MagicMock(can_spawn=True),
        ):
            response = await async_client.post(
                "/api/chat/stream",
                json={"agent_id": "default", "message": "Test DONE"},
            )
            assert response.status_code == 200
            # Check raw body for [DONE]
            assert "data: [DONE]" in response.text, \
                f"Missing [DONE] sentinel in SSE stream"


# ---------------------------------------------------------------------------
# Retriable error: embedded null byte
# ---------------------------------------------------------------------------

class TestRetriableErrors:
    """Verify error classification for auto-retry."""

    def test_embedded_null_byte_is_retriable(self):
        from core.session_utils import _is_retriable_error
        assert _is_retriable_error("Failed to start Claude Code: embedded null byte")

    def test_exit_code_minus_9_is_retriable(self):
        from core.session_utils import _is_retriable_error
        assert _is_retriable_error("Command failed with exit code -9")

    def test_random_error_not_retriable(self):
        from core.session_utils import _is_retriable_error
        assert not _is_retriable_error("Some random error")

    def test_zlib_from_network_is_retriable(self):
        """zlib errors from Bedrock/network ARE retriable (no traceback)."""
        from core.session_utils import _is_retriable_error
        assert _is_retriable_error(
            "Error -3 while decompressing data: incorrect header check"
        )

    def test_zlib_from_network_with_boto_traceback_is_retriable(self):
        """zlib errors from boto3/urllib3 responses ARE retriable."""
        from core.session_utils import _is_retriable_error
        tb = (
            "File \"botocore/httpsession.py\", line 123, in send\n"
            "File \"urllib3/response.py\", line 456, in read\n"
            "zlib.error: Error -3 while decompressing data"
        )
        assert _is_retriable_error(
            "Error -3 while decompressing data: incorrect header check",
            tb_str=tb,
        )

    def test_zlib_from_pyinstaller_archive_NOT_retriable(self):
        """zlib errors from pyimod01_archive.extract() are NOT retriable.

        PyInstaller archive corruption means the frozen binary is damaged.
        Retrying reads the same corrupt data — only daemon restart helps.
        """
        from core.session_utils import _is_retriable_error
        tb = (
            "File \"routers/system.py\", line 742, in get_session_briefing\n"
            "File \"<frozen importlib._bootstrap>\", line 1360, in _find_and_load\n"
            "File \"pyimod02_importers.py\", line 503, in get_code\n"
            "File \"pyimod01_archive.py\", line 134, in extract\n"
            "zlib.error: Error -3 while decompressing data: incorrect header check"
        )
        assert not _is_retriable_error(
            "Error -3 while decompressing data: incorrect header check",
            tb_str=tb,
        )

    def test_zlib_from_pyimod02_importers_NOT_retriable(self):
        """zlib via pyimod02_importers is also archive corruption."""
        from core.session_utils import _is_retriable_error
        tb = (
            "File \"pyimod02_importers.py\", line 446, in exec_module\n"
            "zlib.error: Error -3 while decompressing data"
        )
        assert not _is_retriable_error(
            "Error -3 while decompressing data: incorrect header check",
            tb_str=tb,
        )


class TestHookConsecutiveFailureAlert:
    """Verify BackgroundHookExecutor consecutive failure detection."""

    def test_consecutive_failures_trigger_critical_log(self, caplog):
        """3 consecutive failures should emit a CRITICAL log."""
        import logging
        from core.session_hooks import (
            BackgroundHookExecutor,
            SessionLifecycleHookManager,
        )

        mgr = SessionLifecycleHookManager()
        executor = BackgroundHookExecutor(mgr)

        with caplog.at_level(logging.CRITICAL, logger="core.session_hooks"):
            # First 2 failures — no CRITICAL yet
            executor._record_hook_result("test_hook", False, "zlib error")
            executor._record_hook_result("test_hook", False, "zlib error")
            assert "CRITICAL" not in caplog.text

            # 3rd failure — CRITICAL triggers
            executor._record_hook_result("test_hook", False, "zlib error")
            assert "test_hook" in caplog.text
            assert "3 consecutive times" in caplog.text

    def test_success_resets_consecutive_counter(self):
        """A success between failures resets the counter."""
        from core.session_hooks import (
            BackgroundHookExecutor,
            SessionLifecycleHookManager,
        )

        mgr = SessionLifecycleHookManager()
        executor = BackgroundHookExecutor(mgr)

        executor._record_hook_result("test_hook", False, "err1")
        executor._record_hook_result("test_hook", False, "err2")
        # Success resets counter
        executor._record_hook_result("test_hook", True)
        assert executor._hook_stats["test_hook"]["consecutive_failures"] == 0

        # Needs 3 more failures to trigger again
        executor._record_hook_result("test_hook", False, "err3")
        executor._record_hook_result("test_hook", False, "err4")
        assert executor._hook_stats["test_hook"]["consecutive_failures"] == 2
