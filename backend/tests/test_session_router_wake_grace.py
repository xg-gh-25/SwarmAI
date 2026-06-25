"""Tests for grace-respecting eviction in the queue-WAKE loop of _acquire_chat_slot.

Verified incident (2026-06-21, daemon log):
  - A queued reconnect session (reconF-e93d0d16, mcps=0) was waiting for a slot.
  - The instant a user's chat session went STREAMING->IDLE (07:59:01.630), the
    queued waker woke on _slot_available and called _evict_idle(force=True) at
    session_router.py:924, which BYPASSES the grace period.
  - It force-killed the user's warm tab (idle 0s, exit -9) — _eviction_key prefers
    highest-RSS, and a just-answered tab is the largest.
  - User then waited 2m28s (08:00:44 -> 08:03:12) to re-acquire a slot.
  - System RAM at the time: 11% (4177MB/36GB) — NOT a memory-pressure eviction.

Fix (P3): L924 wake-path uses force=False so the per-wake eviction attempt
respects grace and CANNOT force-kill a freshly-idled (within-grace) session.
Progress is still guaranteed by the UNCHANGED L930-936 timeout last-resort
(force=True), which fires only after QUEUE_TIMEOUT.

These tests exercise the WAKE loop specifically (L901-925) — distinct from
test_eviction_queue_before_force.py which covers the initial-deny path (L865-891)
and the post-timeout last-resort (L930).
"""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_router import SessionRouter
from core.session_unit import SessionState


@pytest.fixture(autouse=True)
def _owned_session_ids():
    """Pin window-ownership to a deterministic empty set across platforms.

    ``_evict_idle`` late-imports ``routers.settings.owned_session_ids`` for the
    R6 §9.9 orphan-only filter, sourced from ``open_tabs.json``. That file is
    present on a dev macOS box (real call returns live IDs → synthetic units are
    orphans → tests pass) but ABSENT on CI → fail-safe returns ``None`` →
    eviction refuses → eviction-expecting tests fail. Environment leak, not a
    product bug (COE01 class). ``set()`` = "window connected, zero open tabs" →
    every synthetic unit is an orphan and eviction is exercised deterministically.
    """
    with patch("routers.settings.owned_session_ids", return_value=set()):
        yield


@pytest.fixture
def router():
    prompt_builder = MagicMock()
    config = MagicMock()
    return SessionRouter(prompt_builder=prompt_builder, config=config)


def _make_unit(session_id: str, state: SessionState, idle_seconds: float = 0,
               is_channel: bool = False, rss_bytes: int = 500_000_000):
    """Mock SessionUnit with controlled state/timing. Mirrors the helper in
    test_eviction_queue_before_force.py so harness behavior matches."""
    unit = MagicMock()
    unit.session_id = session_id
    unit.state = state
    unit.is_channel_session = is_channel
    unit.last_used = time.time() - idle_seconds
    unit.is_alive = state in (SessionState.STREAMING, SessionState.IDLE,
                              SessionState.WAITING_INPUT)
    unit._hooks_enqueued = False
    unit.kill = AsyncMock()
    unit.is_post_disconnect_flushing = False
    metrics = MagicMock()
    metrics.rss_bytes = rss_bytes
    unit._last_metrics = metrics
    return unit


