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


# ── AC1 (CRITICAL): history-bearing session must NEVER adopt ────────────────

def _seed_bucket(router, prewarm_id, model="claude-opus-4-8", agent="agent-default"):
    key = ("desktop", agent, model)
    router._desktop_prewarm_pool[key] = prewarm_id
    router._desktop_prewarm_meta[prewarm_id] = {
        "ctx_hash": router._desktop_ctx_hash(),
        "spawned_monotonic": router._now_monotonic(),
    }
    return key


def _patch_db_count(monkeypatch, count):
    """monkeypatch database.db.messages.count_by_session → returns `count`."""
    class _Msgs:
        async def count_by_session(self, sid):
            return count
    class _DB:
        messages = _Msgs()
    monkeypatch.setitem(__import__("sys").modules, "database",
                        type("M", (), {"db": _DB()}))


def test_history_bearing_session_never_adopts(flag_on, monkeypatch):
    """AC1 CRITICAL — a reopened tab WITH history (count_by_session >= 1 at the
    PRE-persist intercept, i.e. at least one prior row) must NOT adopt a baseline
    prewarm subprocess: adopt would give it a history-free system_prompt +
    warm-reuse via query(), and (adopted → IDLE → is_cold_resume False) the resume
    block would never be built → history silently lost. It must fall through to
    cold/--resume instead.

    ⚠️ Threshold is >= 1 (NOT > 1): the adopt-intercept runs BEFORE the current
    message is persisted (session_router :2855), so count here is prior-history
    rows ONLY. Even a single prior row means "has history".

    Mutation: remove the history guard → this goes RED (adopt succeeds).
    """
    import asyncio
    router = _router()
    unit = _idle_prewarm_unit()
    prewarm_id = unit.session_id
    router._units[prewarm_id] = unit
    key = _seed_bucket(router, prewarm_id)
    _patch_db_count(monkeypatch, 1)  # exactly one prior row = has history

    ok = asyncio.run(router._try_adopt_desktop_pool(
        "real-hist", "agent-default", "claude-opus-4-8"))

    assert ok is False, "history-bearing session must not adopt"
    assert "real-hist" not in router._units
    assert key in router._desktop_prewarm_pool, "pool entry must be left for a new tab"


def test_fresh_session_still_adopts_with_zero_history(flag_on, monkeypatch):
    """The complement: a brand-new tab (0 prior rows at pre-persist intercept)
    still adopts — proves the guard is >=1, not over-broad (doesn't block fresh
    tabs, which is the whole point of prewarm)."""
    import asyncio
    router = _router()
    unit = _idle_prewarm_unit()
    prewarm_id = unit.session_id
    router._units[prewarm_id] = unit
    _seed_bucket(router, prewarm_id)
    _patch_db_count(monkeypatch, 0)  # brand-new tab, no prior rows

    ok = asyncio.run(router._try_adopt_desktop_pool(
        "real-fresh", "agent-default", "claude-opus-4-8"))

    assert ok is True, "fresh session (0 history) must still adopt"
    assert "real-fresh" in router._units


# ── AC3 (HIGH): stale prewarm unit is KILLED, not leaked ────────────────────

def test_stale_prewarm_unit_is_killed(flag_on, monkeypatch):
    """AC3 — the stale branch of _try_adopt_desktop_pool must KILL the discarded
    prewarm unit (not just pop the dicts). A prewarm-prefixed unit is exempt from
    TTL + orphan-reaper, so leaving it in _units leaks a live subprocess until
    memory pressure.

    Mutation: remove the kill in the stale branch → this goes RED (unit survives).
    """
    import asyncio
    from unittest.mock import AsyncMock
    router = _router()
    unit = _idle_prewarm_unit()
    prewarm_id = unit.session_id
    unit.kill = AsyncMock()  # observe the kill
    router._units[prewarm_id] = unit
    _patch_db_count(monkeypatch, 0)  # fresh session (history guard passes)
    key = ("desktop", "agent-default", "claude-opus-4-8")
    router._desktop_prewarm_pool[key] = prewarm_id
    router._desktop_prewarm_meta[prewarm_id] = {
        "ctx_hash": "STALE-HASH", "spawned_monotonic": router._now_monotonic(),
    }

    ok = asyncio.run(router._try_adopt_desktop_pool(
        "real-stale", "agent-default", "claude-opus-4-8"))

    assert ok is False  # stale → cold fallback
    unit.kill.assert_awaited_once()  # the discarded unit is killed
    assert prewarm_id not in router._units  # not leaked


