"""ESCALATE→SSE: the breaker trip surfaces a user-facing recovery_exhausted event.

When the RecoveryCoordinator's attempt-breaker trips (RecoveryVerdict.ESCALATE),
self-heal gives up — and previously the user was told NOTHING (only a
logger.warning). This wires the existing one-shot terminal signal to a
user-facing `recovery_exhausted` SSE event, gated on the
``terminal_signal_count`` DELTA so it fires EXACTLY ONCE per exhaustion episode
(the ESCALATE verdict itself recurs every tick while the breaker holds).

Design decision #3 (pulled forward from R4). run_d8dce02a.
"""

from core.session_healing import MAX_HEAL_ATTEMPTS, RecoveryVerdict
from core.session_unit import SessionUnit


def _exhaust_breaker(unit: SessionUnit) -> None:
    """Drive the unit's breaker past MAX_HEAL_ATTEMPTS and clear cooldown so the
    next decide() returns ESCALATE (not DEFER)."""
    coord = unit._recovery_coordinator
    for _ in range(MAX_HEAL_ATTEMPTS):
        coord.record_heal_start("hang_detected")
    unit._healing_loop._last_heal_time = 0.0


def _decide_escalate(unit: SessionUnit):
    return unit._recovery_coordinator.decide(
        "hang_detected", enabled=True, user_stopped=False,
        state="streaming", graceful_pending=False,
    )


class TestRecoveryExhaustedEvent:
    def test_emit_returns_event_once_on_first_escalate(self):
        """The first ESCALATE after the breaker trips yields a recovery_exhausted
        event with type + sessionId + message (AC1, AC2)."""
        unit = SessionUnit(session_id="rex-1", agent_id="agent-1")
        _exhaust_breaker(unit)
        d = _decide_escalate(unit)
        assert d.verdict is RecoveryVerdict.ESCALATE

        evt = unit._maybe_recovery_exhausted_event("hang_detected")
        assert evt is not None, "first ESCALATE must surface a recovery_exhausted event"
        assert evt["type"] == "recovery_exhausted"
        assert evt["sessionId"] == "rex-1"  # self.session_id, always set
        assert isinstance(evt.get("message"), str) and evt["message"], "human message required"

    def test_no_double_emit_across_repeated_escalate_ticks(self):
        """Subsequent ESCALATE ticks in the SAME episode yield nothing — the
        gate is the terminal_signal_count delta, not the (recurring) verdict
        (AC1: exactly once per episode, never per-tick spam)."""
        unit = SessionUnit(session_id="rex-2", agent_id="agent-1")
        _exhaust_breaker(unit)

        emitted = []
        for _ in range(4):  # 4 ticks, breaker stays tripped
            _decide_escalate(unit)
            evt = unit._maybe_recovery_exhausted_event("hang_detected")
            if evt is not None:
                emitted.append(evt)

        assert len(emitted) == 1, (
            f"recovery_exhausted must fire exactly once per episode, got {len(emitted)}"
        )

    def test_refires_on_new_episode_after_success(self):
        """A successful heal resets the terminal signal; a NEW exhaustion episode
        surfaces a fresh event (the high-water mark must not stick) (AC1)."""
        unit = SessionUnit(session_id="rex-3", agent_id="agent-1")

        # Episode 1
        _exhaust_breaker(unit)
        _decide_escalate(unit)
        first = unit._maybe_recovery_exhausted_event("hang_detected")
        assert first is not None

        # Recovery succeeds — resets the terminal signal (count stays monotonic).
        unit._recovery_coordinator.record_heal_success()

        # Episode 2: exhaust again → must surface a fresh event.
        _exhaust_breaker(unit)
        _decide_escalate(unit)
        second = unit._maybe_recovery_exhausted_event("hang_detected")
        assert second is not None, (
            "a new exhaustion episode must re-surface the event (mark not stuck)"
        )

    def test_no_emit_when_breaker_not_tripped(self):
        """A healthy unit (breaker not tripped, no terminal signal) yields
        nothing — the event is exclusive to genuine exhaustion (AC1)."""
        unit = SessionUnit(session_id="rex-4", agent_id="agent-1")
        # Do NOT exhaust — terminal signal never fired.
        evt = unit._maybe_recovery_exhausted_event("hang_detected")
        assert evt is None, "no recovery_exhausted without a real breaker trip"
