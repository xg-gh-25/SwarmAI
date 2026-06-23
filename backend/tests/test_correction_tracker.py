"""Tests for Evolution v3 MVP: CorrectionClassTracker.

Tests the correction class tracker — a pure state machine that
records correction events, tracks gate effectiveness, and auto-resolves
classes after 30 days of no post-gate recurrence.
"""

import json
import time
from pathlib import Path

import pytest

from core.evolution.correction_tracker import CorrectionClassTracker


@pytest.fixture
def tracker(tmp_path):
    """Create a tracker with a temp state file."""
    return CorrectionClassTracker(state_path=tmp_path / "tracker.json")


@pytest.fixture
def seeded_tracker(tmp_path):
    """Create a tracker pre-seeded with CLASS_A data."""
    state_path = tmp_path / "tracker.json"
    state_path.write_text(json.dumps({
        "CLASS_A": {
            "count": 11,
            "last": "2026-06-01",
            "active_gate": "GC12",
            "gate_deployed": "2026-06-01",
            "post_gate_count": 0,
            "resolved": False,
            "evidence": [],
        }
    }))
    return CorrectionClassTracker(state_path=state_path)


class TestRecordCorrection:
    """Test recording correction events."""

    def test_first_event_creates_class(self, tracker):
        tracker.record("CLASS_A", evidence="skipped adversarial")
        state = tracker.get_class("CLASS_A")
        assert state is not None
        assert state["count"] == 1
        assert state["resolved"] is False
        assert state["post_gate_count"] == 0

    def test_increments_count(self, tracker):
        tracker.record("CLASS_A", evidence="first")
        tracker.record("CLASS_A", evidence="second")
        state = tracker.get_class("CLASS_A")
        assert state["count"] == 2

    def test_updates_last_timestamp(self, tracker):
        tracker.record("CLASS_A", evidence="test")
        state = tracker.get_class("CLASS_A")
        # last should be today's date (YYYY-MM-DD format)
        assert len(state["last"]) == 10
        assert state["last"].count("-") == 2

    def test_increments_post_gate_count_when_gate_active(self, seeded_tracker):
        seeded_tracker.record("CLASS_A", evidence="new bypass")
        state = seeded_tracker.get_class("CLASS_A")
        assert state["count"] == 12
        assert state["post_gate_count"] == 1

    def test_persists_to_disk(self, tracker):
        tracker.record("CLASS_B", evidence="test")
        # Read raw file
        raw = json.loads(tracker._state_path.read_text())
        assert "CLASS_B" in raw
        assert raw["CLASS_B"]["count"] == 1

    def test_stores_evidence(self, tracker):
        tracker.record("CLASS_A", evidence="skipped adversarial review")
        state = tracker.get_class("CLASS_A")
        assert "evidence" in state
        assert len(state["evidence"]) == 1
        assert state["evidence"][0]["text"] == "skipped adversarial review"

    def test_evidence_capped_at_10(self, tracker):
        for i in range(15):
            tracker.record("CLASS_A", evidence=f"event {i}")
        state = tracker.get_class("CLASS_A")
        assert len(state["evidence"]) == 10
        # Keeps last 10 (indices 5-14)
        assert state["evidence"][0]["text"] == "event 5"

    def test_empty_evidence_not_stored(self, tracker):
        tracker.record("CLASS_A", evidence="")
        state = tracker.get_class("CLASS_A")
        assert state["evidence"] == []


class TestRegisterGate:
    """Test gate registration."""

    def test_register_gate_on_existing_class(self, seeded_tracker):
        seeded_tracker.register_gate("CLASS_A", gate_id="GC15", description="new fix")
        state = seeded_tracker.get_class("CLASS_A")
        assert state["active_gate"] == "GC15"
        assert state["post_gate_count"] == 0  # Reset on new gate

    def test_register_gate_on_new_class(self, tracker):
        tracker.register_gate("CLASS_X", gate_id="GC99", description="experimental")
        state = tracker.get_class("CLASS_X")
        assert state is not None
        assert state["active_gate"] == "GC99"
        assert state["count"] == 0


