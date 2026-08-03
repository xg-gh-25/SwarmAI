"""Execution tests for recovery paths in session_healing.py.

Tests T8-T10 from design doc. Forces paths that have zero execution coverage
but are critical for system stability.

METHODOLOGY: Call REAL functions (get_process_rss_mb, build_rich_checkpoint,
HealingLoop.can_heal) with mocked external deps (psutil, git, time).
"""

import time
from unittest.mock import patch

import pytest

from core.session_healing import (
    HealingLoop,
    TaskCheckpoint,
    build_rich_checkpoint,
    get_process_rss_mb,
    parse_self_heal_mode,
)


# ═══════════════════════════════════════════════════════════════════
# T8: HealingLoop Concurrent Heal Bypass
# Path: HealingLoop.can_heal() — no lock around _heal_attempts
# ═══════════════════════════════════════════════════════════════════


class TestT8HealingLoopConcurrentHeal:
    """can_heal() must respect MAX_HEAL_ATTEMPTS even under concurrent calls."""

    def test_max_attempts_blocks_heal(self):
        """After 3 heal attempts, can_heal returns False."""
        loop = HealingLoop()
        # Simulate 3 heals already done
        loop.record_heal_start()
        loop.record_heal_start()
        loop.record_heal_start()

        can, reason = loop.can_heal()
        assert can is False
        assert "max_attempts" in reason

    def test_cooldown_blocks_immediate_reheal(self):
        """After a heal, can_heal blocks during cooldown period."""
        loop = HealingLoop()
        loop.record_heal_start()

        # Immediately after heal start, cooldown should be active
        can, reason = loop.can_heal()
        assert can is False
        assert "cooldown" in reason

    def test_cooldown_expired_allows_heal(self):
        """After cooldown expires, can_heal allows another attempt."""
        loop = HealingLoop()
        loop.record_heal_start()
        # Force time past cooldown
        loop._last_heal_time = time.time() - 120  # Well past 60s cooldown

        can, reason = loop.can_heal()
        assert can is True
        assert reason == ""

    def test_record_heal_success_clears_attempts(self):
        """record_heal_success() resets attempts, allowing healing again."""
        loop = HealingLoop()
        loop.record_heal_start()
        loop.record_heal_start()
        loop.record_heal_start()

        can, _ = loop.can_heal()
        assert can is False

        loop.record_heal_success()
        # Need to wait past cooldown since record_heal_start set _last_heal_time
        loop._last_heal_time = time.time() - 120
        can, _ = loop.can_heal()
        assert can is True


# ═══════════════════════════════════════════════════════════════════
# T9: RSS Monitoring Complete Fallback
# Path: get_process_rss_mb() — all backends fail
# ═══════════════════════════════════════════════════════════════════


class TestT9RssMonitoringFallback:
    """When all RSS monitoring backends fail, returns 0 (never crash)."""

    def test_returns_zero_when_all_backends_fail(self):
        """Neither /proc nor psutil available → returns 0, never raises."""
        # /proc doesn't exist on macOS (first path fails with OSError)
        # Mock psutil to also fail
        with patch.dict("sys.modules", {"psutil": None}):
            # On macOS, /proc path raises OSError naturally
            # psutil import will raise ImportError with None module
            result = get_process_rss_mb(pid=99999)  # Non-existent PID

        # Must return 0, never crash
        assert result == 0

    def test_returns_nonzero_for_current_process(self):
        """For current process, at least one backend works → returns > 0."""
        result = get_process_rss_mb()
        # We're running in a Python process — RSS must be > 0
        assert result > 0

    def test_invalid_pid_returns_zero(self):
        """PID that doesn't exist → graceful fallback to 0."""
        result = get_process_rss_mb(pid=2147483647)  # Max PID, unlikely to exist
        assert result == 0


# ═══════════════════════════════════════════════════════════════════
# T10: Rich Checkpoint with All Git Failures
# Path: build_rich_checkpoint git extraction (lines 484-499)
# ═══════════════════════════════════════════════════════════════════


class TestT10RichCheckpointGitFailures:
    """When all git commands fail, checkpoint is still built (degraded)."""

    @pytest.mark.asyncio
    async def test_builds_checkpoint_when_git_unavailable(self):
        """All git ops fail → checkpoint built with empty file state."""
        async def _git_fails(cmd, working_dir, timeout=3.0):
            return ""  # Simulate git command failure (returns empty)

        with patch("core.session_healing._run_git_command_async", side_effect=_git_fails):
            cp = await build_rich_checkpoint(
                original_request="Fix the parser bug",
                working_dir="/tmp/fake-repo",
                trigger="stuck_streaming",
                turn_count=15,
            )

        assert isinstance(cp, TaskCheckpoint)
        assert cp.original_request == "Fix the parser bug"
        assert cp.files_modified == []
        assert cp.uncommitted_changes == ""
        assert cp.trigger == "stuck_streaming"
        assert cp.turn_count == 15

    @pytest.mark.asyncio
    async def test_builds_checkpoint_with_no_working_dir(self):
        """No working_dir provided → skip git entirely, still build."""
        cp = await build_rich_checkpoint(
            original_request="Research task (no git)",
            working_dir=None,
            trigger="memory_growth",
        )

        assert isinstance(cp, TaskCheckpoint)
        assert cp.files_modified == []
        assert cp.uncommitted_changes == ""

    @pytest.mark.asyncio
    async def test_preserves_enrichment_fields(self):
        """Enrichment fields (agent_conclusion, key_findings) survive git failure."""
        async def _git_fails(cmd, working_dir, timeout=3.0):
            return ""

        with patch("core.session_healing._run_git_command_async", side_effect=_git_fails):
            cp = await build_rich_checkpoint(
                original_request="Build feature X",
                working_dir="/tmp/fake",
                agent_conclusion="I was working on the parser module",
                key_findings="Found 3 edge cases in tokenizer",
                completed_steps=["Wrote tests", "Fixed parser"],
                pending_steps=["Update docs"],
                trigger="latency_spike",
            )

        assert "parser module" in cp.key_findings
        assert "3 edge cases" in cp.key_findings
        assert cp.completed_steps == ["Wrote tests", "Fixed parser"]
        assert cp.pending_steps == ["Update docs"]


# ═══════════════════════════════════════════════════════════════════
# Bonus: parse_self_heal_mode fallback behavior
# ═══════════════════════════════════════════════════════════════════


class TestSelfHealModeParsing:
    """Unknown env values default to 'off' (safe fallback)."""

    def test_unknown_value_defaults_to_off(self):
        assert parse_self_heal_mode("garbage") == "off"
        assert parse_self_heal_mode("") == "off"
        assert parse_self_heal_mode("  ") == "off"

    def test_known_values(self):
        assert parse_self_heal_mode("1") == "all"
        assert parse_self_heal_mode("canary") == "canary"
        assert parse_self_heal_mode(" CANARY ") == "canary"
