"""Tests for Evolution v3 MVP: CorrectionClassTracker.

Tests the correction class tracker — a pure state machine that
records correction events, tracks gate effectiveness, and auto-resolves
classes after 30 days of no post-gate recurrence.
"""

import json

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

    def test_unregister_rule_clears_active_rule(self, tracker):
        # register then unregister must fully invert register_rule
        tracker.register_rule("CLASS_A", "RULE_CLASS_A")
        assert tracker.get_class("CLASS_A")["active_rule"] == "RULE_CLASS_A"
        tracker.record("CLASS_A", evidence="post-rule recurrence")
        count_before_unregister = tracker.get_class("CLASS_A")["count"]
        assert tracker.get_class("CLASS_A")["post_rule_count"] == 1
        tracker.unregister_rule("CLASS_A")
        post = tracker.get_class("CLASS_A")
        assert post["active_rule"] is None
        assert post["rule_deployed"] is None
        assert post["post_rule_count"] == 0
        # count/evidence must be preserved — unregister reverts only the rule marker
        assert post["count"] == count_before_unregister

    def test_unregister_rule_persists_to_disk(self, tracker):
        tracker.register_rule("CLASS_C", "RULE_CLASS_C")
        tracker.unregister_rule("CLASS_C")
        raw = json.loads(tracker._state_path.read_text())
        assert raw["CLASS_C"]["active_rule"] is None

    def test_unregister_rule_unknown_class_is_noop(self, tracker):
        # must not raise or create a phantom class
        tracker.unregister_rule("CLASS_NEVER_SEEN")
        assert tracker.get_class("CLASS_NEVER_SEEN") is None

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


class TestRecordIdempotentByRef:
    """run_448a4f7f: autonomous cognitive recording must be idempotent by
    correction_ref. The live data showed ONE correction_ref (d6d44889) parked
    3x in governance_pending.json with 3 DIFFERENT evidence strings — text-dedup
    would triple-count it. Dedup MUST key on the stable correction_ref, with a
    persisted seen-ref ledger separate from the 10-cap evidence list (Gate-1)."""

    def test_record_with_ref_counts_once(self, tracker):
        """Same correction_ref recorded twice → count increments ONCE."""
        tracker.record("CLASS_A", evidence="symptom phrasing 1", correction_ref="r-abc")
        tracker.record("CLASS_A", evidence="symptom phrasing 2 (different text)",
                       correction_ref="r-abc")
        state = tracker.get_class("CLASS_A")
        assert state["count"] == 1, "same correction_ref must not double-count"

    def test_distinct_refs_count_separately(self, tracker):
        tracker.record("CLASS_A", evidence="e1", correction_ref="r-1")
        tracker.record("CLASS_A", evidence="e2", correction_ref="r-2")
        assert tracker.get_class("CLASS_A")["count"] == 2

    def test_ref_dedup_survives_evidence_cap_rolloff(self, tracker):
        """The seen-ref ledger must NOT be the 10-cap evidence list: after >10
        distinct refs, re-recording the FIRST ref must still be a no-op even
        though its evidence aged out of the rolling window."""
        for i in range(12):
            tracker.record("CLASS_A", evidence=f"e{i}", correction_ref=f"r-{i}")
        assert tracker.get_class("CLASS_A")["count"] == 12
        # r-0's evidence has rolled off the 10-cap; re-record must still no-op
        tracker.record("CLASS_A", evidence="e0-again", correction_ref="r-0")
        assert tracker.get_class("CLASS_A")["count"] == 12, \
            "ref dedup must persist beyond the evidence-retention window"

    def test_ref_dedup_persists_across_reload(self, tmp_path):
        """The seen-ref ledger persists to disk — a new tracker instance still
        dedupes (cross-session: the maintenance hook builds a fresh tracker)."""
        sp = tmp_path / "tracker.json"
        t1 = CorrectionClassTracker(state_path=sp)
        t1.record("CLASS_A", evidence="e", correction_ref="r-x")
        t2 = CorrectionClassTracker(state_path=sp)
        t2.record("CLASS_A", evidence="e again", correction_ref="r-x")
        assert t2.get_class("CLASS_A")["count"] == 1

    def test_no_ref_preserves_legacy_behavior(self, tracker):
        """record() without a correction_ref keeps current behavior (the
        operational 'counted' path passes no ref) — every call counts."""
        tracker.record("CLASS_A", evidence="a")
        tracker.record("CLASS_A", evidence="b")
        assert tracker.get_class("CLASS_A")["count"] == 2

    def test_drift_merge_unions_recorded_refs(self, tmp_path):
        """Gate-2 M1: when two raw keys canonicalize to one class, _merge_drift
        sums count — so it MUST also UNION recorded_refs across all members. If
        it kept only members[0]'s refs, a dropped ref could re-record and inflate
        the counter past true recurrence (false structural proposal)."""
        sp = tmp_path / "tracker.json"
        # Two raw keys that canonicalize to CLASS_A (drift), each with distinct refs.
        sp.write_text(json.dumps({
            "CLASS_A": {"count": 2, "recorded_refs": ["r-1", "r-2"], "evidence": []},
            "class a": {"count": 1, "recorded_refs": ["r-3"], "evidence": []},
        }))
        tracker = CorrectionClassTracker(state_path=sp)
        state = tracker.get_class("CLASS_A")
        assert state["count"] == 3, "merge sums count"
        assert set(state["recorded_refs"]) == {"r-1", "r-2", "r-3"}, \
            "merge must UNION refs — a dropped ref re-inflates the counter"
        # And the union holds: re-recording any merged ref is a no-op.
        tracker.record("CLASS_A", evidence="dup", correction_ref="r-3")
        assert tracker.get_class("CLASS_A")["count"] == 3, "merged ref stays deduped"


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


