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
from unittest.mock import MagicMock, patch

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


# --- AC6: accept rule -> register_rule + removed ---

def test_accept_rule_calls_register_rule_and_removes(svc):
    with patch("core.evolution.correction_tracker.CorrectionClassTracker") as TrackerCls:
        tracker = MagicMock()
        TrackerCls.return_value = tracker
        res = svc.decide_governance("CLASS_B:rule", "accept")
    assert res["action_taken"] == "registered_rule"
    tracker.register_rule.assert_called_once()
    tracker.register_gate.assert_not_called()
    # removed from the queue
    assert "CLASS_B:rule" not in {p["id"] for p in svc.get_pending_governance()["proposals"]}


# --- AC6 + Gate-1 Check-4: accept gate -> register_GATE (not a dead rung) ---

def test_accept_gate_calls_register_gate(svc):
    with patch("core.evolution.correction_tracker.CorrectionClassTracker") as TrackerCls:
        tracker = MagicMock()
        TrackerCls.return_value = tracker
        res = svc.decide_governance("CLASS_A:gate", "accept")
    assert res["action_taken"] == "registered_gate"
    tracker.register_gate.assert_called_once()
    tracker.register_rule.assert_not_called()


# --- AC6: reject -> removed, NO register, NO counter ---

def test_reject_removes_without_register(svc):
    with patch("core.evolution.correction_tracker.CorrectionClassTracker") as TrackerCls:
        tracker = MagicMock()
        TrackerCls.return_value = tracker
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
    with patch("core.evolution.correction_tracker.CorrectionClassTracker"):
        svc.decide_governance("CLASS_A:gate", "accept")
        svc.decide_governance("CLASS_B:rule", "reject")
    for name, original in gov.items():
        assert (tmp_path / ".context" / name).read_text() == original, f"{name} mutated!"


# --- not-found + invalid decision ---

def test_unknown_id_returns_not_found(svc):
    assert svc.decide_governance("NOPE:rule", "accept")["status"] == "not_found"


def test_invalid_decision_errors(svc):
    assert svc.decide_governance("CLASS_B:rule", "frobnicate")["status"] == "error"
