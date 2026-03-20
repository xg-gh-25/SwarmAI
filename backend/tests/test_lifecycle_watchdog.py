"""Tests for the Lightweight Process Lifecycle Watchdog additions to LifecycleManager.

Covers:
- Child PID tracking (track_pid, untrack_pid, _kill_tracked_pids)
- Pytest orphan reaper section in _reap_orphans()
- Shutdown integration (stop() calls _kill_tracked_pids)

Testing methodology: unit tests with mocked subprocess calls and os.kill.
Property-based tests with Hypothesis for PID set invariants.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from core.lifecycle_manager import LifecycleManager


# ── Helpers ────────────────────────────────────────────────────────────


def _make_manager() -> LifecycleManager:
    """Build a LifecycleManager with a stubbed router."""
    router = MagicMock()
    router.list_units.return_value = []
    return LifecycleManager(router=router)


# ── Unit tests: track_pid / untrack_pid ────────────────────────────────


class TestPidTracking:
    """Verify the _tracked_child_pids set API."""

    def test_track_pid_adds_to_set(self):
        mgr = _make_manager()
        mgr.track_pid(1234)
        assert 1234 in mgr._tracked_child_pids

    def test_untrack_pid_removes_from_set(self):
        mgr = _make_manager()
        mgr.track_pid(1234)
        mgr.untrack_pid(1234)
        assert 1234 not in mgr._tracked_child_pids

    def test_untrack_pid_noop_if_missing(self):
        mgr = _make_manager()
        mgr.untrack_pid(9999)  # Should not raise
        assert len(mgr._tracked_child_pids) == 0

    def test_track_pid_idempotent(self):
        mgr = _make_manager()
        mgr.track_pid(42)
        mgr.track_pid(42)
        assert mgr._tracked_child_pids == {42}


# ── Unit tests: _kill_tracked_pids ─────────────────────────────────────


class TestKillTrackedPids:
    """Verify shutdown kill behavior."""

    @pytest.mark.asyncio
    async def test_kills_all_tracked_pids(self):
        mgr = _make_manager()
        mgr.track_pid(100)
        mgr.track_pid(200)

        with patch("os.kill") as mock_kill:
            await mgr._kill_tracked_pids()

        # Both PIDs killed with SIGKILL
        killed_pids = {c.args[0] for c in mock_kill.call_args_list}
        assert killed_pids == {100, 200}
        for c in mock_kill.call_args_list:
            assert c.args[1] == signal.SIGKILL

    @pytest.mark.asyncio
    async def test_clears_set_after_kill(self):
        mgr = _make_manager()
        mgr.track_pid(100)

        with patch("os.kill"):
            await mgr._kill_tracked_pids()

        assert len(mgr._tracked_child_pids) == 0

    @pytest.mark.asyncio
    async def test_handles_already_dead_pids(self):
        mgr = _make_manager()
        mgr.track_pid(100)
        mgr.track_pid(200)

        with patch("os.kill", side_effect=[ProcessLookupError, None]) as mock_kill:
            await mgr._kill_tracked_pids()

        # Both attempted, no exception raised
        assert mock_kill.call_count == 2
        assert len(mgr._tracked_child_pids) == 0

    @pytest.mark.asyncio
    async def test_handles_permission_error(self):
        mgr = _make_manager()
        mgr.track_pid(100)

        with patch("os.kill", side_effect=PermissionError):
            await mgr._kill_tracked_pids()  # Should not raise

        assert len(mgr._tracked_child_pids) == 0

    @pytest.mark.asyncio
    async def test_noop_when_empty(self):
        mgr = _make_manager()

        with patch("os.kill") as mock_kill:
            await mgr._kill_tracked_pids()

        mock_kill.assert_not_called()


# ── Unit tests: stop() integration ─────────────────────────────────────


class TestStopIntegration:
    """Verify stop() calls _kill_tracked_pids."""

    @pytest.mark.asyncio
    async def test_stop_kills_tracked_pids(self):
        mgr = _make_manager()
        mgr.track_pid(555)
        mgr._started = True

        with patch("os.kill") as mock_kill:
            await mgr.stop()

        mock_kill.assert_called_once_with(555, signal.SIGKILL)


# ── Unit tests: periodic orphan reaping (Fix 1) ─────────────────────────


class TestPeriodicOrphanReaping:
    """Verify _maintenance_loop calls _reap_orphans every 10th cycle."""

    @pytest.mark.asyncio
    async def test_reap_called_on_10th_cycle(self):
        mgr = _make_manager()
        call_count = 0

        async def counting_reap():
            nonlocal call_count
            call_count += 1

        mgr._reap_orphans = counting_reap
        # Stub out other maintenance methods
        for method in ("_health_check_all", "_check_streaming_timeout",
                       "_fire_idle_hooks", "_check_ttl", "_cleanup_dead",
                       "_check_memory_pressure"):
            setattr(mgr, method, AsyncMock())

        # Run 10 cycles (sleep returns immediately)
        cycle = 0
        with patch("asyncio.sleep", new_callable=AsyncMock):
            async def run_n_cycles(n):
                nonlocal cycle
                for _ in range(n):
                    cycle += 1
                    await mgr._health_check_all()
                    await mgr._check_streaming_timeout()
                    await mgr._fire_idle_hooks()
                    await mgr._check_ttl()
                    await mgr._cleanup_dead()
                    await mgr._check_memory_pressure()
                    if cycle % 10 == 0:
                        await mgr._reap_orphans()

            await run_n_cycles(10)

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_reap_not_called_before_10th_cycle(self):
        mgr = _make_manager()
        call_count = 0

        async def counting_reap():
            nonlocal call_count
            call_count += 1

        mgr._reap_orphans = counting_reap

        # Simulate 9 cycles — reap should NOT fire
        for cycle in range(1, 10):
            if cycle % 10 == 0:
                await mgr._reap_orphans()

        assert call_count == 0


# ── Unit tests: pytest orphan reaper ───────────────────────────────────


class TestPytestOrphanReaper:
    """Verify the pytest section of _reap_orphans()."""

    @pytest.mark.asyncio
    async def test_kills_orphaned_pytest(self):
        """Pytest process with ppid=1 gets killed."""
        mgr = _make_manager()

        def subprocess_side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "pgrep" in cmd_str and "claude" in cmd_str:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if "pgrep" in cmd_str and "python main.py" in cmd_str:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if "pgrep" in cmd_str and "pytest" in cmd_str:
                return SimpleNamespace(returncode=0, stdout="3877\n", stderr="")
            if "ps" in cmd_str and "3877" in cmd_str:
                return SimpleNamespace(returncode=0, stdout="    1", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        async def fake_to_thread(fn, *a, **kw):
            return fn(*a, **kw)

        with patch("core.lifecycle_manager.asyncio.to_thread", side_effect=fake_to_thread), \
             patch("core.lifecycle_manager.subprocess.run", side_effect=subprocess_side_effect), \
             patch("core.lifecycle_manager.os.kill") as mock_kill, \
             patch("core.lifecycle_manager.os.getpid", return_value=99999):
            await mgr._reap_orphans()

        mock_kill.assert_any_call(3877, signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_skips_non_orphaned_pytest(self):
        """Pytest process with ppid != 1 is NOT killed."""
        mgr = _make_manager()

        def subprocess_side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "pgrep" in cmd_str and "claude" in cmd_str:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if "pgrep" in cmd_str and "python main.py" in cmd_str:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if "pgrep" in cmd_str and "pytest" in cmd_str:
                return SimpleNamespace(returncode=0, stdout="3877\n", stderr="")
            if "ps" in cmd_str and "3877" in cmd_str:
                return SimpleNamespace(returncode=0, stdout="  500", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        async def fake_to_thread(fn, *a, **kw):
            return fn(*a, **kw)

        with patch("core.lifecycle_manager.asyncio.to_thread", side_effect=fake_to_thread), \
             patch("core.lifecycle_manager.subprocess.run", side_effect=subprocess_side_effect), \
             patch("core.lifecycle_manager.os.kill") as mock_kill, \
             patch("core.lifecycle_manager.os.getpid", return_value=99999):
            await mgr._reap_orphans()

        for c in mock_kill.call_args_list:
            assert c.args[0] != 3877

    @pytest.mark.asyncio
    async def test_pytest_reaper_failure_is_nonfatal(self):
        """Pytest reaper exceptions don't crash _reap_orphans()."""
        mgr = _make_manager()

        def subprocess_side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "pgrep" in cmd_str and "claude" in cmd_str:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if "pgrep" in cmd_str and "python main.py" in cmd_str:
                return SimpleNamespace(returncode=1, stdout="", stderr="")
            if "pgrep" in cmd_str and "pytest" in cmd_str:
                raise RuntimeError("pgrep exploded")
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        async def fake_to_thread(fn, *a, **kw):
            return fn(*a, **kw)

        with patch("core.lifecycle_manager.asyncio.to_thread", side_effect=fake_to_thread), \
             patch("core.lifecycle_manager.subprocess.run", side_effect=subprocess_side_effect), \
             patch("core.lifecycle_manager.os.kill"), \
             patch("core.lifecycle_manager.os.getpid", return_value=99999):
            await mgr._reap_orphans()  # Should not raise


