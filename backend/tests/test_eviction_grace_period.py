"""Tests for eviction grace period in SessionRouter.

Verifies that _evict_idle respects the EVICTION_GRACE_SECONDS threshold:
- Sessions idle < grace period are NOT evicted (protected)
- Sessions idle >= grace period ARE evicted (normal behavior)
- force=True bypasses grace period (queue timeout fallback)
- Channel eviction is unaffected by grace period
- Protected states (STREAMING, WAITING_INPUT) remain immune regardless
"""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_router import SessionRouter
from core.session_unit import SessionState


@pytest.fixture(autouse=True)
def _owned_session_ids():
    """Pin window-ownership to a deterministic empty set across platforms.

    ``_evict_idle`` late-imports ``routers.settings.owned_session_ids`` to apply
    the R6 §9.9 orphan-only filter (a window-owned chat tab is never evicted).
    Its source of truth is ``open_tabs.json``: present on a dev macOS box (so
    the real call returns live IDs and the synthetic test units look like
    orphans → tests pass), but ABSENT on CI → the fail-safe returns ``None`` →
    eviction refuses → the eviction-expecting tests fail. That divergence is an
    environment leak, not a product bug (COE01 class). Pinning to ``set()`` =
    "a window is connected and reports zero open tabs", so every synthetic unit
    is an orphan and eviction logic is exercised deterministically. Tests that
    don't reach the filter (channel_only, protected-state, all-fresh) are
    unaffected.
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


def _make_unit(session_id: str, state: SessionState, idle_seconds: float,
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
    # Metrics for eviction priority
    metrics = MagicMock()
    metrics.rss_bytes = rss_bytes
    unit._last_metrics = metrics
    return unit


class TestEvictionGracePeriod:
    """Grace period prevents eviction of recently-active sessions."""

    @pytest.mark.asyncio
    async def test_idle_below_grace_period_not_evicted(self, router):
        """Session idle 19s should NOT be evicted (grace=300s)."""
        # Session idle for only 19 seconds — too fresh to evict
        unit_fresh = _make_unit("fresh-tab", SessionState.IDLE, idle_seconds=19)
        requesting = _make_unit("requesting", SessionState.COLD, idle_seconds=0)

        router._units = {"fresh-tab": unit_fresh, "requesting": requesting}

        result = await router._evict_idle(exclude=requesting)
        assert result is False
        unit_fresh.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_idle_above_grace_period_evicted(self, router):
        """Session idle 600s should be evicted (grace=300s)."""
        unit_stale = _make_unit("stale-tab", SessionState.IDLE, idle_seconds=600)
        requesting = _make_unit("requesting", SessionState.COLD, idle_seconds=0)

        router._units = {"stale-tab": unit_stale, "requesting": requesting}

        result = await router._evict_idle(exclude=requesting)
        assert result is True
        unit_stale.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_force_bypasses_grace_period(self, router):
        """force=True should evict even if idle < grace period."""
        unit_fresh = _make_unit("fresh-tab", SessionState.IDLE, idle_seconds=19)
        requesting = _make_unit("requesting", SessionState.COLD, idle_seconds=0)

        router._units = {"fresh-tab": unit_fresh, "requesting": requesting}

        result = await router._evict_idle(exclude=requesting, force=True)
        assert result is True
        unit_fresh.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_mixed_idle_only_stale_evicted(self, router):
        """With mixed idle times, only stale sessions are candidates."""
        unit_fresh = _make_unit("fresh", SessionState.IDLE, idle_seconds=30)
        unit_stale = _make_unit("stale", SessionState.IDLE, idle_seconds=400)
        requesting = _make_unit("requesting", SessionState.COLD, idle_seconds=0)

        router._units = {
            "fresh": unit_fresh,
            "stale": unit_stale,
            "requesting": requesting,
        }

        result = await router._evict_idle(exclude=requesting)
        assert result is True
        # Stale one evicted, fresh one untouched
        unit_stale.kill.assert_called_once()
        unit_fresh.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_protected_states_never_evicted(self, router):
        """STREAMING and WAITING_INPUT are never evicted regardless of idle time."""
        unit_streaming = _make_unit("streaming", SessionState.STREAMING, idle_seconds=999)
        unit_waiting = _make_unit("waiting", SessionState.WAITING_INPUT, idle_seconds=999)
        requesting = _make_unit("requesting", SessionState.COLD, idle_seconds=0)

        router._units = {
            "streaming": unit_streaming,
            "waiting": unit_waiting,
            "requesting": requesting,
        }

        result = await router._evict_idle(exclude=requesting, force=True)
        assert result is False
        unit_streaming.kill.assert_not_called()
        unit_waiting.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_channel_eviction_unaffected_by_grace(self, router):
        """Channel eviction (channel_only=True) ignores grace period."""
        unit_channel = _make_unit("channel", SessionState.IDLE, idle_seconds=5,
                                  is_channel=True)
        requesting = _make_unit("requesting", SessionState.COLD, idle_seconds=0,
                                is_channel=True)

        router._units = {"channel": unit_channel, "requesting": requesting}

        # Channel eviction should work regardless of idle duration
        result = await router._evict_idle(exclude=requesting, channel_only=True)
        assert result is True
        unit_channel.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_grace_period_constant_value(self, router):
        """EVICTION_GRACE_SECONDS should be 300 (5 minutes)."""
        assert router.EVICTION_GRACE_SECONDS == 300

    @pytest.mark.asyncio
    async def test_all_fresh_no_force_returns_false(self, router):
        """When all sessions are fresh and force=False, nothing is evicted."""
        units = {
            f"tab-{i}": _make_unit(f"tab-{i}", SessionState.IDLE, idle_seconds=60)
            for i in range(3)
        }
        requesting = _make_unit("requesting", SessionState.COLD, idle_seconds=0)
        units["requesting"] = requesting
        router._units = units

        result = await router._evict_idle(exclude=requesting)
        assert result is False
        for uid, u in units.items():
            if uid != "requesting":
                u.kill.assert_not_called()