class TestAutoResolve:
    """Test 30-day auto-resolve logic."""

    def test_resolves_after_30_days_no_recurrence(self, tmp_path):
        """Class with gate deployed 31 days ago, post_gate=0 → resolved."""
        from datetime import datetime, timedelta
        old_date = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d")
        state_path = tmp_path / "tracker.json"
        state_path.write_text(json.dumps({
            "CLASS_A": {
                "count": 11,
                "last": old_date,
                "active_gate": "GC12",
                "gate_deployed": old_date,
                "post_gate_count": 0,
                "resolved": False,
            }
        }))
        tracker = CorrectionClassTracker(state_path=state_path)
        resolved = tracker.check_auto_resolve()
        assert "CLASS_A" in resolved
        state = tracker.get_class("CLASS_A")
        assert state["resolved"] is True

    def test_does_not_resolve_if_post_gate_nonzero(self, tmp_path):
        """Class with post_gate > 0 should NOT resolve even after 30 days."""
        from datetime import datetime, timedelta
        old_date = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d")
        state_path = tmp_path / "tracker.json"
        state_path.write_text(json.dumps({
            "CLASS_A": {
                "count": 11,
                "last": old_date,
                "active_gate": "GC12",
                "gate_deployed": old_date,
                "post_gate_count": 2,
                "resolved": False,
            }
        }))
        tracker = CorrectionClassTracker(state_path=state_path)
        resolved = tracker.check_auto_resolve()
        assert "CLASS_A" not in resolved

    def test_does_not_resolve_without_gate(self, tmp_path):
        """Class without an active gate should NOT auto-resolve."""
        from datetime import datetime, timedelta
        old_date = (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d")
        state_path = tmp_path / "tracker.json"
        state_path.write_text(json.dumps({
            "CLASS_A": {
                "count": 5,
                "last": old_date,
                "active_gate": None,
                "gate_deployed": None,
                "post_gate_count": 0,
                "resolved": False,
            }
        }))
        tracker = CorrectionClassTracker(state_path=state_path)
        resolved = tracker.check_auto_resolve()
        assert "CLASS_A" not in resolved


class TestBriefingSummary:
    """Test briefing output generation."""

    def test_empty_tracker_returns_empty(self, tracker):
        lines = tracker.briefing_lines()
        assert lines == []

    def test_resolved_classes_excluded(self, tmp_path):
        state_path = tmp_path / "tracker.json"
        state_path.write_text(json.dumps({
            "CLASS_A": {
                "count": 11, "last": "2026-06-01",
                "active_gate": "GC12", "gate_deployed": "2026-06-01",
                "post_gate_count": 0, "resolved": True,
            }
        }))
        tracker = CorrectionClassTracker(state_path=state_path)
        lines = tracker.briefing_lines()
        assert lines == []

    def test_green_status_when_gate_effective(self, seeded_tracker):
        lines = seeded_tracker.briefing_lines()
        assert len(lines) == 1
        assert "CLASS_A" in lines[0]
        assert "✅" in lines[0]  # ✅

    def test_amber_status_on_first_recurrence(self, seeded_tracker):
        seeded_tracker.record("CLASS_A", evidence="one slip")
        lines = seeded_tracker.briefing_lines()
        assert "⚠️" in lines[0]  # ⚠️

    def test_red_status_on_threshold(self, seeded_tracker):
        seeded_tracker.record("CLASS_A", evidence="slip 1")
        seeded_tracker.record("CLASS_A", evidence="slip 2")
        lines = seeded_tracker.briefing_lines()
        assert "\U0001f534" in lines[0]  # 🔴


class TestEdgeCases:
    """Edge cases and robustness."""

    def test_missing_state_file_creates_fresh(self, tmp_path):
        tracker = CorrectionClassTracker(state_path=tmp_path / "nonexistent.json")
        tracker.record("TEST", evidence="hi")
        assert tracker.get_class("TEST")["count"] == 1

    def test_corrupted_state_file_resets(self, tmp_path):
        state_path = tmp_path / "tracker.json"
        state_path.write_text("not valid json {{{")
        tracker = CorrectionClassTracker(state_path=state_path)
        # Should not crash, starts fresh
        tracker.record("TEST", evidence="recovery")
        assert tracker.get_class("TEST")["count"] == 1

    def test_concurrent_safety_via_atomic_write(self, tmp_path):
        """Verify state file is written atomically (tmp + rename)."""
        state_path = tmp_path / "tracker.json"
        tracker = CorrectionClassTracker(state_path=state_path)
        tracker.record("CLASS_A", evidence="test")
        # File should exist and be valid JSON
        data = json.loads(state_path.read_text())
        assert data["CLASS_A"]["count"] == 1
        # No .tmp file lingering
        assert not list(tmp_path.glob("*.tmp"))

    def test_get_class_returns_copy(self, tracker):
        """Verify get_class returns a copy, not a mutable reference."""
        tracker.record("CLASS_A", evidence="test")
        state = tracker.get_class("CLASS_A")
        state["count"] = 999  # Mutate the copy
        # Internal state should be unchanged
        actual = tracker.get_class("CLASS_A")
        assert actual["count"] == 1


# === v3 Phase 3: register_rule + post_rule_count mutual exclusion ===

def test_register_rule_sets_active_rule(tmp_path):
    """AC2: register_rule mirrors register_gate — sets active_rule, resets post_rule_count."""
    tr = CorrectionClassTracker(state_path=tmp_path / "t.json")
    tr.register_rule("CLASS_X", "RULE_CLASS_X_2026-06-23", "test rule")
    st = tr.get_class("CLASS_X")
    assert st["active_rule"] == "RULE_CLASS_X_2026-06-23"
    assert st["rule_deployed"] is not None
    assert st["post_rule_count"] == 0


def test_register_rule_creates_entry_if_absent(tmp_path):
    tr = CorrectionClassTracker(state_path=tmp_path / "t.json")
    tr.register_rule("NEW_CLASS", "R1")
    assert tr.get_class("NEW_CLASS") is not None


def test_post_rule_count_increments_when_rule_active_no_gate(tmp_path):
    """AC3: record() increments post_rule_count only when active_rule set AND no gate."""
    tr = CorrectionClassTracker(state_path=tmp_path / "t.json")
    tr.register_rule("CLASS_X", "R1")
    tr.record("CLASS_X")
    tr.record("CLASS_X")
    assert tr.get_class("CLASS_X")["post_rule_count"] == 2


def test_gate_supersedes_rule_freezes_post_rule_count(tmp_path):
    """AC3: once a gate is registered, post_rule_count FREEZES (gate supersedes rule)."""
    tr = CorrectionClassTracker(state_path=tmp_path / "t.json")
    tr.register_rule("CLASS_X", "R1")
    tr.record("CLASS_X")  # post_rule_count -> 1
    tr.register_gate("CLASS_X", "G1")  # gate now supersedes
    tr.record("CLASS_X")  # should bump post_gate_count, NOT post_rule_count
    st = tr.get_class("CLASS_X")
    assert st["post_rule_count"] == 1, "post_rule_count must freeze once gate active"
    assert st["post_gate_count"] == 1


def test_record_no_keyerror_on_legacy_state_without_rule_fields(tmp_path):
    """AC3: legacy entries (no active_rule key) must not KeyError on record()."""
    sp = tmp_path / "t.json"
    sp.write_text(json.dumps({
        "CLASS_A": {"count": 11, "last": "2026-06-01", "active_gate": "GC12",
                    "gate_deployed": "2026-06-01", "post_gate_count": 0,
                    "resolved": False, "evidence": []}  # NO active_rule/post_rule_count
    }))
    tr = CorrectionClassTracker(state_path=sp)
    tr.record("CLASS_A")  # must not raise
    assert tr.get_class("CLASS_A")["count"] == 12
