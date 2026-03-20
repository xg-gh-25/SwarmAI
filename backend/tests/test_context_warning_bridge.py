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
    unit._transition(SessionState.IDLE)       # COLD→IDLE
    unit._transition(SessionState.STREAMING)   # IDLE→STREAMING
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
    async def test_ok_event_below_70pct(self):
        """Real bridge yields context_warning with level=ok when usage is below 70%."""
        unit = _make_unit(model_name="claude-sonnet-4-6")
        _wire_client(unit, [_make_result_message(input_tokens=500_000)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 1
        assert warnings[0]["level"] == "ok"
        assert warnings[0]["pct"] == 50
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
    async def test_ok_event_when_model_name_none(self):
        """Real bridge uses self._model_name=None → default 200K window, emits ok."""
        unit = _make_unit(model_name=None)
        # 100K tokens with 200K default window = 50% → ok level
        _wire_client(unit, [_make_result_message(input_tokens=100_000)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 1
        assert warnings[0]["level"] == "ok"
        assert warnings[0]["pct"] == 50
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


# ── Bug condition exploration tests ──────────────────────────────
# These tests encode EXPECTED behavior — they MUST FAIL on unfixed code.
# Failure confirms the bugs exist.  They will PASS after the fix.


from hypothesis import given, settings, assume
from hypothesis import strategies as st


# ── Preservation property tests ──────────────────────────────────
# These tests verify EXISTING correct behavior that must be preserved
# across the fix.  They MUST PASS on both unfixed and fixed code.


class TestPreservation_WarnCriticalEvents:
    """Preservation — Warn/critical events are yielded unchanged by the bridge.

    **Validates: Requirements 3.1, 3.2, 3.3**

    These property-based tests verify that:
    - Property 3: For all input_tokens producing warn/critical levels, the
      bridge yields the event with identical content to build_context_warning.
    - Property 4: For all invalid input_tokens (None, zero, negative),
      build_context_warning returns None and no event is emitted.

    These tests MUST PASS on UNFIXED code (they test existing correct behavior).
    """

    @pytest.mark.asyncio
    @given(
        input_tokens=st.integers(min_value=140_000, max_value=200_000),
    )
    @settings(max_examples=30, deadline=None)
    async def test_warn_critical_events_preserved_200k_model(
        self, input_tokens: int
    ):
        """**Validates: Requirements 3.1, 3.2**

        For all input_tokens in [140_000, 200_000] with a 200K model,
        build_context_warning returns a warn or critical event.  The bridge
        MUST yield that event with identical content.
        """
        from core.prompt_builder import PromptBuilder

        # Pre-condition: must produce warn or critical
        evt = PromptBuilder.build_context_warning(input_tokens, "claude-haiku-3-5")
        assume(evt is not None and evt.get("level") in ("warn", "critical"))

        unit = _make_unit(model_name="claude-haiku-3-5")
        _wire_client(unit, [_make_result_message(input_tokens=input_tokens)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 1, (
            f"Expected 1 context_warning event for {evt['level']}-level input "
            f"(input_tokens={input_tokens}, pct={evt['pct']}), got {len(warnings)}."
        )
        # Verify identical content — level, pct, tokensEst, message, type
        assert warnings[0] == evt, (
            f"Bridge yielded event differs from build_context_warning output.\n"
            f"  Expected: {evt}\n"
            f"  Got:      {warnings[0]}"
        )

    @pytest.mark.asyncio
    @given(
        input_tokens=st.one_of(
            st.none(),
            st.just(0),
            st.integers(max_value=-1),
        ),
    )
    @settings(max_examples=30, deadline=None)
    async def test_invalid_input_produces_no_event(
        self, input_tokens
    ):
        """**Validates: Requirements 3.3**

        For all input_tokens that are None, zero, or negative,
        build_context_warning returns None and no context_warning
        SSE event is emitted by the bridge.
        """
        from core.prompt_builder import PromptBuilder

        # Verify build_context_warning returns None for invalid input
        evt = PromptBuilder.build_context_warning(input_tokens, "claude-haiku-3-5")
        assert evt is None, (
            f"build_context_warning should return None for input_tokens={input_tokens}, "
            f"got {evt}"
        )

        unit = _make_unit(model_name="claude-haiku-3-5")
        _wire_client(unit, [_make_result_message(input_tokens=input_tokens)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 0, (
            f"Expected no context_warning event for invalid input_tokens={input_tokens}, "
            f"got {len(warnings)} events."
        )


# ── Bug condition exploration tests ──────────────────────────────
# These tests encode EXPECTED behavior — they MUST FAIL on unfixed code.
# Failure confirms the bugs exist.  They will PASS after the fix.


class TestBugConditionExploration_OkLevelFiltered:
    """Bug 1 — Ok-level context_warning events are filtered out by the bridge.

    **Validates: Requirements 1.1, 1.2, 2.1, 2.2**

    The bridge in session_unit.py has ``if warning_evt.get("level") != "ok"``
    which drops ok-level events.  These tests confirm the bug by asserting
    that ok-level events SHOULD be yielded (expected behavior).

    On UNFIXED code these tests FAIL — confirming the bug exists.
    On FIXED code these tests PASS — confirming the fix works.
    """

    @pytest.mark.asyncio
    @given(
        input_tokens=st.integers(min_value=1, max_value=139_999),
    )
    @settings(max_examples=30, deadline=None)
    async def test_ok_level_event_yielded_for_200k_model(self, input_tokens: int):
        """**Validates: Requirements 1.1, 2.1**

        For any input_tokens in [1, 139_999] with a 200K model (default),
        build_context_warning returns an ok-level event.  The bridge SHOULD
        yield it — but on unfixed code, the != "ok" filter drops it.
        """
        from core.prompt_builder import PromptBuilder

        # Pre-condition: build_context_warning must return an ok-level event
        evt = PromptBuilder.build_context_warning(input_tokens, "claude-haiku-3-5")
        assume(evt is not None and evt.get("level") == "ok")

        unit = _make_unit(model_name="claude-haiku-3-5")
        _wire_client(unit, [_make_result_message(input_tokens=input_tokens)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        # Expected behavior: ok-level event IS yielded by the bridge
        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 1, (
            f"Expected 1 context_warning event for ok-level input "
            f"(input_tokens={input_tokens}, pct={evt['pct']}), got {len(warnings)}. "
            f"Bug: the != 'ok' filter drops ok-level events."
        )
        assert warnings[0]["level"] == "ok"
        assert warnings[0]["pct"] == evt["pct"]

    @pytest.mark.asyncio
    async def test_ok_level_concrete_example_5000_tokens(self):
        """**Validates: Requirements 1.1, 1.2, 2.1, 2.2**

        Concrete example from bugfix.md: input_tokens=5000 with 200K model
        → pct=2, level="ok" → event generated but NOT yielded by bridge.
        """
        from core.prompt_builder import PromptBuilder

        evt = PromptBuilder.build_context_warning(5000, "claude-haiku-3-5")
        assert evt is not None
        assert evt["level"] == "ok"
        assert evt["pct"] == 2  # 5000/200000 * 100 = 2.5 → rounds to 2

        unit = _make_unit(model_name="claude-haiku-3-5")
        _wire_client(unit, [_make_result_message(input_tokens=5000)])

        with _patch_sdk_modules():
            events = await _collect_events(unit)

        warnings = [e for e in events if e.get("type") == "context_warning"]
        assert len(warnings) == 1, (
            "Expected ok-level context_warning to be yielded, but bridge "
            "filtered it out via the != 'ok' condition."
        )
        assert warnings[0]["level"] == "ok"


class TestBugConditionExploration_SystemPromptMetadataMissing:
    """Bug 2 — No system_prompt_metadata SSE event in the streaming path.

    **Validates: Requirements 1.3, 1.4, 2.3, 2.4**

    The current code has no system_prompt_metadata event emission at all.
    These tests confirm the bug by asserting that a system_prompt_metadata
    SSE event SHOULD be emitted when metadata exists in session_registry.

    On UNFIXED code these tests FAIL — confirming the bug exists.
    On FIXED code these tests PASS — confirming the fix works.
    """

    @pytest.mark.asyncio
    @given(
        token_count=st.integers(min_value=100, max_value=50_000),
        file_count=st.integers(min_value=1, max_value=10),
    )
    @settings(max_examples=20, deadline=None)
    async def test_system_prompt_metadata_event_emitted(
        self, token_count: int, file_count: int
    ):
        """**Validates: Requirements 2.3, 2.4**

        For any session where system_prompt_metadata exists in the registry,
        the streaming path SHOULD yield a system_prompt_metadata SSE event.
        On unfixed code, no such event exists.
        """
        session_id = "test-spm-bug"
        sample_metadata = {
            "files": [
                {"name": f"file_{i}.md", "tokens": token_count // file_count}
                for i in range(file_count)
            ],
            "totalTokens": token_count,
            "truncated": False,
        }

        unit = _make_unit(session_id=session_id, model_name="claude-sonnet-4-6")
        _wire_client(unit, [_make_result_message(input_tokens=500_000)])

        with _patch_sdk_modules(), \
             patch("core.session_registry.system_prompt_metadata",
                   {session_id: sample_metadata}):
            events = await _collect_events(unit)

        # Expected behavior: system_prompt_metadata event IS emitted
        spm_events = [
            e for e in events if e.get("type") == "system_prompt_metadata"
        ]
        assert len(spm_events) == 1, (
            f"Expected 1 system_prompt_metadata SSE event (metadata exists "
            f"in registry for session '{session_id}'), got {len(spm_events)}. "
            f"Bug: no system_prompt_metadata event emission in streaming path."
        )
        assert spm_events[0]["totalTokens"] == token_count

    @pytest.mark.asyncio
    async def test_system_prompt_metadata_concrete_example(self):
        """**Validates: Requirements 1.3, 1.4, 2.3, 2.4**

        Concrete example: metadata populated in session_registry after
        streaming, but no SSE event delivers it to the frontend.
        """
        session_id = "test-spm-concrete"
        sample_metadata = {
            "files": [
                {"name": "AGENT.md", "tokens": 3200},
                {"name": "MEMORY.md", "tokens": 1500},
            ],
            "totalTokens": 4700,
            "truncated": False,
            "fullPromptText": "You are a helpful assistant...",
        }

        unit = _make_unit(session_id=session_id, model_name="claude-sonnet-4-6")
        _wire_client(unit, [_make_result_message(input_tokens=100_000)])

        with _patch_sdk_modules(), \
             patch("core.session_registry.system_prompt_metadata",
                   {session_id: sample_metadata}):
            events = await _collect_events(unit)

        spm_events = [
            e for e in events if e.get("type") == "system_prompt_metadata"
        ]
        assert len(spm_events) == 1, (
            "Expected system_prompt_metadata SSE event after streaming "
            "completes, but none was emitted. Bug: metadata only available "
            "via separate API call, not via SSE pipeline."
        )
        # Verify metadata content is passed through
        assert spm_events[0]["totalTokens"] == 4700
        assert len(spm_events[0]["files"]) == 2