# ── AC4 (MED): sacred-first-tab count excludes prewarm; budget keeps it ──────

def test_user_chat_alive_count_excludes_prewarm():
    """AC4 — _user_chat_alive_count (used by the sacred-first-tab check) must
    EXCLUDE prewarm-prefixed units so a warm pool doesn't disable the sacred
    first-tab grant. But alive_count (used by spawn_budget) must STILL COUNT
    them — a prewarm unit is a real subprocess consuming real RAM.

    Mutation: make _user_chat_alive_count == _chat_alive_count → RED here.
    """
    router = _router()
    p = _idle_prewarm_unit()
    router._units[p.session_id] = p

    # A prewarm unit exists → sacred-check count must read 0 (no USER tab yet),
    # but budget/alive count must read 1 (RAM is really occupied).
    assert router._user_chat_alive_count == 0, "sacred count must exclude prewarm"
    assert router.alive_count == 1, "budget count must INCLUDE prewarm (real RAM)"


def test_user_chat_alive_count_counts_real_tab():
    """The complement: a real (non-prewarm) chat unit IS counted by the sacred
    count — proves it isn't just always-zero."""
    router = _router()
    real = SessionUnit(session_id="real-tab", agent_id="agent-default")
    real.state = SessionState.IDLE
    real._client = object()
    router._units["real-tab"] = real
    assert router._user_chat_alive_count == 1


# ── AC6 (MED): ctx_hash derives from the real workspace path ────────────────

def test_ctx_hash_derives_from_workspace_path(monkeypatch, tmp_path):
    """AC6 — _desktop_ctx_hash must fingerprint the ACTUAL workspace .context dir
    (what the prewarm subprocess built its prompt from), not a hardcoded
    ~/.swarm-ai/SwarmWS/.context. Under a custom workspace (SWARM_DATA_DIR /
    non-default workspace_path) the hardcode reads the WRONG dir → staleness
    detection is blind.

    Mutation: revert to the hardcoded Path.home() dir → this goes RED (hash reads
    the wrong dir, doesn't reflect the tmp .context we created).
    """
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "SOUL.md").write_text("soul-v1")
    (ctx / "AGENT.md").write_text("agent-v1")

    from core.initialization_manager import initialization_manager
    monkeypatch.setattr(
        initialization_manager, "get_cached_workspace_path",
        lambda: str(tmp_path), raising=True,
    )
    import core.session_router as sr
    h1 = sr.SessionRouter._desktop_ctx_hash()
    assert h1, "hash must be non-empty when the derived .context dir exists"

    # Mutating a file in the DERIVED dir must change the hash (proves it reads there).
    (ctx / "SOUL.md").write_text("soul-v2-changed")
    h2 = sr.SessionRouter._desktop_ctx_hash()
    assert h1 != h2, "hash must track the derived workspace .context, not a hardcode"


# ── AC2 (HIGH): DE-SCOPED — see IMPROVEMENT note + run summary ──────────────
# The proposed fix (make graceful _evict_idle reclaim a SOLE prewarm immediately
# instead of deferring to force=True) would REVERSE an explicit XG-directed
# anti-starvation contract (run_f107f442: "B 不能 regression — force=True is the
# SOLE anti-starvation guarantee", guarded by test_eviction_queue_before_force.py
# ::TestPrewarmEvictionDowngrade). The finding's real cost is a <5-min startup
# window (after grace, the existing filter already reclaims the aged prewarm), and
# the skeptic rated #2 REFINE/low-value/borders-redundant. Reversing an XG contract
# for a narrow latency win is an L2 decision handed back to XG, NOT taken mid-run.
# Flag-enablement SAFETY does not depend on #2 (that is #1/#3/#4/#5/#6).


# ── AC8: flag OFF → no warm, adopt intercept is a no-op (byte-identical) ────

def test_warm_desktop_pool_noop_when_flag_off(monkeypatch):
    """With SWARM_DESKTOP_PREWARM unset/off, warm_desktop_pool does nothing —
    the cold path is unchanged."""
    import asyncio
    monkeypatch.delenv("SWARM_DESKTOP_PREWARM", raising=False)
    router = _router()
    asyncio.run(router.warm_desktop_pool(depth=2))
    assert router._desktop_prewarm_pool == {}