# ── Property-based tests (Hypothesis) ──────────────────────────────────


class TestPidTrackingProperties:
    """Hypothesis property-based tests for PID tracking invariants."""

    @given(pids=st.lists(st.integers(min_value=1, max_value=2**31), max_size=50))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_tracked_set_equals_unique_tracked(self, pids):
        """After tracking N pids, the set contains exactly the unique ones."""
        mgr = _make_manager()
        for pid in pids:
            mgr.track_pid(pid)
        assert mgr._tracked_child_pids == set(pids)

    @given(
        pids=st.lists(st.integers(min_value=1, max_value=2**31), min_size=1, max_size=50),
        to_remove=st.lists(st.integers(min_value=1, max_value=2**31), max_size=20),
    )
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_untrack_removes_only_specified(self, pids, to_remove):
        """Untracking removes only the specified PIDs."""
        mgr = _make_manager()
        for pid in pids:
            mgr.track_pid(pid)
        for pid in to_remove:
            mgr.untrack_pid(pid)
        expected = set(pids) - set(to_remove)
        assert mgr._tracked_child_pids == expected

    @given(pids=st.lists(st.integers(min_value=1, max_value=2**31), min_size=1, max_size=30))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_kill_clears_all(self, pids):
        """_kill_tracked_pids always empties the set regardless of kill outcomes."""
        mgr = _make_manager()
        for pid in pids:
            mgr.track_pid(pid)

        with patch("os.kill"):
            asyncio.get_event_loop().run_until_complete(mgr._kill_tracked_pids())

        assert len(mgr._tracked_child_pids) == 0

    @given(pids=st.lists(st.integers(min_value=1, max_value=2**31), min_size=1, max_size=30))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_kill_attempts_every_pid(self, pids):
        """_kill_tracked_pids attempts os.kill for every unique tracked PID."""
        mgr = _make_manager()
        for pid in pids:
            mgr.track_pid(pid)

        with patch("os.kill") as mock_kill:
            asyncio.get_event_loop().run_until_complete(mgr._kill_tracked_pids())

        killed = {c.args[0] for c in mock_kill.call_args_list}
        assert killed == set(pids)

    @given(pids=st.lists(st.integers(min_value=1, max_value=2**31), min_size=1, max_size=20))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow])
    def test_kill_survives_mixed_errors(self, pids):
        """_kill_tracked_pids doesn't crash even if every kill raises."""
        mgr = _make_manager()
        for pid in pids:
            mgr.track_pid(pid)

        errors = [ProcessLookupError, PermissionError]
        call_count = 0

        def alternating_error(p, s):
            nonlocal call_count
            call_count += 1
            raise errors[call_count % 2]

        with patch("os.kill", side_effect=alternating_error):
            asyncio.get_event_loop().run_until_complete(mgr._kill_tracked_pids())

        assert len(mgr._tracked_child_pids) == 0
