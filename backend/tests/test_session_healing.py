"""Tests for session self-healing module.

Tests HealthSensor trigger detection, TaskCheckpoint serialization,
HealingLoop state management, rich checkpoint population, graceful
pre-kill injection, and canary mode gating. Pure unit tests — no
subprocess spawning or SessionUnit integration.
"""

import inspect
import time
from unittest.mock import AsyncMock, patch

import pytest

from core.session_healing import (
    CHANNEL_MAX_TURNS,
    DESKTOP_MAX_TURNS,
    ERROR_CASCADE_THRESHOLD,
    HANG_TIMEOUT_S,
    HEAL_COOLDOWN_S,
    MAX_HEAL_ATTEMPTS,
    RSS_WINDOW,
    TURN_APPROACH_BUFFER,
    WRAP_UP_PROMPT,
    HealingLoop,
    HealthSensor,
    TaskCheckpoint,
    build_rich_checkpoint,
    parse_self_heal_mode,
)


# ─── HealthSensor Tests ──────────────────────────────────────────────────────


class TestHealthSensorLatency:
    """Latency degradation was REMOVED (run_099724ca).

    The `latency_degradation` self-heal signal force-killed healthy IDLE
    sessions between turns based purely on RELATIVE completed-turn latency
    (recent-5 avg > 2.5x opening-10 avg). Its kill->--resume response made
    latency WORSE (replays full context, 2x multiplier), and every real cause
    of rising latency already has a correct owner (context-bloat->soft-compact,
    memory->RSS-restart, legit-heavier-work->no action). See EVOLUTION/DDD.
    These tests pin that a latency shape NO LONGER triggers a heal.
    """

    def test_latency_spike_no_longer_triggers(self):
        """The 00e5ba40 shape (10 fast turns then 5 turns at 3x) must NOT heal.

        This is the exact false-kill: healthy session, latency rose because the
        later turns were legitimately heavier work. Before the fix this returned
        (True, "latency_degradation"); after removal it must NOT.
        """
        sensor = HealthSensor(max_turns=500)
        # Baseline: 10 turns at 100ms
        for _ in range(10):
            sensor.record_turn(100.0, 1400, False)
        # Recent: 5 turns at 300ms (3x baseline — would have been >2.5x trigger)
        for _ in range(5):
            sensor.record_turn(300.0, 1400, False)
        should, trigger = sensor.should_checkpoint()
        assert trigger != "latency_degradation"
        # With stable RSS + no errors + turns far from the limit, nothing fires.
        assert not should
        assert trigger == ""

    def test_extreme_latency_still_no_trigger(self):
        """Even a 10x latency blowup (still SSE-alive) must not force a kill.

        A genuinely slow-but-progressing turn is not a hang; hang_detected
        (300s silence) + turn floors are the real safety nets, not latency.
        """
        sensor = HealthSensor(max_turns=500)
        for _ in range(10):
            sensor.record_turn(50.0, 1400, False)
        for _ in range(5):
            sensor.record_turn(500.0, 1400, False)  # 10x
        should, trigger = sensor.should_checkpoint()
        assert trigger != "latency_degradation"
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


