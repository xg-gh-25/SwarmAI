"""Governance review API + service: only GC proposals surface; accept / reject
write the verdict into the GC row (so it is not mined again) and remove the
proposal; defer keeps it; nothing ever writes SOUL/AGENT/STEERING
(docs/design/2026-10-26-evolution-loop-admission.md §3.2, §5)."""

from __future__ import annotations

import json

import pytest

from core.eval_service import EvalService
from core.evolution import governance_miner as gm

CHECKED = "checked against: R27 — new because it names the grep step"


@pytest.fixture
def svc(tmp_path):
    ctx = tmp_path / ".context"
    ctx.mkdir(parents=True)
    (ctx / "EVOLUTION.md").write_text(
        "## Governance Candidates\n\n"
        "| ID | Proposed Rule | Evidence | Count | Since | Checked |\n"
        "|----|---|---|---|---|---|\n"
        f'| GC14 | "rule fourteen" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |\n'
        f'| GC15 | "rule fifteen" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |\n',
        encoding="utf-8",
    )
    proposals = [
        {"skill_name": "save-activity", "confidence": 0.6, "changes": []},           # old skill row
        {"target": "governance", "proposal_kind": "gate", "source_class": "CLASS_C",  # old bucket row
         "occurrence_count": 18, "proposed_rule": "escalate to a CODE GATE", "confidence": 0.8},
        {"target": "governance", "gc_id": "GC14", "proposed_rule": "rule fourteen"},
        {"target": "governance", "gc_id": "GC15", "proposed_rule": "rule fifteen"},
    ]
    (ctx / ".evolution_proposals.json").write_text(json.dumps(proposals), encoding="utf-8")
    return EvalService(workspace_root=tmp_path)


def _ids(svc):
    return [p["id"] for p in svc.get_pending_governance()["proposals"]]


def test_only_gc_rows_surface(svc):
    assert _ids(svc) == ["GC14", "GC15"]


@pytest.mark.parametrize("decision,status,mark", [
    ("accept", "accepted", gm.PROMOTED), ("reject", "rejected", gm.REJECTED)])
def test_decision_marks_row_removes_proposal_and_is_not_mined_again(svc, tmp_path, decision, status, mark):
    res = svc.decide_governance("GC14", decision)
    assert (res["status"], res["action_taken"]) == (status, "marked+removed")
    assert _ids(svc) == ["GC15"]
    evo = tmp_path / ".context" / "EVOLUTION.md"
    assert f"| GC14 | \"rule fourteen\" | C1, C2, C3 | {mark} " in evo.read_text(encoding="utf-8")
    assert [p.gc_id for p in gm.generate_governance_proposals(evo)] == ["GC15"]


def test_defer_keeps_proposal_and_row(svc, tmp_path):
    before = (tmp_path / ".context" / "EVOLUTION.md").read_text(encoding="utf-8")
    assert svc.decide_governance("GC14", "defer")["status"] == "deferred"
    assert _ids(svc) == ["GC14", "GC15"]
    assert (tmp_path / ".context" / "EVOLUTION.md").read_text(encoding="utf-8") == before


def test_old_rows_survive_a_decision_untouched(svc):
    svc.decide_governance("GC14", "reject")
    raw = json.loads(svc._proposals_path().read_text())
    assert any(r.get("skill_name") == "save-activity" for r in raw)
    assert any(r.get("source_class") == "CLASS_C" for r in raw)


def test_old_bucket_row_cannot_be_decided(svc):
    assert svc.decide_governance("CLASS_C:gate", "accept")["status"] == "not_found"


def test_never_writes_governance_files(svc, tmp_path):
    for name in ("SOUL.md", "AGENT.md", "STEERING.md"):
        (tmp_path / ".context" / name).write_text("ORIGINAL\n")
    svc.decide_governance("GC14", "accept")
    svc.decide_governance("GC15", "reject")
    for name in ("SOUL.md", "AGENT.md", "STEERING.md"):
        assert (tmp_path / ".context" / name).read_text() == "ORIGINAL\n", name


def test_unknown_id_and_invalid_decision(svc):
    assert svc.decide_governance("NOPE", "accept")["status"] == "not_found"
    assert svc.decide_governance("GC14", "frobnicate")["status"] == "error"


def test_remove_governance_proposal_is_flock_safe_single(tmp_path):
    from core.evolution.governance_router import remove_governance_proposal
    p = tmp_path / "props.json"
    p.write_text(json.dumps([
        {"skill_name": "x"},
        {"target": "governance", "gc_id": "GC1", "id": "GC1"},
        {"target": "governance", "gc_id": "GC2", "id": "GC2"},
    ]))
    assert remove_governance_proposal("GC1", p) is True
    rows = json.loads(p.read_text())
    assert [r.get("id") for r in rows if r.get("target") == "governance"] == ["GC2"]
    assert any("skill_name" in r for r in rows)
