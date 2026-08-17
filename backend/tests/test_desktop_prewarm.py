"""Desktop tab cold-start prewarm — 方案A (design v2 §4/§5, run_f107f442).

The daemon keeps a small pool of pre-spawned "baseline-prompt" subprocesses; a
new desktop chat tab's first message ADOPTS one instead of paying the 8-14s SDK
__aenter__ cold handshake. This is the desktop generalization of the channel
prewarm (gateway.py) that already exists for Slack.

The load-bearing fix these tests pin (Gate-1 #5, benefit-killer):
``adopt_prewarmed_unit`` re-keys ``unit.session_id`` from ``prewarm-<uuid>`` to
the real id — which DESTROYS the ``prewarm-`` prefix that BOTH warm-reuse
exemption sites key on (``session_router._is_warm_reuse`` and
``session_unit`` poison_guard). Without a bridge, the adopted unit's FIRST
message hits poison_guard recycle (kill+respawn) → the whole prewarm benefit is
lost. The fix is a TRANSIENT one-shot flag ``_adopted_prewarm_fresh`` set at
adopt, honored at BOTH exemption sites, and CLEARED at STREAMING entry (so the
SECOND message sees the normal poison_guard again).

This is NOT the rejected persistent ``is_prewarm`` identity field
(IMPROVEMENT.md:1400) — it is a self-clearing one-turn bridge over the re-key
instant, and does not replace the prefix authority anywhere else.

Methodology: drive the REAL predicates (``_is_warm_reuse``, the poison_guard
condition) against real SessionUnit state — no mock of the function under
change. Mutation check: removing the ``_adopted_prewarm_fresh`` disjunct makes
``test_adopted_unit_warm_reuses_on_first_message`` go RED.
"""
from __future__ import annotations

import pytest

from core.session_router import _is_warm_reuse, PREWARM_SESSION_PREFIX
from core.session_unit import SessionUnit, SessionState


def _idle_prewarm_unit(on_state_change=None) -> SessionUnit:
    """A freshly-spawned prewarm unit: IDLE, live client, never streamed."""
    unit = SessionUnit(
        session_id=f"{PREWARM_SESSION_PREFIX}abc-123",
        agent_id="agent-default",
        on_state_change=on_state_change,
    )
    unit.state = SessionState.IDLE
    unit._client = object()  # stand-in for a live SDK client
    unit._last_turn_clean = False  # never completed a turn
    return unit


@pytest.fixture
def flag_on(monkeypatch):
    """Enable the SWARM_DESKTOP_PREWARM strangler flag for pool-path tests."""
    monkeypatch.setenv("SWARM_DESKTOP_PREWARM", "1")


# ── AC7: warm-reuse survives the adopt re-key (the benefit-killer fix) ──────

def test_prewarm_prefix_unit_warm_reuses_before_adopt():
    """Baseline: a prewarm-prefixed IDLE unit is warm-reuse eligible (阶段一)."""
    unit = _idle_prewarm_unit()
    assert _is_warm_reuse(unit) is True


def test_adopted_unit_warm_reuses_on_first_message():
    """After adopt re-keys session_id (prefix GONE), the unit must STILL be
    warm-reuse eligible on its first message — via the _adopted_prewarm_fresh
    one-shot bridge. This is the exact benefit-killer Gate-1 #5 found.

    Mutation: drop the `or unit._adopted_prewarm_fresh` disjunct → RED here.
    """
    unit = _idle_prewarm_unit()
    # Simulate adopt_prewarmed_unit re-key: prefix is lost, bridge flag set.
    unit.session_id = "real-session-xyz"
    unit._adopted_prewarm_fresh = True
    assert not unit.session_id.startswith(PREWARM_SESSION_PREFIX)  # prefix gone
    assert unit._last_turn_clean is False  # never streamed
    # Without the bridge this would be False → poison_guard recycle → benefit lost
    assert _is_warm_reuse(unit) is True