class TestHealthSensorMaxTurnsSync:
    """Test max_turns threshold sourcing + post-construction sync.

    Regression guard for the max_turns propagation bug: HealthSensor was
    hardcoded to 500 while channel sessions actually run the CLI at 100, so
    turn_approaching fired at 480 — beyond the 100 the CLI enforces — i.e.
    structurally unreachable. The fix sources the default from DESKTOP_MAX_TURNS
    and adds set_max_turns() so SessionRouter can sync channel sessions to 100.
    """

    def test_platform_constants_values(self):
        """AC1/AC3: platform constants have the expected values."""
        assert DESKTOP_MAX_TURNS == 500
        assert CHANNEL_MAX_TURNS == 100

    def test_prompt_builder_shares_the_same_constants(self):
        """Drift guard: prompt_builder MUST import the SAME constants, not its
        own literals. Otherwise the CLI limit and the heal threshold can drift
        apart silently — re-introducing the unreachable-trigger bug class.
        """
        from core import prompt_builder

        src = inspect.getsource(prompt_builder.PromptBuilder.build_options)
        # The max_turns block must reference the named constants, never bare
        # 100/500 literals for the platform defaults.
        assert "CHANNEL_MAX_TURNS" in src, (
            "prompt_builder must use CHANNEL_MAX_TURNS, not a literal 100"
        )
        assert "DESKTOP_MAX_TURNS" in src, (
            "prompt_builder must use DESKTOP_MAX_TURNS, not a literal 500"
        )

    def test_default_uses_desktop_constant(self):
        """AC3: a fresh desktop sensor must threshold at DESKTOP_MAX_TURNS (500)."""
        sensor = HealthSensor()
        assert sensor._max_turns == DESKTOP_MAX_TURNS

    def test_set_max_turns_makes_channel_threshold_reachable(self):
        """AC2: after set_max_turns(100), turn_approaching fires at 100-buffer.

        This is the core bug: before the fix the threshold stayed at 500 so a
        channel session (real limit 100) could never reach turn_approaching.
        Force execution of the heal path at the channel boundary.
        """
        sensor = HealthSensor(max_turns=DESKTOP_MAX_TURNS)
        sensor.set_max_turns(CHANNEL_MAX_TURNS)
        assert sensor._max_turns == CHANNEL_MAX_TURNS
        # Record turns up to the channel approach boundary (100 - 20 = 80)
        for _ in range(CHANNEL_MAX_TURNS - TURN_APPROACH_BUFFER):
            sensor.record_turn(100.0, 1400, False)
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "turn_approaching"

    def test_set_max_turns_no_premature_trigger_below_boundary(self):
        """AC2: one turn before the channel boundary must NOT trigger."""
        sensor = HealthSensor(max_turns=DESKTOP_MAX_TURNS)
        sensor.set_max_turns(CHANNEL_MAX_TURNS)
        # 79 turns: still below 80 boundary
        for _ in range(CHANNEL_MAX_TURNS - TURN_APPROACH_BUFFER - 1):
            sensor.record_turn(100.0, 1400, False)
        should, trigger = sensor.should_checkpoint()
        assert not should

    def test_reset_still_zeroes_turn_count_after_sync(self):
        """AC4: reset() must still zero turn_count even after set_max_turns().

        The heal regression guard: respawned subprocess = fresh turn counter.
        Preserving count would re-trigger turn_approaching immediately (infinite
        loop). Verify the setter doesn't break reset semantics.
        """
        sensor = HealthSensor(max_turns=DESKTOP_MAX_TURNS)
        sensor.set_max_turns(CHANNEL_MAX_TURNS)
        for _ in range(50):
            sensor.record_turn(100.0, 1400, False)
        assert sensor.turn_count == 50
        sensor.reset()
        assert sensor.turn_count == 0
        # Threshold persists across reset (reset only clears per-subprocess counters)
        assert sensor._max_turns == CHANNEL_MAX_TURNS


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

    def test_hang_suppressed_during_waiting_input(self, monkeypatch):
        """hang_detected must NOT fire when session is in WAITING_INPUT.

        The user may take arbitrarily long to answer a permission prompt.
        Killing the session during WAITING_INPUT causes the 'streaming stops
        mid-response' bug that forces users to re-send their message.
        """
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, False)
        # Simulate time passing well beyond hang threshold
        monkeypatch.setattr(
            time, "time", lambda: sensor._last_activity_time + HANG_TIMEOUT_S + 60
        )
        # Without state hint → hang fires (backward compat)
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "hang_detected"
        # With waiting_input state → suppressed
        should, trigger = sensor.should_checkpoint(session_state="waiting_input")
        assert not should
        assert trigger == ""

    def test_hang_suppressed_during_streaming(self, monkeypatch):
        """hang_detected must NOT fire when session is in STREAMING.

        Extended thinking can take 5-10 minutes without SDK events.
        """
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, False)
        monkeypatch.setattr(
            time, "time", lambda: sensor._last_activity_time + HANG_TIMEOUT_S + 60
        )
        should, trigger = sensor.should_checkpoint(session_state="streaming")
        assert not should
        assert trigger == ""


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
            key_findings="HealthSensor detects 4 trigger types",
            trigger="memory_growth",
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
        """Simulate a session degrading: healthy → error cascade → heal decision.

        (Was a latency-spike flow; latency_degradation was removed in
        run_099724ca. error_cascade is the stable trigger vehicle for the
        healthy→degrade→heal→reset→healthy cycle.)
        """
        sensor = HealthSensor(max_turns=500)
        loop = HealingLoop()

        # Phase 1: Healthy operation (100 turns)
        for _ in range(100):
            sensor.record_turn(100.0, 1400, False)
            should, _ = sensor.should_checkpoint()
            assert not should

        # Phase 2: Errors start cascading
        for _ in range(ERROR_CASCADE_THRESHOLD):
            sensor.record_turn(300.0, 1500, True)

        # Should now trigger
        should, trigger = sensor.should_checkpoint()
        assert should
        assert trigger == "error_cascade"

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

    def test_negative_error_cascade_detection(self):
        """A real trigger (error_cascade) correctly triggers checkpoint.

        (Was test_negative_latency_spike_detection — latency signal removed.)
        """
        sensor = HealthSensor(max_turns=500)
        for _ in range(ERROR_CASCADE_THRESHOLD):
            sensor.record_turn(300.0, 1900, True)
        should, trigger = sensor.should_checkpoint()
        assert should is True
        assert trigger == "error_cascade"


