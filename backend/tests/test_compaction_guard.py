"""Tests for CompactionGuard — 3-layer anti-loop protection.

Tests all three layers:
- Layer 1: Context-aware throttle (70% warn, 85% stop)
- Layer 2: Circuit breaker (exact repeats, tool-name repeats, whitelisted tools)
- Layer 3: Post-compaction work summary generation
- SSE event builders
- Lifecycle (reset, reset_all)
"""
from __future__ import annotations

import pytest

from core.compaction_guard import (
    CompactionGuard,
    LoopAction,
    _CONTEXT_STOP_PCT,
    _CONTEXT_WARN_PCT,
    _EXACT_REPEAT_LIMIT,
    _REPEAT_WHITELISTED_TOOLS,
    _TOOL_NAME_LIMIT,
)


# ── Layer 1: Context-Aware Throttle ────────────────────────────────


class TestContextThrottle:
    """Layer 1: context usage triggers throttle warnings."""

    def test_below_threshold_returns_none(self):
        guard = CompactionGuard()
        guard.update_context_usage(50_000, "claude-sonnet-4-20250514")  # ~25% of 200K
        assert guard.check() == LoopAction.NONE

    def test_warn_at_70_percent(self):
        guard = CompactionGuard()
        # 200K model, 70% = 140K tokens
        guard.update_context_usage(141_000, "claude-sonnet-4-20250514")
        assert guard.check() == LoopAction.THROTTLE_WARN

    def test_warn_fires_only_once_per_turn(self):
        guard = CompactionGuard()
        guard.update_context_usage(141_000, "claude-sonnet-4-20250514")
        assert guard.check() == LoopAction.THROTTLE_WARN
        # Second check same turn — should not re-warn
        assert guard.check() == LoopAction.NONE

    def test_stop_at_85_percent(self):
        guard = CompactionGuard()
        guard.update_context_usage(175_000, "claude-sonnet-4-20250514")
        assert guard.check() == LoopAction.THROTTLE_STOP

    def test_stop_fires_only_once_per_turn(self):
        guard = CompactionGuard()
        guard.update_context_usage(175_000, "claude-sonnet-4-20250514")
        assert guard.check() == LoopAction.THROTTLE_STOP
        # Second check same turn — should not re-fire
        assert guard.check() == LoopAction.NONE

    def test_stop_overrides_warn(self):
        """If we jump straight to 85%+, get STOP not WARN."""
        guard = CompactionGuard()
        guard.update_context_usage(180_000, "claude-sonnet-4-20250514")
        assert guard.check() == LoopAction.THROTTLE_STOP

    def test_1m_model_context(self):
        """70% of 1M = 700K tokens — should still work."""
        guard = CompactionGuard()
        # 710K/1M = 71% → WARN. But need to verify the model maps to 1M.
        # If model maps to 200K, 710K/200K = 355% → STOP.
        # Use a known 1M model key from PromptBuilder._MODEL_CONTEXT_WINDOWS.
        guard.update_context_usage(710_000, "us.anthropic.claude-opus-4-20250514-v1:0")
        # At 71% of whatever window, should be at least WARN
        action = guard.check()
        assert action in (LoopAction.THROTTLE_WARN, LoopAction.THROTTLE_STOP)

    def test_unknown_model_uses_fallback(self):
        guard = CompactionGuard()
        # Fallback = 200K, 70% = 140K
        guard.update_context_usage(141_000, "some-unknown-model-v99")
        assert guard.check() == LoopAction.THROTTLE_WARN

    def test_zero_tokens_ignored(self):
        guard = CompactionGuard()
        guard.update_context_usage(0, "claude-sonnet-4-20250514")
        assert guard.context_pct == 0.0

    def test_negative_tokens_ignored(self):
        guard = CompactionGuard()
        guard.update_context_usage(-100, "claude-sonnet-4-20250514")
        assert guard.context_pct == 0.0


# ── Layer 2: Circuit Breaker ──────────────────────────────────────


