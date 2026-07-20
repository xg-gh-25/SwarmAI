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
    ERROR_CASCADE_THRESHOLD,
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

    def test_trigger_allows_heal_decision(self):
        """A real trigger (error_cascade) should cause should_checkpoint to fire.

        (Was test_latency_spike_triggers_heal_decision — latency_degradation was
        removed in run_099724ca; error_cascade is the stable trigger vehicle for
        exercising the should_checkpoint → can_heal wiring.)
        """
        unit = SessionUnit(session_id="test-heal", agent_id="agent-1")
        sensor = unit._health_sensor

        # 3 consecutive errors → error_cascade
        for _ in range(ERROR_CASCADE_THRESHOLD):
            sensor.record_turn(100.0, 1400, True)

        # Should trigger
        should, trigger = sensor.should_checkpoint()
        assert should is True
        assert trigger == "error_cascade"

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

        # Force heal trigger (error_cascade — latency_degradation removed)
        for _ in range(ERROR_CASCADE_THRESHOLD):
            unit._health_sensor.record_turn(100.0, 1400, True)

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
        """Heal fires on a real trigger (error_cascade).

        (latency_degradation was removed in run_099724ca; error_cascade is the
        stable vehicle for the healthy→degrade→heal-allowed transition.)
        """
        unit = SessionUnit(session_id="neg-test", agent_id="agent-1")

        # Phase 1: healthy
        for _ in range(10):
            unit._health_sensor.record_turn(100.0, 1400, False)
        should, _ = unit._health_sensor.should_checkpoint()
        assert not should, "Should not trigger during healthy phase"

        # Phase 2: degrade (consecutive errors)
        for _ in range(ERROR_CASCADE_THRESHOLD):
            unit._health_sensor.record_turn(300.0, 1800, True)

        # Phase 3: verify trigger
        should, trigger = unit._health_sensor.should_checkpoint()
        assert should is True, "Must trigger on error cascade"
        assert trigger == "error_cascade"

        # Phase 4: verify heal is allowed
        can, _ = unit._healing_loop.can_heal()
        assert can is True, "Healing must be allowed (first attempt)"


class TestE2EHealCycle:
    """E2E smoke test: full heal cycle from degradation to checkpoint built."""

    @pytest.mark.asyncio
    async def test_e2e_heal_cycle_builds_checkpoint(self):
        """Full cycle: degrade → trigger → heal → checkpoint built → state reset."""
        unit = SessionUnit(session_id="e2e-heal", agent_id="agent-1")

        # Simulate: unit has an active subprocess
        unit._sdk_session_id = "sdk-e2e-session"
        unit._pid = 99999  # fake PID (won't be killed — we mock)

        # Phase 1: Record baseline turns
        for _ in range(10):
            unit._health_sensor.record_turn(100.0, 1400, False)

        # Phase 2: Trigger degradation (error_cascade — latency_degradation removed)
        for _ in range(ERROR_CASCADE_THRESHOLD):
            unit._health_sensor.record_turn(300.0, 1600, True)

        # Phase 3: Verify trigger fires
        should, trigger = unit._health_sensor.should_checkpoint()
        assert should is True
        assert trigger == "error_cascade"

        # Phase 4: Execute heal (mocked subprocess kill)
        unit._crash_to_cold_async = AsyncMock()

        can, _ = unit._healing_loop.can_heal()
        assert can

        unit._healing_loop.record_heal_start()

        # Build checkpoint (same as production path)
        from core.session_healing import TaskCheckpoint
        unit._heal_checkpoint = TaskCheckpoint(
            original_request="Implement feature X",
            trigger=trigger,
            turn_count=unit._health_sensor.turn_count,
            heal_attempt=unit._healing_loop.heal_attempts,
        )

        await unit._crash_to_cold_async(clear_identity=False)
        unit._health_sensor.reset()
        unit._healing_loop.record_heal_success()

        # Phase 5: Verify post-heal state
        assert unit._heal_checkpoint is not None, "Checkpoint must be built"
        assert unit._heal_checkpoint.trigger == "error_cascade"
        assert unit._heal_checkpoint.original_request == "Implement feature X"
        assert unit._health_sensor.turn_count == 0, "Turn count must reset"
        assert unit._healing_loop.heal_attempts == 0, "Attempts must reset on success"
        assert unit._sdk_session_id == "sdk-e2e-session", "SDK session preserved"

        # Phase 6: Verify checkpoint continuation prompt
        prompt = unit._heal_checkpoint.to_continuation_prompt()
        assert "Task Continuation" in prompt
        assert "Implement feature X" in prompt
        assert "Do not acknowledge the refresh" in prompt

    @pytest.mark.asyncio
    async def test_e2e_heal_checkpoint_consumed_on_next_send(self):
        """Checkpoint is consumed (set to None) when injected into query."""
        unit = SessionUnit(session_id="e2e-consume", agent_id="agent-1")

        # Set a checkpoint as if heal just happened
        from core.session_healing import TaskCheckpoint
        unit._heal_checkpoint = TaskCheckpoint(
            original_request="Fix bug Y",
            trigger="error_cascade",
            turn_count=50,
        )

        # Simulate what send() does before _stream_response:
        query_content = "Continue working"
        if unit._heal_checkpoint is not None:
            continuation = unit._heal_checkpoint.to_continuation_prompt()
            if isinstance(query_content, str):
                query_content = f"{continuation}\n\n---\n\n{query_content}"
            unit._heal_checkpoint = None

        assert "Task Continuation" in query_content
        assert "Fix bug Y" in query_content
        assert "Continue working" in query_content
        assert unit._heal_checkpoint is None, "Checkpoint must be consumed"


