"""R3a: multi-shape policy template for RecoveryCoordinator.

The 8 recovery kill paths have FOUR distinct decision shapes (validated against
code): attempt-breaker (self-heal), cooldown-gated-threshold (RSS-proactive, OOM),
bare-threshold (RSS-streaming, stuck-WAITING, TTL), behavioral/graceful-escalation
(tool-hang, streaming-timeout). R3 hardwired decide() to the attempt-breaker.

R3a sets the correct template WITHOUT migrating all 8:
- expand RecoveryVerdict 5 → 7 (add PROCEED_INTERRUPT, PROCEED_KILL_HARD)
- a Policy protocol: decide(ctx) → RecoveryDecision; each policy declares its own
  protected/eligible states (guard is policy-driven, not hardcoded)
- implement AttemptBreakerPolicy (pure extract of self-heal) + CooldownThresholdPolicy
  (RSS-proactive); BareThreshold + GracefulEscalation are R3b–g slots
- route RSS-proactive's cooldown decision through the Coordinator

This test pins the template; it does NOT migrate the other 6 triggers.
"""

import pytest

from core.session_healing import (
    HealingLoop,
    MAX_HEAL_ATTEMPTS,
    RecoveryCoordinator,
    RecoveryVerdict,
    RecoveryDecision,
    AttemptBreakerPolicy,
    CooldownThresholdPolicy,
    BareThresholdPolicy,
    RecoveryContext,
)


# ─── AC1: verdict vocabulary expanded to 7 (additive) ─────────────────────


def test_verdict_has_seven_members_including_new_two():
    names = {v.name for v in RecoveryVerdict}
    # original 5 (R3) preserved
    assert {"SKIP", "DEFER", "PROCEED_GRACEFUL", "PROCEED_KILL", "ESCALATE"} <= names
    # R3a additions
    assert "PROCEED_INTERRUPT" in names, "warm non-destructive verdict (tool-hang)"
    assert "PROCEED_KILL_HARD" in names, "kill + drop --resume identity (timeout/OOM)"
    assert len(names) == 7


# ─── AC3: CooldownThresholdPolicy (RSS-proactive shape) ───────────────────


def test_cooldown_policy_defers_within_cooldown():
    pol = CooldownThresholdPolicy(cooldown_s=180.0)
    ctx = RecoveryContext(
        trigger="rss_proactive", enabled=True, user_stopped=False,
        state="idle", now=100.0, last_recovery=50.0,  # 50s ago < 180s
    )
    d = pol.decide(ctx)
    assert d.verdict is RecoveryVerdict.DEFER
    assert "cooldown" in d.reason.lower()


def test_cooldown_policy_proceeds_past_cooldown():
    pol = CooldownThresholdPolicy(cooldown_s=180.0)
    ctx = RecoveryContext(
        trigger="rss_proactive", enabled=True, user_stopped=False,
        state="idle", now=300.0, last_recovery=50.0,  # 250s ago > 180s
    )
    d = pol.decide(ctx)
    assert d.verdict is RecoveryVerdict.PROCEED_KILL


def test_cooldown_boundary_exactly_equal_proceeds():
    """elapsed == cooldown → proceed (parity with old `elapsed < COOLDOWN` = False)."""
    pol = CooldownThresholdPolicy(cooldown_s=180.0)
    ctx = RecoveryContext(
        trigger="rss_proactive", enabled=True, user_stopped=False,
        state="idle", now=180.0, last_recovery=0.0,  # elapsed exactly == cooldown
    )
    assert pol.decide(ctx).verdict is RecoveryVerdict.PROCEED_KILL


def test_cooldown_first_call_neg_inf_proceeds():
    """First call: _last_proactive_restart = float('-inf') → always proceed (parity)."""
    pol = CooldownThresholdPolicy(cooldown_s=180.0)
    ctx = RecoveryContext(
        trigger="rss_proactive", enabled=True, user_stopped=False,
        state="idle", now=100.0, last_recovery=float("-inf"),
    )
    assert pol.decide(ctx).verdict is RecoveryVerdict.PROCEED_KILL


def test_cooldown_via_context_overrides_default():
    """cooldown passed via context (the decide_rss path) — stateless, no mutation."""
    pol = CooldownThresholdPolicy(cooldown_s=0.0)  # no default
    within = RecoveryContext(
        trigger="rss_proactive", enabled=True, user_stopped=False,
        state="idle", now=100.0, last_recovery=50.0, cooldown_s=180.0,
    )
    assert pol.decide(within).verdict is RecoveryVerdict.DEFER
    past = RecoveryContext(
        trigger="rss_proactive", enabled=True, user_stopped=False,
        state="idle", now=300.0, last_recovery=50.0, cooldown_s=180.0,
    )
    assert pol.decide(past).verdict is RecoveryVerdict.PROCEED_KILL