def test_adopted_bridge_defaults_false_for_normal_unit():
    """A normal (non-adopted) IDLE unit with an unclean last turn is NOT
    warm-reuse eligible — the bridge must default False so a real
    first-message-disconnect zombie (recover_from_disconnect) still recycles."""
    unit = SessionUnit(session_id="real-normal", agent_id="agent-default")
    unit.state = SessionState.IDLE
    unit._client = object()
    unit._last_turn_clean = False
    assert _is_warm_reuse(unit) is False


# ── AC7 (R27 twin): poison_guard exemption + one-shot clear at STREAMING ────

def test_poison_guard_exempts_adopted_fresh_unit():
    """The poison_guard recycle condition (session_unit) is the EXACT COMPLEMENT
    of _is_warm_reuse — it must ALSO honor _adopted_prewarm_fresh, or the two
    gates disagree and the adopted first message recycles anyway.

    Drives the REAL predicate `_should_poison_guard_recycle()` (the exact method
    send() calls) — NOT an inline re-implementation — so a production revert of
    the bridge disjunct makes this RED (Gate-2 anti-theater fix, C044).

    Mutation: drop `or self._adopted_prewarm_fresh` from the real method → RED.
    """
    unit = _idle_prewarm_unit()
    unit.session_id = "real-session-xyz"  # adopt re-keyed → prefix gone
    unit._adopted_prewarm_fresh = True
    # Real method — a re-keyed adopted unit must NOT recycle on its first message.
    assert unit._should_poison_guard_recycle() is False


def test_poison_guard_recycles_real_zombie():
    """The complement: a genuine turn-2+ zombie (prefix gone, bridge cleared,
    unclean) MUST recycle — proves the bridge doesn't over-exempt."""
    unit = _idle_prewarm_unit()
    unit.session_id = "real-session-xyz"
    unit._adopted_prewarm_fresh = False  # bridge already consumed at STREAMING
    unit._last_turn_clean = False        # last turn didn't complete cleanly
    assert unit._should_poison_guard_recycle() is True


def test_streaming_entry_clears_the_bridge():
    """The one-shot bridge must be CLEARED once a turn starts, so the SECOND
    message sees the normal poison_guard. The clear happens at the same
    STREAMING-entry chokepoint that sets _last_turn_clean=False (session_unit
    :~1106). We drive that transition and assert the flag flips off."""
    unit = _idle_prewarm_unit()
    unit.session_id = "real-session-xyz"
    unit._adopted_prewarm_fresh = True

    # Drive the REAL STREAMING-entry transition (the single chokepoint that
    # resets per-turn flags at session_unit :~1120).
    unit._transition(SessionState.STREAMING)

    assert unit._adopted_prewarm_fresh is False
    assert unit._last_turn_clean is False


def test_adopt_prewarmed_unit_sets_bridge_flag():
    """adopt_prewarmed_unit must SET the bridge flag when it re-keys, else the
    whole chain is dead. Driven against the real router adopt method."""
    import asyncio
    from core.session_router import SessionRouter

    router = SessionRouter(prompt_builder=object())  # adopt path never uses it
    unit = _idle_prewarm_unit()
    prewarm_id = unit.session_id
    router._units[prewarm_id] = unit

    ok = asyncio.run(router.adopt_prewarmed_unit(prewarm_id, "real-session-xyz"))
    assert ok is True
    assert unit.session_id == "real-session-xyz"
    assert unit._adopted_prewarm_fresh is True


# ── AC2/AC6: bucket-validated + staleness-guarded desktop adopt ─────────────

def _router() -> "object":
    from core.session_router import SessionRouter
    return SessionRouter(prompt_builder=object())