# ─── Rich Checkpoint Tests ──────────────────────────────────────────────────


class TestBuildRichCheckpoint:
    """Test build_rich_checkpoint() populates all fields from git + context."""

    @pytest.mark.asyncio
    async def test_fills_files_modified_from_git(self):
        """AC1: files_modified populated from git diff --name-only."""
        mock_git_output = "backend/core/session_healing.py\nbackend/core/session_unit.py\n"
        with patch(
            "core.session_healing._run_git_command_async",
            new_callable=AsyncMock,
            side_effect=[mock_git_output, "M session_healing.py\nM session_unit.py\n"],
        ):
            cp = await build_rich_checkpoint(
                original_request="Fix the auth bug",
                working_dir="/tmp/test",
                file_tracker_paths=["auth.py", "config.py"],
            )
        # git diff --name-only returns full relative paths
        assert any("session_healing.py" in f for f in cp.files_modified)
        assert any("session_unit.py" in f for f in cp.files_modified)
        assert len(cp.files_modified) == 2

    @pytest.mark.asyncio
    async def test_fills_uncommitted_changes(self):
        """AC1: uncommitted_changes populated from git status."""
        with patch(
            "core.session_healing._run_git_command_async",
            new_callable=AsyncMock,
            side_effect=["file1.py\nfile2.py\n", "M file1.py\nA file2.py\n"],
        ):
            cp = await build_rich_checkpoint(
                original_request="Add feature X",
                working_dir="/tmp/test",
            )
        assert cp.uncommitted_changes != ""
        assert "file1.py" in cp.uncommitted_changes

    @pytest.mark.asyncio
    async def test_includes_file_tracker_paths(self):
        """AC1: file_tracker_paths included in key_findings."""
        with patch(
            "core.session_healing._run_git_command_async",
            new_callable=AsyncMock,
            return_value="",
        ):
            cp = await build_rich_checkpoint(
                original_request="Debug the issue",
                working_dir="/tmp/test",
                file_tracker_paths=["router.py", "hooks.py", "models.py"],
            )
        assert "router.py" in cp.key_findings
        assert "hooks.py" in cp.key_findings

    @pytest.mark.asyncio
    async def test_graceful_on_git_failure(self):
        """Should not crash if git commands fail."""
        with patch(
            "core.session_healing._run_git_command_async",
            new_callable=AsyncMock,
            side_effect=Exception("git not found"),
        ):
            cp = await build_rich_checkpoint(
                original_request="Do something",
                working_dir="/tmp/test",
            )
        # Should still have the original request
        assert cp.original_request == "Do something"
        assert cp.files_modified == []

    @pytest.mark.asyncio
    async def test_continuation_prompt_has_real_content(self):
        """AC1: to_continuation_prompt() has 5+ lines of real context."""
        with patch(
            "core.session_healing._run_git_command_async",
            new_callable=AsyncMock,
            side_effect=["a.py\nb.py\n", "M a.py\nM b.py\n"],
        ):
            cp = await build_rich_checkpoint(
                original_request="Implement session healing",
                working_dir="/tmp/test",
                file_tracker_paths=["c.py"],
                turn_count=150,
                trigger="memory_growth",
            )
        prompt = cp.to_continuation_prompt()
        lines = [l for l in prompt.split("\n") if l.strip()]
        assert len(lines) >= 5
        assert "Implement session healing" in prompt
        assert "a.py" in prompt


