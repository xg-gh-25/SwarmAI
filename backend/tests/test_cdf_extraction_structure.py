"""What an extractor writes is shaped where it is extracted (design
2026-11-02-cdf-extraction-input; EVOLUTION K007): every knowledge unit says when it
applies, every gap names its next step, and the structured-output schema every model
extractor answers in requires both, so an entry without them is never produced. The
session review also sees the exchange just before its window, as context it may not cite."""
from __future__ import annotations

import asyncio
import json

import pytest

from core.ingestion import contracts_v2
from core.ingestion.sources import extract_llm


def _cognition(**extra):
    return {"kind": "cognition", "type": "guideline", "scope": "project:P", "title": "T",
            "what": "Do X.", "evidence": ["event:e1"], **extra}


def _gap(**extra):
    return {"kind": "gap", "gap_kind": "debt", "scope": "project:P", "title": "T",
            "what": "X is open.", "evidence": ["event:e1"], **extra}


def test_a_knowledge_unit_says_when_it_applies():
    with pytest.raises(ValueError):
        contracts_v2.SOURCE_UNIT.validate_python(_cognition())
    with pytest.raises(ValueError):
        contracts_v2.SOURCE_UNIT.validate_python(_cognition(applicability=""))
    assert contracts_v2.SOURCE_UNIT.validate_python(_cognition(applicability="When changing X.")).applicability


def test_a_gap_names_its_next_step():
    with pytest.raises(ValueError):
        contracts_v2.SOURCE_UNIT.validate_python(_gap())
    assert contracts_v2.SOURCE_UNIT.validate_python(_gap(proposed_next="Measure X first.")).proposed_next


def test_every_model_extractor_is_asked_for_both():
    schema = extract_llm.unit_schema(("cognition", "gap", "event"))
    item = lambda kind: schema["properties"][f"{kind}_units"]["items"]
    assert "applicability" in item("cognition")["required"]
    assert "proposed_next" in item("gap")["required"]


def test_the_pipeline_report_template_carries_them():
    from core.run_protocol.templates import template
    report = template("reflect")["document"]
    assert report["decisions"] and all("applicability" in row for row in report["decisions"])
    from core.run_protocol import reflect
    from core.run_protocol.templates import fill
    filled = fill(report, {"<ANCHOR>": "event:a1", "<PROJECT>": "P"})
    assert reflect.prepare_lenient(filled, allowed_anchors={"event:a1"})["dropped"] == []


def test_a_run_report_row_the_contract_no_longer_accepts_costs_only_itself(monkeypatch):
    """A report prepared before the contract changed may hold a row without the new
    fields: that row is recorded invalid, the others are still enqueued."""
    from core.ingestion import api_v2
    from core.ingestion.sources import s3_run
    good = _cognition(applicability="When changing X.")
    old = _cognition()                                   # prepared under the old contract
    enqueued, attempts = [], []
    monkeypatch.setattr(s3_run, "_roots", lambda engine: ((), ()))
    monkeypatch.setattr(s3_run, "_snapshot", lambda engine: {})
    monkeypatch.setattr(api_v2, "enqueue_stored", lambda *a, **k: enqueued.append(a[2]) or "ok")
    monkeypatch.setattr(api_v2, "record_extraction_attempt", lambda attempt, **k: attempts.append(k))

    class Engine:
        run_id = "run_x"
        def read_reflect_report(self):
            return {"delivered": [], "decisions": [], "corrections": [], "practices": [good, old], "gaps": []}
    assert s3_run.enqueue_report(Engine()) == ["ok"]
    assert [u.title for u in enqueued] == ["T"] and len(attempts[0]["invalid_units"]) == 1


def test_a_report_prepared_before_the_contract_changed_does_not_fail_delivery():
    """RF-1..4 re-read the prepared report: a row the contract has since come to refuse
    does not fail the run's delivery (REFLECT never blocks landing)."""
    from types import SimpleNamespace
    from core.run_protocol import checks
    old = _cognition()                                   # no applicability: prepared before
    report = {"delivered": [], "decisions": [], "corrections": [], "practices": [old], "gaps": []}
    assert checks._evaluate_check("RF-1", SimpleNamespace(run={"reflect_report": report}))[0] is True
    broken = {k: v for k, v in report.items() if k != "gaps"}      # not the five-part form
    assert checks._evaluate_check("RF-1", SimpleNamespace(run={"reflect_report": broken}))[0] is False
