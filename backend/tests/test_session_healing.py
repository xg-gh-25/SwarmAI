"""Tests for session self-healing module.

Tests HealthSensor trigger detection, TaskCheckpoint serialization,
and HealingLoop state management. Pure unit tests — no subprocess
spawning or SessionUnit integration.
"""

import time

import pytest

from core.session_healing import (
    ERROR_CASCADE_THRESHOLD,
    HANG_TIMEOUT_S,
    HEAL_COOLDOWN_S,
    LATENCY_BASELINE_WINDOW,
    LATENCY_MULTIPLIER,
    LATENCY_WINDOW,
    MAX_HEAL_ATTEMPTS,
    RSS_GROWTH_THRESHOLD_MB,
    RSS_WINDOW,
    TURN_APPROACH_BUFFER,
    HealingLoop,
    HealthSensor,
    TaskCheckpoint,
)


# ─── HealthSensor Tests ──────────────────────────────────────────────────────


class TestHealthSensorLatency:
    """Test latency degradation detection."""

    def test_no_trigger_when_latency_stable(self):
        """Stable latency should not trigger heal."""
        sensor = HealthSensor(max_turns=500)
        # Record 20 turns with stable 100ms latency
        for _ in range(20):
            sensor.record_turn(100.0, 1400, False)
        should, trigger = sensor.should_checkpoint()
        assert not should
        assert trigger == ""

    def test_trigger_on_latency_spike(self):
        """Latency spike (>2.5× baseline) should trigger heal."""
        sensor = HealthSensor(max_turns=500)
        # Baseline: 10 turns at 100ms
        for _ in range(LATENCY_BASELINE_WINDOW):
            sensor.record_turn(100.0, 1400, False)
        # Recent: 5 turns at 300ms (3× baseline > 2.5× threshold)
        for _ in range(LATENCY_WINDOW):
            sensor.record_turn(300.0, 1400, False)
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "latency_degradation"

    def test_no_trigger_with_mild_latency_increase(self):
        """Mild increase (2× baseline, below 2.5× threshold) should not trigger."""
        sensor = HealthSensor(max_turns=500)
        # Baseline: 10 turns at 100ms
        for _ in range(LATENCY_BASELINE_WINDOW):
            sensor.record_turn(100.0, 1400, False)
        # Recent: 5 turns at 200ms (2× baseline, below 2.5× threshold)
        for _ in range(LATENCY_WINDOW):
            sensor.record_turn(200.0, 1400, False)
        should, trigger = sensor.should_checkpoint()
        assert not should

    def test_not_enough_data_no_trigger(self):
        """Too few turns should not trigger (need LATENCY_BASELINE_WINDOW)."""
        sensor = HealthSensor(max_turns=500)
        for _ in range(5):
            sensor.record_turn(500.0, 1400, False)
        should, trigger = sensor.should_checkpoint()
        assert not should

    def test_none_max_turns_uses_default(self):
        """max_turns=None should default to 500 (not crash)."""
        sensor = HealthSensor(max_turns=None)
        assert sensor._max_turns == 500
        # Should not crash on should_checkpoint
        should, trigger = sensor.should_checkpoint()
        assert not should


class TestHealthSensorRSS:
    """Test memory growth detection."""

    def test_trigger_on_rss_growth(self):
        """RSS growth > 400MB over RSS_WINDOW should trigger."""
        sensor = HealthSensor(max_turns=500)
        # Need LATENCY_BASELINE_WINDOW turns to avoid latency check early-exit
        for i in range(RSS_WINDOW):
            rss = 1400 + (i * 50)  # 1400 → 1850 (450MB growth)
            sensor.record_turn(100.0, rss, False)
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "memory_growth"

    def test_no_trigger_with_stable_rss(self):
        """Stable RSS should not trigger."""
        sensor = HealthSensor(max_turns=500)
        for _ in range(RSS_WINDOW):
            sensor.record_turn(100.0, 1500, False)
        should, trigger = sensor.should_checkpoint()
        assert not should


class TestHealthSensorErrors:
    """Test error cascade detection."""

    def test_trigger_on_consecutive_errors(self):
        """N consecutive errors should trigger."""
        sensor = HealthSensor(max_turns=500)
        for _ in range(ERROR_CASCADE_THRESHOLD):
            sensor.record_turn(100.0, 1400, True)
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "error_cascade"

    def test_error_resets_on_success(self):
        """A successful turn resets the error counter."""
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, True)
        sensor.record_turn(100.0, 1400, True)
        sensor.record_turn(100.0, 1400, False)  # success resets
        sensor.record_turn(100.0, 1400, True)
        should, trigger = sensor.should_checkpoint()
        assert not should  # only 1 consecutive error, not 3


