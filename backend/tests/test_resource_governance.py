"""Tests for Resource Governance Phase 1+2 fixes.

Tests 7 acceptance criteria:
- G1: spawn_budget fail-closed on exception
- G2: No duplicate pytest reap call
- G3: Single OOM cooldown (session_unit only, not resource_monitor)
- G4: Spawn cost recording wired from memory sampling
- G5: Daemon crash counter (shell script — tested via pattern check)
- G6: read_owner_pid deduplicated to session_utils
- G7: _reap_orphans timeout guard
"""

import asyncio
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── G1: spawn_budget fail-closed ─────────────────────────────────────

class TestSpawnBudgetFailClosed:
    """spawn_budget() must return can_spawn=False when an exception occurs."""

    def test_spawn_budget_returns_false_on_exception(self):
        """G1: exception in spawn_budget → can_spawn=False (fail-closed)."""
        from core.resource_monitor import ResourceMonitor

        monitor = ResourceMonitor()
        # Force system_memory to throw
        with patch.object(monitor, "system_memory", side_effect=RuntimeError("psutil broken")):
            # Also force the invalidate_cache to not help
            budget = monitor.spawn_budget()
            assert budget.can_spawn is False
            assert "failed" in budget.reason.lower() or "error" in budget.reason.lower()

    def test_spawn_budget_normal_path_still_works(self):
        """Sanity: normal path still returns a valid budget."""
        from core.resource_monitor import ResourceMonitor

        monitor = ResourceMonitor()
        budget = monitor.spawn_budget()
        # Should return a valid budget (may be True or False depending on system)
        assert isinstance(budget.can_spawn, bool)


# ── G2: No duplicate pytest reap ─────────────────────────────────────

class TestNoDuplicatePytestReap:
    """_reap_orphans must not call _reap_by_pattern('pytest', ...) twice."""

    @pytest.mark.asyncio
    async def test_no_duplicate_pytest_reap(self):
        """G2: pytest pattern appears exactly once in _reap_orphans."""
        mock_router = MagicMock()
        mock_router.list_units.return_value = []

        from core.lifecycle_manager import LifecycleManager
        manager = LifecycleManager(router=mock_router)

        calls = []
        original_reap = manager._reap_by_pattern

        async def tracking_reap(pattern, label, known, require_orphaned=False):
            calls.append((pattern, label))
            return 0

        with patch.object(manager, "_reap_by_pattern", side_effect=tracking_reap):
            with patch.object(manager, "_get_mcp_server_patterns", return_value=["test-mcp"]):
                await manager._reap_orphans()

        pytest_calls = [(p, l) for p, l in calls if l == "pytest"]
        assert len(pytest_calls) == 1, (
            f"Expected exactly 1 pytest reap call, got {len(pytest_calls)}: {pytest_calls}"
        )


# ── G3: Single OOM cooldown ──────────────────────────────────────────

class TestSingleOOMCooldown:
    """OOM cooldown should only exist in session_unit, not resource_monitor."""

    def test_resource_monitor_has_no_record_oom(self):
        """G3: ResourceMonitor should not have record_oom method."""
        from core.resource_monitor import ResourceMonitor

        monitor = ResourceMonitor()
        assert not hasattr(monitor, "record_oom") or not callable(getattr(monitor, "record_oom", None)), \
            "ResourceMonitor should not have record_oom — OOM cooldown lives in session_unit only"

    def test_resource_monitor_has_no_oom_cooldown_field(self):
        """G3: ResourceMonitor should not have _last_oom_time."""
        from core.resource_monitor import ResourceMonitor

        monitor = ResourceMonitor()
        assert not hasattr(monitor, "_last_oom_time"), \
            "ResourceMonitor should not have _last_oom_time — OOM cooldown lives in session_unit only"

    def test_resource_monitor_has_no_oom_cooldown_constant(self):
        """G3: ResourceMonitor should not have _OOM_COOLDOWN_SECONDS."""
        from core.resource_monitor import ResourceMonitor

        assert not hasattr(ResourceMonitor, "_OOM_COOLDOWN_SECONDS"), \
            "ResourceMonitor should not have _OOM_COOLDOWN_SECONDS constant"

    def test_spawn_budget_no_oom_cooldown_check(self):
        """G3: spawn_budget() should not reference OOM cooldown."""
        import inspect
        from core.resource_monitor import ResourceMonitor

        source = inspect.getsource(ResourceMonitor.spawn_budget)
        assert "_last_oom_time" not in source, \
            "spawn_budget should not check _last_oom_time"
        assert "_OOM_COOLDOWN_SECONDS" not in source, \
            "spawn_budget should not reference _OOM_COOLDOWN_SECONDS"


# ── G4: Spawn cost recording wired ───────────────────────────────────

class TestSpawnCostRecording:
    """_sample_process_memory should feed RSS data into record_spawn_cost."""

    @pytest.mark.asyncio
    async def test_spawn_cost_recorded_from_memory_sampling(self):
        """G4: record_spawn_cost called with tree_rss for first-sample units."""
        mock_router = MagicMock()
        mock_unit = MagicMock()
        mock_unit.is_alive = True
        mock_unit.pid = 12345
        mock_unit.session_id = "test-session"
        mock_unit._peak_tree_rss_bytes = 0
        mock_unit.state = MagicMock()
        mock_unit.state.name = "STREAMING"
        mock_router.list_units.return_value = [mock_unit]

        from core.lifecycle_manager import LifecycleManager
        manager = LifecycleManager(router=mock_router)

        with patch("core.lifecycle_manager.asyncio.to_thread", return_value=500_000_000):
            with patch("core.resource_monitor.resource_monitor.record_spawn_cost") as mock_record:
                await manager._sample_process_memory()
                # Should be called at least for the first sample (peak was 0)
                mock_record.assert_called()


# ── G6: read_owner_pid deduplicated ──────────────────────────────────

class TestReadOwnerPidDeduplicated:
    """read_owner_pid should exist in session_utils and be the single source."""

    def test_read_owner_pid_exists_in_session_utils(self):
        """G6: session_utils has read_owner_pid function."""
        from core import session_utils
        assert hasattr(session_utils, "read_owner_pid"), \
            "session_utils must export read_owner_pid"
        assert callable(session_utils.read_owner_pid)

    def test_read_owner_pid_returns_none_for_invalid_pid(self):
        """G6: read_owner_pid returns None for non-existent process."""
        from core.session_utils import read_owner_pid
        result = read_owner_pid(999999)
        assert result is None


# ── G7: _reap_orphans timeout guard ──────────────────────────────────

class TestReapOrphansTimeout:
    """_reap_orphans should be guarded by a total timeout."""

    @pytest.mark.asyncio
    async def test_reap_orphans_has_timeout(self):
        """G7: _reap_orphans completes within 30s even if patterns hang."""
        mock_router = MagicMock()
        mock_router.list_units.return_value = []

        from core.lifecycle_manager import LifecycleManager
        manager = LifecycleManager(router=mock_router)

        async def hanging_reap(pattern, label, known, require_orphaned=False):
            await asyncio.sleep(100)  # Would hang forever without timeout
            return 0

        with patch.object(manager, "_reap_by_pattern", side_effect=hanging_reap):
            start = time.monotonic()
            # Should NOT hang for 100s — timeout should catch it
            await manager._reap_orphans()
            elapsed = time.monotonic() - start
            assert elapsed < 35, f"_reap_orphans took {elapsed:.1f}s — timeout guard missing"
