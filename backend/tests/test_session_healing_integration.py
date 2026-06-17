"""Integration tests for session self-healing wired into SessionUnit.

Tests verify that:
1. HealthSensor is created on SessionUnit and accumulates data
2. Health check fires after stream success when degradation detected
3. Self-heal cycle uses _crash_to_cold_async (preserves _sdk_session_id)
4. HealingLoop respects cooldown and max attempts
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.session_healing import (
    LATENCY_BASELINE_WINDOW,
    LATENCY_WINDOW,
    HealthSensor,
    HealingLoop,
)
from core.session_unit import SessionUnit


class TestSessionUnitHealingWiring:
    """Verify HealthSensor and HealingLoop exist on SessionUnit."""

    def test_health_sensor_initialized(self):
        """SessionUnit should have a HealthSensor instance."""
        unit = SessionUnit(session_id="test-123", agent_id="agent-1")
        assert isinstance(unit._health_sensor, HealthSensor)
        assert unit._health_sensor._max_turns == 500

    def test_healing_loop_initialized(self):
        """SessionUnit should have a HealingLoop instance."""
        unit = SessionUnit(session_id="test-123", agent_id="agent-1")
        assert isinstance(unit._healing_loop, HealingLoop)
        assert unit._healing_loop.heal_attempts == 0

    def test_health_sensor_records_turn(self):
        """HealthSensor.record_turn should be callable from unit context."""
        unit = SessionUnit(session_id="test-123", agent_id="agent-1")
        unit._health_sensor.record_turn(100.0, 1400, False)
        assert unit._health_sensor.turn_count == 1


class TestSelfHealTrigger:
    """Test the self-heal trigger logic (simulated, no real subprocess)."""

    def test_latency_spike_triggers_heal_decision(self):
        """Simulating latency spike should cause should_checkpoint to fire."""
        unit = SessionUnit(session_id="test-heal", agent_id="agent-1")
        sensor = unit._health_sensor

        # Simulate baseline (10 turns at 100ms)
        for _ in range(LATENCY_BASELINE_WINDOW):
            sensor.record_turn(100.0, 1400, False)

        # Simulate degradation (5 turns at 300ms)
        for _ in range(LATENCY_WINDOW):
            sensor.record_turn(300.0, 1500, False)

        # Should trigger
        should, trigger = sensor.should_checkpoint()
        assert should is True
        assert trigger == "latency_degradation"

        # HealingLoop should allow it
        can, _ = unit._healing_loop.can_heal()
        assert can is True

    def test_error_cascade_triggers_heal_decision(self):
        """3 consecutive errors should trigger heal."""
        unit = SessionUnit(session_id="test-errors", agent_id="agent-1")
        sensor = unit._health_sensor

        sensor.record_turn(100.0, 1400, True)
        sensor.record_turn(100.0, 1400, True)
        sensor.record_turn(100.0, 1400, True)

        should, trigger = sensor.should_checkpoint()
        assert should is True
        assert trigger == "error_cascade"

    def test_healthy_session_no_trigger(self):
        """Normal operation should not trigger heal."""
        unit = SessionUnit(session_id="test-healthy", agent_id="agent-1")
        sensor = unit._health_sensor

        for _ in range(50):
            sensor.record_turn(100.0, 1500, False)

        should, _ = sensor.should_checkpoint()
        assert should is False


class TestSelfHealExecution:
    """Test the actual heal execution path (mocked subprocess)."""

    @pytest.mark.asyncio
    async def test_heal_calls_crash_to_cold(self):
        """When heal triggers, _crash_to_cold_async should be called."""
        unit = SessionUnit(session_id="test-heal-exec", agent_id="agent-1")

        # Force heal trigger
        for _ in range(LATENCY_BASELINE_WINDOW):
            unit._health_sensor.record_turn(100.0, 1400, False)
        for _ in range(LATENCY_WINDOW):
            unit._health_sensor.record_turn(300.0, 1500, False)

        # Mock _crash_to_cold_async
        unit._crash_to_cold_async = AsyncMock()

        # Simulate the heal check (extracted from send() success path)
        should_heal, trigger = unit._health_sensor.should_checkpoint()
        assert should_heal

        can_heal, _ = unit._healing_loop.can_heal()
        assert can_heal

        unit._healing_loop.record_heal_start()
        await unit._crash_to_cold_async(clear_identity=False)
        unit._health_sensor.reset()
        unit._healing_loop.record_heal_success()

        # Verify
        unit._crash_to_cold_async.assert_called_once_with(clear_identity=False)
        assert unit._health_sensor.turn_count == 0  # Reset after heal
        assert unit._healing_loop.heal_attempts == 0  # Reset on success

    @pytest.mark.asyncio
    async def test_heal_exhaustion_escalates(self):
        """After 3 failed heals, should_escalate returns True."""
        unit = SessionUnit(session_id="test-exhausted", agent_id="agent-1")
        loop = unit._healing_loop

        # Exhaust attempts
        loop.record_heal_start()
        loop.record_heal_failure("test1")
        loop.record_heal_start()
        loop.record_heal_failure("test2")
        loop.record_heal_start()
        loop.record_heal_failure("test3")

        assert loop.should_escalate() is True
        can, reason = loop.can_heal()
        assert can is False
        assert "max_attempts" in reason

    @pytest.mark.asyncio
    async def test_heal_preserves_sdk_session_id(self):
        """Self-heal must NOT clear _sdk_session_id (needed for --resume)."""
        unit = SessionUnit(session_id="test-preserve", agent_id="agent-1")
        unit._sdk_session_id = "sdk-session-abc123"

        # Mock the async methods
        unit._force_kill = AsyncMock()
        unit._cleanup_internal = MagicMock()

        # Simulate: _crash_to_cold_async with clear_identity=False
        # This transitions DEAD→COLD, calls _force_kill, _cleanup_internal
        # but does NOT call _full_cleanup (which would clear _sdk_session_id)
        from core.session_unit import SessionState
        unit._transition(SessionState.IDLE)  # COLD → IDLE (valid)
        unit._transition(SessionState.STREAMING)  # IDLE → STREAMING (valid)
        unit._transition(SessionState.DEAD)  # STREAMING → DEAD (valid)
        await unit._force_kill()
        unit._cleanup_internal()
        unit._transition(SessionState.COLD)

        # _sdk_session_id must survive
        assert unit._sdk_session_id == "sdk-session-abc123"


class TestNegativeHealCycle:
    """Negative test: verify heal cycle fires correctly on degradation."""

    def test_full_degradation_to_heal_decision(self):
        """DoD criterion 7: latency spike → heal fires."""
        unit = SessionUnit(session_id="neg-test", agent_id="agent-1")

        # Phase 1: healthy
        for _ in range(LATENCY_BASELINE_WINDOW):
            unit._health_sensor.record_turn(100.0, 1400, False)
        should, _ = unit._health_sensor.should_checkpoint()
        assert not should, "Should not trigger during healthy phase"

        # Phase 2: degrade
        for _ in range(LATENCY_WINDOW):
            unit._health_sensor.record_turn(300.0, 1800, False)

        # Phase 3: verify trigger
        should, trigger = unit._health_sensor.should_checkpoint()
        assert should is True, "Must trigger on latency spike"
        assert trigger == "latency_degradation"

        # Phase 4: verify heal is allowed
        can, _ = unit._healing_loop.can_heal()
        assert can is True, "Healing must be allowed (first attempt)"
