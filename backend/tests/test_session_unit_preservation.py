"""Preservation property tests for _read_formatted_response() baseline behavior.

Captures the correct behavior of non-ResultMessage processing and no-usage
ResultMessages on UNFIXED code.  These tests verify paths that do NOT trigger
the NameError bug (Bug 1) and must PASS on both unfixed and fixed code.

**Validates: Requirements 3.1, 3.2, 3.6, 3.7**

Testing methodology: unit tests with mocked Claude SDK types, verifying that
message types other than ResultMessage-with-positive-usage continue to produce
the expected SSE event formats and state transitions.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


# ---------------------------------------------------------------------------
# Mock SDK types — same pattern as test_session_unit_nameerror_bug.py
# ---------------------------------------------------------------------------

class _MockResultMessage:
    """Mock for claude_agent_sdk.ResultMessage."""
    pass


class _MockAssistantMessage:
    """Mock for claude_agent_sdk.AssistantMessage."""
    pass


class _MockSystemMessage:
    """Mock for claude_agent_sdk.SystemMessage."""
    pass


class _MockTextBlock:
    """Mock for claude_agent_sdk.TextBlock."""
    pass


class _MockToolUseBlock:
    """Mock for claude_agent_sdk.ToolUseBlock."""
    pass


class _MockToolResultBlock:
    """Mock for claude_agent_sdk.ToolResultBlock."""
    pass


class _MockUserMessage:
    """Mock for claude_agent_sdk.UserMessage (carries sub-agent tool results)."""
    pass


class _MockStreamEvent:
    """Mock for claude_agent_sdk.types.StreamEvent."""
    pass


class _MockThinkingBlock:
    """Mock for claude_agent_sdk.types.ThinkingBlock."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _patch_sdk_modules:
    """Context manager that injects mock SDK modules and patches the
    permission_manager to avoid cross-event-loop Queue errors in xdist."""

    def __enter__(self):
        self._dict_patch = patch.dict(sys.modules, {
            "claude_agent_sdk": MagicMock(**{
                "ResultMessage": _MockResultMessage,
                "AssistantMessage": _MockAssistantMessage,
                "SystemMessage": _MockSystemMessage,
                "TextBlock": _MockTextBlock,
                "ToolUseBlock": _MockToolUseBlock,
                "ToolResultBlock": _MockToolResultBlock,
                "UserMessage": _MockUserMessage,
            }),
            "claude_agent_sdk.types": MagicMock(**{
                "StreamEvent": _MockStreamEvent,
                "ThinkingBlock": _MockThinkingBlock,
            }),
        })
        # Patch permission_manager.get_session_queue to return a fresh Queue
        # bound to the current event loop (avoids "bound to a different event
        # loop" RuntimeError when xdist reuses workers).
        self._pm_patch = patch(
            "core.permission_manager.permission_manager.get_session_queue",
            return_value=asyncio.Queue(),
        )
        # Mock the fire-and-forget token-usage DB write. The orchestrator
        # schedules `asyncio.get_running_loop().create_task(db.record_token_usage(...))`
        # after a result yield; with the real coroutine it outlives the
        # function-scoped event loop → "Task was destroyed but it is pending"
        # unraisable warnings that pytest escalates to ERRORs on a later test
        # whose abandoned async generator triggers the GC (the blank-result
        # ordering tests below). An AsyncMock resolves instantly → no pending
        # task at loop close. Test-only hygiene; does not touch prod behavior.
        self._db_patch = patch(
            "database.db.record_token_usage",
            new=AsyncMock(return_value=None),
        )
        self._dict_patch.start()
        try:
            self._pm_patch.start()
            self._db_patch.start()
        except Exception:
            self._dict_patch.stop()
            raise
        return self

    def __exit__(self, *exc):
        self._db_patch.stop()
        self._pm_patch.stop()
        self._dict_patch.stop()
        return False


def _make_unit(session_id: str = "test-preservation") -> SessionUnit:
    """Create a SessionUnit in STREAMING state with a mocked client."""
    unit = SessionUnit(session_id=session_id, agent_id="default")
    unit._transition(SessionState.IDLE)       # COLD→IDLE
    unit._transition(SessionState.STREAMING)   # IDLE→STREAMING
    return unit


