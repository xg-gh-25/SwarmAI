"""Tests for resource management hardening (run_bc707066).

Covers 3 fixes:
- Fix 1: STREAMING sessions killed when RSS > 3GB or system pressure > 85%
- Fix 2: compute_max_tabs logs at DEBUG normally, INFO only on change or low headroom
- Fix 3: Channel session rotation kills old SessionUnit subprocess

Testing methodology: unit tests with mocked resource_monitor and session state.
"""
from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_unit import SessionState, SessionUnit


# ── Helpers ─────────────────────────────────────────────────────────────

def _make_unit(session_id: str = "test-session") -> SessionUnit:
    """Build a SessionUnit in COLD state."""
    return SessionUnit(session_id=session_id, agent_id="default")


def _set_streaming_with_pid(unit: SessionUnit, pid: int = 12345) -> None:
    """Put unit into STREAMING state with a fake PID."""
    unit.state = SessionState.IDLE
    unit._client = MagicMock()
    unit._wrapper = MagicMock()
    unit._wrapper.pid = pid  # SessionUnit.pid property reads _wrapper.pid
    unit._wrapper.process = MagicMock()
    unit._wrapper.process.pid = pid
    # Transition to STREAMING (bypass _transition to avoid PID watchdog)
    unit.state = SessionState.STREAMING
    unit._streaming_start_time = time.time()
    unit._last_event_time = time.time()


# ── Fix 1: STREAMING RSS Kill ───────────────────────────────────────────


class TestStreamingRSSKill:
    """STREAMING sessions with excessive RSS are killed."""

    @pytest.mark.asyncio
    async def test_streaming_rss_above_7gb_triggers_kill(self):
        """AC1: STREAMING session with RSS > 7GB should be killed."""
        from core.lifecycle_manager import LifecycleManager

        unit = _make_unit("streaming-bloat")
        _set_streaming_with_pid(unit, pid=99999)
        unit.kill = AsyncMock()

        router = MagicMock()
        router.list_units.return_value = [unit]

        mgr = LifecycleManager(router=router)

        # Mock resource_monitor imported inside the method
        mock_rm = MagicMock()
        mock_rm.process_tree_rss.return_value = int(7.5 * 1024**3)  # 7.5GB > 7GB
        mock_rm.system_memory.return_value = MagicMock(percent_used=70.0)
        with patch("core.resource_monitor.resource_monitor", mock_rm):
            await mgr._streaming_rss_check()

        unit.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_rss_below_threshold_not_killed(self):
        """STREAMING session with RSS < 7GB should NOT be killed."""
        from core.lifecycle_manager import LifecycleManager

        unit = _make_unit("streaming-ok")
        _set_streaming_with_pid(unit, pid=88888)
        unit.kill = AsyncMock()

        router = MagicMock()
        router.list_units.return_value = [unit]

        mgr = LifecycleManager(router=router)

        mock_rm = MagicMock()
        mock_rm.process_tree_rss.return_value = int(1.5 * 1024**3)
        mock_rm.system_memory.return_value = MagicMock(percent_used=60.0)
        with patch("core.resource_monitor.resource_monitor", mock_rm):
            await mgr._streaming_rss_check()

        unit.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_system_pressure_above_evict_pct_kills_heaviest_streaming(self):
        """AC2: System memory > MEMORY_EVICT_PCT (90%) kills heaviest STREAMING."""
        import core.resource_monitor as rm_mod
        from core.lifecycle_manager import LifecycleManager

        unit1 = _make_unit("small-stream")
        _set_streaming_with_pid(unit1, pid=11111)
        unit1.kill = AsyncMock()

        unit2 = _make_unit("big-stream")
        _set_streaming_with_pid(unit2, pid=22222)
        unit2.kill = AsyncMock()

        router = MagicMock()
        router.list_units.return_value = [unit1, unit2]

        mgr = LifecycleManager(router=router)

        rss_by_pid = {11111: int(1.0 * 1024**3), 22222: int(2.0 * 1024**3)}

        mock_rm = MagicMock()
        mock_rm.process_tree_rss.side_effect = lambda pid: rss_by_pid.get(pid, 0)
        mock_rm.system_memory.return_value = MagicMock(percent_used=92.0)  # > MEMORY_EVICT_PCT (90%)

        # Direct module attribute swap — ensures from-import inside method
        # resolves to our mock regardless of thread context.
        orig = rm_mod.resource_monitor
        rm_mod.resource_monitor = mock_rm
        try:
            await mgr._streaming_rss_check()
        finally:
            rm_mod.resource_monitor = orig

        # Only the heaviest should be killed
        unit2.kill.assert_called_once()
        unit1.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_idle_sessions_not_affected_by_streaming_rss_check(self):
        """Only STREAMING sessions are checked — IDLE has its own mechanism."""
        from core.lifecycle_manager import LifecycleManager

        unit = _make_unit("idle-heavy")
        unit.state = SessionState.IDLE
        unit._wrapper = MagicMock()
        unit._wrapper.process = MagicMock()
        unit._wrapper.process.pid = 77777
        unit.kill = AsyncMock()

        router = MagicMock()
        router.list_units.return_value = [unit]

        mgr = LifecycleManager(router=router)

        mock_rm = MagicMock()
        mock_rm.process_tree_rss.return_value = int(5.0 * 1024**3)
        mock_rm.system_memory.return_value = MagicMock(percent_used=92.0)
        with patch("core.resource_monitor.resource_monitor", mock_rm):
            await mgr._streaming_rss_check()

        unit.kill.assert_not_called()


