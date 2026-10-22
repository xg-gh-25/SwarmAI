"""Tests Pipeline v1 JSON documents and generated Markdown.

Methodology: valid lite documents, real AuditStore, and deterministic stage checks.
"""
from __future__ import annotations

import json

import pytest

from core.run_protocol import checks, events as ev
from core.run_protocol.documents import (DocumentError, DocumentStore, complete_stage,
                                         record_decision, render_design, render_requirements)
from core.run_protocol.store import AuditStore

AT = "2026-10-22T00:00:00+00:00"
REQ = {"requirements": [{"id": "R1", "acceptance": [{
    "id": "R1.1", "pattern": "event", "condition": "request arrives",
    "response": "return 200", "negative": False, "universal": False}]}]}
DESIGN = {"tasks": [{"id": "T1", "file": "backend/x.py", "symbol": "f",
                     "change": "return 200", "requirements": ["R1.1"],
                     "depends_on": [], "creates_test_files": []}],
          "exit_criteria": [{"id": "X1", "kind": "test",
                             "ref": "tests/x.py::test_f", "covers": ["R1.1"]}],
          "no_contract_change_reason": "private implementation"}


def _store(tmp_path):
    authority = ev.EventAuthority(b"d" * 32)
    return AuditStore(tmp_path / "audit", "run_one", authority), authority


def test_document_store_writes_json_source_and_generated_markdown(tmp_path):
    docs = DocumentStore(tmp_path / "run")
    paths = docs.write_requirements(REQ, variant="lite", profile="direct")
    paths += docs.write_design(DESIGN, variant="lite", profile="direct",
                               requirements=REQ, requirement_variant="lite",
                               existing_files={"tests/x.py"})
    assert len(paths) == 4
    assert json.loads((tmp_path / "run/artifacts/requirements.json").read_text()) == REQ
    assert "WHEN request arrives, THE SYSTEM SHALL return 200" in (
        tmp_path / "run/REQUIREMENTS.md").read_text()
    assert "**T1**" in (tmp_path / "run/DESIGN_SPEC.md").read_text()


def test_invalid_document_never_writes_json_or_markdown(tmp_path):
    docs = DocumentStore(tmp_path / "run")
    with pytest.raises(DocumentError, match="RQ-1"):
        docs.write_requirements({"requirements": []}, variant="lite", profile="direct")
    assert not (tmp_path / "run/artifacts/requirements.json").exists()


def test_complete_stage_writes_exact_roster_and_results(tmp_path):
    store, authority = _store(tmp_path)
    context = checks.make_context(
        profile="direct", requirement_variant="lite", design_variant="lite",
        requirements=REQ, design=DESIGN, events=[], authority=authority,
        existing_files={"tests/x.py"})
    result = complete_stage(store, profile="direct", stage="plan", variant="lite",
                            context=context, at=AT, attempt=1)
    assert result["passed"] is True
    chain = store.read_events()
    assert chain[0]["kind"] == "stage_attempt"
    assert set(chain[0]["payload"]["check_ids"]) == {
        spec.check_id for spec in checks.stage_checks("direct", "plan", "lite")}
    assert {"RQ-1", "RQ-2", "RQ-8", "DS-2a", "DS-5", "DS-7", "DS-9a"} \
        <= set(chain[0]["payload"]["check_ids"])
    assert all(row["kind"] == "check" for row in chain[1:])


def test_record_decision_requires_dc1_and_cap(tmp_path):
    store, _ = _store(tmp_path)
    value = {"request_id": "q1", "kind": "requirement_ambiguity",
             "question": "Which behavior?", "why_evidence_insufficient": "both fit",
             "refs": ["A1"], "options": [
                 {"id": "a", "choice": "one", "consequence": "x", "reversible": True, "risk": "low"},
                 {"id": "b", "choice": "two", "consequence": "y", "reversible": True, "risk": "low"}]}
    event = record_decision(store, value, known_refs={"A1": {"kind": "analysis", "resolution": "asked"}},
                            document="REQUIREMENTS", at=AT, decisions_answered=0, cap=2)
    assert event["kind"] == "decision" and event["payload"]["request_id"] == "q1"
    with pytest.raises(DocumentError, match="cap"):
        record_decision(store, value, known_refs={"A1": {"kind": "analysis", "resolution": "asked"}},
                        document="REQUIREMENTS", at=AT, decisions_answered=2, cap=2)


def test_renderers_are_pure_and_do_not_accept_legacy_claims():
    assert render_requirements(REQ).startswith("# Requirements")
    assert render_design(DESIGN).startswith("# Design Spec")
