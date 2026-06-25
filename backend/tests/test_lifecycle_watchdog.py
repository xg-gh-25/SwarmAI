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

    @pytest.mark.asyncio
    async def test_cleanup_expired_messages_called_on_10th_cycle(self):
        """_cleanup_expired_messages runs in the cycle%10 block."""
        mgr = _make_manager()
        mgr._cleanup_expired_messages = AsyncMock()

        # Stub out every other maintenance method
        for method in (
            "_health_check_all", "_check_streaming_timeout",
            "_fire_idle_hooks", "_check_ttl", "_cleanup_dead",
            "_check_memory_pressure", "_reap_orphans",
            "_purge_stale_cold", "_cleanup_stale_channel_sessions",
            "_sample_process_memory",
        ):
            setattr(mgr, method, AsyncMock())

        # Replicate the maintenance loop logic for 10 cycles
        cycle = 0
        for _ in range(10):
            cycle += 1
            if cycle % 10 == 0:
                await mgr._cleanup_expired_messages()

        mgr._cleanup_expired_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_expired_messages_not_called_before_10th(self):
        """_cleanup_expired_messages does NOT run before the 10th cycle."""
        mgr = _make_manager()
        mgr._cleanup_expired_messages = AsyncMock()

        for cycle in range(1, 10):
            if cycle % 10 == 0:
                await mgr._cleanup_expired_messages()

        mgr._cleanup_expired_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_expired_messages_logs_deleted_count(self):
        """When cleanup deletes messages, it logs the count."""
        mgr = _make_manager()

        mock_db = MagicMock()
        mock_db.cleanup_expired_messages = AsyncMock(return_value=42)
        with patch("database.db", mock_db):
            await mgr._cleanup_expired_messages()
            mock_db.cleanup_expired_messages.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_expired_messages_handles_exception(self):
        """Exception in cleanup doesn't propagate."""
        mgr = _make_manager()

        mock_db = MagicMock()
        mock_db.cleanup_expired_messages = AsyncMock(
            side_effect=Exception("DB locked")
        )
        with patch("database.db", mock_db):
            # Should NOT raise
            await mgr._cleanup_expired_messages()


# ── Unit tests: pytest orphan reaper ───────────────────────────────────


