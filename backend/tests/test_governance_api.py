"""Tests for Evolution Pipeline v3 Phase 3: governance review API + service.

Covers the accept/reject/defer loop on .evolution_proposals.json (filtered to
target=='governance'). DoD negatives:
  AC6: accept -> register_rule/register_gate + item removed; reject -> removed,
       NO register, NO counter increment; defer -> kept.
  AC7: accept/reject/defer NEVER write SOUL/AGENT/STEERING.
  Gate-1 Check-3: a mixed file (skill-opt + governance rows) only surfaces
       governance rows, and skill-opt rows survive a rewrite untouched.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core.eval_service import EvalService


@pytest.fixture
def svc(tmp_path):
    """EvalService rooted at a tmp workspace, with a seeded mixed proposals file."""
    ctx = tmp_path / ".context"
    ctx.mkdir(parents=True)
    proposals = [
        # skill-optimization row (NOT governance) — must be ignored + preserved
        {"skill_name": "save-activity", "confidence": 0.6, "changes": []},
        # governance rule proposal
        {"target": "governance", "proposal_kind": "rule", "source_class": "CLASS_B",
         "occurrence_count": 5, "proposed_rule": "rule text", "evidence": [], "confidence": 0.6},
        # governance gate proposal
        {"target": "governance", "proposal_kind": "gate", "source_class": "CLASS_A",
         "occurrence_count": 12, "proposed_rule": "gate text", "evidence": [], "confidence": 0.8},
    ]
    (ctx / ".evolution_proposals.json").write_text(json.dumps(proposals), encoding="utf-8")
    return EvalService(workspace_root=tmp_path)


# --- AC5: list pending (governance only, with ids) ---

def test_get_pending_filters_to_governance_with_ids(svc):
    res = svc.get_pending_governance()
    assert res["total"] == 2  # skill-opt row excluded
    ids = {p["id"] for p in res["proposals"]}
    assert ids == {"CLASS_B:rule", "CLASS_A:gate"}


# --- Bug3a (run_685db747): accept-side axis guard against STALE non-cognitive proposals ---
# A pre-guard (pre-2026-06-25) code version could write an OPERATIONAL/UNCLASSIFIED
# governance proposal. Producers are now guarded (escalation_ladder:111) but the
# CONSUMER was not — so a stale non-cognitive row stayed visible + acceptable, which
# is how OPERATIONAL got a spurious active_rule. Fail closed at consumption.

@pytest.fixture
def svc_with_stale_operational(tmp_path):
    ctx = tmp_path / ".context"
    ctx.mkdir(parents=True)
    proposals = [
        {"target": "governance", "proposal_kind": "rule", "source_class": "CLASS_B",
         "occurrence_count": 5, "proposed_rule": "cognitive rule", "evidence": [], "confidence": 0.6},
        # STALE non-cognitive row (would be written only by pre-guard code)
        {"target": "governance", "proposal_kind": "rule", "source_class": "OPERATIONAL",
         "occurrence_count": 32, "proposed_rule": "Recurring OPERATIONAL pattern (32x)", "evidence": [], "confidence": 0.9},
    ]
    (ctx / ".evolution_proposals.json").write_text(json.dumps(proposals), encoding="utf-8")
    return EvalService(workspace_root=tmp_path)


def test_pending_excludes_stale_non_cognitive(svc_with_stale_operational):
    res = svc_with_stale_operational.get_pending_governance()
    ids = {p["id"] for p in res["proposals"]}
    assert "OPERATIONAL:rule" not in ids  # non-cognitive must not be offered
    assert "CLASS_B:rule" in ids          # cognitive still offered


def test_accept_stale_non_cognitive_is_refused(svc_with_stale_operational):
    with patch("core.evolution.correction_tracker.CorrectionClassTracker", autospec=True) as TrackerCls:
        tracker = TrackerCls.return_value
        res = svc_with_stale_operational.decide_governance("OPERATIONAL:rule", "accept")
    # must NOT register a rule for a non-cognitive class
    tracker.register_rule.assert_not_called()
    assert res["status"] != "accepted"


# --- AC6: accept rule -> register_rule + removed ---

def test_accept_rule_calls_register_rule_and_removes(svc):
    # autospec=True: bind the mock to the REAL CorrectionClassTracker API so a
    # renamed/removed register_rule/register_gate fails this test instead of
    # silently passing (mock-masks-breakage precedent — _create_health_todo).
    with patch("core.evolution.correction_tracker.CorrectionClassTracker", autospec=True) as TrackerCls:
        tracker = TrackerCls.return_value
        res = svc.decide_governance("CLASS_B:rule", "accept")
    assert res["action_taken"] == "registered_rule"
    tracker.register_rule.assert_called_once()
    tracker.register_gate.assert_not_called()
    # removed from the queue
    assert "CLASS_B:rule" not in {p["id"] for p in svc.get_pending_governance()["proposals"]}


# --- AC6 + Gate-1 Check-4: accept gate -> register_GATE (not a dead rung) ---

def test_accept_gate_calls_register_gate(svc):
    with patch("core.evolution.correction_tracker.CorrectionClassTracker", autospec=True) as TrackerCls:
        tracker = TrackerCls.return_value
        res = svc.decide_governance("CLASS_A:gate", "accept")
    # accept-gate now ALSO scaffolds an inert gate stub (run_90b8aeed ②→③ last mile),
    # so the action is "registered_gate" optionally suffixed "+scaffolded" when the
    # stub was written (skipped if a gate file already exists). Either is valid.
    assert res["action_taken"] in ("registered_gate", "registered_gate+scaffolded")
    tracker.register_gate.assert_called_once()
    tracker.register_rule.assert_not_called()


# --- AC6: reject -> removed, NO register, NO counter ---

def test_reject_removes_without_register(svc):
    with patch("core.evolution.correction_tracker.CorrectionClassTracker", autospec=True) as TrackerCls:
        tracker = TrackerCls.return_value
        svc.decide_governance("CLASS_B:rule", "reject")
        tracker.register_rule.assert_not_called()
        tracker.register_gate.assert_not_called()
        tracker.record.assert_not_called()
    assert "CLASS_B:rule" not in {p["id"] for p in svc.get_pending_governance()["proposals"]}


# --- AC6: defer -> kept ---

def test_defer_keeps_proposal(svc):
    res = svc.decide_governance("CLASS_B:rule", "defer")
    assert res["status"] == "deferred"
    assert "CLASS_B:rule" in {p["id"] for p in svc.get_pending_governance()["proposals"]}


# --- Gate-1 Check-3: skill-opt rows survive a governance rewrite ---

def test_skill_opt_rows_preserved_after_decision(svc):
    svc.decide_governance("CLASS_B:rule", "reject")
    raw = json.loads((svc._proposals_path()).read_text())
    skill_rows = [r for r in raw if "skill_name" in r]
    assert len(skill_rows) == 1
    assert skill_rows[0]["skill_name"] == "save-activity"


# --- AC7 (NEGATIVE): never writes SOUL/AGENT/STEERING ---

def test_ac7_never_writes_governance_files(svc, tmp_path):
    gov = {}
    for name in ("SOUL.md", "AGENT.md", "STEERING.md"):
        f = tmp_path / ".context" / name
        f.write_text("ORIGINAL\n")
        gov[name] = f.read_text()
    with patch("core.evolution.correction_tracker.CorrectionClassTracker", autospec=True):
        svc.decide_governance("CLASS_A:gate", "accept")
        svc.decide_governance("CLASS_B:rule", "reject")
    for name, original in gov.items():
        assert (tmp_path / ".context" / name).read_text() == original, f"{name} mutated!"


# --- not-found + invalid decision ---

def test_unknown_id_returns_not_found(svc):
    assert svc.decide_governance("NOPE:rule", "accept")["status"] == "not_found"


def test_invalid_decision_errors(svc):
    assert svc.decide_governance("CLASS_B:rule", "frobnicate")["status"] == "error"


# === Adversarial Gate-2 HIGH/LOW fixes ===

def test_remove_governance_proposal_is_flock_safe_single(tmp_path):
    """HIGH-1/LOW-2: remove_governance_proposal removes exactly ONE matching id,
    under flock, preserving non-governance + other governance rows."""
    from core.evolution.governance_router import remove_governance_proposal
    p = tmp_path / "props.json"
    p.write_text(json.dumps([
        {"skill_name": "x"},  # non-gov preserved
        {"target": "governance", "source_class": "CLASS_A", "proposal_kind": "rule", "id": "CLASS_A:rule"},
        {"target": "governance", "source_class": "CLASS_B", "proposal_kind": "gate", "id": "CLASS_B:gate"},
    ]))
    assert remove_governance_proposal("CLASS_A:rule", p) is True
    rows = json.loads(p.read_text())
    ids = [r.get("id") for r in rows if r.get("target") == "governance"]
    assert ids == ["CLASS_B:gate"]  # only CLASS_A:rule removed
    assert any("skill_name" in r for r in rows)  # skill-opt row preserved


def test_remove_governance_proposal_missing_returns_false(tmp_path):
    from core.evolution.governance_router import remove_governance_proposal
    p = tmp_path / "props.json"
    p.write_text(json.dumps([{"target": "governance", "source_class": "CLASS_A", "proposal_kind": "rule", "id": "CLASS_A:rule"}]))
    assert remove_governance_proposal("NOPE:rule", p) is False
    assert len(json.loads(p.read_text())) == 1  # nothing removed
