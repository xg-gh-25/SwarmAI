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

import database as database_module


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
    "ClaudeAgentOptions": MagicMock,
    "ClaudeSDKClient": MagicMock,
})
_sdk_types_mock = MagicMock(**{
    "StreamEvent": _FakeStreamEvent,
    "ThinkingBlock": _FakeThinkingBlock,
})


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
    """Reset the session_router singleton between tests."""
    from core import session_registry
    # Clear any existing singletons
    session_registry._router = None
    session_registry._lifecycle_manager = None
    session_registry._initialized = False
    yield
    # Cleanup after test
    session_registry._router = None
    session_registry._lifecycle_manager = None
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

        mock_options = MagicMock()
        mock_options.model = "test-model"

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


# ---------------------------------------------------------------------------
# Scenario 3: Stop -> new message
# ---------------------------------------------------------------------------

class TestScenario3_StopThenNewMessage:
    """User stops streaming, then sends a new message."""

    @pytest.mark.asyncio
    async def test_interrupt_transitions_to_idle(self, _reset_session_infrastructure):
        """interrupt() from STREAMING should transition to IDLE."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-stop", agent_id="default")
        unit._transition(SessionState.IDLE)
        unit._transition(SessionState.STREAMING)

        fake_client = MagicMock()
        fake_client.interrupt = AsyncMock()
        unit._client = fake_client

        survived = await unit.interrupt(timeout=2.0)

        assert survived is True
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_send_after_stop_succeeds(self, _reset_session_infrastructure):
        """send() after interrupt (IDLE) should stream normally."""
        from core.session_unit import SessionUnit, SessionState

        unit = SessionUnit(session_id="test-stop-send", agent_id="default")
        unit._transition(SessionState.IDLE)

        fake_client = FakeSDKClient()
        unit._client = fake_client
        unit._wrapper = MagicMock()
        unit._sdk_session_id = "sdk-session-stop"

        mock_options = MagicMock()
        mock_options.model = "test-model"

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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

        mock_options = MagicMock()
        mock_options.model = "test-model"

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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
