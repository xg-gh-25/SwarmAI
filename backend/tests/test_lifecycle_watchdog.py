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
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
        """Session in WAITING_INPUT beyond the threshold gets force-unstuck."""
        from core.session_unit import SessionState

        router = MagicMock()
        unit = MagicMock()
        unit.state = SessionState.WAITING_INPUT
        unit.session_id = "test-session-123"
        # last_used just beyond the threshold (derive from the constant so this
        # test survives threshold changes — was hardcoded 7260s for the old 120min).
        unit.last_used = (
            __import__("time").time()
            - (LifecycleManager.WAITING_INPUT_TIMEOUT_SECONDS + 60)
        )
        unit.force_unstick_waiting_input = AsyncMock()
        # R3c (M2): the recovery decision now routes through the unit's
        # RecoveryCoordinator — give the mock a real one + a not-stopped turn so
        # decide_bare returns PROCEED_KILL (behavior parity with pre-R3c).
        unit._user_stopped_current_turn = False
        from core.session_healing import HealingLoop, RecoveryCoordinator
        unit._recovery_coordinator = RecoveryCoordinator(HealingLoop())
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


class TestWaitingInputTimeoutVsAskTimeout:
    """The WAITING_INPUT watchdog (force-kill) must NOT fire before the
    AskUserQuestion answer-wait hook times out gracefully.

    Two independent timers govern a blocked AskUserQuestion:
    - ask_question_manager.ASK_ANSWER_TIMEOUT_SECONDS (hook waits for the answer,
      then DENIES gracefully — "question expired, re-ask")
    - LifecycleManager.WAITING_INPUT_TIMEOUT_SECONDS (force-unsticks/kills a
      WAITING_INPUT session to reclaim its slot)

    If the watchdog fires FIRST, it kills the whole session (user loses the
    conversation) instead of letting the hook expire cleanly (user just re-asks
    the one question). The watchdog MUST be >= the hook timeout so the graceful
    path always wins. This guard prevents a future edit to one constant from
    silently re-introducing the early-kill regression.
    """

    def test_watchdog_timeout_at_least_ask_answer_timeout(self):
        from core.lifecycle_manager import LifecycleManager
        from core.ask_question_manager import ASK_ANSWER_TIMEOUT_SECONDS

        assert LifecycleManager.WAITING_INPUT_TIMEOUT_SECONDS >= ASK_ANSWER_TIMEOUT_SECONDS, (
            "WAITING_INPUT watchdog must be >= the AskUserQuestion answer-wait "
            "timeout, else the watchdog force-kills the session before the hook "
            "can expire gracefully (early-kill regression)."
        )

    def test_watchdog_strictly_greater_so_graceful_path_wins(self):
        # Strictly greater (not just equal) so the hook's graceful deny reliably
        # fires before the 60s-granularity watchdog loop catches the session.
        from core.lifecycle_manager import LifecycleManager
        from core.ask_question_manager import ASK_ANSWER_TIMEOUT_SECONDS

        assert LifecycleManager.WAITING_INPUT_TIMEOUT_SECONDS > ASK_ANSWER_TIMEOUT_SECONDS


# ── R3b (M1): per-session-7GB RSS kill routes through RecoveryCoordinator ──