# ── Fix 2: compute_max_tabs Log Level ───────────────────────────────────


class TestComputeMaxTabsLogLevel:
    """compute_max_tabs uses DEBUG normally, INFO only on change or low headroom."""

    def test_stable_result_logs_at_debug(self, caplog):
        """AC3: Repeated same result logs at DEBUG, not INFO."""
        from core.resource_monitor import ResourceMonitor

        rm = ResourceMonitor()
        # Seed with known state
        rm._last_max_tabs_result = 4

        mock_mem = MagicMock(
            total=36 * 1024**3,
            available=15 * 1024**3,
            used=21 * 1024**3,
            effective_used=21 * 1024**3,
            percent_used=57.0,
            pressure_level="ok",
        )
        rm._cached_memory = mock_mem
        rm._cache_time = time.time()

        with caplog.at_level(logging.DEBUG, logger="core.resource_monitor"):
            result = rm.compute_max_tabs()

        assert result == 4
        # Should be DEBUG, not INFO
        info_msgs = [r for r in caplog.records if r.levelno == logging.INFO
                     and "compute_max_tabs" in r.message]
        assert len(info_msgs) == 0, f"Expected no INFO logs, got: {info_msgs}"

    def test_result_change_logs_at_info(self, caplog):
        """When result changes (e.g., 4→3), log at INFO."""
        from core.resource_monitor import ResourceMonitor

        rm = ResourceMonitor()
        rm._last_max_tabs_result = 4  # Previous was 4

        # Force low headroom → result = 3
        mock_mem = MagicMock(
            total=36 * 1024**3,
            available=2 * 1024**3,
            used=34 * 1024**3,
            effective_used=34 * 1024**3,
            percent_used=94.0,
            pressure_level="warn",
        )
        rm._cached_memory = mock_mem
        rm._cache_time = time.time()

        with caplog.at_level(logging.DEBUG, logger="core.resource_monitor"):
            result = rm.compute_max_tabs()

        # Result should be 2 (min) since headroom is negative
        assert result == 2
        # Should emit INFO because result changed
        info_msgs = [r for r in caplog.records if r.levelno == logging.INFO
                     and "compute_max_tabs" in r.message]
        assert len(info_msgs) >= 1

    def test_low_headroom_logs_at_info(self, caplog):
        """When headroom < 3GB, log at INFO even if result is same."""
        from core.resource_monitor import ResourceMonitor

        rm = ResourceMonitor()
        rm._last_max_tabs_result = 4  # Same result

        # Headroom just barely allows 4 tabs (low headroom scenario)
        # total=36GB, used=30.5GB → headroom_to_90% = 36*0.9 - 30.5 = 1.9GB
        mock_mem = MagicMock(
            total=36 * 1024**3,
            available=5.5 * 1024**3,
            used=int(30.5 * 1024**3),
            effective_used=int(30.5 * 1024**3),
            percent_used=84.7,
            pressure_level="ok",
        )
        rm._cached_memory = mock_mem
        rm._cache_time = time.time()

        with caplog.at_level(logging.DEBUG, logger="core.resource_monitor"):
            result = rm.compute_max_tabs()

        # Headroom = 36*0.9 - 30.5 = 1.9GB < 3GB → INFO
        info_msgs = [r for r in caplog.records if r.levelno == logging.INFO
                     and "compute_max_tabs" in r.message]
        assert len(info_msgs) >= 1


# ── Fix 3: Channel Session Rotation Kills Old Unit ──────────────────────


class TestChannelRotationKillsOldSession:
    """Gateway rotation must kill old SessionUnit on TTL rotation."""

    def _make_router(self):
        """Create a SessionRouter with mocked prompt_builder."""
        from core.session_router import SessionRouter
        return SessionRouter(prompt_builder=MagicMock())

    @pytest.mark.asyncio
    async def test_rotation_kills_old_session_unit(self):
        """AC4: When rotating channel session, old SessionUnit is killed."""
        router = self._make_router()

        # Create old unit and mark as channel session
        old_unit = router.get_or_create_unit("old-session-123", "default")
        old_unit.is_channel_session = True
        old_unit.state = SessionState.IDLE
        old_unit._client = MagicMock()
        old_unit._wrapper = MagicMock()
        old_unit._wrapper.process = MagicMock()
        old_unit._wrapper.process.pid = 55555
        old_unit.kill = AsyncMock()

        # Simulate what gateway should do on rotation
        await router.kill_rotated_channel_session("old-session-123")

        old_unit.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_rotation_kill_handles_missing_session(self):
        """Kill rotated session gracefully handles non-existent session_id."""
        router = self._make_router()

        # Should not raise
        await router.kill_rotated_channel_session("nonexistent-session")

    @pytest.mark.asyncio
    async def test_rotation_kill_handles_already_cold_session(self):
        """Kill rotated session is no-op for already COLD session."""
        router = self._make_router()

        unit = router.get_or_create_unit("cold-session", "default")
        unit.is_channel_session = True
        # Already COLD — no subprocess
        assert unit.state == SessionState.COLD
        unit.kill = AsyncMock()

        await router.kill_rotated_channel_session("cold-session")

        # kill() not needed for COLD (no subprocess)
        unit.kill.assert_not_called()
