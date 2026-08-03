"""R3: RecoveryCoordinator — single recovery DECISION authority.

The hang-class audit (run_d73c3e9a) found 8 kill paths each owning their own
circuit breaker, deciding independently. R3 introduces ONE decision authority
and routes the first (highest-frequency) trigger — self-heal — through it.
Strangler-fig: HealingLoop is UNCHANGED and kept; the Coordinator DELEGATES to
it. The other 7 kill paths are untouched (migrated in R3a–R3g).

Design: Knowledge/Designs/2026-06-24-session-lifecycle-unified-recovery-design.md

This test pins the decision matrix, the terminal-signal-exactly-once invariant,
and that the Coordinator is a thin wrapper over HealingLoop (behavior parity).
"""


from core.session_healing import (
    HealingLoop,
    MAX_HEAL_ATTEMPTS,
    RecoveryCoordinator,
    RecoveryVerdict,
)


def _coord():
    """Fresh coordinator wrapping a fresh HealingLoop (delegate, not absorb)."""
    return RecoveryCoordinator(HealingLoop())


# ─── AC1: decision branch matrix ──────────────────────────────────────────


def test_disabled_returns_skip():
    c = _coord()
    d = c.decide("hang_detected", enabled=False, user_stopped=False,
                 state="streaming", graceful_pending=False)
    assert d.verdict is RecoveryVerdict.SKIP


def test_user_stopped_returns_skip():
    c = _coord()
    d = c.decide("hang_detected", enabled=True, user_stopped=True,
                 state="streaming", graceful_pending=False)
    assert d.verdict is RecoveryVerdict.SKIP


def test_waiting_input_returns_skip():
    """WAITING_INPUT is protected — user is mid-answer, never self-heal-kill it."""
    c = _coord()
    d = c.decide("hang_detected", enabled=True, user_stopped=False,
                 state="waiting_input", graceful_pending=False)
    assert d.verdict is RecoveryVerdict.SKIP


def test_turn_approaching_first_time_is_graceful():
    c = _coord()
    d = c.decide("turn_approaching", enabled=True, user_stopped=False,
                 state="streaming", graceful_pending=False)
    assert d.verdict is RecoveryVerdict.PROCEED_GRACEFUL


def test_turn_approaching_when_pending_proceeds_to_kill():
    """Second phase of the two-phase graceful wrap → actual kill."""
    c = _coord()
    d = c.decide("turn_approaching", enabled=True, user_stopped=False,
                 state="streaming", graceful_pending=True)
    assert d.verdict is RecoveryVerdict.PROCEED_KILL


def test_immediate_trigger_proceeds_to_kill():
    for trigger in ("memory_growth", "error_cascade", "hang_detected"):
        c = _coord()
        d = c.decide(trigger, enabled=True, user_stopped=False,
                     state="streaming", graceful_pending=False)
        assert d.verdict is RecoveryVerdict.PROCEED_KILL, trigger


def test_cooldown_returns_defer():
    c = _coord()
    # One heal start arms the cooldown.
    c.record_heal_start("hang_detected")
    d = c.decide("hang_detected", enabled=True, user_stopped=False,
                 state="streaming", graceful_pending=False)
    assert d.verdict is RecoveryVerdict.DEFER
    assert "cooldown" in d.reason.lower()


def test_max_attempts_returns_escalate():
    c = _coord()
    # Exhaust attempts. record_heal_start bumps _heal_attempts; bypass cooldown by
    # resetting last_heal_time so the breaker (not cooldown) is what trips.
    for _ in range(MAX_HEAL_ATTEMPTS):
        c.record_heal_start("hang_detected")
    c._loop._last_heal_time = 0.0  # clear cooldown so ESCALATE (not DEFER) wins
    d = c.decide("hang_detected", enabled=True, user_stopped=False,
                 state="streaming", graceful_pending=False)
    assert d.verdict is RecoveryVerdict.ESCALATE


# ─── AC4: terminal recovery signal fires exactly once ─────────────────────