class TestHasRed:
    """has_red() — structural red signal (M4-3), format-independent.

    Replaces the briefing's old substring-scan of the 🔴 emoji in briefing_lines.
    Each test asserts has_red() AGREES with the emoji presence so the structural
    signal is provably equivalent to (and more robust than) the render scan.
    """

    def _emoji_red(self, tracker) -> bool:
        return any("\U0001f534" in ln for ln in tracker.briefing_lines())

    def test_empty_tracker_not_red(self, tracker):
        assert tracker.has_red() is False
        assert tracker.has_red() == self._emoji_red(tracker)

    def test_green_gate_not_red(self, seeded_tracker):
        assert seeded_tracker.has_red() is False
        assert seeded_tracker.has_red() == self._emoji_red(seeded_tracker)

    def test_amber_not_red(self, seeded_tracker):
        seeded_tracker.record("CLASS_A", evidence="one slip")  # post_gate=1 → ⚠️
        assert seeded_tracker.has_red() is False
        assert seeded_tracker.has_red() == self._emoji_red(seeded_tracker)

    def test_red_at_threshold(self, seeded_tracker):
        seeded_tracker.record("CLASS_A", evidence="slip 1")
        seeded_tracker.record("CLASS_A", evidence="slip 2")  # post_gate=2 → 🔴
        assert seeded_tracker.has_red() is True
        assert seeded_tracker.has_red() == self._emoji_red(seeded_tracker)

    def test_resolved_red_class_excluded(self, tmp_path):
        # A resolved class at red count must NOT register as red.
        state_path = tmp_path / "tracker.json"
        state_path.write_text(json.dumps({
            "CLASS_A": {
                "count": 20, "last": "2026-06-01",
                "active_gate": "GC12", "gate_deployed": "2026-06-01",
                "post_gate_count": 5, "resolved": True,
            }
        }))
        tracker = CorrectionClassTracker(state_path=state_path)
        assert tracker.has_red() is False
        assert tracker.has_red() == self._emoji_red(tracker)

    def test_rule_recurrence_red(self, tmp_path):
        # No gate, but rule recurrence past threshold → red (matches briefing_lines).
        state_path = tmp_path / "tracker.json"
        state_path.write_text(json.dumps({
            "CLASS_B": {
                "count": 10, "last": "2026-06-01",
                "active_rule": "R16b", "rule_deployed": "2026-06-01",
                "post_rule_count": 2, "resolved": False,
            }
        }))
        tracker = CorrectionClassTracker(state_path=state_path)
        assert tracker.has_red() is True
        assert tracker.has_red() == self._emoji_red(tracker)