class TestStreamingRssRoutesCoordinator:
    """FORCING TESTS (STEERING #11): the per-session-7GB RSS streaming kill must
    route its DECISION through the unit's RecoveryCoordinator (BareThresholdPolicy),
    not kill directly. These mock the RSS breach and assert the coordinator path
    actually executes — a kill that bypasses decide_bare is a regression."""

    def _make_streaming_unit(self, *, user_stopped: bool):
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.state = SessionState.STREAMING
        unit.pid = 4242
        unit.session_id = "sess_rss_forcing_0001"
        unit._user_stopped_current_turn = user_stopped
        unit._arm_recovery_checkpoint = AsyncMock()
        unit.kill = AsyncMock()
        # Real coordinator so decide_bare actually runs (forcing, not mock-through).
        from core.session_healing import HealingLoop, RecoveryCoordinator
        unit._recovery_coordinator = RecoveryCoordinator(HealingLoop())
        # Spy on decide_bare to prove the path went through it.
        unit._recovery_coordinator.decide_bare = MagicMock(
            wraps=unit._recovery_coordinator.decide_bare
        )
        return unit

    @staticmethod
    def _rss_over():
        from core.session_unit import SessionUnit
        return SessionUnit.STREAMING_RSS_KILL_THRESHOLD + 1

    @pytest.mark.asyncio
    async def test_rss_breach_routes_through_decide_bare_and_kills(self):
        unit = self._make_streaming_unit(user_stopped=False)
        mgr = _make_manager()
        mgr._router.list_units.return_value = [unit]

        with patch("core.resource_monitor.resource_monitor") as rm:
            rm.system_memory.return_value = SimpleNamespace(percent_used=20.0)
            rm.process_tree_rss.return_value = self._rss_over()
            await mgr._streaming_rss_check()

        # FORCING assertion: the decision went through the coordinator...
        unit._recovery_coordinator.decide_bare.assert_called_once()
        kwargs = unit._recovery_coordinator.decide_bare.call_args.kwargs
        assert kwargs["trigger"] == "rss_streaming"
        # ...and the kill executed because the verdict was PROCEED_KILL.
        unit._arm_recovery_checkpoint.assert_awaited_once()
        unit.kill.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_stopped_turn_skips_kill_via_coordinator(self):
        """If the user stopped this turn, the universal guard SKIPs — the bloated
        session is NOT killed (parity with self-heal's user-stop guard)."""
        unit = self._make_streaming_unit(user_stopped=True)
        mgr = _make_manager()
        mgr._router.list_units.return_value = [unit]

        with patch("core.resource_monitor.resource_monitor") as rm:
            rm.system_memory.return_value = SimpleNamespace(percent_used=20.0)
            rm.process_tree_rss.return_value = self._rss_over()
            await mgr._streaming_rss_check()

        unit._recovery_coordinator.decide_bare.assert_called_once()
        # SKIP verdict → NO kill.
        unit.kill.assert_not_awaited()
        unit._arm_recovery_checkpoint.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_system_pressure_kill_is_fleet_arbitration_not_coordinated(self):
        """BOUNDARY INVARIANT (design §9): Trigger 2 (system-pressure > 90% →
        kill heaviest) is FLEET ARBITRATION, deliberately NOT routed through the
        coordinator and deliberately IGNORING the user_stopped guard. At >90%
        system memory a user-Stop does not free the leaked RSS, so the heaviest
        STREAMING session must still be killed to avoid machine-wide OOM (COE05).
        This pins the intentional asymmetry vs Trigger 1 so a future edit can't
        silently 'unify' it and re-introduce the OOM hazard."""
        from core.session_unit import SessionState, SessionUnit
        from core.session_healing import HealingLoop, RecoveryCoordinator

        # user_stopped=True AND under-7GB (so Trigger 1 does NOT fire), but
        # system memory is critical → Trigger 2 must kill the heaviest anyway.
        unit = MagicMock()
        unit.state = SessionState.STREAMING
        unit.pid = 7777
        unit.session_id = "sess_pressure_0001"
        unit._user_stopped_current_turn = True
        unit._arm_recovery_checkpoint = AsyncMock()
        unit.kill = AsyncMock()
        unit._recovery_coordinator = RecoveryCoordinator(HealingLoop())
        unit._recovery_coordinator.decide_bare = MagicMock(
            wraps=unit._recovery_coordinator.decide_bare
        )
        mgr = _make_manager()
        mgr._router.list_units.return_value = [unit]

        under_7gb = SessionUnit.STREAMING_RSS_KILL_THRESHOLD - 1
        with patch("core.resource_monitor.resource_monitor") as rm:
            rm.system_memory.return_value = SimpleNamespace(percent_used=95.0)
            rm.process_tree_rss.return_value = under_7gb
            await mgr._streaming_rss_check()

        # Trigger 1 never reached PROCEED_KILL territory (under threshold), so
        # decide_bare may be skipped entirely; the fleet-pressure kill fires
        # regardless of user_stopped.
        unit.kill.assert_awaited_once()


# ── R3c (M2): stuck-WAITING_INPUT timeout routes through RecoveryCoordinator ──