def test_try_adopt_desktop_pool_matches_bucket(flag_on):
    """A pooled IDLE unit whose bucket == (desktop, agent, model) is adopted;
    the pool entry is popped and the unit re-keyed to the real session_id."""
    import asyncio
    router = _router()
    unit = _idle_prewarm_unit()
    prewarm_id = unit.session_id
    router._units[prewarm_id] = unit
    key = ("desktop", "agent-default", "claude-opus-4-8")
    router._desktop_prewarm_pool[key] = prewarm_id
    router._desktop_prewarm_meta[prewarm_id] = {
        "ctx_hash": router._desktop_ctx_hash(),
        "spawned_monotonic": router._now_monotonic(),
    }

    ok = asyncio.run(router._try_adopt_desktop_pool(
        "real-1", "agent-default", "claude-opus-4-8"))
    assert ok is True
    assert "real-1" in router._units
    assert key not in router._desktop_prewarm_pool  # entry consumed
    assert router._units["real-1"]._adopted_prewarm_fresh is True


def test_try_adopt_desktop_pool_rejects_wrong_model(flag_on):
    """Bucket mismatch (wrong model) → no adopt (falls through to cold). A
    wrong-model adopt = wrong persona/tools (Gate-1 M2)."""
    import asyncio
    router = _router()
    unit = _idle_prewarm_unit()
    prewarm_id = unit.session_id
    router._units[prewarm_id] = unit
    router._desktop_prewarm_pool[("desktop", "agent-default", "claude-opus-4-8")] = prewarm_id
    router._desktop_prewarm_meta[prewarm_id] = {
        "ctx_hash": router._desktop_ctx_hash(),
        "spawned_monotonic": router._now_monotonic(),
    }

    ok = asyncio.run(router._try_adopt_desktop_pool(
        "real-2", "agent-default", "claude-sonnet-4"))  # different model
    assert ok is False
    assert "real-2" not in router._units


def test_try_adopt_desktop_pool_rejects_stale_ctx(flag_on):
    """A pooled unit whose context-file hash changed since spawn is discarded
    at adopt (cold fallback) — the固化 baseline prompt would be stale (AC6)."""
    import asyncio
    router = _router()
    unit = _idle_prewarm_unit()
    prewarm_id = unit.session_id
    router._units[prewarm_id] = unit
    key = ("desktop", "agent-default", "claude-opus-4-8")
    router._desktop_prewarm_pool[key] = prewarm_id
    router._desktop_prewarm_meta[prewarm_id] = {
        "ctx_hash": "STALE-HASH-does-not-match-current",
        "spawned_monotonic": router._now_monotonic(),
    }

    ok = asyncio.run(router._try_adopt_desktop_pool(
        "real-3", "agent-default", "claude-opus-4-8"))
    assert ok is False  # stale → cold fallback


# ── AC4/AC5: H2 single-sink cleanup keyed on the prewarm prefix ─────────────

def test_dead_transition_cleans_pool_entry():
    """When a pooled unit transitions to DEAD (via ANY kill path → _transition
    → _on_unit_state_change), its pool + meta entries are removed. Keyed on
    PREWARM_SESSION_PREFIX (no is_prewarm field)."""
    router = _router()
    unit = _idle_prewarm_unit(on_state_change=router._on_unit_state_change)
    prewarm_id = unit.session_id
    router._units[prewarm_id] = unit
    key = ("desktop", "agent-default", "claude-opus-4-8")
    router._desktop_prewarm_pool[key] = prewarm_id
    router._desktop_prewarm_meta[prewarm_id] = {"ctx_hash": "x", "spawned_monotonic": 0.0}

    unit._transition(SessionState.DEAD)

    assert key not in router._desktop_prewarm_pool  # H2 sink cleaned it
    assert prewarm_id not in router._desktop_prewarm_meta


# ── AC8: flag OFF → no warm, adopt intercept is a no-op (byte-identical) ────

def test_warm_desktop_pool_noop_when_flag_off(monkeypatch):
    """With SWARM_DESKTOP_PREWARM unset/off, warm_desktop_pool does nothing —
    the cold path is unchanged."""
    import asyncio
    monkeypatch.delenv("SWARM_DESKTOP_PREWARM", raising=False)
    router = _router()
    asyncio.run(router.warm_desktop_pool(depth=2))
    assert router._desktop_prewarm_pool == {}