# ─── Graceful Pre-Kill Tests ────────────────────────────────────────────────


class TestWrapUpPrompt:
    """Test the graceful pre-kill wrap-up prompt."""

    def test_wrap_up_prompt_exists_and_nonempty(self):
        """AC2: WRAP_UP_PROMPT constant is defined and meaningful."""
        assert WRAP_UP_PROMPT
        assert len(WRAP_UP_PROMPT) > 50
        assert "wrap" in WRAP_UP_PROMPT.lower() or "finish" in WRAP_UP_PROMPT.lower()

    def test_wrap_up_prompt_instructs_agent(self):
        """AC2: Wrap-up prompt gives clear instruction to finish current work."""
        assert "continue" in WRAP_UP_PROMPT.lower() or "checkpoint" in WRAP_UP_PROMPT.lower()


# ─── Canary Mode Tests ──────────────────────────────────────────────────────


class TestCanaryMode:
    """Test the 3-mode SWARMAI_SELF_HEAL parsing."""

    def test_parse_mode_off(self):
        """'0' means off."""
        assert parse_self_heal_mode("0") == "off"

    def test_parse_mode_all(self):
        """'1' means all sessions."""
        assert parse_self_heal_mode("1") == "all"

    def test_parse_mode_canary(self):
        """'canary' means first non-channel session only."""
        assert parse_self_heal_mode("canary") == "canary"

    def test_parse_mode_empty(self):
        """Empty string defaults to off."""
        assert parse_self_heal_mode("") == "off"

    def test_parse_mode_invalid(self):
        """Invalid values default to off (safe default)."""
        assert parse_self_heal_mode("yes") == "off"
        assert parse_self_heal_mode("true") == "off"


# ─── Whitelist Hang Detection Tests ─────────────────────────────────────────


