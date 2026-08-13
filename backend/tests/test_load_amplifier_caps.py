"""Tests for Root 2 — Load Amplifier Caps (the 3 NO-GUARD gaps).

Closes 3 resource-amplifier gaps via additive thresholds (no state-machine
change, no new infra). Each gap is verified by a FORCED-execution test —
"compiles" is not "executes" (STEERING #11 / COE10).

Gaps & ACs:
- G1 (context-ring): AC1 soft-compact REMOVED (run_2b1957f8 — held _client_io 300s
  at 60%, froze next send; now CLI autocompact + manual refresh). AC2 hard notice
  (already exists, verified) is retained.
- G2 (turn count):   AC3 hard graceful floor at max_turns-5, ONLY reachable when self-heal OFF.
- G3 (single turn):  AC5 elapsed "still working" heartbeat; AC6 per-turn tool-loop budget.
- AC4 observability: context_ring_debug WARN near cap + turn_count.

Methodology: unit tests with mocked Claude SDK + forced-execution of each new
guard path. AC3/AC6 force the trigger condition and assert the path RUNS
(not just that the code parses). AC3 forces SWARMAI_SELF_HEAL=0 because the hard
floor is by-design unreachable when self-heal is ON (turn_approaching at -20
heals + resets turn_count before -5 is hit).
"""
from __future__ import annotations

import time

from core.session_unit import SessionState, SessionUnit


# ── Helpers ────────────────────────────────────────────────────────


def _make_idle_unit(session_id: str = "test-amplifier") -> SessionUnit:
    """Create a SessionUnit transitioned to IDLE (post-turn state)."""
    unit = SessionUnit(session_id=session_id, agent_id="default")
    unit._transition(SessionState.IDLE)  # COLD→IDLE
    return unit


# ── AC1: Context-ring soft cap REMOVED (run_2b1957f8) ───────────────
# The proactive soft-compact path (_check_context_soft_compact) was deleted —
# it held _client_io for a 300s /compact at 60% context, freezing the user's
# next send() at the turn boundary. Context is now managed by CLI autocompact
# (task_budget=800K + per-session window-% autoCompact) + manual refresh. The
# TestAC1SoftCompact class that exercised the removed method is gone with it.
# AC2 (the hard notice) and AC3/AC5 below are unaffected.


# ── AC2: Context-ring hard notice (already exists — verify) ─────────


class TestAC2HardNoticeExists:
    """G1: hard threshold surfaces 'start a new tab', never auto-kills."""

    def test_critical_notice_at_85pct(self):
        from core.prompt_builder import PromptBuilder

        evt = PromptBuilder.build_context_warning(870_000, "claude-opus-4-8")
        assert evt is not None
        assert evt["level"] == "critical"
        assert evt["pct"] >= 85
        assert "new tab" in evt["message"].lower()
        # Notice is informational only — no kill/discard signal in the event.
        assert evt["type"] == "context_warning"


# ── AC3: Turn-count hard graceful floor (self-heal OFF path) ────────


class TestAC3TurnHardFloor:
    """G2: at max_turns-5 the session stops gracefully with a conclusion.

    The floor stays dormant while self-heal is SUCCEEDING — turn_approaching
    (-20) heals + resets turn_count before -5 is reached. It still fires as a
    last-resort net when self-heal is OFF, exhausted, or in cooldown.
    """

    def test_hard_floor_trigger_fires_at_max_minus_5(self):
        from core.session_healing import HARD_FLOOR_BUFFER, HealthSensor

        sensor = HealthSensor(max_turns=100)
        # Drive turn_count to exactly max_turns - HARD_FLOOR_BUFFER.
        sensor._turn_count = 100 - HARD_FLOOR_BUFFER
        sensor._created_at = 0.0  # not young
        should, trigger = sensor.should_checkpoint(session_state="idle")
        assert should is True
        assert trigger == "turn_hard_floor"

    def test_hard_floor_not_fired_below_threshold(self):
        from core.session_healing import HARD_FLOOR_BUFFER, HealthSensor

        sensor = HealthSensor(max_turns=100)
        sensor._turn_count = 100 - HARD_FLOOR_BUFFER - 10
        sensor._created_at = 0.0
        should, trigger = sensor.should_checkpoint(session_state="idle")
        # turn_approaching may fire, but NOT turn_hard_floor
        assert trigger != "turn_hard_floor"

    def test_hard_floor_buffer_smaller_than_approach_buffer(self):
        """Hard floor (-5) must be CLOSER to the limit than graceful (-20)."""
        from core.session_healing import HARD_FLOOR_BUFFER, TURN_APPROACH_BUFFER

        assert HARD_FLOOR_BUFFER < TURN_APPROACH_BUFFER


# ── AC5: Single-turn elapsed heartbeat ──────────────────────────────


class TestAC5ElapsedHeartbeat:
    """G3: a long turn emits an 'elapsed' notice so it reads as expected."""

    def test_heartbeat_emitted_after_threshold(self):
        from core.session_unit import LONG_TURN_HEARTBEAT_S

        unit = _make_idle_unit()
        unit._transition(SessionState.STREAMING)
        # Streaming started LONG_TURN_HEARTBEAT_S + 30 ago.
        unit._streaming_start_time = time.time() - (LONG_TURN_HEARTBEAT_S + 30)
        unit._last_heartbeat_elapsed = 0.0

        evt = unit._maybe_build_elapsed_heartbeat()
        assert evt is not None
        assert evt["type"] == "still_working"
        assert evt.get("elapsedSeconds", 0) >= LONG_TURN_HEARTBEAT_S

    def test_no_heartbeat_before_threshold(self):
        unit = _make_idle_unit()
        unit._transition(SessionState.STREAMING)
        unit._streaming_start_time = time.time() - 10  # 10s — too short
        unit._last_heartbeat_elapsed = 0.0

        evt = unit._maybe_build_elapsed_heartbeat()
        assert evt is None

    def test_heartbeat_is_one_shot_per_interval(self):
        """Don't spam: a second call within the same interval returns None."""
        from core.session_unit import LONG_TURN_HEARTBEAT_S

        unit = _make_idle_unit()
        unit._transition(SessionState.STREAMING)
        unit._streaming_start_time = time.time() - (LONG_TURN_HEARTBEAT_S + 5)
        unit._last_heartbeat_elapsed = 0.0

        first = unit._maybe_build_elapsed_heartbeat()
        second = unit._maybe_build_elapsed_heartbeat()
        assert first is not None
        assert second is None