class TestHasGateFailure:
    """has_gate_failure() — the DIVERGENCE signal (M4-3 meta-review fix).

    Distinct from has_red(): divergence on a clean eval score must fire ONLY
    when a deployed structural GATE failed (post_gate >= RED) — the real "the
    loop is broken despite a green score" event. A rule-only chronically-recurring
    class (post_rule >= RED, no gate) is a KNOWN-OPEN item already surfaced by the
    per-class tracker line; headlining it as DIVERGENCE every session forever =
    banner-blindness (meta-review HIGH, verified against live OPERATIONAL class
    recurring 799x on a rule). So gate-failure ⊊ red.
    """

    def test_empty_not_gate_failure(self, tracker):
        assert tracker.has_gate_failure() is False

    def test_gate_recurrence_is_gate_failure(self, tmp_path):
        state_path = tmp_path / "tracker.json"
        state_path.write_text(json.dumps({
            "CLASS_A": {
                "count": 20, "last": "2026-06-01",
                "active_gate": "GC12", "gate_deployed": "2026-06-01",
                "post_gate_count": 2, "resolved": False,
            }
        }))
        t = CorrectionClassTracker(state_path=state_path)
        assert t.has_gate_failure() is True
        assert t.has_red() is True  # gate-failure implies red

    def test_rule_only_recurrence_is_NOT_gate_failure(self, tmp_path):
        # The live OPERATIONAL case: rule recurring past threshold, NO gate.
        # has_red() True (known-open) but has_gate_failure() False → no divergence
        # headline. This is the banner-blindness fix.
        state_path = tmp_path / "tracker.json"
        state_path.write_text(json.dumps({
            "OPERATIONAL": {
                "count": 799, "last": "2026-06-25",
                "active_rule": "RULE_OPERATIONAL", "rule_deployed": "2026-06-01",
                "post_rule_count": 50, "resolved": False,
            }
        }))
        t = CorrectionClassTracker(state_path=state_path)
        assert t.has_red() is True, "rule recurrence is still red (known-open)"
        assert t.has_gate_failure() is False, "but NOT a gate failure → no divergence headline"

    def test_resolved_gate_excluded(self, tmp_path):
        state_path = tmp_path / "tracker.json"
        state_path.write_text(json.dumps({
            "CLASS_A": {
                "count": 20, "active_gate": "GC12", "gate_deployed": "2026-06-01",
                "post_gate_count": 5, "resolved": True,
            }
        }))
        t = CorrectionClassTracker(state_path=state_path)
        assert t.has_gate_failure() is False

    def test_amber_gate_not_failure(self, seeded_tracker):
        seeded_tracker.record("CLASS_A", evidence="one slip")  # post_gate=1 → amber
        assert seeded_tracker.has_gate_failure() is False


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


# === Bugfix run_40fad09e: canonical class-key drift fix ===
#
# The v3 escalation ladder gate rung could never fire for axis-named classes
# because record()/get_class() used the RAW key (lowercase "operational") while
# escalate_class + the dashboard accept path used the CANONICAL key
# ("OPERATIONAL"). The same logical class split into two entries; the accepted
# rule landed on a count=0 phantom while real recurrence accumulated under a
# key whose active_rule stayed null forever.


def _drift_state():
    """The exact live-drift shape: operational(743,no-rule) + OPERATIONAL(0,rule)."""
    return {
        "operational": {
            "count": 743, "last": "2026-06-22", "active_rule": None,
            "rule_deployed": None, "post_rule_count": 0, "active_gate": None,
            "gate_deployed": None, "post_gate_count": 0, "resolved": False,
            "evidence": [{"date": "2026-06-20", "text": "old slip"}],
        },
        "OPERATIONAL": {
            "count": 0, "last": None, "active_rule": "RULE_OPERATIONAL",
            "rule_deployed": "2026-06-23", "post_rule_count": 0, "active_gate": None,
            "gate_deployed": None, "post_gate_count": 0, "resolved": False,
            "evidence": [],
        },
        "CLASS_A": {
            "count": 4, "last": "2026-06-23", "active_rule": None, "rule_deployed": None,
            "post_rule_count": 0, "active_gate": None, "gate_deployed": None,
            "post_gate_count": 0, "resolved": False, "evidence": [],
        },
    }


class TestCanonicalKeyConvergence:
    """AC1: mixed-case record/register/get all hit ONE canonical entry."""

    def test_record_lowercase_then_get_canonical(self, tmp_path):
        tr = CorrectionClassTracker(state_path=tmp_path / "t.json")
        tr.record("operational", evidence="slip")
        # Both spellings resolve to the same single entry.
        assert tr.get_class("operational")["count"] == 1
        assert tr.get_class("OPERATIONAL")["count"] == 1
        assert tr.class_names() == ["OPERATIONAL"]

    def test_register_rule_canonical_record_lowercase_same_entry(self, tmp_path):
        tr = CorrectionClassTracker(state_path=tmp_path / "t.json")
        tr.register_rule("OPERATIONAL", "RULE_OPERATIONAL")
        tr.record("operational")  # lowercase axis name from router
        st = tr.get_class("OPERATIONAL")
        assert st["active_rule"] == "RULE_OPERATIONAL"
        assert st["post_rule_count"] == 1  # the lowercase record hit the ruled entry


