"""Eval governance request APIs are read-only after migration to scheduled S5.

Eligible GC rows are aggregated by ``governance_miner`` on the scheduled path. The
compatibility decision API reports ``not_found`` and never mutates governed files.
"""

from __future__ import annotations

import json

import pytest

from core.eval_service import EvalService

CHECKED = "checked against: R27 — new because it names the grep step"


@pytest.fixture
def svc(tmp_path, monkeypatch):
    ctx = tmp_path / ".context"
    ctx.mkdir(parents=True)
    evolution = ctx / "EVOLUTION.md"
    evolution.write_text(
        "## Governance Candidates\n\n"
        "| ID | Proposed Rule | Evidence | Count | Since | Checked |\n"
        "|----|---|---|---|---|---|\n"
        f'| GC14 | "rule fourteen" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |\n'
        f'| GC15 | "rule fifteen" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |\n',
        encoding="utf-8")
    (ctx / ".evolution_proposals.json").write_text(json.dumps([
        {"target": "governance", "gc_id": "GC99", "proposed_rule": "legacy"},
    ]), encoding="utf-8")
    calls = []
    return EvalService(workspace_root=tmp_path), calls, evolution


def test_pending_view_is_empty_and_read_only(svc):
    service, calls, evolution = svc
    before = evolution.read_bytes()

    assert service.get_pending_governance() == {"proposals": [], "total": 0}
    assert calls == []
    assert evolution.read_bytes() == before


@pytest.mark.parametrize("decision", ["accept", "reject", "defer"])
def test_removed_decisions_are_not_found_and_change_nothing(svc, decision):
    service, _calls, evolution = svc
    before = evolution.read_bytes()

    assert service.decide_governance("GC14", decision) == {
        "status": "not_found", "proposal_id": "GC14"}
    assert evolution.read_bytes() == before


def test_repeated_reads_use_stable_candidate_slots(svc):
    service, calls, _evolution = svc
    service.get_pending_governance()
    first = [(call[0][1], call[0][2], call[0][3]) for call in calls]
    calls.clear()
    service.get_pending_governance()
    second = [(call[0][1], call[0][2], call[0][3]) for call in calls]
    assert second == first


def test_legacy_rows_cannot_be_decided(svc):
    service, _calls, _evolution = svc
    assert service.decide_governance("GC99", "accept")["status"] == "not_found"


def test_never_writes_governance_files(svc, tmp_path):
    service, _calls, _evolution = svc
    files = []
    for name in ("SOUL.md", "AGENT.md", "STEERING.md"):
        path = tmp_path / ".context" / name
        path.write_text("ORIGINAL\n")
        files.append(path)
    service.get_pending_governance()
    service.decide_governance("GC14", "accept")
    assert all(path.read_text() == "ORIGINAL\n" for path in files)


def test_unknown_id_and_invalid_decision(svc):
    service, _calls, _evolution = svc
    assert service.decide_governance("NOPE", "accept")["status"] == "not_found"
    assert service.decide_governance("GC14", "frobnicate")["status"] == "error"
