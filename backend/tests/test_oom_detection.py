"""Tests for OOM / SIGKILL detection in session_unit.

Verifies that _is_oom_signal() correctly identifies OOM-related errors
across multiple SDK error message formats, and falls back to memory
pressure heuristics when no pattern matches.

This test suite guards against silent regression if the Claude SDK
changes its error message format — the core risk identified in the
resource observability design review.
"""

import pytest
from unittest.mock import patch, MagicMock

from core.session_unit import _is_oom_signal, _OOM_PATTERNS


class TestOOMPatternDetection:
    """Verify _is_oom_signal matches known SDK error formats."""

    # Known SDK error messages that indicate OOM / SIGKILL.
    # If the SDK changes these, the test fails and we update _OOM_PATTERNS.
    @pytest.mark.parametrize("error_msg", [
        # Current SDK format variants
        "Command failed with exit code -9",
        "Process exited with exit code: -9",
        "exit code -9: process was killed",
        "exit code: -9",
        # SIGKILL references
        "Process received SIGKILL",
        "killed by signal 9",
        "signal 9 received",
        # macOS jetsam
        "Process terminated by jetsam",
        "jetsam killed process",
        # Terminated process (broken pipe after kill)
        "Cannot write to terminated process",
    ])
    def test_known_oom_patterns_detected(self, error_msg: str):
        """Each known OOM error format must be detected."""
        assert _is_oom_signal(error_msg) is True, (
            f"_is_oom_signal failed to detect OOM pattern in: {error_msg!r}"
        )

    @pytest.mark.parametrize("error_msg", [
        # Normal errors that should NOT be treated as OOM
        "Connection reset by peer",
        "Too many requests",
        "Rate limit exceeded",
        "Internal server error",
        "exit code: 1",
        "exit code: -6",
        "Authentication failed",
        "Timeout waiting for response",
        "",
    ])
    def test_non_oom_errors_not_detected(self, error_msg: str):
        """Non-OOM errors must not trigger OOM detection."""
        # Mock resource_monitor to return non-critical pressure
        # so the fallback heuristic doesn't fire
        mock_mem = MagicMock()
        mock_mem.pressure_level = "ok"
        mock_mem.percent_used = 50.0

        mock_monitor = MagicMock()
        mock_monitor.system_memory.return_value = mock_mem

        with patch("core.resource_monitor.resource_monitor", mock_monitor):
            assert _is_oom_signal(error_msg) is False, (
                f"_is_oom_signal false-positive on: {error_msg!r}"
            )

    def test_case_insensitive_matching(self):
        """Pattern matching must be case-insensitive."""
        assert _is_oom_signal("SIGKILL received") is True
        assert _is_oom_signal("Jetsam killed PID 1234") is True
        assert _is_oom_signal("EXIT CODE -9") is True


class TestOOMFallbackHeuristic:
    """Verify the memory-pressure fallback when no pattern matches."""

    def test_fallback_triggers_on_critical_pressure(self):
        """Unknown error + critical memory pressure → treat as OOM."""
        mock_mem = MagicMock()
        mock_mem.pressure_level = "critical"
        mock_mem.percent_used = 95.0

        mock_monitor = MagicMock()
        mock_monitor.system_memory.return_value = mock_mem

        with patch("core.resource_monitor.resource_monitor", mock_monitor):
            # Error message doesn't match any pattern
            assert _is_oom_signal("Some unknown SDK error format") is True

    def test_fallback_does_not_trigger_on_normal_pressure(self):
        """Unknown error + normal memory → not OOM."""
        mock_mem = MagicMock()
        mock_mem.pressure_level = "ok"
        mock_mem.percent_used = 45.0

        mock_monitor = MagicMock()
        mock_monitor.system_memory.return_value = mock_mem

        with patch("core.resource_monitor.resource_monitor", mock_monitor):
            assert _is_oom_signal("Some unknown SDK error format") is False

    def test_fallback_does_not_trigger_on_warning_pressure(self):
        """Unknown error + warning (not critical) → not OOM."""
        mock_mem = MagicMock()
        mock_mem.pressure_level = "warning"
        mock_mem.percent_used = 80.0

        mock_monitor = MagicMock()
        mock_monitor.system_memory.return_value = mock_mem

        with patch("core.resource_monitor.resource_monitor", mock_monitor):
            assert _is_oom_signal("Some unknown SDK error format") is False

    def test_fallback_graceful_when_monitor_unavailable(self):
        """If resource_monitor raises, fall back to patterns only."""
        mock_monitor = MagicMock()
        mock_monitor.system_memory.side_effect = Exception("psutil broken")

        with patch("core.resource_monitor.resource_monitor", mock_monitor):
            # No pattern match + broken monitor → False (safe default)
            assert _is_oom_signal("Some unknown error") is False


class TestOOMPatternCompleteness:
    """Meta-tests to ensure pattern list stays maintained."""

    def test_pattern_list_not_empty(self):
        """_OOM_PATTERNS must have at least the core patterns."""
        assert len(_OOM_PATTERNS) >= 5, (
            "Too few OOM patterns — check if patterns were accidentally removed"
        )

    def test_all_patterns_are_lowercase(self):
        """All patterns must be lowercase (matching is case-insensitive)."""
        for pattern in _OOM_PATTERNS:
            assert pattern == pattern.lower(), (
                f"Pattern must be lowercase: {pattern!r}"
            )

    def test_exit_code_minus_9_covered(self):
        """The most critical pattern (exit code -9) must always be present."""
        has_exit_9 = any("exit code" in p and "-9" in p for p in _OOM_PATTERNS)
        assert has_exit_9, (
            "exit code -9 pattern missing from _OOM_PATTERNS — "
            "this is the primary OOM signal from the Claude SDK"
        )