async def _collect_events(unit: SessionUnit) -> list[dict]:
    """Iterate _read_formatted_response() and collect all yielded events."""
    events: list[dict] = []
    async for event in unit._read_formatted_response():
        events.append(event)
    return events


def _make_result_message(usage=None):
    """Create a mock ResultMessage with configurable usage data."""
    msg = _MockResultMessage()
    msg.is_error = False
    msg.subtype = None
    msg.result = ""
    msg.error = ""
    msg.usage = usage
    msg.duration_ms = 1000
    msg.total_cost_usd = 0.01
    msg.num_turns = 1
    msg.session_id = None
    return msg


def _set_mock_client(unit: SessionUnit, messages: list):
    """Wire a list of mock messages into the unit's client."""
    async def _mock_response():
        for msg in messages:
            yield msg

    mock_client = MagicMock()
    mock_client.receive_response = MagicMock(return_value=_mock_response())
    unit._client = mock_client


# ---------------------------------------------------------------------------
# Preservation Tests — ResultMessage with no/zero usage
# ---------------------------------------------------------------------------

class TestResultMessageNoUsagePreservation:
    """ResultMessage with no usage data must complete without error.

    These paths do NOT trigger the NameError because the condition
    ``if input_tokens and input_tokens > 0 and options:`` short-circuits
    before reaching the undefined ``options`` variable.

    **Validates: Requirements 3.1, 3.6, 3.7**
    """

    @pytest.mark.asyncio
    async def test_result_message_usage_none(self):
        """ResultMessage with usage=None raises RuntimeError (empty response guard).

        When usage is None and no content was emitted during the stream,
        the empty-response guard detects this as a likely transient failure
        (429/503/timeout) and raises RuntimeError for the retry loop.
        This is correct behavior — an API call that produces zero tokens
        with no content should not silently succeed.
        """
        unit = _make_unit()
        _set_mock_client(unit, [_make_result_message(usage=None)])

        with _patch_sdk_modules():
            with pytest.raises(RuntimeError, match="API returned empty response"):
                await _collect_events(unit)

    @pytest.mark.asyncio
    async def test_result_message_usage_empty_dict(self):
        """ResultMessage with usage={} raises RuntimeError (empty response guard).

        When usage is {}, output_tokens evaluates to 0 and no content was
        emitted. The empty-response guard correctly identifies this as a
        transient API failure and raises for retry.
        """
        unit = _make_unit()
        _set_mock_client(unit, [_make_result_message(usage={})])

        with _patch_sdk_modules():
            with pytest.raises(RuntimeError, match="API returned empty response"):
                await _collect_events(unit)

    @pytest.mark.asyncio
    async def test_result_message_input_tokens_zero(self):
        """ResultMessage with input_tokens=0 yields result event, transitions STREAMING→IDLE.

        When input_tokens=0, ``if input_tokens`` evaluates to False (0 is falsy),
        so the context warning bridge is skipped — the bug is NOT triggered.
        """
        unit = _make_unit()
        _set_mock_client(unit, [_make_result_message(
            usage={"input_tokens": 0, "output_tokens": 50}
        )])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        result_events = [e for e in events if e.get("type") == "result"]
        assert len(result_events) == 1
        assert result_events[0]["usage"]["input_tokens"] == 0
        assert unit.state == SessionState.IDLE


