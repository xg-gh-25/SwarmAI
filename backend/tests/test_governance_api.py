"""Eval governance APIs: the pending list is the GC table, read every time; a decision
is written through the governed path (design 2026-10-26-evolution-loop-admission §3.2).
Legacy .evolution_proposals.json rows are never read; SOUL/AGENT/STEERING are never
written."""

from __future__ import annotations

import json

import pytest

from core.eval_service import EvalService
from tests.test_cdf_v2_e2e import cdf_env  # noqa: F401  (fixture)

CHECKED = "checked against: R27 — new because it names the grep step"


@pytest.fixture
def svc(cdf_env):
    from core.ingestion.api import connect_rw
    from core.ingestion.writer import ensure_baseline
    ws = cdf_env["workspace"]
    evolution = ws / ".context" / "EVOLUTION.md"
    evolution.write_text(
        "# Evolution\n\n## Governance Candidates\n\n"
        "| ID | Proposed Rule | Evidence | Count | Since | Checked |\n"
        "|----|---|---|---|---|---|\n"
        f'| GC14 | "rule fourteen" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |\n'
        f'| GC15 | "rule fifteen" | C1, C2, C3 | 3/3 | 2026-10-01 | {CHECKED} |\n',
        encoding="utf-8")
    (ws / ".context" / ".evolution_proposals.json").write_text(json.dumps([
        {"target": "governance", "gc_id": "GC99", "proposed_rule": "legacy"},
    ]), encoding="utf-8")
    conn = connect_rw()
    try:
        ensure_baseline(conn=conn)
    finally:
        conn.close()
    return EvalService(workspace_root=ws), evolution


def test_pending_lists_the_checked_gc_rows_and_reads_nothing_else(svc):
    service, evolution = svc
    before = evolution.read_bytes()
    pending = service.get_pending_governance()
    assert [p["gc_id"] for p in pending["proposals"]] == ["GC14", "GC15"] and pending["total"] == 2
    assert pending["proposals"][0]["checked"] == CHECKED
    assert evolution.read_bytes() == before


@pytest.mark.parametrize("decision,mark", [("accept", "✔ ACCEPTED"), ("reject", "✖ REJECTED")])
def test_a_decision_marks_the_row_and_it_leaves_the_list(svc, decision, mark):
    service, evolution = svc
    result = service.decide_governance("GC14", decision)
    assert result["status"] == ("accepted" if decision == "accept" else "rejected")
    assert mark in evolution.read_text(encoding="utf-8").split("GC14", 1)[1].split("\n", 1)[0]
    assert [p["gc_id"] for p in service.get_pending_governance()["proposals"]] == ["GC15"]
    assert service.decide_governance("GC14", "accept")["status"] == "not_found"


def test_defer_changes_nothing(svc):
    service, evolution = svc
    before = evolution.read_bytes()
    assert service.decide_governance("GC14", "defer")["status"] == "deferred"
    assert evolution.read_bytes() == before


def test_legacy_rows_cannot_be_decided(svc):
    service, _evolution = svc
    assert service.decide_governance("GC99", "accept")["status"] == "not_found"


def test_never_writes_governance_files(svc, cdf_env):
    service, _evolution = svc
    files = []
    for name in ("SOUL.md", "AGENT.md", "STEERING.md"):
        path = cdf_env["workspace"] / ".context" / name
        path.write_text("ORIGINAL\n")
        files.append(path)
    service.decide_governance("GC14", "accept")
    assert all(path.read_text() == "ORIGINAL\n" for path in files)


def test_unknown_id_and_invalid_decision(svc):
    service, _evolution = svc
    assert service.decide_governance("NOPE", "accept")["status"] == "not_found"
    assert service.decide_governance("GC14", "frobnicate")["status"] == "error"
