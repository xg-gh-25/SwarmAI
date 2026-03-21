"""CompactionGuard bugfix exploration and preservation tests.

This test file contains two groups of tests for the CompactionGuard bugfix spec:

**Group A — Bug Condition Exploration (Task 1):**
  Tests 1a–1c encode the EXPECTED (correct) behavior for three bugs.
  These tests MUST FAIL on unfixed code, confirming the bugs exist:
  - 1a: Dynamic threshold — ``_compute_activation_pct`` method doesn't exist yet
  - 1b: Escalation persistence — ``reset()`` wipes escalation to MONITORING
  - 1c: Progress detection — no consecutive non-productive tracker exists

**Group B — Preservation (Task 2):**
  Tests 2a–2f verify existing behavior that must remain unchanged after the fix.
  These tests MUST PASS on unfixed code:
  - 2a: 200K threshold constant is 85
  - 2b: PASSIVE phase always returns MONITORING
  - 2c: ``reset_all()`` fully resets all state
  - 2d: ``record_tool_call()`` never raises on malformed input
  - 2e: Set-overlap detection works in ACTIVE phase
  - 2f: Single-tool repetition detection works in ACTIVE phase

Testing methodology: pytest with Hypothesis for property-based tests.
"""
from __future__ import annotations

import pytest
from hypothesis import given, strategies as st, settings

from core.compaction_guard import (
    CompactionGuard,
    EscalationLevel,
    GuardPhase,
    _CONTEXT_ACTIVATION_PCT,
)


# ═══════════════════════════════════════════════════════════════════
# GROUP A — Bug Condition Exploration Tests (Task 1)
# These tests encode EXPECTED behavior and MUST FAIL on unfixed code.
# ═══════════════════════════════════════════════════════════════════


class TestBugConditionExploration:
    """Bug condition exploration tests — expected to FAIL on unfixed code.

    Each test encodes the correct behavior that the fix should produce.
    Failure on unfixed code confirms the bug exists.
    """

    # ── 1a: Dynamic Threshold ────────────────────────────────────

    def test_dynamic_threshold_method_exists(self):
        """_compute_activation_pct should exist and return scaled values.

        **Validates: Requirements 1.1, 2.1**

        On unfixed code: method doesn't exist → AttributeError → FAIL.
        After fix: 1M window → 40% threshold.
        """
        guard = CompactionGuard()
        # This method doesn't exist on unfixed code → AttributeError
        result = guard._compute_activation_pct(1_000_000)
        assert result == 40.0  # 1M window → 40% threshold

    # ── 1b: Escalation Persistence ───────────────────────────────

    def test_reset_preserves_escalation(self):
        """reset() should NOT wipe escalation level.

        **Validates: Requirements 1.2, 2.2**

        On unfixed code: reset() sets _escalation = MONITORING → FAIL.
        After fix: escalation persists across reset().
        """
        guard = CompactionGuard()
        guard._escalation = EscalationLevel.SOFT_WARN
        guard.reset()
        # On unfixed code: reset() sets _escalation = MONITORING → FAIL
        assert guard.escalation == EscalationLevel.SOFT_WARN

    # ── 1c: Progress Detection ───────────────────────────────────

    def test_progress_detection_fires(self):
        """15+ consecutive non-productive calls should trigger escalation.

        **Validates: Requirements 1.3, 2.3**

        On unfixed code: no progress tracker → returns MONITORING → FAIL.
        After fix: 20 consecutive Read calls → at least SOFT_WARN.
        """
        guard = CompactionGuard()
        guard._phase = GuardPhase.ACTIVE
        # Record 20 consecutive Read calls
        for _ in range(20):
            guard.record_tool_call("Read", {"path": "test.py"})
        level = guard.check()
        # On unfixed code: no progress tracker → returns MONITORING → FAIL
        assert level in (
            EscalationLevel.SOFT_WARN,
            EscalationLevel.HARD_WARN,
            EscalationLevel.KILL,
        )