class TestBlankResultDoesNotYieldResultEvent:
    """Regression: a blank / blank-success result must RAISE for retry BEFORE
    yielding any ``result`` event.

    THE BUG (frontend/backend desync, observed live on session 2e87b27f):
    the orchestrator used to yield the ``result`` SSE event and THEN run the
    blank-result guard. A ``result`` is the frontend's definitive turn-end
    signal — it stops the spinner, marks the tab idle, and clears pending
    state. So the blank turn was finalized in the UI; the subsequent send()
    respawn then streamed the retry into a tab the UI believed was done (the
    retry's ``session_start`` arrives in ``idle`` mode → the streaming reducer
    no-ops it and nothing re-arms ``isStreaming``). Moving the guard BEFORE the
    yield means a blank turn never reaches the frontend as a turn-end: send()
    respawns and the retry's ``session_start`` lands while the UI is still
    STREAMING, so spinner/input/tab-status stay consistent with the backend.

    These tests assert the ORDERING (no ``result`` leaks to the stream before
    the raise) — something the pure-predicate tests in
    test_blank_api_result_retry.py cannot check (they only verify the boolean).
    """

    async def _collect_until_raise(self, unit) -> list[dict]:
        """Iterate _read_formatted_response, accumulating every event yielded
        BEFORE the expected blank-result RuntimeError fires."""
        events: list[dict] = []
        with pytest.raises(RuntimeError, match="API returned empty response"):
            async for event in unit._read_formatted_response():
                events.append(event)
        return events

    @pytest.mark.asyncio
    async def test_blank_success_yields_no_result_event(self):
        """subtype="success" + 0 output + no content → raises, no result event."""
        unit = _make_unit(session_id="test-blank-noyield")
        msg = _make_result_message(usage={"input_tokens": 0, "output_tokens": 0})
        msg.subtype = "success"  # the live prod signature (2e87b27f)
        _set_mock_client(unit, [msg])

        with _patch_sdk_modules():
            events = await self._collect_until_raise(unit)

        assert not any(e.get("type") == "result" for e in events), (
            "blank-success result must NOT be yielded before the retry raises — "
            "a result event finalizes the turn in the UI and desyncs the retry"
        )

    @pytest.mark.asyncio
    async def test_blank_empty_subtype_yields_no_result_event(self):
        """Empty subtype (Bedrock 429/503/timeout) → raises, no result event."""
        unit = _make_unit(session_id="test-blank-empty")
        msg = _make_result_message(usage={"input_tokens": 0, "output_tokens": 0})
        msg.subtype = ""
        _set_mock_client(unit, [msg])

        with _patch_sdk_modules():
            events = await self._collect_until_raise(unit)

        assert not any(e.get("type") == "result" for e in events)

    @pytest.mark.asyncio
    async def test_blank_after_init_emits_session_start_but_not_result(self):
        """The stream still emits session_start (the turn genuinely started),
        but the blank result raises before any result — proving the absent
        result event is the guard firing, not an early bail before streaming."""
        unit = _make_unit(session_id="test-blank-init")

        sys_msg = _MockSystemMessage()
        sys_msg.subtype = "init"
        sys_msg.data = {"session_id": "sdk-xyz"}
        sys_msg.session_id = None

        result_msg = _make_result_message(usage={"input_tokens": 0, "output_tokens": 0})
        result_msg.subtype = "success"

        _set_mock_client(unit, [sys_msg, result_msg])

        with _patch_sdk_modules():
            events = await self._collect_until_raise(unit)

        types = [e.get("type") for e in events]
        assert "session_start" in types
        assert "result" not in types

    @pytest.mark.asyncio
    async def test_nonblank_result_still_yields_result_event(self):
        """Guard must NOT over-capture: a normal result (output_tokens>0) still
        yields exactly one result event and transitions to IDLE."""
        unit = _make_unit(session_id="test-nonblank")
        msg = _make_result_message(usage={"input_tokens": 10, "output_tokens": 50})
        msg.subtype = "success"
        _set_mock_client(unit, [msg])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        result_events = [e for e in events if e.get("type") == "result"]
        assert len(result_events) == 1
        assert unit.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Preservation Tests — AssistantMessage processing
# ---------------------------------------------------------------------------