class TestHangDetectionWhitelist:
    """Verify hang detection uses whitelist (only IDLE/COLD/None trigger)."""

    def test_idle_triggers_hang(self, monkeypatch):
        """IDLE state should trigger hang_detected after timeout."""
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, False)
        monkeypatch.setattr(
            time, "time", lambda: sensor._last_activity_time + HANG_TIMEOUT_S + 60
        )
        should, trigger = sensor.should_checkpoint(session_state="idle")
        assert should
        assert trigger == "hang_detected"

    def test_cold_triggers_hang(self, monkeypatch):
        """COLD state should trigger hang_detected after timeout."""
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, False)
        monkeypatch.setattr(
            time, "time", lambda: sensor._last_activity_time + HANG_TIMEOUT_S + 60
        )
        should, trigger = sensor.should_checkpoint(session_state="cold")
        assert should
        assert trigger == "hang_detected"

    def test_none_triggers_hang_backward_compat(self, monkeypatch):
        """None state (no arg) should trigger — backward compatibility."""
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, False)
        monkeypatch.setattr(
            time, "time", lambda: sensor._last_activity_time + HANG_TIMEOUT_S + 60
        )
        should, trigger = sensor.should_checkpoint(session_state=None)
        assert should
        assert trigger == "hang_detected"

    def test_streaming_never_triggers_hang(self, monkeypatch):
        """STREAMING state must NEVER trigger hang_detected."""
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, False)
        monkeypatch.setattr(
            time, "time", lambda: sensor._last_activity_time + HANG_TIMEOUT_S + 600
        )
        should, trigger = sensor.should_checkpoint(session_state="streaming")
        assert not should or trigger != "hang_detected"

    def test_waiting_input_never_triggers_hang(self, monkeypatch):
        """WAITING_INPUT state must NEVER trigger hang_detected."""
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, False)
        monkeypatch.setattr(
            time, "time", lambda: sensor._last_activity_time + HANG_TIMEOUT_S + 600
        )
        should, trigger = sensor.should_checkpoint(session_state="waiting_input")
        assert not should or trigger != "hang_detected"

    def test_dead_never_triggers_hang(self, monkeypatch):
        """DEAD state must NEVER trigger hang_detected (nothing to heal)."""
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, False)
        monkeypatch.setattr(
            time, "time", lambda: sensor._last_activity_time + HANG_TIMEOUT_S + 600
        )
        should, trigger = sensor.should_checkpoint(session_state="dead")
        assert not should or trigger != "hang_detected"

    def test_unknown_future_state_never_triggers_hang(self, monkeypatch):
        """Any unknown/future state must NOT trigger hang_detected (whitelist)."""
        sensor = HealthSensor(max_turns=500)
        sensor.record_turn(100.0, 1400, False)
        monkeypatch.setattr(
            time, "time", lambda: sensor._last_activity_time + HANG_TIMEOUT_S + 600
        )
        should, trigger = sensor.should_checkpoint(session_state="some_future_state")
        assert not should or trigger != "hang_detected"

    def test_all_session_states_accounted_for(self, monkeypatch):
        """Every SessionState value must be explicitly covered by hang detection.

        This test FAILS if a new state is added to SessionState without updating
        the whitelist in should_checkpoint(). Prevents silent detection gaps.
        """
        from core.session_unit import SessionState

        # States where hang_detected SHOULD fire
        detect_states = {"idle", "cold"}
        # States where hang_detected MUST NOT fire (have their own liveness)
        excluded_states = {"streaming", "waiting_input", "dead"}
        # None = backward compat (no state passed)

        all_states = {s.value for s in SessionState}
        covered = detect_states | excluded_states
        uncovered = all_states - covered

        assert not uncovered, (
            f"New SessionState(s) {uncovered} added but not accounted for in "
            f"hang detection whitelist (session_healing.py). Add them to either "
            f"detect_states or excluded_states in this test AND update "
            f"should_checkpoint() accordingly."
        )


# ─── Observability Tests ─────────────────────────────────────────────────────


class TestHealingLoopObservability:
    """Verify observability counters and structured logging."""

    def test_trigger_counts_tracked(self):
        """record_heal_start with trigger name increments per-trigger count."""
        loop = HealingLoop()
        loop.record_heal_start(trigger="hang_detected")
        loop.record_heal_start(trigger="hang_detected")
        loop.record_heal_start(trigger="memory_growth")
        assert loop.trigger_counts == {"hang_detected": 2, "memory_growth": 1}

    def test_recent_triggers_capped_at_20(self):
        """recent_triggers deque never exceeds 20 entries."""
        loop = HealingLoop()
        # Reset attempts counter between calls to avoid max_attempts
        for i in range(25):
            loop._heal_attempts = 0  # Reset to allow more heals
            loop.record_heal_start(trigger=f"trigger_{i}")
        assert len(loop.recent_triggers) == 20
        # Most recent should be trigger_24
        assert loop.recent_triggers[-1][1] == "trigger_24"

    def test_trigger_counts_empty_trigger_not_tracked(self):
        """Empty trigger string should not be tracked."""
        loop = HealingLoop()
        loop.record_heal_start(trigger="")
        assert loop.trigger_counts == {}
        assert len(loop.recent_triggers) == 0

    def test_total_heals_still_increments(self):
        """total_heals increments regardless of trigger tracking."""
        loop = HealingLoop()
        loop.record_heal_start(trigger="test_trigger")
        assert loop.total_heals == 1
        loop._heal_attempts = 0  # Allow next
        loop.record_heal_start(trigger="")
        assert loop.total_heals == 2