def test_terminal_signal_fires_exactly_once_at_max():
    c = _coord()
    for _ in range(MAX_HEAL_ATTEMPTS):
        c.record_heal_start("hang_detected")
    c._loop._last_heal_time = 0.0

    assert c.terminal_recovery_reached is False
    # First ESCALATE decision trips the terminal signal.
    c.decide("hang_detected", enabled=True, user_stopped=False,
             state="streaming", graceful_pending=False)
    assert c.terminal_recovery_reached is True
    assert c.terminal_signal_count == 1
    # A second ESCALATE decision must NOT double-fire.
    c.decide("hang_detected", enabled=True, user_stopped=False,
             state="streaming", graceful_pending=False)
    assert c.terminal_signal_count == 1


def test_terminal_signal_resets_on_success():
    """A successful heal clears the terminal state (fresh budget)."""
    c = _coord()
    for _ in range(MAX_HEAL_ATTEMPTS):
        c.record_heal_start("hang_detected")
    c._loop._last_heal_time = 0.0
    c.decide("hang_detected", enabled=True, user_stopped=False,
             state="streaming", graceful_pending=False)
    assert c.terminal_recovery_reached is True

    c.record_heal_success()
    assert c.terminal_recovery_reached is False
    assert c._loop.heal_attempts == 0  # delegates to HealingLoop reset


# ─── AC2/AC3/AC6: delegation parity (Coordinator wraps, does not replace) ──


def test_coordinator_delegates_to_held_healing_loop():
    """record_* passthroughs mutate the SAME HealingLoop (no second breaker)."""
    loop = HealingLoop()
    c = RecoveryCoordinator(loop)
    c.record_heal_start("hang_detected")
    assert loop.heal_attempts == 1, "coordinator must delegate to the held loop"
    c.record_heal_success()
    assert loop.heal_attempts == 0


def test_coordinator_does_not_create_second_breaker():
    """The Coordinator holds the injected loop, it does not make its own."""
    loop = HealingLoop()
    c = RecoveryCoordinator(loop)
    assert c._loop is loop


# ─── Integration: SessionUnit wires its coordinator to its breaker ────────
# (adversarial LOW: parity was unit-tested but not integration-tested for the
#  478-caller session_unit restructure)


def test_session_unit_coordinator_wraps_its_healing_loop():
    """SessionUnit's coordinator delegates to the SAME HealingLoop instance —
    proving the restructured self-heal path has one breaker, not two."""
    from core.session_unit import SessionUnit

    unit = SessionUnit(session_id="test-coord-wire", agent_id="agent-1")
    assert unit._recovery_coordinator._loop is unit._healing_loop, \
        "coordinator must wrap the unit's own breaker (single breaker invariant)"


def test_session_unit_coordinator_decide_matches_breaker_state():
    """Driving the unit's breaker is reflected through its coordinator's decide()
    — the integration seam the self-heal block now relies on."""
    from core.session_unit import SessionUnit

    unit = SessionUnit(session_id="test-coord-decide", agent_id="agent-1")
    coord = unit._recovery_coordinator

    # Healthy → an immediate trigger proceeds to kill.
    d = coord.decide("hang_detected", enabled=True, user_stopped=False,
                     state="streaming", graceful_pending=False)
    assert d.verdict is RecoveryVerdict.PROCEED_KILL

    # Protected state always skips (the ordering subtlety: before the breaker).
    d = coord.decide("hang_detected", enabled=True, user_stopped=False,
                     state="waiting_input", graceful_pending=False)
    assert d.verdict is RecoveryVerdict.SKIP

    # Exhaust the unit's breaker → escalate (parity with old should_escalate path).
    for _ in range(MAX_HEAL_ATTEMPTS):
        coord.record_heal_start("hang_detected")
    unit._healing_loop._last_heal_time = 0.0
    d = coord.decide("hang_detected", enabled=True, user_stopped=False,
                     state="streaming", graceful_pending=False)
    assert d.verdict is RecoveryVerdict.ESCALATE
    assert coord.terminal_recovery_reached is True