class TestAssistantMessagePreservation:
    """AssistantMessage processing must yield correct SSE event format.

    **Validates: Requirements 3.7**
    """

    @pytest.mark.asyncio
    async def test_assistant_message_with_text_block(self):
        """AssistantMessage with TextBlock yields {"type": "assistant", "content": [{"type": "text", ...}]}."""
        unit = _make_unit()

        # Build an AssistantMessage with a TextBlock
        text_block = _MockTextBlock()
        text_block.text = "Hello, world!"

        assistant_msg = _MockAssistantMessage()
        assistant_msg.content = [text_block]
        assistant_msg.model = "claude-sonnet-4-20250514"
        assistant_msg.session_id = None

        # Follow with a ResultMessage to complete the stream
        result_msg = _make_result_message(usage=None)

        _set_mock_client(unit, [assistant_msg, result_msg])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        assistant_events = [e for e in events if e.get("type") == "assistant"]
        assert len(assistant_events) == 1

        evt = assistant_events[0]
        assert len(evt["content"]) == 1
        assert evt["content"][0]["type"] == "text"
        assert evt["content"][0]["text"] == "Hello, world!"
        assert evt["model"] == "claude-sonnet-4-20250514"
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_empty_thinking_block_skipped(self):
        """AssistantMessage with an empty-content ThinkingBlock must NOT emit a
        thinking block. Bedrock CAN return thinking blocks with empty content +
        a signature under certain conditions (signature-only, redacted
        reasoning); persisting them pollutes the DB with ghost rows and renders
        nothing. The empty block must be dropped at the source. NOTE: empty is
        the rare exception — the common case under adaptive thinking is full
        plaintext (verified 2026-06-01, v1.17.5). This test guards the
        empty-block path specifically. See:
        Knowledge/Notes/2026-06-01-thinking-block-7layer-diagnosis.md"""
        unit = _make_unit()

        thinking_block = _MockThinkingBlock()
        thinking_block.thinking = ""          # rare case: redacted/empty content
        thinking_block.signature = "ErUBCkY..."  # signature present but useless without content

        text_block = _MockTextBlock()
        text_block.text = "The answer is 391."

        assistant_msg = _MockAssistantMessage()
        assistant_msg.content = [thinking_block, text_block]
        assistant_msg.model = "claude-opus-4-8"
        assistant_msg.session_id = None

        result_msg = _make_result_message(usage=None)
        _set_mock_client(unit, [assistant_msg, result_msg])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        assistant_events = [e for e in events if e.get("type") == "assistant"]
        assert len(assistant_events) == 1
        blocks = assistant_events[0]["content"]
        # The empty thinking block is dropped; only the text block survives.
        thinking_blocks = [b for b in blocks if b["type"] == "thinking"]
        assert thinking_blocks == [], "empty thinking block must not be persisted"
        text_blocks = [b for b in blocks if b["type"] == "text"]
        assert len(text_blocks) == 1
        assert text_blocks[0]["text"] == "The answer is 391."

    @pytest.mark.asyncio
    async def test_whitespace_only_thinking_block_skipped(self):
        """A ThinkingBlock with only whitespace content must also be skipped —
        matches the stated intent of dropping content-free thinking blocks."""
        unit = _make_unit()

        thinking_block = _MockThinkingBlock()
        thinking_block.thinking = "  \n  "   # whitespace-only
        thinking_block.signature = "ErUBsig..."

        text_block = _MockTextBlock()
        text_block.text = "done"

        assistant_msg = _MockAssistantMessage()
        assistant_msg.content = [thinking_block, text_block]
        assistant_msg.model = "claude-opus-4-8"
        assistant_msg.session_id = None

        result_msg = _make_result_message(usage=None)
        _set_mock_client(unit, [assistant_msg, result_msg])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        assistant_events = [e for e in events if e.get("type") == "assistant"]
        thinking_blocks = [b for b in assistant_events[0]["content"] if b["type"] == "thinking"]
        assert thinking_blocks == [], "whitespace-only thinking must not be persisted"

    @pytest.mark.asyncio
    async def test_empty_thinking_only_marks_content_emitted(self):
        """An AssistantMessage whose ONLY block is empty thinking must still set
        _content_emitted=True. The model DID respond (empty thinking is valid
        Opus 4.8 output); skipping the block must not remove the proof, or
        zombie-detection (streaming_dur<2s + not _content_emitted → kill+retry)
        would false-fire and kill a healthy subprocess. Regression guard for the
        empty-thinking fix removing an implicit safety net (LL08 pattern)."""
        unit = _make_unit()

        thinking_block = _MockThinkingBlock()
        thinking_block.thinking = ""
        thinking_block.signature = "ErUBsig..."

        assistant_msg = _MockAssistantMessage()
        assistant_msg.content = [thinking_block]  # ONLY an empty thinking block
        assistant_msg.model = "claude-opus-4-8"
        assistant_msg.session_id = None

        result_msg = _make_result_message(usage=None)
        _set_mock_client(unit, [assistant_msg, result_msg])

        with _patch_sdk_modules():
            await _collect_events(unit)

        assert unit._content_emitted is True, (
            "empty-thinking-only turn must mark content emitted to avoid "
            "false zombie-kill"
        )

    @pytest.mark.asyncio
    async def test_nonempty_thinking_preserves_signature(self):
        """A ThinkingBlock WITH content must be emitted with BOTH thinking and
        signature preserved (signature was previously dropped at line 2095)."""
        unit = _make_unit()

        thinking_block = _MockThinkingBlock()
        thinking_block.thinking = "Let me compute 17 * 23 step by step..."
        thinking_block.signature = "ErUBCkYIByJ..."

        assistant_msg = _MockAssistantMessage()
        assistant_msg.content = [thinking_block]
        assistant_msg.model = "claude-opus-4-6"
        assistant_msg.session_id = None

        result_msg = _make_result_message(usage=None)
        _set_mock_client(unit, [assistant_msg, result_msg])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        assistant_events = [e for e in events if e.get("type") == "assistant"]
        assert len(assistant_events) == 1
        thinking_blocks = [b for b in assistant_events[0]["content"] if b["type"] == "thinking"]
        assert len(thinking_blocks) == 1
        tb = thinking_blocks[0]
        assert tb["thinking"] == "Let me compute 17 * 23 step by step..."
        assert tb["signature"] == "ErUBCkYIByJ...", "signature must be preserved, not dropped"