class TestDriftMigration:
    """AC2 + AC3: idempotent self-healing merge, no data loss."""

    def test_merge_collapses_drift_into_canonical(self, tmp_path):
        sp = tmp_path / "t.json"
        sp.write_text(json.dumps(_drift_state()))
        tr = CorrectionClassTracker(state_path=sp)
        # operational + OPERATIONAL merged into ONE canonical entry.
        names = set(tr.class_names())
        assert "operational" not in names
        assert names == {"OPERATIONAL", "CLASS_A"}
        op = tr.get_class("OPERATIONAL")
        assert op["count"] == 743  # summed (743 + 0)
        assert op["active_rule"] == "RULE_OPERATIONAL"  # OR-merged from the ruled member
        assert op["rule_deployed"] == "2026-06-23"

    def test_merge_preserves_single_member_classes_unchanged(self, tmp_path):
        sp = tmp_path / "t.json"
        sp.write_text(json.dumps(_drift_state()))
        tr = CorrectionClassTracker(state_path=sp)
        ca = tr.get_class("CLASS_A")
        assert ca["count"] == 4  # untouched

    def test_merge_is_idempotent(self, tmp_path):
        """AC2: running the merge twice yields identical state."""
        sp = tmp_path / "t.json"
        sp.write_text(json.dumps(_drift_state()))
        tr1 = CorrectionClassTracker(state_path=sp)
        tr1.record("OPERATIONAL")  # forces a _save of the merged state
        first = json.loads(sp.read_text())
        tr2 = CorrectionClassTracker(state_path=sp)  # re-loads + re-merges
        tr2.record("OPERATIONAL")
        second = json.loads(sp.read_text())
        # Drop the count delta from the two extra records; compare structure/keys.
        assert set(first.keys()) == set(second.keys())
        assert first["OPERATIONAL"]["active_rule"] == second["OPERATIONAL"]["active_rule"]

    def test_merge_preserves_evidence(self, tmp_path):
        sp = tmp_path / "t.json"
        sp.write_text(json.dumps(_drift_state()))
        tr = CorrectionClassTracker(state_path=sp)
        op = tr.get_class("OPERATIONAL")
        texts = [e["text"] for e in op["evidence"]]
        assert "old slip" in texts  # evidence from the lowercase member survived

    def test_gate1_loser_postcount_and_none_deploy_no_crash(self, tmp_path):
        """AC6 (Gate-1): loser member has non-zero post_rule_count AND None
        rule_deployed — must NOT crash _load, and the merged counter must come
        from the rule-WINNER only (not summed across members)."""
        sp = tmp_path / "t.json"
        sp.write_text(json.dumps({
            # loser: stale post_rule_count against a rule with None deploy date
            "operational": {
                "count": 10, "last": "2026-06-01", "active_rule": None,
                "rule_deployed": None, "post_rule_count": 5, "active_gate": None,
                "gate_deployed": None, "post_gate_count": 0, "resolved": False,
                "evidence": [],
            },
            # winner: owns the active rule
            "OPERATIONAL": {
                "count": 3, "last": "2026-06-20", "active_rule": "RULE_OP",
                "rule_deployed": "2026-06-20", "post_rule_count": 1, "active_gate": None,
                "gate_deployed": None, "post_gate_count": 0, "resolved": False,
                "evidence": [],
            },
        }))
        tr = CorrectionClassTracker(state_path=sp)  # must not raise
        op = tr.get_class("OPERATIONAL")
        assert op["count"] == 13  # counts summed
        # post_rule_count carried from the rule-WINNER only (1), NOT summed (would be 6).
        assert op["post_rule_count"] == 1
        assert op["active_rule"] == "RULE_OP"


    def test_gate2_mixed_type_dates_still_merge_not_degrade(self, tmp_path):
        """Gate-2 HIGH: a legacy entry with a non-string (int) date must NOT make
        the merge raise — that would degrade _load to raw state and silently
        RE-BURY the drift this fix exists to cure. The merge must SUCCEED."""
        sp = tmp_path / "t.json"
        sp.write_text(json.dumps({
            "operational": {
                "count": 743, "last": "2026-06-01", "active_rule": None,
                "rule_deployed": None, "post_rule_count": 0, "active_gate": None,
                "gate_deployed": None, "post_gate_count": 0, "resolved": False,
                "evidence": [{"date": 20260601, "text": "int-dated evidence"}],
            },
            "OPERATIONAL": {
                "count": 5, "last": 20260620, "active_rule": "RULE_OP",  # int last!
                "rule_deployed": 20260620, "post_rule_count": 0, "active_gate": None,
                "gate_deployed": None, "post_gate_count": 0, "resolved": False,
                "evidence": [],
            },
        }))
        tr = CorrectionClassTracker(state_path=sp)
        # MUST have merged (not degraded to raw — which would keep 'operational').
        assert tr.class_names() == ["OPERATIONAL"], "merge must succeed despite int dates"
        assert tr.get_class("OPERATIONAL")["count"] == 748

    def test_gate2_merge_clears_non_winner_rule_fields(self, tmp_path):
        """Gate-2 LOW-3 follow-on: copying members[0] must NOT leak a stale
        active_rule when members[0] is not the rule winner."""
        sp = tmp_path / "t.json"
        sp.write_text(json.dumps({
            # members[0] has NO rule
            "operational": {
                "count": 100, "last": "2026-06-01", "active_rule": None,
                "rule_deployed": None, "post_rule_count": 0, "active_gate": None,
                "gate_deployed": None, "post_gate_count": 0, "resolved": False,
                "evidence": [],
            },
            "OPERATIONAL": {
                "count": 2, "last": "2026-06-20", "active_rule": "RULE_OP",
                "rule_deployed": "2026-06-20", "post_rule_count": 0, "active_gate": None,
                "gate_deployed": None, "post_gate_count": 0, "resolved": False,
                "evidence": [],
            },
        }))
        tr = CorrectionClassTracker(state_path=sp)
        op = tr.get_class("OPERATIONAL")
        assert op["active_rule"] == "RULE_OP"  # winner's rule, regardless of order


