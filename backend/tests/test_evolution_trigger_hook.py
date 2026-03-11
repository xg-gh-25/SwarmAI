"""Unit tests for the ToolFailureTracker evolution trigger hook.

Tests the per-session failure tracking, nudge generation, cooldown,
reset-on-success, and max-nudges-per-session cap.

Testing methodology: unit tests with deterministic inputs.
Key invariants:
- First failure never nudges
- Second consecutive failure with same signature nudges
- Success resets failure count for that tool
- Max 3 nudges per session
- Cooldown prevents rapid re-nudging
"""

import time
from unittest.mock import patch

from hooks.evolution_trigger_hook import (
    ToolFailureTracker,
    check_tool_result_for_failure,
    format_evolution_nudge,
    _failure_signature,
    FAILURE_THRESHOLD,
    NUDGE_COOLDOWN_SECONDS,
)


class TestFailureSignature:
    """Tests for _failure_signature derivation."""

    def test_lowercases_tool_name(self):
        sig = _failure_signature("Bash", "error")
        assert sig.startswith("bash:")

    def test_truncates_error_to_100_chars(self):
        long_error = "x" * 200
        sig = _failure_signature("Bash", long_error)
        # tool_name: + 100 chars
        assert len(sig) == len("bash:") + 100

    def test_strips_whitespace(self):
        sig = _failure_signature("Bash", "  error message  ")
        assert sig == "bash:error message"


class TestToolFailureTracker:
    """Tests for ToolFailureTracker nudge logic."""

    def test_first_failure_no_nudge(self):
        t = ToolFailureTracker()
        result = t.record_failure("Bash", "command not found")
        assert result is None

    def test_second_failure_nudges(self):
        t = ToolFailureTracker()
        t.record_failure("Bash", "command not found")
        result = t.record_failure("Bash", "command not found")
        assert result is not None
        assert "Bash" in result

    def test_different_errors_dont_cross_nudge(self):
        t = ToolFailureTracker()
        t.record_failure("Bash", "command not found")
        result = t.record_failure("Bash", "permission denied")
        assert result is None

    def test_reset_tool_clears_count(self):
        t = ToolFailureTracker()
        t.record_failure("Bash", "command not found")
        t.reset_tool("Bash")
        result = t.record_failure("Bash", "command not found")
        assert result is None  # back to first failure

    def test_reset_tool_case_insensitive(self):
        t = ToolFailureTracker()
        t.record_failure("Bash", "error")
        t.reset_tool("bash")  # lowercase
        result = t.record_failure("Bash", "error")
        assert result is None

    def test_max_nudges_per_session(self):
        t = ToolFailureTracker()
        nudges = 0
        for i in range(10):
            err = f"error_{i}"
            t.record_failure("Tool", err)
            r = t.record_failure("Tool", err)
            if r is not None:
                nudges += 1
        assert nudges == 3  # capped at 3

    def test_cooldown_prevents_rapid_nudge(self):
        t = ToolFailureTracker()
        t.record_failure("Bash", "err")
        r1 = t.record_failure("Bash", "err")
        assert r1 is not None
        # Third failure immediately — should be blocked by cooldown
        r2 = t.record_failure("Bash", "err")
        assert r2 is None


class TestCheckToolResultForFailure:
    """Tests for the stateless check_tool_result_for_failure function."""

    def test_error_records_failure(self):
        t = ToolFailureTracker()
        check_tool_result_for_failure("Bash", "err", True, t)
        result = check_tool_result_for_failure("Bash", "err", True, t)
        assert result is not None

    def test_success_resets_tool(self):
        t = ToolFailureTracker()
        check_tool_result_for_failure("Bash", "err", True, t)
        check_tool_result_for_failure("Bash", "ok", False, t)  # success
        result = check_tool_result_for_failure("Bash", "err", True, t)
        assert result is None  # reset, back to first failure


class TestFormatEvolutionNudge:
    """Tests for nudge message formatting."""

    def test_contains_tool_name(self):
        nudge = format_evolution_nudge("Bash", "command not found", 3)
        assert "Bash" in nudge

    def test_truncates_error_text(self):
        long_error = "x" * 500
        nudge = format_evolution_nudge("Bash", long_error, 2)
        assert len(nudge) < 600  # error truncated to 200 chars

    def test_contains_trigger_marker(self):
        nudge = format_evolution_nudge("Bash", "err", 2)
        assert "EVOLUTION TRIGGER" in nudge