class TestHealthSensorTurnLimit:
    """Test turn limit approach detection."""

    def test_trigger_near_max_turns(self):
        """Should trigger when within TURN_APPROACH_BUFFER of max."""
        sensor = HealthSensor(max_turns=100)
        # Record turns until we're at max_turns - TURN_APPROACH_BUFFER
        for _ in range(100 - TURN_APPROACH_BUFFER):
            sensor.record_turn(100.0, 1400, False)
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "turn_approaching"

    def test_no_trigger_far_from_limit(self):
        """Should not trigger when far from max_turns."""
        sensor = HealthSensor(max_turns=500)
        for _ in range(10):
            sensor.record_turn(100.0, 1400, False)
        should, trigger = sensor.should_checkpoint()
        assert not should


class TestHealthSensorHang:
    """Test hang detection."""

    def test_trigger_on_hang(self, monkeypatch):
        """No activity for HANG_TIMEOUT_S should trigger."""
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, False)
        # Simulate time passing
        monkeypatch.setattr(
            time, "time", lambda: sensor._last_activity_time + HANG_TIMEOUT_S + 1
        )
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "hang_detected"

    def test_activity_resets_hang_timer(self):
        """record_activity should reset the hang timer."""
        sensor = HealthSensor(max_turns=500)
        sensor.record_activity()
        should, trigger = sensor.should_checkpoint()
        assert not should


class TestHealthSensorReset:
    """Test sensor reset after heal."""

    def test_reset_clears_signals(self):
        """After reset, no triggers should fire."""
        sensor = HealthSensor(max_turns=500)
        # Build up to a trigger state
        for _ in range(ERROR_CASCADE_THRESHOLD):
            sensor.record_turn(100.0, 1400, True)
        should, _ = sensor.should_checkpoint()
        assert should  # confirm trigger
        # Reset
        sensor.reset()
        should, trigger = sensor.should_checkpoint()
        assert not should
        assert trigger == ""

    def test_reset_clears_turn_count(self):
        """Turn count resets because respawned subprocess has its own counter."""
        sensor = HealthSensor(max_turns=500)
        for _ in range(50):
            sensor.record_turn(100.0, 1400, False)
        assert sensor.turn_count == 50
        sensor.reset()
        assert sensor.turn_count == 0

    def test_no_infinite_loop_after_turn_approaching_heal(self):
        """After heal from turn_approaching, should NOT immediately re-trigger."""
        sensor = HealthSensor(max_turns=100)
        # Drive to turn_approaching trigger
        for _ in range(100 - TURN_APPROACH_BUFFER):
            sensor.record_turn(100.0, 1400, False)
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "turn_approaching"
        # Reset (simulating heal)
        sensor.reset()
        # Should NOT re-trigger (turn_count is now 0)
        should, trigger = sensor.should_checkpoint()
        assert not should


# ─── TaskCheckpoint Tests ─────────────────────────────────────────────────────


class TestTaskCheckpoint:
    """Test checkpoint creation and continuation prompt."""

    def test_minimal_checkpoint(self):
        """Minimal checkpoint with just the request."""
        cp = TaskCheckpoint(original_request="Fix the auth bug")
        prompt = cp.to_continuation_prompt()
        assert "Fix the auth bug" in prompt
        assert "Task Continuation" in prompt
        assert "Do not acknowledge the refresh" in prompt

    def test_full_checkpoint(self):
        """Full checkpoint with all fields."""
        cp = TaskCheckpoint(
            original_request="Implement session healing",
            completed_steps=["Created module", "Wrote tests"],
            pending_steps=["Frontend changes"],
            files_modified=["session_healing.py"],
            uncommitted_changes="2 files changed, +200/-0",
            pipeline_run_id="run_abc123",
            pipeline_stage="build",
            key_findings="HealthSensor detects 5 trigger types",
            trigger="latency_degradation",
            turn_count=200,
            heal_attempt=1,
        )
        prompt = cp.to_continuation_prompt()
        assert "Implement session healing" in prompt
        assert "Created module" in prompt
        assert "Frontend changes" in prompt
        assert "2 files changed" in prompt
        assert "run_abc123" in prompt

    def test_checkpoint_is_immutable(self):
        """TaskCheckpoint should be frozen (immutable)."""
        cp = TaskCheckpoint(original_request="test")
        with pytest.raises(Exception):
            cp.original_request = "modified"  # type: ignore[misc]


