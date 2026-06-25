"""Tests for Evolution Pipeline v3 Phase 1: governance_router.

The router takes a JudgmentClassification and decides:
  - operational / counter_state="counted"  -> tracker.record() ONCE (auto-count)
  - cognitive / counter_state="pending_confirm" -> park in pending queue,
    tracker.record() NEVER called, emit a SOUL Intake Gate brief.

DoD negative tests:
  - AC3: CLASS_A classification NEVER auto-increments the counter.
  - AC4: operational classification calls record() exactly once (auto path live).
  - AC6: cognitive routing emits an Intake brief (classify/parent/conflict/budget).

Safety (design §9): router NEVER writes SOUL/AGENT/STEERING. The pending queue
is the only persistence; promotion happens later through the human gate.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from core.evolution.judgment_classifier import JudgmentClassification
from core.evolution.governance_router import (
    classify_new_corrections,
    route_classification,
)


def _cognitive(class_name="CLASS_A", principle="P1"):
    return JudgmentClassification(
        correction_ref="1781696331.24:e95e5923",
        axis="cognitive",
        class_name=class_name,
        parent_principle=principle,
        skill_spread=[],
        blast_radius=0,
        evidence=["shipped 3 unverified subsystems"],
        tier="llm",
        confidence=0.8,
        counter_state="pending_confirm",
    )


def _operational():
    return JudgmentClassification(
        correction_ref="1781055459.41:cbcf9db7",
        axis="operational",
        class_name=None,
        parent_principle=None,
        skill_spread=["Bash"],
        blast_radius=1,
        evidence=["Exit code 2: genuine defect"],
        tier="mechanical",
        confidence=0.5,
        counter_state="counted",
    )


def _ignored():
    """Operational NOISE — counter_state='ignored'. Must neither count nor park."""
    return JudgmentClassification(
        correction_ref="1781055459.99:cbcf9db7",
        axis="operational",
        class_name=None,
        parent_principle=None,
        skill_spread=["Bash"],
        blast_radius=1,
        evidence=["File does not exist. current working directory is /Users/..."],
        tier="mechanical",
        confidence=0.5,
        counter_state="ignored",
    )


@pytest.fixture
def pending_path(tmp_path):
    return tmp_path / "governance_pending.json"


# --- AC3 (NEGATIVE): CLASS_A NEVER auto-increments the counter ---

def test_ac3_class_a_never_calls_record(pending_path):
    """AC3: a CLASS_A (cognitive) classification must NOT call tracker.record()."""
    tracker = MagicMock()
    route_classification(_cognitive("CLASS_A"), tracker, pending_path=pending_path)
    tracker.record.assert_not_called()


def test_ac3_all_cognitive_classes_park(pending_path):
    """Every cognitive CLASS parks in pending_confirm, none auto-count."""
    tracker = MagicMock()
    for cls in ("CLASS_A", "CLASS_B", "CLASS_C"):
        route_classification(_cognitive(cls), tracker, pending_path=pending_path)
    tracker.record.assert_not_called()
    queue = json.loads(pending_path.read_text())
    assert len(queue) == 3
    assert {item["class_name"] for item in queue} == {"CLASS_A", "CLASS_B", "CLASS_C"}


# --- AC4: operational calls record() exactly once (auto-count path LIVE) ---

def test_ac4_operational_records_once(pending_path):
    """AC4: operational classification auto-counts via record() exactly once."""
    tracker = MagicMock()
    route_classification(_operational(), tracker, pending_path=pending_path)
    tracker.record.assert_called_once()


# --- Noise gate: counter_state='ignored' neither counts NOR parks ---

def test_ignored_does_not_call_record(pending_path):
    """An 'ignored' (noise) classification must NOT increment the tracker."""
    tracker = MagicMock()
    route_classification(_ignored(), tracker, pending_path=pending_path)
    tracker.record.assert_not_called()


def test_ignored_does_not_park(pending_path):
    """An 'ignored' classification must NOT fall through into the pending queue
    (the latent PARK bug: without an explicit guard, ignored would be treated as
    the pending_confirm fallthrough and wrongly parked as cognitive)."""
    tracker = MagicMock()
    brief = route_classification(_ignored(), tracker, pending_path=pending_path)
    assert brief is None
    assert not pending_path.exists() or json.loads(pending_path.read_text()) == []


def test_ignored_returns_none(pending_path):
    """Ignored routes to a clean no-op (returns None, no Intake brief)."""
    assert route_classification(_ignored(), MagicMock(), pending_path=pending_path) is None


def test_ac4_operational_does_not_park(pending_path):
    """Operational records do NOT go into the pending-confirm queue."""
    tracker = MagicMock()
    route_classification(_operational(), tracker, pending_path=pending_path)
    # queue file either absent or empty
    if pending_path.exists():
        assert json.loads(pending_path.read_text()) == []


# --- AC6: cognitive routing emits a SOUL Intake Gate brief ---

def test_ac6_cognitive_emits_intake_brief(pending_path):
    """AC6: cognitive routing returns an Intake brief with the 4 SOUL gate keys."""
    tracker = MagicMock()
    brief = route_classification(_cognitive("CLASS_A", "P1"), tracker, pending_path=pending_path)
    assert brief is not None
    for key in ("classify", "parent", "conflict", "budget"):
        assert key in brief, f"intake brief missing '{key}'"
    assert brief["parent"] == "P1"


def test_ac6_operational_no_brief(pending_path):
    """Operational routing returns None (no governance brief needed)."""
    tracker = MagicMock()
    brief = route_classification(_operational(), tracker, pending_path=pending_path)
    assert brief is None


# --- Pending queue is flock-safe append (re-read under lock) ---

def test_pending_queue_appends_not_overwrites(pending_path):
    """A second cognitive routing appends, preserving the first."""
    tracker = MagicMock()
    route_classification(_cognitive("CLASS_A"), tracker, pending_path=pending_path)
    route_classification(_cognitive("CLASS_B"), tracker, pending_path=pending_path)
    queue = json.loads(pending_path.read_text())
    assert len(queue) == 2


def test_none_classification_is_noop(pending_path):
    """Routing a None classification (degraded) is a safe no-op."""
    tracker = MagicMock()
    brief = route_classification(None, tracker, pending_path=pending_path)
    assert brief is None
    tracker.record.assert_not_called()


# --- Watermark gating (Gate-1 fix: no re-processing, no double-count) ---

def _write_corpus(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def test_watermark_only_processes_new_records(tmp_path):
    """Gate-1 fix: a second run with no new records processes nothing."""
    corpus = tmp_path / "corrections.jsonl"
    wm = tmp_path / "wm.json"
    pending = tmp_path / "pending.json"
    tracker = MagicMock()
    _write_corpus(corpus, [
        {"ts": 100.0, "session_id": "a", "type": "tool_failure", "tool": "Bash", "error": "boom"},
        {"ts": 200.0, "session_id": "b", "type": "tool_failure", "tool": "Glob", "error": "boom2"},
    ])
    s1 = classify_new_corrections(
        corrections_path=corpus, watermark_path=wm, pending_path=pending, tracker=tracker
    )
    assert s1["processed"] == 2
    assert s1["operational"] == 2
    # Second run: no new records past watermark -> zero processed, zero new record() calls.
    tracker.reset_mock()
    s2 = classify_new_corrections(
        corrections_path=corpus, watermark_path=wm, pending_path=pending, tracker=tracker
    )
    assert s2["processed"] == 0
    tracker.record.assert_not_called()


def test_watermark_picks_up_appended_records(tmp_path):
    """After watermark advances, only a newly-appended record is processed."""
    corpus = tmp_path / "corrections.jsonl"
    wm = tmp_path / "wm.json"
    pending = tmp_path / "pending.json"
    tracker = MagicMock()
    _write_corpus(corpus, [{"ts": 100.0, "session_id": "a", "type": "tool_failure", "tool": "Bash", "error": "x"}])
    classify_new_corrections(corrections_path=corpus, watermark_path=wm, pending_path=pending, tracker=tracker)
    # Append a newer record.
    _write_corpus(corpus, [
        {"ts": 100.0, "session_id": "a", "type": "tool_failure", "tool": "Bash", "error": "x"},
        {"ts": 300.0, "session_id": "c", "type": "tool_failure", "tool": "Read", "error": "y"},
    ])
    tracker.reset_mock()
    s = classify_new_corrections(corrections_path=corpus, watermark_path=wm, pending_path=pending, tracker=tracker)
    assert s["processed"] == 1
    tracker.record.assert_called_once()


def test_classify_new_corrections_missing_corpus_is_safe(tmp_path):
    """No corpus file -> safe empty summary, no crash."""
    s = classify_new_corrections(
        corrections_path=tmp_path / "nope.jsonl",
        watermark_path=tmp_path / "wm.json",
        pending_path=tmp_path / "p.json",
        tracker=MagicMock(),
    )
    assert s["processed"] == 0


def test_watermark_never_regresses(tmp_path):
    """Adversarial #1/#2: a stale concurrent write cannot move the watermark
    backwards — the monotonic re-read under lock guards it."""
    corpus = tmp_path / "corrections.jsonl"
    wm = tmp_path / "wm.json"
    pending = tmp_path / "pending.json"
    _write_corpus(corpus, [{"ts": 100.0, "session_id": "a", "type": "tool_failure", "tool": "Bash", "error": "x"}])
    classify_new_corrections(corrections_path=corpus, watermark_path=wm, pending_path=pending, tracker=MagicMock())
    assert json.loads(wm.read_text())["last_ts"] == 100.0
    # Simulate a concurrent run that already advanced the watermark to 500 on disk.
    wm.write_text(json.dumps({"last_ts": 500.0}))
    # Our run sees only ts=100 in corpus (already processed); must NOT regress to 100.
    s = classify_new_corrections(corrections_path=corpus, watermark_path=wm, pending_path=pending, tracker=MagicMock())
    assert json.loads(wm.read_text())["last_ts"] == 500.0
    assert s["processed"] == 0


def test_classify_new_corrections_caps_records(tmp_path):
    """max_records caps how many are processed in one run (rest next run)."""
    corpus = tmp_path / "corrections.jsonl"
    _write_corpus(corpus, [
        {"ts": float(i), "session_id": str(i), "type": "tool_failure", "tool": "Bash", "error": "e"}
        for i in range(1, 11)
    ])
    s = classify_new_corrections(
        corrections_path=corpus,
        watermark_path=tmp_path / "wm.json",
        pending_path=tmp_path / "p.json",
        tracker=MagicMock(),
        max_records=3,
    )
    assert s["processed"] == 3


# === Phase 2: escalate_class (escalation ladder wiring) ===

from core.evolution.governance_router import escalate_class


class _FakeTracker:
    """Minimal tracker stub: get_class returns injected state; record is spied."""
    def __init__(self, state):
        self._state = state
        self.recorded = []
    def get_class(self, name):
        return dict(self._state) if self._state else None
    def record(self, name, evidence=""):
        self.recorded.append((name, evidence))


def test_ac4_threshold_no_fix_writes_rule_proposal(tmp_path):
    """AC4: count>=3 + no active_gate -> a rule proposal is written to the sink."""
    proposals = tmp_path / "proposals.json"
    tracker = _FakeTracker({"count": 5, "active_gate": None, "resolved": False})
    p = escalate_class("CLASS_B", tracker, proposals_path=proposals)
    assert p is not None
    assert p["proposal_kind"] == "rule"
    written = json.loads(proposals.read_text())
    assert len(written) == 1
    assert written[0]["source_class"] == "CLASS_B"
    # escalation must NOT increment the counter
    assert tracker.recorded == []


def test_existing_fix_writes_nothing(tmp_path):
    """A class with an existing structural fix (active_gate) -> no proposal, no file."""
    proposals = tmp_path / "proposals.json"
    tracker = _FakeTracker({"count": 11, "active_gate": "GC12", "resolved": False})
    p = escalate_class("CLASS_A", tracker, proposals_path=proposals)
    assert p is None
    assert not proposals.exists() or json.loads(proposals.read_text()) == []


def test_below_threshold_writes_nothing(tmp_path):
    """AC2: count<3 -> no proposal written."""
    proposals = tmp_path / "proposals.json"
    tracker = _FakeTracker({"count": 2, "active_gate": None})
    assert escalate_class("CLASS_C", tracker, proposals_path=proposals) is None
    assert not proposals.exists() or json.loads(proposals.read_text()) == []


def test_ac3_never_touches_governance_files(tmp_path, monkeypatch):
    """AC3 (NEGATIVE): escalate_class writes ONLY the proposal file, never SOUL/AGENT/STEERING."""
    proposals = tmp_path / "proposals.json"
    # Create decoy governance files; assert they are byte-identical after escalation.
    gov = {}
    for name in ("SOUL.md", "AGENT.md", "STEERING.md"):
        f = tmp_path / name
        f.write_text("ORIGINAL governance content\n")
        gov[name] = f.read_text()
    tracker = _FakeTracker({"count": 9, "active_gate": None})
    escalate_class("CLASS_B", tracker, proposals_path=proposals)
    for name, original in gov.items():
        assert (tmp_path / name).read_text() == original, f"{name} was mutated!"
    assert proposals.exists()  # only the proposal file changed


def test_escalate_missing_class_is_noop(tmp_path):
    """A class not in the tracker -> None, no file."""
    proposals = tmp_path / "proposals.json"
    tracker = _FakeTracker(None)
    assert escalate_class("CLASS_Z", tracker, proposals_path=proposals) is None


def test_proposal_dedup_kind_aware(tmp_path):
    """Re-escalating the same class+kind replaces (not duplicates) the proposal."""
    proposals = tmp_path / "proposals.json"
    tracker = _FakeTracker({"count": 5, "active_gate": None})
    escalate_class("CLASS_B", tracker, proposals_path=proposals)
    escalate_class("CLASS_B", tracker, proposals_path=proposals)
    written = json.loads(proposals.read_text())
    assert len(written) == 1  # deduped by (source_class, kind)
