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
    _CONSEC_HARD,
    _CONSEC_KILL,
    _CONSEC_SOFT,
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
        """Context-gated detectors don't fire below threshold, even with overlap."""
        guard = CompactionGuard()
        # Build baseline with alternating calls (avoid consecutive trigger)
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        guard.activate()
        guard._context_pct = 50.0
        # Post-compaction: same baseline tools, interleaved (no consecutive)
        for i in range(3):
            guard.record_tool_call("Bash", {"command": "pytest"})
            guard.record_tool_call("Read", {"file_path": "/a.py"})
        assert guard.check() == EscalationLevel.MONITORING

    def test_active_above_85_with_loop_escalates(self):
        """Context-gated set-overlap fires above threshold."""
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        guard.activate()
        guard._context_pct = 90.0
        # Post-compaction: same baseline tools, interleaved, ≥5 calls
        for i in range(3):
            guard.record_tool_call("Bash", {"command": "pytest"})
            guard.record_tool_call("Read", {"file_path": "/a.py"})
        # 6 calls, all overlap baseline → set-overlap fires
        assert guard.check() == EscalationLevel.SOFT_WARN

    def test_escalation_order_soft_hard_kill(self):
        """Set-overlap escalates one step per check() call."""
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})
        guard.activate()
        guard._context_pct = 90.0
        for i in range(3):
            guard.record_tool_call("Bash", {"command": "pytest"})
            guard.record_tool_call("Read", {"file_path": "/a.py"})
        # First detection → SOFT_WARN
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


# ── Bash Command Normalization ────────────────────────────────────


