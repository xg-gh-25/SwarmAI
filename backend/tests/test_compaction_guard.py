"""Tests for CompactionGuard — Two-phase anti-loop protection.

Tests the rewritten CompactionGuard with:
- Two-phase architecture (PASSIVE → ACTIVE)
- Graduated escalation (MONITORING → SOFT_WARN → HARD_WARN → KILL)
- Set-overlap and single-tool repetition loop detection
- Rich work summaries with input details
- SSE event builders
- Lifecycle (reset, reset_all)
- Heuristic compaction detection (30pt context drop)

Covers sub-tasks 1.1–1.10 of the compaction-guard-redesign spec.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from core.compaction_guard import (
    CompactionGuard,
    EscalationLevel,
    GuardPhase,
    ToolRecord,
    _COMPACTION_DROP_THRESHOLD,
    _CONTEXT_ACTIVATION_PCT,
    _MIN_POST_COMPACTION_CALLS,
    _OVERLAP_THRESHOLD,
    _SINGLE_TOOL_REPEAT_LIMIT,
)


# ── 1.1: Enums, Data Models, Constants ──────────────────────────


class TestEnumsAndConstants:
    """Sub-task 1.1: Verify enums, dataclass, and constants."""

    def test_guard_phase_values(self):
        assert GuardPhase.PASSIVE.value == "passive"
        assert GuardPhase.ACTIVE.value == "active"

    def test_escalation_level_values(self):
        assert EscalationLevel.MONITORING.value == "monitoring"
        assert EscalationLevel.SOFT_WARN.value == "soft_warn"
        assert EscalationLevel.HARD_WARN.value == "hard_warn"
        assert EscalationLevel.KILL.value == "kill"

    def test_tool_record_fields(self):
        rec = ToolRecord(
            tool_name="Bash",
            input_hash="abc123",
            input_detail='{"command": "pytest"}',
        )
        assert rec.tool_name == "Bash"
        assert rec.input_hash == "abc123"
        assert rec.input_detail == '{"command": "pytest"}'
        assert isinstance(rec.timestamp, float)

    def test_constants_exist(self):
        assert _CONTEXT_ACTIVATION_PCT == 85
        assert _OVERLAP_THRESHOLD == 0.60
        assert _MIN_POST_COMPACTION_CALLS == 5
        assert _SINGLE_TOOL_REPEAT_LIMIT == 5
        assert _COMPACTION_DROP_THRESHOLD == 30


# ── 1.2: Init and Properties ────────────────────────────────────


class TestInitAndProperties:
    """Sub-task 1.2: Verify __init__ state and properties."""

    def test_initial_phase_is_passive(self):
        guard = CompactionGuard()
        assert guard.phase == GuardPhase.PASSIVE

    def test_initial_escalation_is_monitoring(self):
        guard = CompactionGuard()
        assert guard.escalation == EscalationLevel.MONITORING

    def test_initial_context_pct_is_zero(self):
        guard = CompactionGuard()
        assert guard.context_pct == 0.0

    def test_initial_collections_empty(self):
        guard = CompactionGuard()
        assert len(guard._pre_compaction_set) == 0
        assert len(guard._rolling_baseline_set) == 0
        assert len(guard._post_compaction_sequence) == 0
        assert len(guard._tool_records) == 0


# ── 1.3: record_tool_call() ──────────────────────────────────────


class TestRecordToolCall:
    """Sub-task 1.3: Verify record_tool_call tracking."""

    def test_appends_to_post_compaction_sequence(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        assert len(guard._post_compaction_sequence) == 1
        name, hash_ = guard._post_compaction_sequence[0]
        assert name == "Bash"
        assert isinstance(hash_, str)

    def test_adds_to_rolling_baseline_set(self):
        guard = CompactionGuard()
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        assert len(guard._rolling_baseline_set) == 1

    def test_appends_tool_record(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        assert len(guard._tool_records) == 1
        rec = guard._tool_records[0]
        assert rec.tool_name == "Bash"
        assert isinstance(rec.input_hash, str)
        assert isinstance(rec.input_detail, str)

    def test_input_detail_truncated_to_200(self):
        guard = CompactionGuard()
        long_input = {"data": "x" * 500}
        guard.record_tool_call("Write", long_input)
        assert len(guard._tool_records[0].input_detail) <= 200

    def test_none_input_produces_empty_detail(self):
        guard = CompactionGuard()
        guard.record_tool_call("TodoWrite", None)
        assert guard._tool_records[0].input_detail == ""

    def test_string_input_detail(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", "pytest -q")
        assert guard._tool_records[0].input_detail == "pytest -q"

    def test_duplicate_calls_tracked_in_sequence(self):
        guard = CompactionGuard()
        for _ in range(3):
            guard.record_tool_call("Bash", {"command": "pytest"})
        # Sequence has 3 entries (duplicates allowed)
        assert len(guard._post_compaction_sequence) == 3
        # Set has 1 entry (deduped)
        assert len(guard._rolling_baseline_set) == 1


# ── 1.4: update_context_usage() ──────────────────────────────────


class TestUpdateContextUsage:
    """Sub-task 1.4: Context usage computation and heuristic detection."""

    def test_computes_context_pct(self):
        guard = CompactionGuard()
        with patch(
            "core.prompt_builder.PromptBuilder.get_model_context_window",
            return_value=200_000,
        ):
            guard.update_context_usage(100_000, "claude-sonnet-4-20250514")
        assert guard.context_pct == pytest.approx(50.0)

    def test_unknown_model_fallback_200k(self):
        guard = CompactionGuard()
        # Force import failure to trigger 200K fallback
        with patch(
            "core.prompt_builder.PromptBuilder.get_model_context_window",
            side_effect=Exception("unknown"),
        ):
            guard.update_context_usage(100_000, "unknown-model")
        assert guard.context_pct == pytest.approx(50.0)

    def test_zero_tokens_ignored(self):
        guard = CompactionGuard()
        guard.update_context_usage(0, "claude-sonnet-4-20250514")
        assert guard.context_pct == 0.0

    def test_negative_tokens_ignored(self):
        guard = CompactionGuard()
        guard.update_context_usage(-100, "claude-sonnet-4-20250514")
        assert guard.context_pct == 0.0

    def test_heuristic_compaction_detection_30pt_drop(self):
        """≥30pt drop triggers PASSIVE → ACTIVE transition."""
        guard = CompactionGuard()
        with patch(
            "core.prompt_builder.PromptBuilder.get_model_context_window",
            return_value=200_000,
        ):
            # First call: 80%
            guard.update_context_usage(160_000, "model")
            assert guard.phase == GuardPhase.PASSIVE
            # Second call: 40% (40pt drop ≥ 30)
            guard.update_context_usage(80_000, "model")
            assert guard.phase == GuardPhase.ACTIVE

    def test_no_activation_on_small_drop(self):
        """<30pt drop does NOT trigger activation."""
        guard = CompactionGuard()
        with patch(
            "core.prompt_builder.PromptBuilder.get_model_context_window",
            return_value=200_000,
        ):
            guard.update_context_usage(160_000, "model")  # 80%
            guard.update_context_usage(120_000, "model")  # 60% (20pt drop)
            assert guard.phase == GuardPhase.PASSIVE

    def test_prev_context_pct_updated_after_check(self):
        guard = CompactionGuard()
        with patch(
            "core.prompt_builder.PromptBuilder.get_model_context_window",
            return_value=200_000,
        ):
            guard.update_context_usage(100_000, "model")
        assert guard._prev_context_pct == pytest.approx(50.0)


# ── 1.5: activate() ─────────────────────────────────────────────


class TestActivate:
    """Sub-task 1.5: PASSIVE → ACTIVE transition."""

    def test_passive_to_active(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.activate()
        assert guard.phase == GuardPhase.ACTIVE

    def test_snapshots_baseline(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        guard.activate()
        assert len(guard._pre_compaction_set) == 2

    def test_clears_post_compaction_sequence(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.activate()
        assert len(guard._post_compaction_sequence) == 0

    def test_idempotent_when_already_active(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.activate()
        baseline_size = len(guard._pre_compaction_set)
        # Record more tools, then activate again
        guard.record_tool_call("Read", {"file_path": "/b.py"})
        guard.activate()  # Should be no-op
        assert guard.phase == GuardPhase.ACTIVE
        assert len(guard._pre_compaction_set) == baseline_size


# ── 1.6: _detect_loop() ─────────────────────────────────────────


class TestDetectLoop:
    """Sub-task 1.6: Set-overlap and single-tool repetition detection."""

    def test_set_overlap_triggers_above_60pct(self):
        guard = CompactionGuard()
        # Build baseline with 3 tools
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        guard.record_tool_call("Edit", {"file_path": "/a.py"})
        guard.activate()
        # Post-compaction: 4 of 5 match baseline (80% > 60%)
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        guard.record_tool_call("Edit", {"file_path": "/a.py"})
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Write", {"file_path": "/new.py"})
        assert guard._detect_loop() is True

    def test_set_overlap_no_trigger_below_60pct(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.activate()
        # Post-compaction: 2 of 5 match (40% < 60%)
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/new1.py"})
        guard.record_tool_call("Read", {"file_path": "/new2.py"})
        guard.record_tool_call("Read", {"file_path": "/new3.py"})
        assert guard._detect_loop() is False

    def test_set_overlap_requires_min_5_calls(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.activate()
        # Only 4 calls (< 5 minimum), all matching
        for _ in range(4):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard._detect_loop() is False

    def test_single_tool_repeat_triggers_at_5(self):
        guard = CompactionGuard()
        guard.activate()
        for _ in range(5):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard._detect_loop() is True

    def test_single_tool_repeat_below_5_no_trigger(self):
        guard = CompactionGuard()
        guard.activate()
        for _ in range(4):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard._detect_loop() is False

    def test_different_inputs_not_duplicates(self):
        guard = CompactionGuard()
        guard.activate()
        for i in range(10):
            guard.record_tool_call("Read", {"file_path": f"/file_{i}.py"})
        # All different inputs — no single-tool repeat
        # And no baseline overlap (baseline is empty after activate with no pre-tools)
        assert guard._detect_loop() is False

    def test_empty_sequence_no_loop(self):
        guard = CompactionGuard()
        guard.activate()
        assert guard._detect_loop() is False


# ── 1.7: check() ────────────────────────────────────────────────


class TestCheck:
    """Sub-task 1.7: Graduated escalation logic."""

    def test_passive_always_monitoring(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.MONITORING

    def test_active_below_85_returns_monitoring(self):
        guard = CompactionGuard()
        guard.activate()
        guard._context_pct = 50.0
        # Even with loop pattern present
        for _ in range(5):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.MONITORING

    def test_active_above_85_with_loop_escalates(self):
        guard = CompactionGuard()
        guard.activate()
        guard._context_pct = 90.0
        for _ in range(5):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.SOFT_WARN

    def test_escalation_order_soft_hard_kill(self):
        guard = CompactionGuard()
        guard.activate()
        guard._context_pct = 90.0
        # First detection → SOFT_WARN
        for _ in range(5):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.SOFT_WARN
        # Second detection → HARD_WARN
        assert guard.check() == EscalationLevel.HARD_WARN
        # Third detection → KILL
        assert guard.check() == EscalationLevel.KILL

    def test_kill_stays_kill(self):
        guard = CompactionGuard()
        guard._phase = GuardPhase.ACTIVE
        guard._escalation = EscalationLevel.KILL
        assert guard.check() == EscalationLevel.KILL
        assert guard.check() == EscalationLevel.KILL

    def test_no_loop_returns_monitoring(self):
        guard = CompactionGuard()
        guard.activate()
        guard._context_pct = 90.0
        # Only 2 calls — not enough for loop detection
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        assert guard.check() == EscalationLevel.MONITORING

    def test_check_exception_returns_monitoring(self):
        """check() must never raise — returns MONITORING on error."""
        guard = CompactionGuard()
        guard._phase = GuardPhase.ACTIVE
        guard._context_pct = 90.0
        # Corrupt internal state to force exception
        guard._post_compaction_sequence = None  # type: ignore
        assert guard.check() == EscalationLevel.MONITORING


# ── 1.8: work_summary() ─────────────────────────────────────────


class TestWorkSummary:
    """Sub-task 1.8: Rich work summary generation."""

    def test_empty_when_no_tools(self):
        guard = CompactionGuard()
        assert guard.work_summary() == ""

    def test_contains_tool_names_and_counts(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        guard.record_tool_call("Bash", {"command": "echo done"})
        summary = guard.work_summary()
        assert "Bash: ×2" in summary
        assert "Read: ×1" in summary
        assert "3 total calls" in summary

    def test_contains_critical_instructions(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        summary = guard.work_summary()
        assert "CRITICAL" in summary
        assert "Do NOT re-run" in summary

    def test_sorted_by_count_descending(self):
        guard = CompactionGuard()
        for _ in range(5):
            guard.record_tool_call("Bash", {"command": f"echo {_}"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        summary = guard.work_summary()
        bash_pos = summary.index("Bash")
        read_pos = summary.index("Read")
        assert bash_pos < read_pos

    def test_input_details_included(self):
        guard = CompactionGuard()
        guard.record_tool_call("Read", {"file_path": "/src/main.py"})
        summary = guard.work_summary()
        assert "/src/main.py" in summary

    def test_input_details_truncated_to_200(self):
        guard = CompactionGuard()
        long_path = "/very/long/" + "x" * 300
        guard.record_tool_call("Read", {"file_path": long_path})
        summary = guard.work_summary()
        # Each detail line should be ≤200 chars of content
        for line in summary.split("\n"):
            if line.strip().startswith("- "):
                detail = line.strip()[2:]  # Remove "- " prefix
                assert len(detail) <= 200

    def test_max_5_details_per_group(self):
        guard = CompactionGuard()
        for i in range(10):
            guard.record_tool_call("Read", {"file_path": f"/file_{i}.py"})
        summary = guard.work_summary()
        # Count detail lines for Read group
        detail_lines = [
            l for l in summary.split("\n")
            if l.strip().startswith("- ") and "file_" in l
        ]
        assert len(detail_lines) <= 5


# ── 1.9: build_guard_event() ─────────────────────────────────────


class TestBuildGuardEvent:
    """Sub-task 1.9: SSE event builder."""

    def test_monitoring_returns_none(self):
        guard = CompactionGuard()
        assert guard.build_guard_event(EscalationLevel.MONITORING) is None

    def test_soft_warn_event_structure(self):
        guard = CompactionGuard()
        guard._context_pct = 87.5
        event = guard.build_guard_event(EscalationLevel.SOFT_WARN)
        assert event is not None
        assert event["type"] == "compaction_guard"
        assert event["subtype"] == "soft_warn"
        assert event["context_pct"] == 87.5
        assert isinstance(event["message"], str)
        assert "pattern_description" in event

    def test_hard_warn_event_structure(self):
        guard = CompactionGuard()
        guard._context_pct = 90.0
        event = guard.build_guard_event(EscalationLevel.HARD_WARN)
        assert event is not None
        assert event["type"] == "compaction_guard"
        assert event["subtype"] == "hard_warn"

    def test_kill_event_structure(self):
        guard = CompactionGuard()
        guard._context_pct = 95.0
        event = guard.build_guard_event(EscalationLevel.KILL)
        assert event is not None
        assert event["type"] == "compaction_guard"
        assert event["subtype"] == "kill"
        assert "❌" in event["message"]

    def test_event_messages_differ_by_level(self):
        guard = CompactionGuard()
        guard._context_pct = 90.0
        soft = guard.build_guard_event(EscalationLevel.SOFT_WARN)
        hard = guard.build_guard_event(EscalationLevel.HARD_WARN)
        kill = guard.build_guard_event(EscalationLevel.KILL)
        assert soft["message"] != hard["message"]
        assert hard["message"] != kill["message"]

    def test_pattern_description_with_overlap(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.activate()
        for _ in range(5):
            guard.record_tool_call("Bash", {"command": "pytest"})
        guard._context_pct = 90.0
        # Must call check() first to populate _last_pattern_desc via _detect_loop()
        guard.check()
        event = guard.build_guard_event(EscalationLevel.SOFT_WARN)
        assert event is not None
        desc = event["pattern_description"]
        assert "calls" in desc or "times" in desc


# ── 1.10: reset() and reset_all() ───────────────────────────────


class TestResetLifecycle:
    """Sub-task 1.10: Lifecycle reset methods."""

    def test_reset_preserves_escalation(self):
        """reset() preserves escalation level (bugfix: was incorrectly clearing it)."""
        guard = CompactionGuard()
        guard._phase = GuardPhase.ACTIVE
        guard._escalation = EscalationLevel.HARD_WARN
        guard.reset()
        assert guard.escalation == EscalationLevel.HARD_WARN

    def test_reset_clears_post_compaction_sequence(self):
        guard = CompactionGuard()
        guard.activate()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.reset()
        assert len(guard._post_compaction_sequence) == 0

    def test_reset_preserves_phase(self):
        guard = CompactionGuard()
        guard.activate()
        guard.reset()
        assert guard.phase == GuardPhase.ACTIVE

    def test_reset_preserves_baseline(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.activate()
        baseline_size = len(guard._pre_compaction_set)
        guard.reset()
        assert len(guard._pre_compaction_set) == baseline_size

    def test_reset_preserves_context_pct(self):
        guard = CompactionGuard()
        guard._context_pct = 75.0
        guard.reset()
        assert guard.context_pct == 75.0

    def test_reset_preserves_tool_records(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.reset()
        assert len(guard._tool_records) == 1

    def test_reset_all_restores_passive(self):
        guard = CompactionGuard()
        guard.activate()
        guard._context_pct = 90.0
        guard._escalation = EscalationLevel.KILL
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.reset_all()
        assert guard.phase == GuardPhase.PASSIVE
        assert guard.escalation == EscalationLevel.MONITORING
        assert guard.context_pct == 0.0
        assert len(guard._pre_compaction_set) == 0
        assert len(guard._rolling_baseline_set) == 0
        assert len(guard._post_compaction_sequence) == 0
        assert len(guard._tool_records) == 0


# ── Hash Consistency ─────────────────────────────────────────────


class TestHashInput:
    """Hash determinism for deduplication."""

    def test_dict_order_independent(self):
        h1 = CompactionGuard._hash_input({"a": 1, "b": 2})
        h2 = CompactionGuard._hash_input({"b": 2, "a": 1})
        assert h1 == h2

    def test_none_consistent(self):
        assert CompactionGuard._hash_input(None) == "none"

    def test_string_hashed(self):
        h = CompactionGuard._hash_input("hello")
        assert isinstance(h, str)
        assert len(h) == 12

    def test_different_inputs_different_hashes(self):
        h1 = CompactionGuard._hash_input({"command": "pytest"})
        h2 = CompactionGuard._hash_input({"command": "echo hi"})
        assert h1 != h2