# ---------------------------------------------------------------------------
# Preservation Tests — SystemMessage processing
# ---------------------------------------------------------------------------

class TestSystemMessagePreservation:
    """SystemMessage processing must yield correct SSE event format.

    **Validates: Requirements 3.7**
    """

    @pytest.mark.asyncio
    async def test_system_message_init_yields_session_start(self):
        """SystemMessage with subtype="init" yields {"type": "session_start", "sessionId": ...}."""
        unit = _make_unit(session_id="test-sys-init")

        sys_msg = _MockSystemMessage()
        sys_msg.subtype = "init"
        sys_msg.data = {"session_id": "sdk-session-123"}
        sys_msg.session_id = None

        # Non-empty usage so the result terminates the stream normally. An
        # empty/zero-output result would (correctly) trip the blank-result
        # retry guard — but this test is about SystemMessage(init) → session_start,
        # not empty-result handling (covered by test_result_message_* above).
        result_msg = _make_result_message(usage={"output_tokens": 50})

        _set_mock_client(unit, [sys_msg, result_msg])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        session_start_events = [e for e in events if e.get("type") == "session_start"]
        assert len(session_start_events) == 1
        assert session_start_events[0]["sessionId"] == "test-sys-init"
        assert unit.state == SessionState.IDLE


# ---------------------------------------------------------------------------
# Preservation Tests — StreamEvent processing
# ---------------------------------------------------------------------------

class TestStreamEventPreservation:
    """StreamEvent processing must yield correct SSE event format.

    **Validates: Requirements 3.7**
    """

    @pytest.mark.asyncio
    async def test_stream_event_text_delta(self):
        """StreamEvent with text_delta yields {"type": "text_delta", "text": ..., "index": ...}."""
        unit = _make_unit()

        stream_evt = _MockStreamEvent()
        stream_evt.event = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        }
        stream_evt.session_id = None

        result_msg = _make_result_message(usage=None)

        _set_mock_client(unit, [stream_evt, result_msg])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        text_delta_events = [e for e in events if e.get("type") == "text_delta"]
        assert len(text_delta_events) == 1
        assert text_delta_events[0]["text"] == "Hello"
        assert text_delta_events[0]["index"] == 0
        assert unit.state == SessionState.IDLE