class TestLadderReconnect:
    """AC4: the actual bug — record->register_rule->record x2 reaches the gate rung."""

    def test_full_reconnect_chain_reaches_gate(self, tmp_path):
        # Uses a COGNITIVE class (CLASS_A) — governance escalation is cognitive-only
        # (OPERATIONAL/UNCLASSIFIED are axis-guarded out, see escalation_ladder).
        # This test verifies the RECONNECT MECHANISM: record (drifted casing) →
        # register_rule (canonical) → record x2 → all land on ONE canonical entry
        # and reach the gate rung. The class choice is incidental to that mechanism;
        # it must be cognitive so the axis guard doesn't (correctly) short-circuit it.
        from core.evolution.escalation_ladder import canonical_class_key, decide_escalation
        tr = CorrectionClassTracker(state_path=tmp_path / "t.json")
        # accumulate under a drifted-casing name (as raw signal sources do)
        for _ in range(3):
            tr.record("class a")  # canonicalizes to CLASS_A
        # accept a rule (dashboard passes the canonical source_class)
        tr.register_rule("CLASS_A", "GC-CLASSA")
        # class recurs twice MORE (drifted name again)
        tr.record("class a")
        tr.record("class a")
        st = tr.get_class("CLASS_A")
        assert st["post_rule_count"] == 2, "recurrence post-rule must land on the ruled entry"
        decision = decide_escalation(st, class_name=canonical_class_key("class a"))
        assert decision.kind == "gate", "ladder must escalate rule->gate after RED recurrence"

    def test_operational_axis_never_escalates_even_after_rule_recurrence(self):
        """Companion to the above: the SAME reconnect chain on OPERATIONAL must
        NOT reach a gate — operator-noise axes are guarded out of governance.
        Documents the deliberate contract change (was: OPERATIONAL escalated)."""
        from core.evolution.escalation_ladder import canonical_class_key, decide_escalation
        st = {"count": 5, "active_gate": None, "active_rule": "RULE_OPERATIONAL",
              "post_rule_count": 2, "resolved": False}
        decision = decide_escalation(st, class_name=canonical_class_key("operational"))
        assert decision.kind == "none", "OPERATIONAL must never escalate to governance"


class TestEmptyClassName:
    """AC5: empty/whitespace class name must not crash."""

    def test_record_empty_name_no_crash(self, tmp_path):
        tr = CorrectionClassTracker(state_path=tmp_path / "t.json")
        tr.record("", evidence="x")  # must not raise
        tr.record("   ")  # whitespace -> canonical "" — must not raise
        assert tr.get_class("") is None or isinstance(tr.get_class(""), dict)