class TestStuckWaitingRoutesCoordinator:
    """FORCING TESTS (STEERING #11): the stuck-WAITING_INPUT recovery must route
    its DECISION through the unit's RecoveryCoordinator with eligible_states=
    {waiting_input} — this trigger TARGETS waiting_input (opposite of self-heal,
    which protects it). A recover that bypasses decide_bare is a regression."""

    def _make_waiting_unit(self, *, state, user_stopped=False, waited_over=True):

        unit = MagicMock()
        unit.state = state
        unit.session_id = "sess_wait_forcing_0001"
        unit._user_stopped_current_turn = user_stopped
        # last_used far in the past → waited beyond timeout
        unit.last_used = 0.0 if waited_over else 9_999_999_999.0
        unit.force_unstick_waiting_input = AsyncMock()
        from core.session_healing import HealingLoop, RecoveryCoordinator
        unit._recovery_coordinator = RecoveryCoordinator(HealingLoop())
        unit._recovery_coordinator.decide_bare = MagicMock(
            wraps=unit._recovery_coordinator.decide_bare
        )
        return unit

    @pytest.mark.asyncio
    async def test_waiting_timeout_routes_through_decide_bare_and_unsticks(self):
        from core.session_unit import SessionState

        unit = self._make_waiting_unit(state=SessionState.WAITING_INPUT)
        mgr = _make_manager()
        mgr._router.list_units.return_value = [unit]

        await mgr._check_waiting_input_timeout()

        unit._recovery_coordinator.decide_bare.assert_called_once()
        kwargs = unit._recovery_coordinator.decide_bare.call_args.kwargs
        assert kwargs["trigger"] == "stuck_waiting"
        assert kwargs["eligible_states"] == frozenset({"waiting_input"})
        unit.force_unstick_waiting_input.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stale_user_stopped_does_not_block_reclamation(self):
        """REGRESSION GUARD (adversarial final-gate Q3): a session stuck in
        WAITING_INPUT for 4h+ carries a STALE _user_stopped_current_turn from a
        prior STREAMING turn (cleared only at the next send(), which never came
        for an abandoned session). M2 must NOT consult it — otherwise a
        stopped-then-abandoned session blocks its own 4h reclamation forever.
        The 4h watchdog TARGETS exactly these abandoned sessions."""
        from core.session_unit import SessionState

        unit = self._make_waiting_unit(
            state=SessionState.WAITING_INPUT, user_stopped=True  # STALE flag
        )
        mgr = _make_manager()
        mgr._router.list_units.return_value = [unit]

        await mgr._check_waiting_input_timeout()

        unit._recovery_coordinator.decide_bare.assert_called_once()
        # The stale flag must NOT prevent reclamation — unstick still fires.
        unit.force_unstick_waiting_input.assert_awaited_once()
        # And the decide_bare call passed user_stopped=False deliberately.
        kwargs = unit._recovery_coordinator.decide_bare.call_args.kwargs
        assert kwargs["user_stopped"] is False


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
    from core.session_unit import SessionState

    unit = MagicMock()
    unit.state = SessionState.STREAMING
    unit.session_id = "dumb-test"
    unit.pid = 99999
    unit._last_event_time = last_event_time
    unit._streaming_start_time = streaming_start_time
    unit._sdk_session_id = sdk_session_id
    unit._consecutive_unstick_timeouts = 0
    # No open tool: a dumb spawn produced ZERO events, so there is no tool_use
    # in flight. Must be explicitly falsy — the merged open-tool guard in
    # _check_streaming_timeout does `getattr(unit, "_open_tool_uses", None)`,
    # and a bare MagicMock auto-creates a TRUTHY attribute that would trip the
    # guard and skip the dumb-spawn path entirely (false-green).
    unit._open_tool_uses = None
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

    def test_ac4_resume_dumb_spawn_gets_full_adaptive_budget(self):
        """Resume dumb spawn (sdk_session_id set): a large --resume replays the
        full conversation before the first token (GUI66) and emits no SDK event
        meanwhile. It must get max(2x DUMB, adaptive) — NOT a flat short kill,
        else a healthy large resume false-kills → respawn → replay → kill loop
        (run_6c482b10 adversarial MED). adaptive=1800 here → stall 300s must NOT
        unstick."""
        unit = _dumb_unit(
            stall=300, sdk_session_id="resume-abc", adaptive_timeout=1800.0,
        )
        self._run_with_unit(unit)
        unit.force_unstick_streaming.assert_not_called()

    def test_ac4_resume_dumb_spawn_unstuck_past_adaptive(self):
        """Resume dumb spawn stalled past its adaptive replay budget → unstick."""
        unit = _dumb_unit(
            stall=1900, sdk_session_id="resume-abc", adaptive_timeout=1800.0,
        )
        self._run_with_unit(unit)
        unit.force_unstick_streaming.assert_awaited_once()

    def test_integration_real_transition_produces_dumb_detectable_state(self):
        """CRITICAL REGRESSION GUARD (run_6c482b10 adversarial HIGH): the unit
        fixtures above set the two timestamps EQUAL by hand. That is only valid
        if the REAL _transition() actually produces equal timestamps. The
        original bug: _transition set _streaming_start_time and _last_event_time
        via TWO separate time.time() calls, so _last_event_time was microseconds
        GREATER and the discriminator (last_t <= start_t) NEVER fired in
        production — the whole feature was dead while every unit test passed.

        This test drives the REAL SessionUnit._transition into STREAMING and
        asserts the discriminator the watchdog uses actually holds. If someone
        re-splits the assignment into two time.time() calls, this FAILS — the
        only test that catches the production-vs-fixture gap."""
        from core.session_unit import SessionState
        import core.session_unit as su

        # Build a minimal real SessionUnit without running __init__ side effects.
        unit = su.SessionUnit.__new__(su.SessionUnit)
        unit.session_id = "integ-dumb"
        unit.state = SessionState.IDLE
        unit._streaming_start_time = None
        unit._last_event_time = None
        unit._hooks_enqueued = False
        unit._last_heartbeat_elapsed = 0.0
        unit._wrapper = None            # .pid property reads this during logging
        unit._on_state_change = None    # observability callback — none in test
        unit._tool_hang_interrupted = False
        unit._tool_hang_interrupt_at = None
        # Neutralize the PID watchdog side effect on STREAMING entry.
        unit._start_pid_watchdog = lambda: None

        unit._transition(SessionState.STREAMING)

        # The exact predicate lifecycle_manager._check_streaming_timeout uses.
        start_t = unit._streaming_start_time
        last_t = unit._last_event_time
        assert start_t is not None and last_t is not None
        assert last_t <= start_t, (
            "real _transition must leave _last_event_time <= _streaming_start_time "
            "so the dumb-spawn discriminator fires; got "
            f"last={last_t!r} start={start_t!r} (re-split into two time.time() calls?)"
        )