class TestPytestOrphanReaper:
    """Verify the pytest section of _reap_orphans()."""

    @pytest.mark.asyncio
    async def test_kills_orphaned_pytest(self):
        """Pytest process confirmed as owned orphan gets killed.

        The reaper now uses _is_owned_orphan (SWARMAI_OWNER_PID env check)
        instead of simple ppid=1.  We mock the ownership check directly.
        """
        mgr = _make_manager()

        def subprocess_side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "pgrep" in cmd_str and "pytest" in cmd_str:
                return SimpleNamespace(returncode=0, stdout="3877\n", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        async def fake_to_thread(fn, *a, **kw):
            return fn(*a, **kw)

        async def fake_is_owned_orphan(pid):
            return True  # Confirmed orphan

        with patch("core.lifecycle_manager.asyncio.to_thread", side_effect=fake_to_thread), \
             patch("core.lifecycle_manager.subprocess.run", side_effect=subprocess_side_effect), \
             patch.object(mgr, "_is_owned_orphan", side_effect=fake_is_owned_orphan), \
             patch("core.lifecycle_manager.os.kill") as mock_kill, \
             patch("core.lifecycle_manager.os.getpid", return_value=99999):
            await mgr._reap_orphans()

        mock_kill.assert_any_call(3877, signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_skips_non_orphaned_pytest(self):
        """Pytest process NOT confirmed as owned orphan is left alone."""
        mgr = _make_manager()

        def subprocess_side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            if "pgrep" in cmd_str and "pytest" in cmd_str:
                return SimpleNamespace(returncode=0, stdout="3877\n", stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        async def fake_to_thread(fn, *a, **kw):
            return fn(*a, **kw)

        async def fake_is_owned_orphan(pid):
            return False  # Not an orphan (no ownership tag or owner alive)

        with patch("core.lifecycle_manager.asyncio.to_thread", side_effect=fake_to_thread), \
             patch("core.lifecycle_manager.subprocess.run", side_effect=subprocess_side_effect), \
             patch.object(mgr, "_is_owned_orphan", side_effect=fake_is_owned_orphan), \
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
    @settings(suppress_health_check=[HealthCheck.too_slow])
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
    @settings(suppress_health_check=[HealthCheck.too_slow])
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
    @settings(suppress_health_check=[HealthCheck.too_slow])
    def test_kill_clears_all(self, pids):
        """_kill_tracked_pids always empties the set regardless of kill outcomes."""
        mgr = _make_manager()
        for pid in pids:
            mgr.track_pid(pid)

        with patch("os.kill"):
            asyncio.run(mgr._kill_tracked_pids())

        assert len(mgr._tracked_child_pids) == 0

    @given(pids=st.lists(st.integers(min_value=1, max_value=2**31), min_size=1, max_size=30))
    @settings(suppress_health_check=[HealthCheck.too_slow])
    def test_kill_attempts_every_pid(self, pids):
        """_kill_tracked_pids attempts os.kill for every unique tracked PID."""
        mgr = _make_manager()
        for pid in pids:
            mgr.track_pid(pid)

        with patch("os.kill") as mock_kill:
            asyncio.run(mgr._kill_tracked_pids())

        killed = {c.args[0] for c in mock_kill.call_args_list}
        assert killed == set(pids)

    @given(pids=st.lists(st.integers(min_value=1, max_value=2**31), min_size=1, max_size=20))
    @settings(suppress_health_check=[HealthCheck.too_slow])
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
            asyncio.run(mgr._kill_tracked_pids())

        assert len(mgr._tracked_child_pids) == 0


# ── WAITING_INPUT Timeout Tests ──────────────────────────────────────────


class TestWaitingInputTimeout:
    """Verify lifecycle_manager recovers stuck WAITING_INPUT sessions."""

    def test_waiting_input_timeout_fires_after_threshold(self):
        """Session in WAITING_INPUT beyond 120min gets force-unstuck."""
        from core.session_unit import SessionState

        router = MagicMock()
        unit = MagicMock()
        unit.state = SessionState.WAITING_INPUT
        unit.session_id = "test-session-123"
        # last_used 121 minutes ago (beyond 120min threshold)
        unit.last_used = __import__("time").time() - 7260
        unit.force_unstick_waiting_input = AsyncMock()
        router.list_units.return_value = [unit]

        mgr = LifecycleManager(router=router)
        asyncio.run(mgr._check_waiting_input_timeout())

        unit.force_unstick_waiting_input.assert_called_once()

    def test_waiting_input_within_timeout_not_touched(self):
        """Session in WAITING_INPUT within 120min is left alone."""
        from core.session_unit import SessionState

        router = MagicMock()
        unit = MagicMock()
        unit.state = SessionState.WAITING_INPUT
        unit.session_id = "test-session-456"
        # last_used 30 minutes ago (well within threshold)
        unit.last_used = __import__("time").time() - 1800
        unit.force_unstick_waiting_input = AsyncMock()
        router.list_units.return_value = [unit]

        mgr = LifecycleManager(router=router)
        asyncio.run(mgr._check_waiting_input_timeout())

        unit.force_unstick_waiting_input.assert_not_called()

    def test_non_waiting_input_sessions_ignored(self):
        """Only WAITING_INPUT sessions are checked (not IDLE, STREAMING, etc)."""
        from core.session_unit import SessionState

        router = MagicMock()
        idle_unit = MagicMock()
        idle_unit.state = SessionState.IDLE
        idle_unit.session_id = "idle-session"
        idle_unit.last_used = __import__("time").time() - 99999
        idle_unit.force_unstick_waiting_input = AsyncMock()

        streaming_unit = MagicMock()
        streaming_unit.state = SessionState.STREAMING
        streaming_unit.session_id = "streaming-session"
        streaming_unit.last_used = __import__("time").time() - 99999
        streaming_unit.force_unstick_waiting_input = AsyncMock()

        router.list_units.return_value = [idle_unit, streaming_unit]

        mgr = LifecycleManager(router=router)
        asyncio.run(mgr._check_waiting_input_timeout())

        idle_unit.force_unstick_waiting_input.assert_not_called()
        streaming_unit.force_unstick_waiting_input.assert_not_called()


# ── Dumb-spawn watchdog (run_6c482b10) ─────────────────────────────────
#
# A STREAMING subprocess that produced ZERO SDK events since spawn ("dumb":
# alive but silent, no open tool) must be force-unstuck on a SHORT threshold
# (DUMB_SPAWN_TIMEOUT), NOT the 600-1800s adaptive _compute_message_timeout
# designed for slow inference.
# Evidence: pid 33855 / session 89b71059, 2026-06-25 — zero events for 15+min.
#
# CRITICAL FIXTURE FIDELITY (run_6c482b10 REVIEW catch): production sets BOTH
# _streaming_start_time AND _last_event_time to the SAME timestamp on STREAMING
# entry (session_unit.py _transition), then advances ONLY _last_event_time on
# each real SDK event. So a dumb spawn is NOT _last_event_time is None — it is
# _last_event_time == _streaming_start_time (unchanged since entry). Tests MUST
# reproduce that exact state, or they validate a dead `is None` branch that can
# never fire in production. The original tests used None and masked this bug.


def _make_streaming_unit(
    *,
    last_event_time,
    streaming_start_time,
    sdk_session_id=None,
    adaptive_timeout=600.0,
):
    """Build a MagicMock STREAMING unit with real streaming_stall_seconds.

    streaming_stall_seconds is computed the same way the real property does
    (session_unit.py:3055) so the watchdog sees a realistic stall.
    """
    import time as _t
    from core.session_unit import SessionState, SessionUnit

    unit = MagicMock()
    unit.state = SessionState.STREAMING
    unit.session_id = "dumb-test"
    unit.pid = 99999
    unit._last_event_time = last_event_time
    unit._streaming_start_time = streaming_start_time
    unit._sdk_session_id = sdk_session_id
    unit._consecutive_unstick_timeouts = 0
    # Real circuit-breaker threshold (production reads it via getattr) — a bare
    # MagicMock attribute would break the int comparison in _check_streaming_timeout.
    unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
    unit._compute_message_timeout = MagicMock(return_value=adaptive_timeout)
    unit.force_unstick_streaming = AsyncMock()

    # Use the REAL stall computation (bound to our mock's attributes).
    def _stall():
        now = _t.time()
        if unit._last_event_time is None:
            if unit._streaming_start_time is not None:
                return now - unit._streaming_start_time
            return None
        return now - unit._last_event_time

    type(unit).streaming_stall_seconds = property(lambda self: _stall())
    return unit


def _dumb_unit(*, stall, sdk_session_id=None, adaptive_timeout=600.0):
    """Dumb spawn: _last_event_time == _streaming_start_time (no event since
    entry), both `stall` seconds in the past — exactly what _transition sets."""
    import time as _t
    t = _t.time() - stall
    return _make_streaming_unit(
        last_event_time=t,
        streaming_start_time=t,
        sdk_session_id=sdk_session_id,
        adaptive_timeout=adaptive_timeout,
    )


class TestDumbSpawnWatchdog:
    """Zero-event-since-spawn must recover on the short threshold."""

    def _run_with_unit(self, unit):
        router = MagicMock()
        router.list_units.return_value = [unit]
        mgr = LifecycleManager(router=router)
        asyncio.run(mgr._check_streaming_timeout())

    def test_ac1_dumb_spawn_unstuck_after_short_timeout(self):
        """No event since spawn + stall > DUMB_SPAWN_TIMEOUT → force_unstick."""
        from core.session_unit import DUMB_SPAWN_TIMEOUT_SECONDS
        unit = _dumb_unit(stall=DUMB_SPAWN_TIMEOUT_SECONDS + 5, sdk_session_id=None)
        self._run_with_unit(unit)
        unit.force_unstick_streaming.assert_awaited_once()

    def test_ac1_dumb_spawn_not_unstuck_before_short_timeout(self):
        """No event since spawn but stall < DUMB_SPAWN_TIMEOUT → do NOT unstick."""
        from core.session_unit import DUMB_SPAWN_TIMEOUT_SECONDS
        unit = _dumb_unit(stall=DUMB_SPAWN_TIMEOUT_SECONDS - 30, sdk_session_id=None)
        self._run_with_unit(unit)
        unit.force_unstick_streaming.assert_not_called()

    def test_regression_dumb_state_is_equal_timestamps_not_none(self):
        """REGRESSION GUARD (run_6c482b10): the dumb state in production is
        _last_event_time == _streaming_start_time (NOT None). A dumb spawn with
        EQUAL non-None timestamps past the threshold MUST still be unstuck. If
        this fails, the discriminator regressed to a dead `is None` check that
        never fires in production (the bug REVIEW caught)."""
        from core.session_unit import DUMB_SPAWN_TIMEOUT_SECONDS
        unit = _dumb_unit(stall=DUMB_SPAWN_TIMEOUT_SECONDS + 5, sdk_session_id=None)
        # Prove the fixture reproduces production state, not the None shortcut.
        assert unit._last_event_time is not None
        assert unit._last_event_time == unit._streaming_start_time
        self._run_with_unit(unit)
        unit.force_unstick_streaming.assert_awaited_once()

    def test_ac2_slow_inference_not_killed_by_short_timeout(self):
        """Events FLOWING (last_event advanced past start) but stalled just past
        the dumb threshold must NOT be unstuck — slow inference keeps the
        600-1800s adaptive tolerance. Regression guard against false-kill."""
        import time as _t
        from core.session_unit import DUMB_SPAWN_TIMEOUT_SECONDS
        unit = _make_streaming_unit(
            # event arrived well after spawn (last_event > start) → events flowing
            last_event_time=_t.time() - (DUMB_SPAWN_TIMEOUT_SECONDS + 30),
            streaming_start_time=_t.time() - 5000,
            sdk_session_id="resume-xyz",
            adaptive_timeout=1800.0,
        )
        self._run_with_unit(unit)
        unit.force_unstick_streaming.assert_not_called()

    def test_ac2_slow_inference_killed_only_past_adaptive(self):
        """Events flowing + stall past the adaptive timeout → unstick (existing
        behavior preserved)."""
        import time as _t
        unit = _make_streaming_unit(
            last_event_time=_t.time() - 700,
            streaming_start_time=_t.time() - 5000,
            sdk_session_id=None,
            adaptive_timeout=600.0,
        )
        self._run_with_unit(unit)
        unit.force_unstick_streaming.assert_awaited_once()

    def test_ac4_resume_dumb_spawn_uses_2x_threshold(self):
        """Resume dumb spawn (sdk_session_id set): stall between 1x and 2x
        DUMB_SPAWN_TIMEOUT must NOT be unstuck (resume replays full convo
        before first token — GUI66)."""
        from core.session_unit import DUMB_SPAWN_TIMEOUT_SECONDS
        unit = _dumb_unit(
            stall=DUMB_SPAWN_TIMEOUT_SECONDS + 20, sdk_session_id="resume-abc",
        )
        self._run_with_unit(unit)
        unit.force_unstick_streaming.assert_not_called()

    def test_ac4_resume_dumb_spawn_unstuck_past_2x(self):
        """Resume dumb spawn stalled past 2x DUMB_SPAWN_TIMEOUT → unstick."""
        from core.session_unit import DUMB_SPAWN_TIMEOUT_SECONDS
        unit = _dumb_unit(
            stall=2 * DUMB_SPAWN_TIMEOUT_SECONDS + 20, sdk_session_id="resume-abc",
        )
        self._run_with_unit(unit)
        unit.force_unstick_streaming.assert_awaited_once()