def test_cooldown_policy_never_escalates_or_graceful():
    """RSS shape imposes NO attempt-breaker, NO escalation, NO graceful — parity."""
    pol = CooldownThresholdPolicy(cooldown_s=180.0)
    # Many consecutive proceeds must never flip to ESCALATE/GRACEFUL.
    for i in range(10):
        ctx = RecoveryContext(
            trigger="rss_proactive", enabled=True, user_stopped=False,
            state="idle", now=1000.0 + i * 200, last_recovery=0.0,
        )
        v = pol.decide(ctx).verdict
        assert v in (RecoveryVerdict.PROCEED_KILL, RecoveryVerdict.DEFER)


# ─── R3b (M1): BareThresholdPolicy (RSS-streaming / stuck-WAITING shape) ──
# Bare-threshold = no cooldown, no attempt-breaker, no escalation, no graceful.
# The caller owns the threshold measurement; the policy answers only "given the
# caller already decided the threshold is met, may I kill in THIS state?".


def test_bare_threshold_proceeds_when_eligible():
    """Caller has measured the threshold breach; policy says PROCEED_KILL."""
    pol = BareThresholdPolicy()
    ctx = RecoveryContext(
        trigger="rss_streaming", enabled=True, user_stopped=False,
        state="streaming",
    )
    assert pol.decide(ctx).verdict is RecoveryVerdict.PROCEED_KILL


def test_bare_threshold_no_cooldown_no_escalation():
    """Bare shape imposes NO cooldown, NO breaker — many consecutive proceeds."""
    pol = BareThresholdPolicy()
    for i in range(10):
        ctx = RecoveryContext(
            trigger="rss_streaming", enabled=True, user_stopped=False,
            state="streaming", now=1000.0 + i * 5, last_recovery=999.0,
        )
        # Even back-to-back (now≈last_recovery), bare threshold never DEFERs.
        assert pol.decide(ctx).verdict is RecoveryVerdict.PROCEED_KILL


def test_bare_threshold_respects_universal_guards():
    """enabled=False and user_stopped=True still SKIP (shared guard)."""
    pol = BareThresholdPolicy()
    disabled = RecoveryContext(
        trigger="rss_streaming", enabled=False, user_stopped=False,
        state="streaming",
    )
    assert pol.decide(disabled).verdict is RecoveryVerdict.SKIP
    stopped = RecoveryContext(
        trigger="rss_streaming", enabled=True, user_stopped=True,
        state="streaming",
    )
    assert pol.decide(stopped).verdict is RecoveryVerdict.SKIP


def test_bare_threshold_eligible_states_configurable():
    """A bare policy can RESTRICT to specific states (default: any non-guarded).
    stuck-WAITING (M2) will use eligible_states={waiting_input}; RSS-streaming
    (M1) targets streaming. A state outside the set → SKIP."""
    pol = BareThresholdPolicy(eligible_states=frozenset({"streaming"}))
    ok = RecoveryContext(
        trigger="rss_streaming", enabled=True, user_stopped=False,
        state="streaming",
    )
    assert pol.decide(ok).verdict is RecoveryVerdict.PROCEED_KILL
    wrong = RecoveryContext(
        trigger="rss_streaming", enabled=True, user_stopped=False,
        state="idle",
    )
    assert wrong.state not in pol._eligible_states
    assert pol.decide(wrong).verdict is RecoveryVerdict.SKIP


def test_bare_threshold_default_any_state():
    """Default (no eligible_states) = eligible in any non-guarded state."""
    pol = BareThresholdPolicy()
    for st in ("streaming", "waiting_input", "idle"):
        ctx = RecoveryContext(
            trigger="rss_streaming", enabled=True, user_stopped=False, state=st,
        )
        assert pol.decide(ctx).verdict is RecoveryVerdict.PROCEED_KILL


def test_coordinator_routes_bare_threshold():
    """Coordinator.decide_bare dispatches to the bare policy."""
    coord = RecoveryCoordinator(HealingLoop())
    d = coord.decide_bare(
        trigger="rss_streaming", enabled=True, user_stopped=False,
        state="streaming",
    )
    assert d.verdict is RecoveryVerdict.PROCEED_KILL
    # guard still applies through the coordinator
    d2 = coord.decide_bare(
        trigger="rss_streaming", enabled=True, user_stopped=True,
        state="streaming",
    )
    assert d2.verdict is RecoveryVerdict.SKIP


def test_coordinator_decide_bare_eligible_states():
    """Coordinator.decide_bare forwards eligible_states restriction."""
    coord = RecoveryCoordinator(HealingLoop())
    d = coord.decide_bare(
        trigger="stuck_waiting", enabled=True, user_stopped=False,
        state="idle", eligible_states=frozenset({"waiting_input"}),
    )
    assert d.verdict is RecoveryVerdict.SKIP
    d2 = coord.decide_bare(
        trigger="stuck_waiting", enabled=True, user_stopped=False,
        state="waiting_input", eligible_states=frozenset({"waiting_input"}),
    )
    assert d2.verdict is RecoveryVerdict.PROCEED_KILL


