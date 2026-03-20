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
import time
from types import SimpleNamespace
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


class _MockStreamEvent:
    """Mock for claude_agent_sdk.types.StreamEvent."""
    pass


class _MockThinkingBlock:
    """Mock for claude_agent_sdk.types.ThinkingBlock."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_sdk_modules():
    """Return a patch.dict context manager that injects mock SDK modules."""
    return patch.dict(sys.modules, {
        "claude_agent_sdk": MagicMock(**{
            "ResultMessage": _MockResultMessage,
            "AssistantMessage": _MockAssistantMessage,
            "SystemMessage": _MockSystemMessage,
            "TextBlock": _MockTextBlock,
            "ToolUseBlock": _MockToolUseBlock,
            "ToolResultBlock": _MockToolResultBlock,
        }),
        "claude_agent_sdk.types": MagicMock(**{
            "StreamEvent": _MockStreamEvent,
            "ThinkingBlock": _MockThinkingBlock,
        }),
    })


def _make_unit(session_id: str = "test-preservation") -> SessionUnit:
    """Create a SessionUnit in STREAMING state with a mocked client."""
    unit = SessionUnit(session_id=session_id, agent_id="default")
    unit._transition(SessionState.STREAMING)
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
        """ResultMessage with usage=None yields result event, transitions STREAMING→IDLE.

        When usage is None, ``input_tokens`` is None, so the ``if input_tokens``
        check is False — the context warning bridge is skipped entirely.
        """
        unit = _make_unit()
        _set_mock_client(unit, [_make_result_message(usage=None)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        result_events = [e for e in events if e.get("type") == "result"]
        assert len(result_events) == 1
        assert result_events[0]["usage"] is None
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_result_message_usage_empty_dict(self):
        """ResultMessage with usage={} yields result event, transitions STREAMING→IDLE.

        When usage is {}, the code does ``getattr(msg, "usage", None) or {}``
        which gives {}.  Then ``if usage`` is False (empty dict is falsy),
        so the result event has ``usage: None``.  The context warning bridge
        is also skipped because ``input_tokens`` is None.
        """
        unit = _make_unit()
        _set_mock_client(unit, [_make_result_message(usage={})])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        result_events = [e for e in events if e.get("type") == "result"]
        assert len(result_events) == 1
        # Empty dict is falsy → ``if usage else None`` yields None
        assert result_events[0]["usage"] is None
        assert unit.state == SessionState.IDLE

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

        result_msg = _make_result_message(usage=None)

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