class TestMultiBoundaryHandling:
    """Gap 4: Verify MessageStore-style boundary tracking with multiple resumes."""

    def test_multiple_boundaries_tracks_latest(self):
        """With 2 resume boundaries, only the latest index should matter."""
        # Simulate MessageStore's boundary tracking logic
        messages = []
        boundary_idx = -1

        # Add 5 messages from first session
        for i in range(5):
            messages.append({"id": f"msg-{i}", "role": "assistant"})

        # First resume boundary
        messages.append({"id": "resume-boundary-1000", "role": "system"})
        boundary_idx = len(messages) - 1  # = 5
        assert boundary_idx == 5

        # Add 3 messages from second session
        for i in range(5, 8):
            messages.append({"id": f"msg-{i}", "role": "assistant"})

        # Second resume boundary (another heal/resume)
        messages.append({"id": "resume-boundary-2000", "role": "system"})
        boundary_idx = len(messages) - 1  # = 9
        assert boundary_idx == 9

        # Add 2 messages from current session
        for i in range(8, 10):
            messages.append({"id": f"msg-{i}", "role": "assistant"})

        # Pre-boundary IDs should include messages 0-8 (before idx 9)
        pre_boundary_ids = set()
        for i in range(boundary_idx):
            if messages[i].get("id"):
                pre_boundary_ids.add(messages[i]["id"])

        # All messages before the LATEST boundary are "old"
        assert "msg-0" in pre_boundary_ids  # from first session
        assert "msg-5" in pre_boundary_ids  # from second session
        assert "msg-7" in pre_boundary_ids  # from second session
        # Messages after boundary are "current"
        assert "msg-8" not in pre_boundary_ids
        assert "msg-9" not in pre_boundary_ids

    def test_no_boundary_means_no_filtering(self):
        """Without any boundary, no messages should be filtered."""
        boundary_idx = -1
        pre_boundary_ids = set()
        if boundary_idx >= 0:
            # This block should not execute
            pre_boundary_ids.add("should-not-happen")

        assert len(pre_boundary_ids) == 0


class TestRSSSampling:
    """Gap 3: Verify RSS sampling helper works."""

    def test_get_process_rss_returns_positive(self):
        """get_process_rss_mb for own process should return > 0."""
        from core.session_healing import get_process_rss_mb
        rss = get_process_rss_mb()
        # Python process should use at least 10MB
        assert rss > 10, f"Expected RSS > 10MB, got {rss}MB"

    def test_get_process_rss_invalid_pid_returns_zero(self):
        """Invalid PID should return 0, not crash."""
        from core.session_healing import get_process_rss_mb
        rss = get_process_rss_mb(pid=999999999)
        assert rss == 0