# ─── HealingLoop Tests ────────────────────────────────────────────────────────


class TestHealingLoop:
    """Test healing loop state management."""

    def test_can_heal_initially(self):
        """Fresh loop should allow healing."""
        loop = HealingLoop()
        can, reason = loop.can_heal()
        assert can
        assert reason == ""

    def test_max_attempts_blocks_heal(self):
        """After MAX_HEAL_ATTEMPTS, healing should be blocked."""
        loop = HealingLoop()
        for _ in range(MAX_HEAL_ATTEMPTS):
            loop.record_heal_start()
        can, reason = loop.can_heal()
        assert not can
        assert "max_attempts" in reason

    def test_cooldown_blocks_heal(self, monkeypatch):
        """Healing within cooldown period should be blocked."""
        loop = HealingLoop()
        loop.record_heal_start()
        loop.record_heal_success()  # reset attempts
        # Try to heal again immediately
        can, reason = loop.can_heal()
        assert not can
        assert "cooldown" in reason

    def test_cooldown_expires(self, monkeypatch):
        """After cooldown expires, healing should be allowed."""
        loop = HealingLoop()
        loop.record_heal_start()
        loop.record_heal_success()
        # Simulate cooldown expiry
        loop._last_heal_time = time.time() - HEAL_COOLDOWN_S - 1
        can, reason = loop.can_heal()
        assert can

    def test_success_resets_attempts(self):
        """Successful heal should reset attempt counter."""
        loop = HealingLoop()
        loop.record_heal_start()
        loop.record_heal_start()
        assert loop.heal_attempts == 2
        loop.record_heal_success()
        assert loop.heal_attempts == 0

    def test_total_heals_persists(self):
        """Total heals should accumulate across resets."""
        loop = HealingLoop()
        loop.record_heal_start()
        loop.record_heal_success()
        loop._last_heal_time = 0  # bypass cooldown
        loop.record_heal_start()
        loop.record_heal_success()
        assert loop.total_heals == 2

    def test_should_escalate(self):
        """After max attempts, should escalate to user."""
        loop = HealingLoop()
        for _ in range(MAX_HEAL_ATTEMPTS):
            loop.record_heal_start()
        assert loop.should_escalate()

    def test_should_not_escalate_before_max(self):
        """Before max attempts, should not escalate."""
        loop = HealingLoop()
        loop.record_heal_start()
        assert not loop.should_escalate()


# ─── Integration-style Tests ─────────────────────────────────────────────────


class TestSensorHealIntegration:
    """Test the sensor→heal decision flow (no actual subprocess)."""

    def test_full_degradation_flow(self):
        """Simulate a session degrading: healthy → latency spike → heal decision."""
        sensor = HealthSensor(max_turns=500)
        loop = HealingLoop()

        # Phase 1: Healthy operation (100 turns)
        for _ in range(100):
            sensor.record_turn(100.0, 1400, False)
            should, _ = sensor.should_checkpoint()
            assert not should

        # Phase 2: Latency starts climbing (inference slowing)
        for _ in range(LATENCY_WINDOW):
            sensor.record_turn(300.0, 1500, False)

        # Should now trigger
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "latency_degradation"

        # Healing loop says: yes, go ahead
        can, _ = loop.can_heal()
        assert can

        # Execute heal
        loop.record_heal_start()
        # ... (actual kill/respawn would happen here) ...
        loop.record_heal_success()
        sensor.reset()

        # After heal: back to healthy
        sensor.record_turn(100.0, 1200, False)
        should, _ = sensor.should_checkpoint()
        assert not should

    def test_negative_latency_spike_detection(self):
        """DoD criterion 7: latency spike correctly triggers checkpoint."""
        sensor = HealthSensor(max_turns=500)
        # Baseline
        for _ in range(LATENCY_BASELINE_WINDOW):
            sensor.record_turn(100.0, 1400, False)
        # Spike
        for _ in range(LATENCY_WINDOW):
            sensor.record_turn(300.0, 1900, False)
        should, trigger = sensor.should_checkpoint()
        assert should is True
        assert trigger == "latency_degradation"