# ═══════════════════════════════════════════════════════════════════
# GROUP B — Preservation Tests (Task 2)
# These tests verify existing behavior and MUST PASS on unfixed code.
# ═══════════════════════════════════════════════════════════════════


class TestPreservation:
    """Preservation tests — must PASS on unfixed code.

    Each test verifies existing behavior that must remain unchanged
    after the bugfix is applied.
    """

    # ── 2a: 200K Threshold ───────────────────────────────────────

    def test_200k_threshold_is_85(self):
        """For 200K windows, activation threshold should be 85%.

        **Validates: Requirements 3.1**

        The module constant _CONTEXT_ACTIVATION_PCT must be 85.
        """
        assert _CONTEXT_ACTIVATION_PCT == 85

    # ── 2b: PASSIVE Phase ────────────────────────────────────────

    def test_passive_phase_returns_monitoring(self):
        """PASSIVE phase always returns MONITORING regardless of state.

        **Validates: Requirements 3.2**

        Even with high context and many tool calls, PASSIVE → MONITORING.
        """
        guard = CompactionGuard()  # starts PASSIVE
        guard._context_pct = 95.0  # high context
        for _ in range(10):
            guard.record_tool_call("Read", {"path": "x"})
        assert guard.check() == EscalationLevel.MONITORING

    # ── 2c: reset_all Full Reset ─────────────────────────────────

    def test_reset_all_clears_everything(self):
        """reset_all() fully resets all state to initial values.

        **Validates: Requirements 3.3**
        """
        guard = CompactionGuard()
        guard._phase = GuardPhase.ACTIVE
        guard._escalation = EscalationLevel.HARD_WARN
        guard._context_pct = 90.0
        guard.record_tool_call("Read", {"path": "x"})
        guard.reset_all()
        assert guard.phase == GuardPhase.PASSIVE
        assert guard.escalation == EscalationLevel.MONITORING
        assert guard.context_pct == 0.0
        assert len(guard._tool_records) == 0

    # ── 2d: Exception Safety ─────────────────────────────────────

    def test_record_tool_call_never_raises(self):
        """record_tool_call() never raises on malformed input.

        **Validates: Requirements 3.9, 3.10**
        """
        guard = CompactionGuard()
        # None input
        guard.record_tool_call("Read", None)
        # Empty string
        guard.record_tool_call("", "")
        # Huge nested dict
        guard.record_tool_call("Read", {"a": {"b": {"c": "d" * 10000}}})
        # No exception = pass

    # ── 2e: Set-Overlap Detection ────────────────────────────────

    def test_set_overlap_detection_works(self):
        """Set-overlap detection escalates when >60% overlap in ACTIVE phase.

        **Validates: Requirements 3.4, 3.5**
        """
        guard = CompactionGuard()
        # Build baseline
        for i in range(5):
            guard.record_tool_call("Read", {"path": f"file{i}.py"})
        guard._phase = GuardPhase.ACTIVE
        guard._pre_compaction_set = set(guard._rolling_baseline_set)
        guard._post_compaction_sequence = []
        # Record same calls again (>60% overlap)
        for i in range(5):
            guard.record_tool_call("Read", {"path": f"file{i}.py"})
        guard._context_pct = 90.0  # above 85% threshold
        level = guard.check()
        assert level != EscalationLevel.MONITORING  # should escalate

    # ── 2f: Single-Tool Repetition ───────────────────────────────

    def test_single_tool_repetition_detection(self):
        """Single-tool repetition (≥5 identical calls) triggers escalation.

        **Validates: Requirements 3.4, 3.5**
        """
        guard = CompactionGuard()
        guard._phase = GuardPhase.ACTIVE
        guard._pre_compaction_set = set()
        # Record same call 5+ times
        for _ in range(6):
            guard.record_tool_call("Read", {"path": "same_file.py"})
        guard._context_pct = 90.0
        level = guard.check()
        assert level != EscalationLevel.MONITORING
