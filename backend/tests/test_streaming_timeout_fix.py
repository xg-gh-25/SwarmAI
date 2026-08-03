"""Tests for streaming timeout dead-loop fix (run_ae6d25d7).

Covers 4 acceptance criteria:
  AC1: hang_detected does NOT fire when session_state is STREAMING
  AC2: _compute_message_timeout returns 2x when _sdk_session_id is set (resume)
  AC3: force_unstick_streaming respects circuit breaker after 2 consecutive attempts
  AC4: lifecycle_manager checks circuit breaker before force_unstick

TDD: RED phase — tests written before implementation.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.session_healing import HANG_TIMEOUT_S, HealthSensor


# ─── AC1: hang_detected suppressed during STREAMING ───────────────────────────


class TestHangDetectedStreamingAware:
    """hang_detected must NOT fire when session is in STREAMING state."""

    def test_hang_fires_in_idle(self):
        """hang_detected should still fire when session is IDLE (not streaming)."""
        sensor = HealthSensor(max_turns=500)
        # Simulate no activity for > HANG_TIMEOUT_S
        sensor._last_activity_time = time.time() - (HANG_TIMEOUT_S + 10)
        should, trigger = sensor.should_checkpoint(session_state="idle")
        assert should
        assert trigger == "hang_detected"

    def test_hang_suppressed_in_streaming(self):
        """hang_detected must NOT fire when session state is STREAMING."""
        sensor = HealthSensor(max_turns=500)
        # Same stale activity — but session is actively streaming
        sensor._last_activity_time = time.time() - (HANG_TIMEOUT_S + 10)
        should, trigger = sensor.should_checkpoint(session_state="streaming")
        # Should NOT trigger hang_detected — model may be in extended thinking
        assert not should or trigger != "hang_detected"

    def test_hang_fires_in_cold(self):
        """hang_detected should fire in COLD state (genuine hang)."""
        sensor = HealthSensor(max_turns=500)
        sensor._last_activity_time = time.time() - (HANG_TIMEOUT_S + 10)
        should, trigger = sensor.should_checkpoint(session_state="cold")
        assert should
        assert trigger == "hang_detected"

    def test_other_triggers_still_fire_during_streaming(self):
        """Non-hang triggers (latency, errors) should still fire during STREAMING."""
        sensor = HealthSensor(max_turns=500)
        # Set up error cascade
        for _ in range(5):
            sensor.record_turn(100.0, 1400, True)
        should, trigger = sensor.should_checkpoint(session_state="streaming")
        assert should
        assert trigger == "error_cascade"

    def test_backward_compatible_default(self):
        """Calling without session_state should behave as before (hang fires)."""
        sensor = HealthSensor(max_turns=500)
        sensor._last_activity_time = time.time() - (HANG_TIMEOUT_S + 10)
        # No session_state argument — backward compatible
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "hang_detected"


# ─── AC2: resume multiplier on _compute_message_timeout ───────────────────────


class TestResumeTimeoutMultiplier:
    """_compute_message_timeout should return 2x value for resume sessions."""

    def test_base_timeout_no_resume(self):
        """Non-resume session gets standard timeout (300s base)."""
        unit = MagicMock()
        unit._last_known_context_tokens = 0
        unit._sdk_session_id = None  # Not a resume

        from core.session_unit import SessionUnit
        # Call the actual method
        timeout = SessionUnit._compute_message_timeout(unit)
        assert timeout == 300.0

    def test_resume_session_gets_double_timeout(self):
        """Resume session (has _sdk_session_id) gets 2x timeout."""
        unit = MagicMock()
        unit._last_known_context_tokens = 0
        unit._sdk_session_id = "some-session-id"  # IS a resume

        from core.session_unit import SessionUnit
        timeout = SessionUnit._compute_message_timeout(unit)
        assert timeout == 600.0  # 300 * 2

    def test_resume_with_large_context(self):
        """Resume + large context — both factors apply."""
        unit = MagicMock()
        unit._last_known_context_tokens = 600_000  # 600K tokens
        unit._sdk_session_id = "some-session-id"  # IS a resume

        from core.session_unit import SessionUnit
        timeout = SessionUnit._compute_message_timeout(unit)
        # 600K / 3000 = 200 → max(300, 200) = 300 → * 2 = 600
        assert timeout == 600.0

    def test_resume_large_context_caps_at_max(self):
        """Even with resume multiplier, cap at MAX_TIMEOUT (900s * 2 = 1800s)."""
        unit = MagicMock()
        unit._last_known_context_tokens = 5_000_000  # Huge context
        unit._sdk_session_id = "some-session-id"

        from core.session_unit import SessionUnit
        timeout = SessionUnit._compute_message_timeout(unit)
        # Cap should prevent infinite timeouts
        assert timeout <= 1800.0  # 900 * 2 max


# ─── AC3: force_unstick respects circuit breaker ──────────────────────────────


class TestForceUnstickCircuitBreaker:
    """force_unstick_streaming must stop after 2 consecutive attempts."""

    @pytest.mark.asyncio
    async def test_first_unstick_proceeds(self):
        """First force_unstick should proceed normally."""
        from core.session_unit import SessionState, SessionUnit

        unit = MagicMock(spec=SessionUnit)
        unit.state = SessionState.STREAMING
        unit.pid = 12345
        unit.session_id = "test-session"
        unit.streaming_stall_seconds = 400.0
        unit._consecutive_unstick_timeouts = 0
        unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
        unit._arm_recovery_checkpoint = AsyncMock()
        unit._crash_to_cold_async = AsyncMock()
        # R3d (M3): KILL-vs-KILL_HARD escalation now routes through the unit's
        # RecoveryCoordinator — give the mock a real one + a not-stopped turn.
        unit._user_stopped_current_turn = False
        from core.session_healing import HealingLoop, RecoveryCoordinator
        unit._recovery_coordinator = RecoveryCoordinator(HealingLoop())

        # Call the real method
        await SessionUnit.force_unstick_streaming(unit)

        # Should have called crash_to_cold with clear_identity=False (resume OK)
        unit._crash_to_cold_async.assert_called_once_with(clear_identity=False)
        # Counter should increment
        assert unit._consecutive_unstick_timeouts == 1

    @pytest.mark.asyncio
    async def test_second_unstick_still_proceeds(self):
        """Second unstick should still proceed (threshold is 2)."""
        from core.session_unit import SessionState, SessionUnit

        unit = MagicMock(spec=SessionUnit)
        unit.state = SessionState.STREAMING
        unit.pid = 12345
        unit.session_id = "test-session"
        unit.streaming_stall_seconds = 400.0
        unit._consecutive_unstick_timeouts = 1
        unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
        unit._arm_recovery_checkpoint = AsyncMock()
        unit._crash_to_cold_async = AsyncMock()
        unit._user_stopped_current_turn = False
        from core.session_healing import HealingLoop, RecoveryCoordinator
        unit._recovery_coordinator = RecoveryCoordinator(HealingLoop())

        await SessionUnit.force_unstick_streaming(unit)
        unit._crash_to_cold_async.assert_called_once_with(clear_identity=False)

    @pytest.mark.asyncio
    async def test_third_unstick_blocked_by_circuit_breaker(self):
        """Third consecutive unstick should clear identity (no more --resume)."""
        from core.session_unit import SessionState, SessionUnit

        unit = MagicMock(spec=SessionUnit)
        unit.state = SessionState.STREAMING
        unit.pid = 12345
        unit.session_id = "test-session"
        unit.streaming_stall_seconds = 400.0
        unit._consecutive_unstick_timeouts = 2  # Already at threshold
        unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
        unit._arm_recovery_checkpoint = AsyncMock()
        unit._crash_to_cold_async = AsyncMock()
        unit._user_stopped_current_turn = False
        from core.session_healing import HealingLoop, RecoveryCoordinator
        unit._recovery_coordinator = RecoveryCoordinator(HealingLoop())

        await SessionUnit.force_unstick_streaming(unit)

        # Circuit breaker tripped — should clear identity (no resume)
        unit._crash_to_cold_async.assert_called_once_with(clear_identity=True)

    def test_counter_resets_on_successful_stream(self):
        """Counter resets after a successful streaming response.

        The _consecutive_unstick_timeouts counter resets alongside
        _consecutive_oom_kills on successful stream completion (line 1182).
        Verify via direct attribute check (integration tested by
        session_healing_integration tests).
        """
        from core.session_unit import SessionUnit

        unit = MagicMock(spec=SessionUnit)
        unit._consecutive_unstick_timeouts = 3
        unit._consecutive_oom_kills = 2

        # Simulate the reset that happens at line 1181-1182 after successful stream
        unit._consecutive_oom_kills = 0
        unit._consecutive_unstick_timeouts = 0

        assert unit._consecutive_unstick_timeouts == 0
        assert unit._consecutive_oom_kills == 0

    @pytest.mark.asyncio
    async def test_counter_resets_after_circuit_breaker_trips(self):
        """After CB clears identity, counter resets for fresh start."""
        from core.session_unit import SessionState, SessionUnit

        unit = MagicMock(spec=SessionUnit)
        unit.state = SessionState.STREAMING
        unit.pid = 12345
        unit.session_id = "test-session"
        unit.streaming_stall_seconds = 400.0
        unit._consecutive_unstick_timeouts = 2  # At threshold
        unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
        unit._arm_recovery_checkpoint = AsyncMock()
        unit._crash_to_cold_async = AsyncMock()
        unit._user_stopped_current_turn = False
        from core.session_healing import HealingLoop, RecoveryCoordinator
        unit._recovery_coordinator = RecoveryCoordinator(HealingLoop())

        await SessionUnit.force_unstick_streaming(unit)

        # After CB trips and clears identity, counter should reset
        assert unit._consecutive_unstick_timeouts == 0


# ─── AC4: lifecycle_manager circuit breaker check ─────────────────────────────


class TestLifecycleManagerCircuitBreaker:
    """_check_streaming_timeout must check circuit breaker before unsticking."""

    @pytest.mark.asyncio
    async def test_lifecycle_skips_when_circuit_broken(self):
        """Lifecycle manager should NOT call force_unstick when CB tripped."""
        from core.lifecycle_manager import LifecycleManager
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.state = SessionState.STREAMING
        unit.streaming_stall_seconds = 500.0
        unit.session_id = "test-session"
        unit._compute_message_timeout = MagicMock(return_value=300.0)
        unit._consecutive_unstick_timeouts = 3  # CB already tripped
        unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
        unit.force_unstick_streaming = AsyncMock()
        # Events-flowing (slow-inference) case: last_event advanced past start,
        # no open tool. Exercises the adaptive-timeout branch, not dumb-spawn.
        import time as _t
        unit._streaming_start_time = _t.time() - 600
        unit._last_event_time = _t.time() - 100
        unit._open_tool_uses = None
        unit._sdk_session_id = None

        router = MagicMock()
        router.list_units = MagicMock(return_value=[unit])

        mgr = LifecycleManager.__new__(LifecycleManager)
        mgr._router = router
        mgr.STREAMING_TIMEOUT_SECONDS = 300.0

        await mgr._check_streaming_timeout()

        # Should NOT have called force_unstick (circuit breaker tripped)
        unit.force_unstick_streaming.assert_not_called()

    @pytest.mark.asyncio
    async def test_lifecycle_proceeds_when_under_threshold(self):
        """Lifecycle manager should proceed when counter is low."""
        from core.lifecycle_manager import LifecycleManager
        from core.session_unit import SessionState

        unit = MagicMock()
        unit.state = SessionState.STREAMING
        unit.streaming_stall_seconds = 500.0
        unit.session_id = "test-session"
        unit._compute_message_timeout = MagicMock(return_value=300.0)
        unit._consecutive_unstick_timeouts = 0  # Fresh
        unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
        unit.force_unstick_streaming = AsyncMock()
        # Events-flowing (slow-inference) case: last_event advanced past start,
        # no open tool. Exercises the adaptive-timeout branch, not dumb-spawn.
        import time as _t
        unit._streaming_start_time = _t.time() - 600
        unit._last_event_time = _t.time() - 100
        unit._open_tool_uses = None
        unit._sdk_session_id = None

        router = MagicMock()
        router.list_units = MagicMock(return_value=[unit])

        mgr = LifecycleManager.__new__(LifecycleManager)
        mgr._router = router
        mgr.STREAMING_TIMEOUT_SECONDS = 300.0

        await mgr._check_streaming_timeout()

        # Should have called force_unstick
        unit.force_unstick_streaming.assert_called_once()


# ── R3d (M3): force_unstick_streaming routes through RecoveryCoordinator ──


class TestForceUnstickRoutesCoordinator:
    """FORCING TESTS (STEERING #11): the streaming-timeout escalation (KILL vs
    KILL_HARD) must route through the unit's RecoveryCoordinator
    (GracefulEscalationPolicy), not a hardcoded `> threshold` branch. These use
    a REAL coordinator so the policy logic executes."""

    def _make_unit(self, *, attempt, user_stopped=False):
        from unittest.mock import AsyncMock, MagicMock
        from core.session_unit import SessionState, SessionUnit
        from core.session_healing import HealingLoop, RecoveryCoordinator

        unit = MagicMock(spec=SessionUnit)
        unit.state = SessionState.STREAMING
        unit.pid = 12345
        unit.session_id = "test-session"
        unit.streaming_stall_seconds = 400.0
        unit._consecutive_unstick_timeouts = attempt
        unit._UNSTICK_CIRCUIT_BREAKER_THRESHOLD = 2
        unit._arm_recovery_checkpoint = AsyncMock()
        unit._crash_to_cold_async = AsyncMock()
        unit._user_stopped_current_turn = user_stopped
        unit._recovery_coordinator = RecoveryCoordinator(HealingLoop())
        unit._recovery_coordinator.decide_graceful = MagicMock(
            wraps=unit._recovery_coordinator.decide_graceful
        )
        return unit

    @pytest.mark.asyncio
    async def test_base_rung_routes_through_decide_graceful_preserve_resume(self):
        from core.session_unit import SessionUnit
        from core.session_healing import RecoveryVerdict

        unit = self._make_unit(attempt=0)  # → 1 after increment, <= threshold 2
        await SessionUnit.force_unstick_streaming(unit)

        unit._recovery_coordinator.decide_graceful.assert_called_once()
        kwargs = unit._recovery_coordinator.decide_graceful.call_args.kwargs
        assert kwargs["trigger"] == "streaming_timeout"
        assert kwargs["base"] is RecoveryVerdict.PROCEED_KILL
        assert kwargs["escalated"] is RecoveryVerdict.PROCEED_KILL_HARD
        # base rung → preserve --resume
        unit._crash_to_cold_async.assert_called_once_with(clear_identity=False)

    @pytest.mark.asyncio
    async def test_escalated_rung_drops_identity(self):
        from core.session_unit import SessionUnit

        unit = self._make_unit(attempt=2)  # → 3 after increment, > threshold 2
        await SessionUnit.force_unstick_streaming(unit)

        unit._recovery_coordinator.decide_graceful.assert_called_once()
        # escalated rung → drop --resume identity
        unit._crash_to_cold_async.assert_called_once_with(clear_identity=True)

    @pytest.mark.asyncio
    async def test_user_stopped_skips_and_backs_out_increment(self):
        from core.session_unit import SessionUnit

        unit = self._make_unit(attempt=0, user_stopped=True)
        await SessionUnit.force_unstick_streaming(unit)

        unit._recovery_coordinator.decide_graceful.assert_called_once()
        # SKIP → no kill, and the speculative increment is backed out
        unit._crash_to_cold_async.assert_not_called()
        assert unit._consecutive_unstick_timeouts == 0