class TestBashCommandNormalization:
    """Bash command normalization for loop detection.

    The pytest dead-loop bug: agent runs the same pytest command with
    different ``| tail -N`` suffixes, ``2>&1`` redirections, and ``cd``
    prefixes. Without normalization, each looks like a unique call and
    the guard never fires.
    """

    def test_strips_tail_suffix(self):
        n = CompactionGuard._normalize_bash_command
        assert n("python -m pytest tests/ -x -q | tail -20") == "python -m pytest tests/ -x -q"
        assert n("python -m pytest tests/ -x -q | tail -30") == "python -m pytest tests/ -x -q"
        assert n("python -m pytest tests/ | head -5") == "python -m pytest tests/"

    def test_strips_2_redirect(self):
        n = CompactionGuard._normalize_bash_command
        assert n("python -m pytest tests/ -x -q 2>&1") == "python -m pytest tests/ -x -q"

    def test_strips_tail_and_redirect_together(self):
        n = CompactionGuard._normalize_bash_command
        assert (
            n("python -m pytest tests/ -x -q 2>&1 | tail -20")
            == "python -m pytest tests/ -x -q"
        )

    def test_strips_cd_prefix(self):
        n = CompactionGuard._normalize_bash_command
        assert (
            n("cd /Users/gawan/swarmai/backend && python -m pytest tests/")
            == "python -m pytest tests/"
        )

    def test_strips_rm_cleanup_prefix(self):
        n = CompactionGuard._normalize_bash_command
        assert (
            n("rm -f /tmp/lock 2>/dev/null; python -m pytest tests/")
            == "python -m pytest tests/"
        )

    def test_strips_combined_preamble(self):
        """The exact pattern from the dead-loop sample."""
        n = CompactionGuard._normalize_bash_command
        cmd1 = "cd /Users/gawan/swarmai/backend && rm -f /private/tmp/claude-503/pytest_lock 2>/dev/null; python -m pytest tests/ -x -q 2>&1 | tail -20"
        cmd2 = "python -m pytest tests/ -x -q 2>&1 | tail -30"
        cmd3 = "python -m pytest tests/ -x -q"
        # All three should normalize to the same core command
        assert n(cmd1) == n(cmd2) == n(cmd3)

    def test_hash_equality_for_cosmetic_bash_variants(self):
        """Bash tool calls with cosmetic variations produce the same hash."""
        base = {"command": "python -m pytest tests/ -x -q"}
        with_tail = {"command": "python -m pytest tests/ -x -q 2>&1 | tail -20"}
        with_cd = {"command": "cd /foo && python -m pytest tests/ -x -q 2>&1 | tail -30"}
        with_rm = {"command": "rm -f /tmp/lock; python -m pytest tests/ -x -q"}

        h_base = CompactionGuard._hash_input(base)
        h_tail = CompactionGuard._hash_input(with_tail)
        h_cd = CompactionGuard._hash_input(with_cd)
        h_rm = CompactionGuard._hash_input(with_rm)

        assert h_base == h_tail == h_cd == h_rm

    def test_description_field_ignored_in_hash(self):
        """Bash tool 'description' varies per call but shouldn't affect hash."""
        cmd1 = {"command": "python -m pytest tests/", "description": "Run tests"}
        cmd2 = {"command": "python -m pytest tests/", "description": "Re-running the test suite"}
        assert CompactionGuard._hash_input(cmd1) == CompactionGuard._hash_input(cmd2)

    def test_non_bash_dicts_unchanged(self):
        """Non-Bash tool inputs (no 'command' key) hash as before."""
        inp = {"file_path": "/a.py", "content": "hello"}
        # Should still produce a valid hash without normalization
        h = CompactionGuard._hash_input(inp)
        assert isinstance(h, str) and len(h) == 12

    def test_preserves_meaningful_differences(self):
        """Different core commands must NOT normalize to the same hash."""
        h1 = CompactionGuard._hash_input({"command": "python -m pytest tests/"})
        h2 = CompactionGuard._hash_input({"command": "python -m pytest tests/ -k session"})
        assert h1 != h2

    def test_single_tool_repeat_catches_normalized_bash_loop(self):
        """Integration: 5 cosmetically different pytest runs trigger loop detection."""
        guard = CompactionGuard()
        guard.activate()
        guard._context_pct = 90.0

        # 5 different-looking but semantically identical pytest invocations
        variants = [
            {"command": "python -m pytest tests/ -x -q"},
            {"command": "python -m pytest tests/ -x -q 2>&1 | tail -20"},
            {"command": "cd /foo && python -m pytest tests/ -x -q 2>&1 | tail -30"},
            {"command": "rm -f /tmp/lock; python -m pytest tests/ -x -q"},
            {"command": "python -m pytest tests/ -x -q | tail -20"},
        ]
        for v in variants:
            guard.record_tool_call("Bash", v)

        # All 5 normalize to the same hash → single-tool repeat ≥5 → loop
        assert guard._detect_loop() is True

    def test_pipe_in_middle_preserved(self):
        """Pipes that are part of the core command (not tail/head) are preserved."""
        n = CompactionGuard._normalize_bash_command
        # grep | wc is core logic, not cosmetic
        result = n("grep -r TODO src/ | wc -l")
        assert "grep -r TODO src/ | wc -l" == result

    def test_empty_command(self):
        n = CompactionGuard._normalize_bash_command
        assert n("") == ""
        assert n("   ") == ""

    def test_chained_rm_prefixes(self):
        """Multiple rm -f cleanup commands chained with ; or &&."""
        n = CompactionGuard._normalize_bash_command
        result = n("rm -f /tmp/a; rm -f /tmp/b; python -m pytest")
        assert result == "python -m pytest"


# ── Consecutive Identical Call Detection ──────────────────────────


