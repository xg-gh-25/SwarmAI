"""Tests for the context warning bridge in session_unit.py.

Verifies that the streaming loop emits context_warning SSE events
when input_tokens exceeds the model's context window thresholds
(70% warn, 85% critical), and stays silent otherwise.

Testing methodology:
- ``TestBuildContextWarning``: Direct unit tests of the PromptBuilder classmethod.
- ``TestContextWarningBridge``: Integration tests that exercise the REAL
  ``_read_formatted_response()`` code path via mocked SDK types, verifying
  that ``self._model_name`` is used (not the old undefined ``options``).

Key properties verified:

- Warning yielded when input_tokens > 70% of context window
- Critical yielded when input_tokens > 85% of context window
- No warning when usage is below 70%
- No warning when input_tokens is None or 0
- Bridge silently swallows exceptions (never blocks streaming)
- self._model_name is used instead of undefined ``options`` variable
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


# ---------------------------------------------------------------------------
# Mock SDK types — needed for _read_formatted_response() isinstance checks
# ---------------------------------------------------------------------------

class _MockResultMessage:
    pass

class _MockAssistantMessage:
    pass

class _MockSystemMessage:
    pass

class _MockTextBlock:
    pass

class _MockToolUseBlock:
    pass

class _MockToolResultBlock:
    pass

class _MockStreamEvent:
    pass

class _MockThinkingBlock:
    pass


def _patch_sdk_modules():
    """Patch claude_agent_sdk modules so isinstance checks work."""
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


def _make_result_message(input_tokens, output_tokens=100):
    """Build a mock ResultMessage with usage data."""
    msg = _MockResultMessage()
    msg.is_error = False
    msg.subtype = None
    msg.result = ""
    msg.error = ""
    msg.session_id = None
    msg.duration_ms = 1234
    msg.total_cost_usd = 0.01
    msg.num_turns = 1
    if input_tokens is not None:
        msg.usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
    else:
        msg.usage = None
    return msg


def _make_unit(session_id="test-cw", model_name="claude-sonnet-4-6"):
    """Create a SessionUnit in STREAMING state with _model_name set."""
    unit = SessionUnit(session_id=session_id, agent_id="default")
    unit._model_name = model_name
    unit._transition(SessionState.STREAMING)
    return unit


async def _collect_events(unit):
    """Iterate _read_formatted_response() and collect all yielded events."""
    events = []
    async for event in unit._read_formatted_response():
        events.append(event)
    return events


def _wire_client(unit, messages):
    """Wire a list of mock messages into the unit's client."""
    async def _mock_response():
        for msg in messages:
            yield msg
    mock_client = MagicMock()
    mock_client.receive_response = MagicMock(return_value=_mock_response())
    unit._client = mock_client


# ── build_context_warning via PromptBuilder directly ─────────────────


class TestBuildContextWarning:
    """Test the PromptBuilder.build_context_warning classmethod directly."""

    def test_returns_warn_at_70pct(self):
        from core.prompt_builder import PromptBuilder
        result = PromptBuilder.build_context_warning(700_000, "claude-sonnet-4-6")
        assert result is not None
        assert result["level"] == "warn"
        assert result["pct"] == 70
        assert result["type"] == "context_warning"

    def test_returns_critical_at_85pct(self):
        from core.prompt_builder import PromptBuilder
        result = PromptBuilder.build_context_warning(850_000, "claude-sonnet-4-6")
        assert result is not None
        assert result["level"] == "critical"
        assert result["pct"] == 85

    def test_returns_ok_below_70pct(self):
        from core.prompt_builder import PromptBuilder
        result = PromptBuilder.build_context_warning(500_000, "claude-sonnet-4-6")
        assert result is not None
        assert result["level"] == "ok"
        assert result["pct"] == 50

    def test_returns_none_for_zero_tokens(self):
        from core.prompt_builder import PromptBuilder
        assert PromptBuilder.build_context_warning(0, "claude-sonnet-4-6") is None

    def test_returns_none_for_none_tokens(self):
        from core.prompt_builder import PromptBuilder
        assert PromptBuilder.build_context_warning(None, "claude-sonnet-4-6") is None

    def test_returns_none_for_negative_tokens(self):
        from core.prompt_builder import PromptBuilder
        assert PromptBuilder.build_context_warning(-100, "claude-sonnet-4-6") is None

    def test_default_model_uses_200k_window(self):
        from core.prompt_builder import PromptBuilder
        result = PromptBuilder.build_context_warning(140_000, None)
        assert result is not None
        assert result["level"] == "warn"


