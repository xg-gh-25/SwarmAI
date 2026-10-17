"""Tests for the CLI-native autocompact user-visibility feature (approach A).

What is tested:
    When the SDK's PreCompact hook fires (CLI-native autocompact), the backend
    must surface a user-visible `context_compacted` SSE event carrying a fixed
    notice text, WITHOUT re-adding the deleted proactive soft-compact. The
    visibility signal must ride an INDEPENDENT flag so it does not collide with
    post_compact_injection's `_compacted` fire-once reset.

Methodology:
    - Unit-test the pure event builder (`PromptBuilder.build_compact_notice`) for
      the exact fixed message text + shape.
    - Unit-test `SessionUnit._emit_post_stream_metadata` emits the event ONLY when
      the independent flag `_compact_notice_pending` is set, and clears it (fire-once).
    - Orthogonality: `_compact_notice_pending` and `_compacted` are independent
      (post_compact_injection resets only `_compacted`).
    - Guard: the deleted soft-compact symbol stays dead, and the autocompact
      trigger stays window-derived via `autoCompactWindow` (asserted in
      test_compaction_tuning.py; test_autocompact_trigger_window_derived below).

Key invariants:
    - The notice text is a BACKEND CONSTANT (not agent-generated).
    - The signal is one-shot per compaction (flag cleared after emit).
    - Reusing `_compacted` for visibility would lose the signal — proven orthogonal.
"""

import re
from pathlib import Path

from core.prompt_builder import PromptBuilder
from core.session_unit import SessionState, SessionUnit


# The fixed notice text XG specified, verbatim (two lines).
EXPECTED_NOTICE = (
    "Conversation above has been summarized\n"
    "The conversation in this session reached the agent context limit. "
    "Earlier messages have been summarized, and the summary is sent to the "
    "agent as context instead of the full text."
)


def _make_idle_unit(session_id: str = "test-compact-notice") -> SessionUnit:
    """Create a SessionUnit transitioned to IDLE (post-turn state)."""
    unit = SessionUnit(session_id=session_id, agent_id="default")
    unit.state = SessionState.IDLE
    return unit


class TestBuildCompactNotice:
    """The pure event-builder: shape + fixed text."""

    def test_returns_context_compacted_event_with_fixed_text(self):
        evt = PromptBuilder.build_compact_notice("auto")
        assert evt is not None
        assert evt["type"] == "context_compacted"
        assert evt["trigger"] == "auto"
        assert evt["message"] == EXPECTED_NOTICE

    def test_trigger_passthrough_manual(self):
        evt = PromptBuilder.build_compact_notice("manual")
        assert evt["trigger"] == "manual"
        # Message is the SAME fixed constant regardless of trigger.
        assert evt["message"] == EXPECTED_NOTICE

    def test_unknown_trigger_defaults_to_auto(self):
        # Defensive: an unexpected trigger value must not crash; it normalizes.
        evt = PromptBuilder.build_compact_notice(None)
        assert evt["type"] == "context_compacted"
        assert evt["trigger"] == "auto"


class TestEmitPostStreamMetadataCompactNotice:
    """_emit_post_stream_metadata appends the notice ONLY when the flag is set."""

    def test_appends_event_when_flag_set(self):
        unit = _make_idle_unit()
        unit._hook_session_context = {
            "_compact_notice_pending": True,
            "_compact_trigger": "auto",
        }
        events = unit._emit_post_stream_metadata(usage=None, num_turns=1)
        compact_events = [e for e in events if e.get("type") == "context_compacted"]
        assert len(compact_events) == 1
        assert compact_events[0]["message"] == EXPECTED_NOTICE
        assert compact_events[0]["trigger"] == "auto"

    def test_no_event_when_flag_absent(self):
        unit = _make_idle_unit()
        unit._hook_session_context = {}
        events = unit._emit_post_stream_metadata(usage=None, num_turns=1)
        assert not [e for e in events if e.get("type") == "context_compacted"]

    def test_no_event_when_context_none(self):
        unit = _make_idle_unit()
        unit._hook_session_context = None
        events = unit._emit_post_stream_metadata(usage=None, num_turns=1)
        assert not [e for e in events if e.get("type") == "context_compacted"]

    def test_flag_is_fire_once_cleared_after_emit(self):
        unit = _make_idle_unit()
        unit._hook_session_context = {
            "_compact_notice_pending": True,
            "_compact_trigger": "manual",
        }
        unit._emit_post_stream_metadata(usage=None, num_turns=1)
        # Flag cleared → a second emission does NOT re-fire the notice.
        assert unit._hook_session_context.get("_compact_notice_pending") is not True
        events2 = unit._emit_post_stream_metadata(usage=None, num_turns=1)
        assert not [e for e in events2 if e.get("type") == "context_compacted"]

    def test_trigger_passed_through_to_event(self):
        unit = _make_idle_unit()
        unit._hook_session_context = {
            "_compact_notice_pending": True,
            "_compact_trigger": "manual",
        }
        events = unit._emit_post_stream_metadata(usage=None, num_turns=1)
        compact = [e for e in events if e.get("type") == "context_compacted"][0]
        assert compact["trigger"] == "manual"


class TestOrthogonalityWithCompacted:
    """AC3: the visibility flag must NOT collide with post_compact_injection's _compacted."""

    def test_emit_does_not_touch_compacted_flag(self):
        unit = _make_idle_unit()
        unit._hook_session_context = {
            "_compact_notice_pending": True,
            "_compact_trigger": "auto",
            "_compacted": True,  # set by the same PreCompact hook, read by post_compact_injection
        }
        unit._emit_post_stream_metadata(usage=None, num_turns=1)
        # Our emit clears ONLY its own flag; _compacted stays for post_compact_injection.
        assert unit._hook_session_context.get("_compacted") is True
        assert unit._hook_session_context.get("_compact_notice_pending") is not True


class TestSoftCompactStaysDeleted:
    """AC4: the deleted proactive soft-compact must NOT be re-added."""

    def _session_unit_src(self) -> str:
        return (
            Path(__file__).resolve().parents[1]
            / "core"
            / "session_unit.py"
        ).read_text()

    def test_check_context_soft_compact_stays_dead(self):
        src = self._session_unit_src()
        # The proactive soft-compact method was deleted (run_2b1957f8). It must
        # not be re-introduced as a live method definition.
        assert "async def _check_context_soft_compact" not in src
        assert "def _check_context_soft_compact" not in src

    def test_autocompact_trigger_window_derived(self):
        # The trigger is window-derived (0.75 × window) and rides the CLI
        # `autoCompactWindow` setting; the old task_budget literals are gone
        # (task_budget never moved the CLI threshold — measured 2026-10-17).
        from core.prompt_builder import (
            CHANNEL_AUTOCOMPACT_TRIGGER,
            _compute_desktop_autocompact_trigger,
        )
        src = (
            Path(__file__).resolve().parents[1] / "core" / "prompt_builder.py"
        ).read_text()
        assert not re.search(r'"total":\s*800_000', src)
        assert "task_budget=task_budget" not in src
        assert CHANNEL_AUTOCOMPACT_TRIGGER == 400_000
        assert _compute_desktop_autocompact_trigger(1_000_000) == 750_000