# ─── AC2: AttemptBreakerPolicy is a pure extract of self-heal ─────────────


def test_attempt_breaker_policy_proceeds_when_healthy():
    pol = AttemptBreakerPolicy(HealingLoop())
    ctx = RecoveryContext(
        trigger="hang_detected", enabled=True, user_stopped=False,
        state="streaming", graceful_pending=False,
    )
    assert pol.decide(ctx).verdict is RecoveryVerdict.PROCEED_KILL


def test_attempt_breaker_policy_graceful_for_turn_approaching():
    pol = AttemptBreakerPolicy(HealingLoop())
    ctx = RecoveryContext(
        trigger="turn_approaching", enabled=True, user_stopped=False,
        state="streaming", graceful_pending=False,
    )
    assert pol.decide(ctx).verdict is RecoveryVerdict.PROCEED_GRACEFUL


def test_attempt_breaker_policy_escalates_at_max():
    loop = HealingLoop()
    pol = AttemptBreakerPolicy(loop)
    for _ in range(MAX_HEAL_ATTEMPTS):
        loop.record_heal_start("hang_detected")
    loop._last_heal_time = 0.0  # clear cooldown so ESCALATE (not DEFER)
    ctx = RecoveryContext(
        trigger="hang_detected", enabled=True, user_stopped=False,
        state="streaming", graceful_pending=False,
    )
    assert pol.decide(ctx).verdict is RecoveryVerdict.ESCALATE


# ─── AC5: guard is policy-driven (protected states belong to the policy) ──


def test_attempt_breaker_protects_waiting_input():
    pol = AttemptBreakerPolicy(HealingLoop())
    ctx = RecoveryContext(
        trigger="hang_detected", enabled=True, user_stopped=False,
        state="waiting_input", graceful_pending=False,
    )
    assert pol.decide(ctx).verdict is RecoveryVerdict.SKIP


def test_cooldown_policy_does_not_protect_waiting_input():
    """A policy can be eligible in a state self-heal protects — proves stuck-WAITING
    (#7, which TARGETS waiting_input) will fit the template later."""
    pol = CooldownThresholdPolicy(cooldown_s=180.0)
    ctx = RecoveryContext(
        trigger="rss_proactive", enabled=True, user_stopped=False,
        state="waiting_input", now=300.0, last_recovery=0.0,
    )
    # Not auto-SKIP'd by a hardcoded protected-state set; RSS policy decides on its
    # own terms (here: past cooldown → proceed).
    assert pol.decide(ctx).verdict is RecoveryVerdict.PROCEED_KILL


# ─── AC1/AC2: universal guards still shared (enabled / user_stopped) ──────


def test_universal_guard_disabled_skips_any_policy():
    for pol in (AttemptBreakerPolicy(HealingLoop()),
                CooldownThresholdPolicy(cooldown_s=180.0)):
        ctx = RecoveryContext(
            trigger="x", enabled=False, user_stopped=False,
            state="streaming", now=300.0, last_recovery=0.0,
        )
        assert pol.decide(ctx).verdict is RecoveryVerdict.SKIP


def test_universal_guard_user_stopped_skips_any_policy():
    for pol in (AttemptBreakerPolicy(HealingLoop()),
                CooldownThresholdPolicy(cooldown_s=180.0)):
        ctx = RecoveryContext(
            trigger="x", enabled=True, user_stopped=True,
            state="streaming", now=300.0, last_recovery=0.0,
        )
        assert pol.decide(ctx).verdict is RecoveryVerdict.SKIP


# ─── AC6: Coordinator dispatches by trigger to a policy via registry ──────


def test_coordinator_dispatches_to_registered_policy():
    loop = HealingLoop()
    coord = RecoveryCoordinator(loop)
    # self-heal triggers dispatch to the attempt-breaker policy
    d = coord.decide(
        "hang_detected", enabled=True, user_stopped=False,
        state="streaming", graceful_pending=False,
    )
    assert d.verdict is RecoveryVerdict.PROCEED_KILL  # backward-compat with R3


def test_coordinator_routes_rss_to_cooldown_policy():
    loop = HealingLoop()
    coord = RecoveryCoordinator(loop)
    # rss_proactive dispatches to the cooldown policy (past cooldown → proceed)
    d = coord.decide_rss(now=300.0, last_recovery=0.0, cooldown_s=180.0,
                         enabled=True, user_stopped=False, state="idle")
    assert d.verdict is RecoveryVerdict.PROCEED_KILL
    d2 = coord.decide_rss(now=100.0, last_recovery=50.0, cooldown_s=180.0,
                          enabled=True, user_stopped=False, state="idle")
    assert d2.verdict is RecoveryVerdict.DEFER