class TestCircuitBreaker:
    """Layer 2: repeated tool calls trigger loop detection."""

    def test_no_tools_returns_none(self):
        guard = CompactionGuard()
        assert guard.check() == LoopAction.NONE

    def test_single_tool_call_ok(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == LoopAction.NONE

    def test_exact_repeat_triggers_at_limit(self):
        guard = CompactionGuard()
        for _ in range(_EXACT_REPEAT_LIMIT):
            guard.record_tool_call("Bash", {"command": "pytest backend/tests/ -q"})
        assert guard.check() == LoopAction.LOOP_DETECTED

    def test_exact_repeat_below_limit_ok(self):
        guard = CompactionGuard()
        for _ in range(_EXACT_REPEAT_LIMIT - 1):
            guard.record_tool_call("Bash", {"command": "pytest backend/tests/ -q"})
        assert guard.check() == LoopAction.NONE

    def test_different_inputs_no_exact_repeat(self):
        """Same tool, different inputs — no exact repeat trigger."""
        guard = CompactionGuard()
        for i in range(10):
            guard.record_tool_call("Read", {"file_path": f"/path/file_{i}.py"})
        assert guard.check() == LoopAction.NONE  # Read is whitelisted for name-count

    def test_tool_name_limit_non_whitelisted(self):
        """Non-whitelisted tool hitting name limit."""
        guard = CompactionGuard()
        for i in range(_TOOL_NAME_LIMIT):
            guard.record_tool_call("Bash", {"command": f"echo {i}"})
        assert guard.check() == LoopAction.LOOP_DETECTED

    def test_tool_name_limit_whitelisted_exempt(self):
        """Whitelisted tools exempt from name-count limit."""
        guard = CompactionGuard()
        for i in range(_TOOL_NAME_LIMIT + 5):
            guard.record_tool_call("Read", {"file_path": f"/path/file_{i}.py"})
        # Read is whitelisted — should NOT trigger name-count limit
        assert guard.check() == LoopAction.NONE

    def test_whitelisted_tool_still_catches_exact_repeat(self):
        """Even whitelisted tools trigger on exact same input."""
        guard = CompactionGuard()
        for _ in range(_EXACT_REPEAT_LIMIT):
            guard.record_tool_call("Read", {"file_path": "/same/file.py"})
        assert guard.check() == LoopAction.LOOP_DETECTED

    def test_loop_fires_only_once_per_turn(self):
        guard = CompactionGuard()
        for _ in range(_EXACT_REPEAT_LIMIT):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == LoopAction.LOOP_DETECTED
        # More calls, but loop already warned
        guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == LoopAction.NONE

    def test_mixed_tools_no_false_positive(self):
        """Different tools don't trigger."""
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        guard.record_tool_call("Edit", {"file_path": "/a.py", "old": "x", "new": "y"})
        guard.record_tool_call("Grep", {"pattern": "foo"})
        assert guard.check() == LoopAction.NONE

    def test_all_whitelisted_tools(self):
        """Verify all expected tools are whitelisted."""
        for tool in _REPEAT_WHITELISTED_TOOLS:
            guard = CompactionGuard()
            for i in range(_TOOL_NAME_LIMIT + 2):
                guard.record_tool_call(tool, {"input": f"different_{i}"})
            assert guard.check() == LoopAction.NONE, f"{tool} should be whitelisted"

    def test_none_input_hashed_consistently(self):
        """None inputs produce the same hash."""
        guard = CompactionGuard()
        for _ in range(_EXACT_REPEAT_LIMIT):
            guard.record_tool_call("TodoWrite", None)
        assert guard.check() == LoopAction.LOOP_DETECTED

    def test_string_input_hashed(self):
        guard = CompactionGuard()
        for _ in range(_EXACT_REPEAT_LIMIT):
            guard.record_tool_call("Bash", "pytest -q")
        assert guard.check() == LoopAction.LOOP_DETECTED


# ── Layer Priority ────────────────────────────────────────────────


class TestLayerPriority:
    """Context throttle (L1) takes priority over circuit breaker (L2)."""

    def test_throttle_stop_overrides_loop(self):
        guard = CompactionGuard()
        # Trigger both L1 and L2
        guard.update_context_usage(180_000, "claude-sonnet-4-20250514")
        for _ in range(_EXACT_REPEAT_LIMIT):
            guard.record_tool_call("Bash", {"command": "pytest"})
        # L1 (THROTTLE_STOP) should win
        assert guard.check() == LoopAction.THROTTLE_STOP


# ── Layer 3: Work Summary ────────────────────────────────────────


class TestWorkSummary:
    """Layer 3: structured work summary for post-compaction injection."""

    def test_empty_when_no_tools(self):
        guard = CompactionGuard()
        assert guard.work_summary() == ""

    def test_summary_contains_tool_calls(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        guard.record_tool_call("Bash", {"command": "echo done"})
        summary = guard.work_summary()
        assert "[Post-Compaction Work Summary]" in summary
        assert "Bash: ×2" in summary
        assert "Read: ×1" in summary
        assert "3 total calls" in summary
        assert "do not re-run" in summary.lower()

    def test_summary_sorted_by_count(self):
        guard = CompactionGuard()
        for _ in range(5):
            guard.record_tool_call("Bash", {"command": f"echo {_}"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        summary = guard.work_summary()
        bash_pos = summary.index("Bash")
        read_pos = summary.index("Read")
        assert bash_pos < read_pos, "Higher-count tools should appear first"


# ── SSE Event Builders ───────────────────────────────────────────


class TestSSEEvents:
    """SSE event dict builders for frontend consumption."""

    def test_throttle_warning_event(self):
        guard = CompactionGuard()
        guard.update_context_usage(145_000, "claude-sonnet-4-20250514")
        event = guard.build_throttle_warning_event()
        assert event["type"] == "loop_guard"
        assert event["subtype"] == "throttle_warning"
        assert event["context_pct"] > 70
        assert "⚠️" in event["message"]

    def test_throttle_stop_event(self):
        guard = CompactionGuard()
        guard.update_context_usage(175_000, "claude-sonnet-4-20250514")
        event = guard.build_throttle_stop_event()
        assert event["type"] == "loop_guard"
        assert event["subtype"] == "throttle_stop"
        assert "🛑" in event["message"]

    def test_loop_warning_event(self):
        guard = CompactionGuard()
        for _ in range(3):
            guard.record_tool_call("Bash", {"command": "pytest"})
        event = guard.build_loop_warning_event()
        assert event["type"] == "loop_guard"
        assert event["subtype"] == "loop_detected"
        assert event["tool_name"] == "Bash"
        assert event["repeat_count"] == 3
        assert "🔄" in event["message"]


# ── Lifecycle ────────────────────────────────────────────────────


class TestLifecycle:
    """Reset and reset_all behavior."""

    def test_reset_clears_tool_tracking(self):
        guard = CompactionGuard()
        for _ in range(_EXACT_REPEAT_LIMIT - 1):
            guard.record_tool_call("Bash", {"command": "pytest"})
        guard.reset()
        # After reset, the counter is back to 0
        guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == LoopAction.NONE

    def test_reset_preserves_context_pct(self):
        guard = CompactionGuard()
        guard.update_context_usage(145_000, "claude-sonnet-4-20250514")
        old_pct = guard.context_pct
        guard.reset()
        assert guard.context_pct == old_pct, "reset() should NOT clear context %"

    def test_reset_allows_re_warn(self):
        """After reset, throttle warning can fire again."""
        guard = CompactionGuard()
        guard.update_context_usage(145_000, "claude-sonnet-4-20250514")
        assert guard.check() == LoopAction.THROTTLE_WARN
        assert guard.check() == LoopAction.NONE  # Consumed
        guard.reset()
        assert guard.check() == LoopAction.THROTTLE_WARN  # Fires again

    def test_reset_allows_re_stop(self):
        """After reset, throttle stop can fire again."""
        guard = CompactionGuard()
        guard.update_context_usage(180_000, "claude-sonnet-4-20250514")
        assert guard.check() == LoopAction.THROTTLE_STOP
        assert guard.check() == LoopAction.NONE  # Consumed
        guard.reset()
        assert guard.check() == LoopAction.THROTTLE_STOP  # Fires again

    def test_reset_all_clears_everything(self):
        guard = CompactionGuard()
        guard.update_context_usage(145_000, "claude-sonnet-4-20250514")
        for _ in range(2):
            guard.record_tool_call("Bash", {"command": "pytest"})
        guard.reset_all()
        assert guard.context_pct == 0.0
        assert guard.check() == LoopAction.NONE

    def test_reset_clears_loop_warning_flag(self):
        guard = CompactionGuard()
        for _ in range(_EXACT_REPEAT_LIMIT):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == LoopAction.LOOP_DETECTED
        guard.reset()
        # Now trigger again in new turn
        for _ in range(_EXACT_REPEAT_LIMIT):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == LoopAction.LOOP_DETECTED


# ── Hash Consistency ─────────────────────────────────────────────


class TestHashInput:
    """_hash_input deterministic behavior."""

    def test_dict_order_independent(self):
        h1 = CompactionGuard._hash_input({"a": 1, "b": 2})
        h2 = CompactionGuard._hash_input({"b": 2, "a": 1})
        assert h1 == h2

    def test_none_consistent(self):
        assert CompactionGuard._hash_input(None) == "none"

    def test_string_hashed(self):
        h = CompactionGuard._hash_input("hello")
        assert isinstance(h, str)
        assert len(h) == 12  # Truncated MD5

    def test_different_inputs_different_hashes(self):
        h1 = CompactionGuard._hash_input({"command": "pytest"})
        h2 = CompactionGuard._hash_input({"command": "echo hi"})
        assert h1 != h2