class TestConsecutiveRepeatDetection:
    """Layer 0: Consecutive identical call detection.

    Catches the pytest dead-loop at low context usage where:
    - No compaction → guard stays PASSIVE
    - Only 6-8 calls → diversity window (20) never fills
    - Context at ~8% → ACTIVE context gate (40%) never fires

    This detector needs only 3 consecutive identical calls. No window,
    no context gate, no phase dependency.
    """

    def test_constants(self):
        assert _CONSEC_SOFT == 3
        assert _CONSEC_HARD == 5
        assert _CONSEC_KILL == 7

    def test_below_threshold_returns_monitoring(self):
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.MONITORING

    def test_3_consecutive_triggers_soft_warn(self):
        guard = CompactionGuard()
        for _ in range(3):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.SOFT_WARN

    def test_5_consecutive_triggers_hard_warn(self):
        guard = CompactionGuard()
        for _ in range(5):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.HARD_WARN

    def test_7_consecutive_triggers_kill(self):
        guard = CompactionGuard()
        for _ in range(7):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.KILL

    def test_broken_by_different_call(self):
        """A different tool call resets the consecutive counter."""
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Read", {"file_path": "/a.py"})  # break
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.MONITORING

    def test_works_in_passive_phase_low_context(self):
        """The whole point: fires at 8% context, PASSIVE phase."""
        guard = CompactionGuard()
        assert guard.phase == GuardPhase.PASSIVE
        guard._context_pct = 8.0  # 8% on a 1M window
        for _ in range(3):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.SOFT_WARN

    def test_normalized_bash_variants_are_consecutive(self):
        """Cosmetically different pytest commands count as consecutive."""
        guard = CompactionGuard()
        guard.record_tool_call("Bash", {"command": "python -m pytest tests/ -x -q"})
        guard.record_tool_call("Bash", {"command": "python -m pytest tests/ -x -q 2>&1 | tail -20"})
        guard.record_tool_call("Bash", {"command": "cd /foo && python -m pytest tests/ -x -q | tail -30"})
        # All 3 normalize to same hash → 3 consecutive
        assert guard.check() == EscalationLevel.SOFT_WARN

    def test_escalation_is_monotonic(self):
        """Once at HARD_WARN via consecutive, adding more calls escalates to KILL."""
        guard = CompactionGuard()
        for _ in range(5):
            guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.HARD_WARN
        guard.record_tool_call("Bash", {"command": "pytest"})
        guard.record_tool_call("Bash", {"command": "pytest"})
        assert guard.check() == EscalationLevel.KILL

    def test_pattern_description_set(self):
        """Pattern description includes tool name and count."""
        guard = CompactionGuard()
        for _ in range(3):
            guard.record_tool_call("Bash", {"command": "pytest"})
        guard.check()
        assert "Bash" in guard._last_pattern_desc
        assert "3" in guard._last_pattern_desc

    def test_reset_all_clears_consecutive(self):
        guard = CompactionGuard()
        for _ in range(3):
            guard.record_tool_call("Bash", {"command": "pytest"})
        guard.reset_all()
        assert guard._consec_count == 0
        assert guard._last_pair is None

    def test_e2e_pytest_dead_loop_scenario(self):
        """Full integration test: the exact pattern from the bug report.

        Agent at 8% context on 1M window, PASSIVE phase, runs pytest
        6 times with cosmetic variations. Guard must escalate.
        """
        guard = CompactionGuard()
        guard._context_pct = 8.0
        guard._context_window = 1_000_000

        # The exact sequence from the bug report (normalized to same hash)
        commands = [
            "python -m pytest tests/ -x -q 2>&1 | tail -30",
            "python -m pytest tests/ -x -q 2>&1 | tail -20",
            "cd /Users/gawan/swarmai/backend && python -m pytest tests/ -x -q 2>&1 | tail -20",
            "rm -f /private/tmp/claude-503/pytest_lock 2>/dev/null; python -m pytest tests/ -x -q 2>&1 | tail -30",
            "python -m pytest tests/ -x -q 2>&1 | tail -20",
            "python -m pytest tests/ -x -q",
        ]

        results = []
        for cmd in commands:
            guard.record_tool_call("Bash", {"command": cmd})
            results.append(guard.check())

        # Call 1-2: MONITORING (< 3 consecutive)
        assert results[0] == EscalationLevel.MONITORING
        assert results[1] == EscalationLevel.MONITORING
        # Call 3: SOFT_WARN (3 consecutive)
        assert results[2] == EscalationLevel.SOFT_WARN
        # Call 5: HARD_WARN (5 consecutive)
        assert results[4] == EscalationLevel.HARD_WARN
        # Still PASSIVE phase the whole time
        assert guard.phase == GuardPhase.PASSIVE
