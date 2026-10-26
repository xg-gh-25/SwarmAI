"""Governance review API + service: proposals are derived from EVOLUTION.md's GC
table every time; accept / reject write the verdict into the row in place; defer
changes nothing; nothing ever writes SOUL/AGENT/STEERING
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
    # Rows an older version left in the old proposal file are not read.
    (ctx / ".evolution_proposals.json").write_text(json.dumps([
        {"skill_name": "save-activity", "changes": []},
        {"target": "governance", "proposal_kind": "gate", "source_class": "CLASS_C", "proposed_rule": "x"},
        {"target": "governance", "gc_id": "GC99", "proposed_rule": "unchecked legacy GC proposal"},
    ]), encoding="utf-8")
    return EvalService(workspace_root=tmp_path)


def _ids(svc):
    return [p["id"] for p in svc.get_pending_governance()["proposals"]]


def test_pending_comes_from_the_gc_table_only(svc):
    assert _ids(svc) == ["GC14", "GC15"]
    assert svc.get_pending_governance()["total"] == 2


@pytest.mark.parametrize("decision,status,mark", [
    ("accept", "accepted", gm.ACCEPTED), ("reject", "rejected", gm.REJECTED)])
def test_decision_marks_the_row_and_it_never_returns(svc, tmp_path, decision, status, mark):
    res = svc.decide_governance("GC14", decision)
    assert (res["status"], res["action_taken"]) == (status, "marked")
    assert ("next_step" in res) == (decision == "accept")
    assert _ids(svc) == ["GC15"]
    assert f'| GC14 | "rule fourteen" | C1, C2, C3 | {mark} ' in (tmp_path / ".context" / "EVOLUTION.md").read_text(encoding="utf-8")


def test_second_decision_finds_nothing(svc):
    svc.decide_governance("GC14", "accept")
    assert svc.decide_governance("GC14", "reject")["status"] == "not_found"


def test_defer_changes_nothing(svc, tmp_path):
    before = (tmp_path / ".context" / "EVOLUTION.md").read_text(encoding="utf-8")
    assert svc.decide_governance("GC14", "defer")["status"] == "deferred"
    assert _ids(svc) == ["GC14", "GC15"]
    assert (tmp_path / ".context" / "EVOLUTION.md").read_text(encoding="utf-8") == before


def test_legacy_rows_cannot_be_decided(svc):
    assert svc.decide_governance("CLASS_C:gate", "accept")["status"] == "not_found"
    assert svc.decide_governance("GC99", "accept")["status"] == "not_found"


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