class TestWakeGraceProtection:
    """The queue-WAKE eviction must respect grace (the verified incident fix)."""

    @pytest.mark.asyncio
    async def test_wake_does_not_kill_freshly_idled_tab_before_timeout(self, router):
        """AC1 — Reproduce 07:59:01 exactly (the verified incident).

        Pool full. The ONLY idle unit is the user's freshly-idled warm tab
        (idle 0s, high RSS) — same as the incident (no stale alternative).
        A queued waker wakes when _slot_available fires. Budget stays denied
        and count stays full (an idle unit is still alive), so the wake path
        reaches _evict_idle.

        Discriminator is TIMING, because the incident had no stale alternative:
          OLD (L924 force=True): grace SKIPPED → user tab force-killed ON WAKE
            (~50ms), long before the 300s timeout. → user_tab.kill called early.
          FIX (L924 force=False): grace BLOCKS → _evict_idle returns False on
            wake → waker keeps waiting → user tab NOT killed until the L930
            timeout fallback (which we never reach here; we cancel first).

        We run acquire as a task, fire the wake, let it process, then check
        whether the kill happened *on the wake* (before timeout). OLD: yes (RED).
        FIX: no (GREEN). QUEUE_TIMEOUT is long so the timeout path can't mask it.
        """
        user_tab = _make_unit("user-32699a22", SessionState.IDLE,
                              idle_seconds=0, rss_bytes=1_500_000_000)
        waker = _make_unit("reconF-e93d0d16", SessionState.COLD, idle_seconds=0)
        router._units = {"user-32699a22": user_tab, "reconF-e93d0d16": waker}
        router.QUEUE_TIMEOUT = 10.0  # long — keep the timeout fallback out of the way

        mock_budget = MagicMock()
        mock_budget.can_spawn = False  # stays denied → wake reaches _evict_idle
        mock_budget.reason = "all_slots_occupied"
        mock_budget.available_mb = 1000
        mock_budget.estimated_cost_mb = 1500
        mock_budget.headroom_mb = 500

        mock_resource = MagicMock()
        mock_resource.compute_max_tabs.return_value = 4  # chat_max=3
        mock_resource.spawn_budget.return_value = mock_budget
        mock_resource.invalidate_cache = MagicMock()

        with patch("core.resource_monitor.resource_monitor", mock_resource):
            with patch.object(type(router), "_chat_alive_count",
                              new_callable=lambda: property(lambda self: 3)):
                with patch.object(type(router), "alive_count",
                                  new_callable=lambda: property(lambda self: 3)):
                    task = asyncio.ensure_future(router._acquire_chat_slot(waker))
                    await asyncio.sleep(0.05)      # let it queue + start waiting
                    router._slot_available.set()   # the incident wake trigger
                    await asyncio.sleep(0.15)       # let the wake be processed

                    # The decisive check: was the within-grace tab killed ON WAKE
                    # (before the 10s timeout)? OLD force=True → True (RED).
                    killed_on_wake = user_tab.kill.called

                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        assert not killed_on_wake, (
            "freshly-idled within-grace tab was force-killed on a queued peer's "
            "wake — this is the 2026-06-21 incident (L924 must use force=False)"
        )

    @pytest.mark.asyncio
    async def test_wake_with_only_within_grace_idle_returns_false_no_kill(self, router):
        """AC1/AC2 (isolated) — Directly exercise _evict_idle(force=False) with a
        single within-grace idle unit: it must return False and NOT kill.

        This isolates the wake-path semantics from the timeout fallback so the
        protection is asserted without QUEUE_TIMEOUT racing the assertion.
        """
        user_tab = _make_unit("user-32699a22", SessionState.IDLE,
                              idle_seconds=0, rss_bytes=1_500_000_000)
        waker = _make_unit("reconF-e93d0d16", SessionState.COLD, idle_seconds=0)
        router._units = {"user-32699a22": user_tab, "reconF-e93d0d16": waker}

        # force=False is what the fix uses on the wake path.
        evicted = await router._evict_idle(exclude=waker, force=False)

        assert evicted is False, "within-grace tab must not be evictable on wake"
        user_tab.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_timeout_last_resort_still_force_evicts(self, router):
        """AC3 — After QUEUE_TIMEOUT, the L930 last-resort force-evict MUST still
        fire (force=True). This is the sole anti-starvation guarantee and must
        be preserved by the fix.
        """
        user_tab = _make_unit("user-tab", SessionState.IDLE,
                             idle_seconds=0, rss_bytes=1_500_000_000)
        waker = _make_unit("waker", SessionState.COLD, idle_seconds=0)
        router._units = {"user-tab": user_tab, "waker": waker}
        router.QUEUE_TIMEOUT = 0.1

        mock_budget = MagicMock()
        mock_budget.can_spawn = False
        mock_budget.reason = "all_slots_occupied"
        mock_budget.available_mb = 1000
        mock_budget.estimated_cost_mb = 1500
        mock_budget.headroom_mb = 500

        mock_resource = MagicMock()
        mock_resource.compute_max_tabs.return_value = 4
        mock_resource.spawn_budget.return_value = mock_budget
        mock_resource.invalidate_cache = MagicMock()

        with patch("core.resource_monitor.resource_monitor", mock_resource):
            with patch.object(type(router), "_chat_alive_count",
                              new_callable=lambda: property(lambda self: 3)):
                with patch.object(type(router), "alive_count",
                                  new_callable=lambda: property(lambda self: 3)):
                    result = await router._acquire_chat_slot(waker)

        # The timeout last-resort (L930, force=True) must have force-evicted.
        user_tab.kill.assert_called_once()
        assert result in ("ready", "queued")

    @pytest.mark.asyncio
    async def test_waker_makes_progress_at_timeout_not_killed_on_wake(self, router):
        """AC3+AC5 (combined, adversarial-driven) — In the within-grace-churn
        case the waker must NOT kill on wake but MUST still get a slot via the
        force=True last-resort. Proves both 'protected during grace' AND
        'progress at timeout' in one path, and that the bounded re-poll
        (WAKE_REPOLL_SECONDS) does not cause an infinite stall.
        """
        user_tab = _make_unit("user-tab", SessionState.IDLE,
                             idle_seconds=0, rss_bytes=1_500_000_000)
        waker = _make_unit("waker", SessionState.COLD, idle_seconds=0)
        router._units = {"user-tab": user_tab, "waker": waker}
        router.QUEUE_TIMEOUT = 0.3
        router.WAKE_REPOLL_SECONDS = 0.05  # force several re-poll cycles

        mock_budget = MagicMock()
        mock_budget.can_spawn = False
        mock_budget.reason = "all_slots_occupied"
        mock_budget.available_mb = 1000
        mock_budget.estimated_cost_mb = 1500
        mock_budget.headroom_mb = 500

        mock_resource = MagicMock()
        mock_resource.compute_max_tabs.return_value = 4
        mock_resource.spawn_budget.return_value = mock_budget
        mock_resource.invalidate_cache = MagicMock()

        with patch("core.resource_monitor.resource_monitor", mock_resource):
            with patch.object(type(router), "_chat_alive_count",
                              new_callable=lambda: property(lambda self: 3)):
                with patch.object(type(router), "alive_count",
                                  new_callable=lambda: property(lambda self: 3)):
                    # Fire several spurious wakes within grace — each must return
                    # False (no kill) and re-poll, never spinning forever.
                    async def _spurious_wakes():
                        for _ in range(3):
                            await asyncio.sleep(0.04)
                            router._slot_available.set()
                    asyncio.get_event_loop().call_soon(
                        lambda: asyncio.ensure_future(_spurious_wakes())
                    )
                    result = await router._acquire_chat_slot(waker)

        # The waker eventually progressed (timeout last-resort force-evicted).
        assert result in ("ready", "queued")
        # And the kill happened via the last-resort (force=True), proving no
        # infinite stall and that within-grace churn does not block forever.
        user_tab.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_stale_idle_evicted_on_wake_without_timeout(self, router):
        """AC5 — A waker gets a slot promptly when a STALE (>grace) idle exists:
        the wake-path force=False eviction still evicts stale units (they pass
        the grace filter), so no 300s wait is needed. No starvation.
        """
        stale_tab = _make_unit("stale-tab", SessionState.IDLE,
                              idle_seconds=600, rss_bytes=500_000_000)
        waker = _make_unit("waker", SessionState.COLD, idle_seconds=0)
        router._units = {"stale-tab": stale_tab, "waker": waker}

        # Directly assert the wake-path semantics: stale unit IS evictable with
        # force=False (it passes the grace filter).
        evicted = await router._evict_idle(exclude=waker, force=False)

        assert evicted is True, "stale (>grace) idle must be evictable on wake"
        stale_tab.kill.assert_called_once()