# ── Bridge integration in streaming loop ─────────────────────────────


class TestContextWarningBridge:
    """Integration tests exercising the REAL _read_formatted_response() code path.

    These tests create a SessionUnit in STREAMING state with _model_name set,
    wire a mocked SDK client that yields a ResultMessage, and verify that the
    context warning bridge in the actual streaming loop uses self._model_name
    (not the old undefined ``options`` variable).
    """

    @pytest.mark.asyncio
    async def test_yields_warn_event_above_70pct(self):
        """Real bridge yields context_warning when >70% of 1M context."""
        unit = _make_unit(model_name="claude-sonnet-4-6")
        _wire_client(unit, [_make_result_message(input_tokens=750_000)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 1
        assert warnings[0]["level"] == "warn"
        assert warnings[0]["pct"] == 75
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_yields_critical_event_above_85pct(self):
        """Real bridge yields context_warning with level=critical at >85%."""
        unit = _make_unit(model_name="claude-sonnet-4-6")
        _wire_client(unit, [_make_result_message(input_tokens=900_000)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 1
        assert warnings[0]["level"] == "critical"
        assert warnings[0]["pct"] == 90
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_no_event_below_70pct(self):
        """Real bridge yields nothing when usage is below 70%."""
        unit = _make_unit(model_name="claude-sonnet-4-6")
        _wire_client(unit, [_make_result_message(input_tokens=500_000)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 0
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_no_event_when_input_tokens_none(self):
        """Real bridge yields nothing when usage is None."""
        unit = _make_unit()
        _wire_client(unit, [_make_result_message(input_tokens=None)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 0
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_no_event_when_input_tokens_zero(self):
        """Real bridge yields nothing when input_tokens is 0."""
        unit = _make_unit()
        _wire_client(unit, [_make_result_message(input_tokens=0)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 0
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_bridge_swallows_exceptions(self):
        """Real bridge never raises — silently swallows PromptBuilder errors."""
        unit = _make_unit(model_name="claude-sonnet-4-6")
        _wire_client(unit, [_make_result_message(input_tokens=900_000)])

        with _patch_sdk_modules(), \
             patch("core.prompt_builder.PromptBuilder.build_context_warning",
                   side_effect=RuntimeError("boom")):
            events = await _collect_events(unit)

        # No warning yielded (exception swallowed), but result event still present
        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 0
        results = [e for e in events if e.get("type") == "result"]
        assert len(results) == 1
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_no_event_when_model_name_none(self):
        """Real bridge uses self._model_name=None → default 200K window."""
        unit = _make_unit(model_name=None)
        # 100K tokens with 200K default window = 50% → no warning
        _wire_client(unit, [_make_result_message(input_tokens=100_000)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 0
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_200k_model_thresholds(self):
        """Real bridge uses correct thresholds for 200K models."""
        unit = _make_unit(model_name="claude-haiku-3-5")
        # 200K model, 150K tokens = 75% → warn
        _wire_client(unit, [_make_result_message(input_tokens=150_000)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 1
        assert warnings[0]["level"] == "warn"
        assert unit.state == SessionState.IDLE

    @pytest.mark.asyncio
    async def test_200k_model_critical(self):
        """200K model at 85% → critical."""
        unit = _make_unit(model_name="claude-haiku-3-5")
        _wire_client(unit, [_make_result_message(input_tokens=170_000)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 1
        assert warnings[0]["level"] == "critical"
        assert warnings[0]["pct"] == 85
        assert unit.state == SessionState.IDLE
