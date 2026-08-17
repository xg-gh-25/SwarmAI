"""R6 Step A — orphan-session reaper (lifecycle_manager._check_orphan_sessions).

The reaper replaces cross-tab eviction's orphan-reclaim role (R6 §9.9): it GCs a
chat session owned by NO live window (closed window / crashed frontend / SSE
drop) so it can't squat a concurrency slot once cross-tab eviction is deleted
(Step C). It must NEVER reap a session a real user still owns.

Every test here is a FORCING test for a Gate-1 skeptic finding:
- WAITING_INPUT false-reap (the BLOCK): a user stepped away mid-question must survive.
- STREAMING never touched.
- Channel sessions exempt.
- Ownership fail-safe: open_tabs unknowable (None) → reap NOTHING.
- Grace: unowned but freshly-idle → survive.
- The actual orphan (unowned + IDLE + past grace + non-channel) → reaped.

Strategy: build a LifecycleManager with a mock router whose list_units() returns
SessionUnit instances walked to the target state; mock open_tabs via the reaper's
_owned_session_ids; assert kill() is/ isn't called.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit
from core.lifecycle_manager import LifecycleManager


_PATHS_FROM_COLD: dict[SessionState, list[SessionState]] = {
    SessionState.COLD: [],
    SessionState.IDLE: [SessionState.IDLE],
    SessionState.STREAMING: [SessionState.IDLE, SessionState.STREAMING],
    SessionState.WAITING_INPUT: [
        SessionState.IDLE, SessionState.STREAMING, SessionState.WAITING_INPUT,
    ],
}


def _make_unit(
    session_id: str,
    state: SessionState,
    *,
    idle_age: float,
    is_channel: bool = False,
) -> SessionUnit:
    """A SessionUnit in `state`, last_used `idle_age` seconds ago, kill() mocked."""
    unit = SessionUnit(session_id=session_id, agent_id="default")
    for hop in _PATHS_FROM_COLD[state]:
        unit._transition(hop)
    unit.is_channel_session = is_channel
    unit.last_used = time.time() - idle_age
    unit._hooks_enqueued = True  # suppress hook path; isolate reap decision
    unit.kill = AsyncMock()
    return unit


def _make_manager(units: list[SessionUnit]) -> LifecycleManager:
    router = MagicMock()
    router.list_units.return_value = units
    mgr = LifecycleManager(router=router)
    # Neutralize _release_session_state side effects (module-level dicts).
    mgr._release_session_state = MagicMock()
    return mgr


PAST_GRACE = LifecycleManager.ORPHAN_GRACE_SECONDS + 60
WITHIN_GRACE = LifecycleManager.ORPHAN_GRACE_SECONDS - 60


async def _run(mgr: LifecycleManager, owned: set[str] | None):
    with patch.object(LifecycleManager, "_owned_session_ids", return_value=owned):
        await mgr._check_orphan_sessions()


@pytest.mark.asyncio
async def test_orphan_idle_unowned_past_grace_is_reaped():
    """The actual orphan: IDLE, not in open_tabs, past grace, non-channel → killed."""
    orphan = _make_unit("orphan-1", SessionState.IDLE, idle_age=PAST_GRACE)
    mgr = _make_manager([orphan])
    await _run(mgr, owned=set())  # a window is connected, reports zero tabs
    orphan.kill.assert_awaited_once()


@pytest.mark.asyncio
async def test_waiting_input_session_is_never_reaped():
    """Gate-1 BLOCK: user stepped away mid-question (WAITING_INPUT) must survive,
    even though its SSE stream is closed and it is not in open_tabs."""
    waiting = _make_unit("waiting-1", SessionState.WAITING_INPUT, idle_age=PAST_GRACE)
    mgr = _make_manager([waiting])
    await _run(mgr, owned=set())
    waiting.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_streaming_session_is_never_reaped():
    """STREAMING is a protected active state — never an orphan."""
    streaming = _make_unit("streaming-1", SessionState.STREAMING, idle_age=PAST_GRACE)
    mgr = _make_manager([streaming])
    await _run(mgr, owned=set())
    streaming.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_channel_session_is_exempt():
    """Channel sessions have no window/tab and are daemon-owned → never reaped."""
    chan = _make_unit("chan-1", SessionState.IDLE, idle_age=PAST_GRACE, is_channel=True)
    mgr = _make_manager([chan])
    await _run(mgr, owned=set())
    chan.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_owned_session_is_never_reaped():
    """A session a live window still has open is not an orphan."""
    owned_unit = _make_unit("owned-1", SessionState.IDLE, idle_age=PAST_GRACE)
    mgr = _make_manager([owned_unit])
    await _run(mgr, owned={"owned-1"})
    owned_unit.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_prewarm_unit_is_exempt_from_orphan_reap():
    """P-a AC1: an unadopted prewarm unit (prewarm- prefix) is IDLE, not in
    open_tabs, non-channel, past grace — it hits every orphan criterion — yet
    must be EXEMPT: it is a warm subprocess awaiting adoption, not an orphan.
    This is the root cause of Slack prewarm adopt=0 (reaped before adoption)."""
    from core.session_router import PREWARM_SESSION_PREFIX
    prewarm = _make_unit(
        f"{PREWARM_SESSION_PREFIX}abc123", SessionState.IDLE, idle_age=PAST_GRACE
    )
    mgr = _make_manager([prewarm])
    await _run(mgr, owned=set())
    prewarm.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_prewarm_unit_is_exempt_from_ttl_kill():
    """P-a AC1: the 24h TTL reaper must also exempt an unadopted prewarm unit —
    otherwise a long-lived prewarm pool unit is TTL-killed like a stale chat."""
    from core.session_router import PREWARM_SESSION_PREFIX
    prewarm = _make_unit(
        f"{PREWARM_SESSION_PREFIX}def456", SessionState.IDLE,
        idle_age=LifecycleManager.TTL_SECONDS + 60,
    )
    mgr = _make_manager([prewarm])
    await mgr._check_ttl()
    prewarm.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_unowned_but_within_grace_survives():
    """Freshly-idle unowned session (id not yet persisted to open_tabs) survives."""
    fresh = _make_unit("fresh-1", SessionState.IDLE, idle_age=WITHIN_GRACE)
    mgr = _make_manager([fresh])
    await _run(mgr, owned=set())
    fresh.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknowable_ownership_reaps_nothing():
    """Gate-1 fail-safe: open_tabs unreadable/missing (None) → reap NOTHING.
    A read error must never be misread as 'no tabs open → all orphans'."""
    would_be_orphan = _make_unit("x-1", SessionState.IDLE, idle_age=PAST_GRACE)
    mgr = _make_manager([would_be_orphan])
    await _run(mgr, owned=None)  # unknowable
    would_be_orphan.kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_fleet_only_orphan_reaped():
    """Integration: a realistic fleet — only the true orphan dies."""
    orphan = _make_unit("orphan", SessionState.IDLE, idle_age=PAST_GRACE)
    owned_tab = _make_unit("tab", SessionState.IDLE, idle_age=PAST_GRACE)
    waiting = _make_unit("waiting", SessionState.WAITING_INPUT, idle_age=PAST_GRACE)
    streaming = _make_unit("stream", SessionState.STREAMING, idle_age=PAST_GRACE)
    chan = _make_unit("chan", SessionState.IDLE, idle_age=PAST_GRACE, is_channel=True)
    mgr = _make_manager([orphan, owned_tab, waiting, streaming, chan])
    await _run(mgr, owned={"tab"})
    orphan.kill.assert_awaited_once()
    for survivor in (owned_tab, waiting, streaming, chan):
        survivor.kill.assert_not_awaited()


class TestOwnedSessionIdsHelper:
    """_owned_session_ids: the fail-safe contract (None vs set())."""

    def test_missing_file_returns_none(self, tmp_path):
        fake = tmp_path / "nope.json"
        with patch("routers.settings._get_open_tabs_path", return_value=fake):
            assert LifecycleManager._owned_session_ids() is None

    def test_malformed_file_returns_none(self, tmp_path):
        f = tmp_path / "open_tabs.json"
        f.write_text("{ this is not json", encoding="utf-8")
        with patch("routers.settings._get_open_tabs_path", return_value=f):
            assert LifecycleManager._owned_session_ids() is None

    def test_valid_file_returns_session_id_set(self, tmp_path):
        import json
        f = tmp_path / "open_tabs.json"
        f.write_text(json.dumps({"tabs": [
            {"id": "t1", "sessionId": "s1"},
            {"id": "t2", "sessionId": "s2"},
            {"id": "t3", "isNew": True},  # new tab, no sessionId yet
        ]}), encoding="utf-8")
        with patch("routers.settings._get_open_tabs_path", return_value=f):
            assert LifecycleManager._owned_session_ids() == {"s1", "s2"}

    def test_empty_tabs_returns_empty_set_not_none(self, tmp_path):
        import json
        f = tmp_path / "open_tabs.json"
        f.write_text(json.dumps({"tabs": []}), encoding="utf-8")
        with patch("routers.settings._get_open_tabs_path", return_value=f):
            assert LifecycleManager._owned_session_ids() == set()
