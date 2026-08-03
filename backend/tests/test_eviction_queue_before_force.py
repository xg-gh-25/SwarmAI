"""Tests for queue-before-force eviction in _acquire_chat_slot.

When spawn_budget is denied and grace period blocks eviction, the router
should QUEUE the request (wait up to 60s for a slot to free) instead of
immediately escalating to force=True. Force eviction is a last resort
after the queue timeout expires.

Evidence: session e006f3d5 killed after 0.7s idle because spawn budget tight
and force=True bypassed grace immediately. 28 exit-9 kills in 24h.

Fix: Insert queue wait between graceful eviction attempt and force eviction.
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
    """Create a SessionRouter with mocked dependencies."""
    prompt_builder = MagicMock()
    config = MagicMock()
    r = SessionRouter(prompt_builder=prompt_builder, config=config)
    return r


def _make_unit(session_id: str, state: SessionState, idle_seconds: float = 0,
               is_channel: bool = False, rss_bytes: int = 500_000_000):
    """Create a mock SessionUnit with controlled state and timing."""
    unit = MagicMock()
    unit.session_id = session_id
    unit.state = state
    unit.is_channel_session = is_channel
    unit.last_used = time.time() - idle_seconds
    unit.is_alive = state in (SessionState.STREAMING, SessionState.IDLE,
                              SessionState.WAITING_INPUT)
    unit._hooks_enqueued = False
    unit.kill = AsyncMock()
    # Post-disconnect flush guard — real SessionUnit exposes this as a property
    # returning bool; MagicMock would return a truthy Mock and be wrongly excluded
    # from eviction candidates. Default False = evictable. Root-1 Phase 2 (L6):
    # eviction now reads is_post_disconnect_flushing (replaces the deleted
    # is_generating_after_disconnect guard).
    unit.is_post_disconnect_flushing = False
    metrics = MagicMock()
    metrics.rss_bytes = rss_bytes
    unit._last_metrics = metrics
    return unit


class TestQueueBeforeForceEviction:
    """When budget denies spawn and grace blocks eviction, queue first."""

    @pytest.mark.asyncio
    async def test_budget_denied_grace_blocks_queues_instead_of_force(self, router):
        """Budget denied + all sessions within grace → queue, not force-kill.

        This is the core fix: previously the code did:
          1. _evict_idle(force=False) → blocked by grace
          2. _evict_idle(force=True) → kills immediately!

        New behavior:
          1. _evict_idle(force=False) → blocked by grace
          2. Queue with timeout (wait for slot to free naturally)
          3. Only force-evict AFTER queue timeout expires
        """
        # Setup: 3 chat slots occupied by fresh sessions (all within grace)
        fresh_unit = _make_unit("fresh-tab", SessionState.IDLE, idle_seconds=30)
        requesting = _make_unit("requesting", SessionState.COLD, idle_seconds=0)

        router._units = {"fresh-tab": fresh_unit, "requesting": requesting}

        # Mock resource_monitor to deny spawn budget
        mock_budget = MagicMock()
        mock_budget.can_spawn = False
        mock_budget.reason = "memory_pressure"
        mock_budget.available_mb = 1000
        mock_budget.estimated_cost_mb = 1500
        mock_budget.headroom_mb = 500

        mock_resource = MagicMock()
        mock_resource.compute_max_tabs.return_value = 4  # max_tabs=4, chat_max=3
        mock_resource.spawn_budget.return_value = mock_budget
        mock_resource.invalidate_cache = MagicMock()

        # Simulate: slot becomes available after 0.1s (another session goes COLD)
        async def _simulate_slot_free():
            await asyncio.sleep(0.05)
            # Budget becomes available
            mock_budget.can_spawn = True
            router._slot_available.set()

        with patch("core.resource_monitor.resource_monitor", mock_resource):
            # Make chat_alive_count < chat_max so we enter the budget-check path
            with patch.object(
                type(router), "_chat_alive_count",
                new_callable=lambda: property(lambda self: 1)
            ):
                # Also need alive_count > 0 to trigger budget check
                with patch.object(
                    type(router), "alive_count",
                    new_callable=lambda: property(lambda self: 1)
                ):
                    # Start the slot-free simulation
                    asyncio.get_event_loop().call_soon(
                        lambda: asyncio.ensure_future(_simulate_slot_free())
                    )

                    result = await router._acquire_chat_slot(requesting)

            # The fresh session should NOT have been force-killed
            fresh_unit.kill.assert_not_called()
            # Should have gotten a slot via queue (budget became available)
            assert result in ("ready", "queued")

    @pytest.mark.asyncio
    async def test_budget_denied_force_eviction_after_queue_timeout(self, router):
        """After queue timeout, force eviction IS used as last resort.

        The force path must still exist — it's the safety net when no slot
        frees within the timeout. But it only fires AFTER waiting.
        """
        fresh_unit = _make_unit("fresh-tab", SessionState.IDLE, idle_seconds=30)
        requesting = _make_unit("requesting", SessionState.COLD, idle_seconds=0)

        router._units = {"fresh-tab": fresh_unit, "requesting": requesting}

        # Mock resource_monitor to always deny spawn budget
        mock_budget = MagicMock()
        mock_budget.can_spawn = False
        mock_budget.reason = "memory_pressure"
        mock_budget.available_mb = 1000
        mock_budget.estimated_cost_mb = 1500
        mock_budget.headroom_mb = 500

        mock_resource = MagicMock()
        mock_resource.compute_max_tabs.return_value = 4
        mock_resource.spawn_budget.return_value = mock_budget
        mock_resource.invalidate_cache = MagicMock()

        # Use a very short timeout for test speed
        router.QUEUE_TIMEOUT = 0.1  # 100ms instead of 60s

        with patch("core.resource_monitor.resource_monitor", mock_resource):
            with patch.object(
                type(router), "_chat_alive_count",
                new_callable=lambda: property(lambda self: 1)
            ):
                with patch.object(
                    type(router), "alive_count",
                    new_callable=lambda: property(lambda self: 1)
                ):
                    result = await router._acquire_chat_slot(requesting)

            # After timeout, force eviction should have been used
            fresh_unit.kill.assert_called_once()
            assert result in ("ready", "queued")

    @pytest.mark.asyncio
    async def test_stale_sessions_still_evicted_immediately(self, router):
        """Sessions idle > grace period are still evicted without queuing.

        Regression guard: the fix only changes behavior for FRESH sessions.
        Stale sessions (idle > 300s) should be evicted immediately as before.
        """
        stale_unit = _make_unit("stale-tab", SessionState.IDLE, idle_seconds=600)
        requesting = _make_unit("requesting", SessionState.COLD, idle_seconds=0)

        router._units = {"stale-tab": stale_unit, "requesting": requesting}

        # Budget denied but stale session available
        mock_budget_denied = MagicMock()
        mock_budget_denied.can_spawn = False
        mock_budget_denied.reason = "memory_pressure"
        mock_budget_denied.available_mb = 1000
        mock_budget_denied.estimated_cost_mb = 1500
        mock_budget_denied.headroom_mb = 500

        mock_budget_ok = MagicMock()
        mock_budget_ok.can_spawn = True

        mock_resource = MagicMock()
        mock_resource.compute_max_tabs.return_value = 4
        # First call: denied. After eviction + invalidate: allowed.
        mock_resource.spawn_budget.side_effect = [mock_budget_denied, mock_budget_ok]
        mock_resource.invalidate_cache = MagicMock()

        with patch("core.resource_monitor.resource_monitor", mock_resource):
            with patch.object(
                type(router), "_chat_alive_count",
                new_callable=lambda: property(lambda self: 1)
            ):
                with patch.object(
                    type(router), "alive_count",
                    new_callable=lambda: property(lambda self: 1)
                ):
                    result = await router._acquire_chat_slot(requesting)

            # Stale session evicted immediately (no queuing needed)
            stale_unit.kill.assert_called_once()
            assert result == "ready"
