"""A run's evidence for its reviewer: findings (alternatives not taken, requirements
analysis) and decisions (each judgment call), modelled on the evidence an AgentSpaces
project agent attaches to a change.

What is tested:
- DESIGN_SPEC (DS-6): with more than one alternative, each has an id, ``chosen`` names
  one, every other one says ``why_not``; a single alternative needs neither
- DESIGN_SPEC (DS-5) and the BUILD / TEST validation report accept an optional
  ``decisions`` list and refuse an entry with an empty field or a duplicate id
- DESIGN_SPEC.md lists the alternatives not taken and the decisions; a design of the old
  shape (no ids, ``chosen`` as text) still renders, its rejected one with no reason
- the REPORT has Decisions and Findings sections, every entry whole on one line, and
  leaves them out when there is nothing to list
- the chat attention block lists the agent's decisions as ``self_answered`` items
- a decision is credited to a person only when the log holds that person's answer, and
  through the real engine a person's recorded answer is credited to them
- model-written HTML / links / images come out escaped in the REPORT
- no check is added for DELIVER, landing or REFLECT
"""
from __future__ import annotations

import json

import pytest

from core import pipeline_stage_events as pse
from core.run_protocol import audit, checks, documents, schemas, validation_report
from tests.pipeline_v1_rig import documents as rig_documents
from tests.pipeline_v1_e2e_common import _decision as rig_decision, rig  # noqa: F401 (fixture)


LONG = "word " * 400          # past every REPORT clip (300 / 600), within the schema limits


def _design(**over):
    doc = {
        "alternatives": [
            {"id": "A1", "what": "extend the documents the run has"},
            {"id": "A2", "what": "a new evidence artifact",
             "why_not": "a parallel store for content the run already holds"},
        ],
        "chosen": "A1",
        "design": {"user_workflow": "the agent fills it, the reviewer reads it",
                   "architecture": "schemas, renderers",
                   "flow": "submit -> validate; invalid -> error; valid -> stored",
                   "states": [{"name": "recorded", "created_by": "submit",
                               "removed_by": "a later submit"}]},
        "properties": [], "failure_modes": [],
        "tasks": [{"id": "T1", "file": "backend/core/x.py", "symbol": "f",
                   "change": "add the field", "requirements": ["R1.1"],
                   "depends_on": [], "creates_test_files": []}],
        "exit_criteria": [{"id": "X1", "kind": "test", "ref": "tests/test_x.py::test_f",
                           "covers": ["R1.1"]}],
        "no_contract_change_reason": "internal",
        "security_boundaries": [],
    }
    doc.update(over)
    return doc


def _issues(doc, variant="full"):
    return {(row.check_id, row.path)
            for row in schemas.validate_design_spec(doc, variant=variant,
                                                    requirement_ids={"R1.1"})}


def _report(**over):
    row = {"repo": "repo", "commands": [{"command": "pytest", "cwd": ".", "result": "pass"}],
           "tests_changed": [], "rationale": "the tests cover it"}
    row.update(over)
    return {"reports": [row]}


def _decision(ident="D1", **over):
    row = {"id": ident, "question": "which store", "choice": "the existing documents",
           "reason": "no second store to drift"}
    row.update(over)
    return row


def test_full_design_refuses_unreasoned_alternative():
    assert _issues(_design()) == set()
    no_reason = _design()
    del no_reason["alternatives"][1]["why_not"]
    assert ("DS-6", "alternatives[1].why_not") in _issues(no_reason)
    empty_reason = _design()
    empty_reason["alternatives"][1]["why_not"] = "  "
    assert ("DS-6", "alternatives[1].why_not") in _issues(empty_reason)
    no_id = _design()
    del no_id["alternatives"][0]["id"]
    assert ("DS-6", "alternatives[0].id") in _issues(no_id)
    # chosen as the approach's text (the old shape) names no alternative id
    assert ("DS-6", "chosen") in _issues(_design(chosen="extend the documents the run has"))
    # a person's answer written back into chosen keeps the id under ``alternative``
    assert _issues(_design(chosen={"alternative": "A1", "decisions": []})) == set()
    # one alternative: nothing was not taken, so neither an id nor a reason is needed
    assert _issues(_design(alternatives=[{"what": "the only way"}],
                           chosen="the only way")) == set()


def test_design_decisions_refused_when_malformed():
    assert _issues(_design(decisions=[_decision()])) == set()
    assert _issues(_design(decisions=[])) == set()
    assert ("DS-5", "decisions[0].reason") in _issues(_design(decisions=[_decision(reason="")]))
    assert ("DS-5", "decisions[1].id") in _issues(
        _design(decisions=[_decision("D1"), _decision("D1")]))
    assert ("DS-5", "decisions[0].id") in _issues(_design(decisions=[_decision("X")]))
    assert ("DS-5", "decisions[0].extra") in _issues(_design(decisions=[_decision(extra=1)]))
    lite = {"tasks": _design()["tasks"], "exit_criteria": _design()["exit_criteria"],
            "no_contract_change_reason": "internal", "decisions": [_decision()]}
    assert _issues(lite, variant="lite") == set()


def test_report_decisions_accepted_and_refused():
    rows = validation_report.parse(_report(decisions=[_decision()]), ["repo"])
    assert rows[0]["decisions"] == [_decision()]
    payload = validation_report.event_payload("build", rows[0], "1" * 40)
    assert payload["decisions"] == [_decision()]
    # absent or empty: the report and its event look exactly as before
    plain = validation_report.parse(_report(decisions=[]), ["repo"])[0]
    assert "decisions" not in plain
    assert "decisions" not in validation_report.event_payload("build", plain, "1" * 40)
    with pytest.raises(validation_report.ReportError, match=r"decisions\[0\]\.choice"):
        validation_report.parse(_report(decisions=[_decision(choice=" ")]), ["repo"])
    with pytest.raises(validation_report.ReportError, match="duplicate decision id"):
        validation_report.parse(_report(decisions=[_decision(), _decision()]), ["repo"])


def test_design_md_lists_not_taken_and_decisions():
    text = documents.render_design(_design(decisions=[_decision(reason=LONG + "\n## Injected")]))
    assert "## Chosen approach\n\n**A1** — extend the documents the run has" in text
    assert ("## Considered and not taken\n\n- **A2** — a new evidence artifact. Not taken: "
            "a parallel store for content the run already holds") in text
    line = next(ln for ln in text.splitlines() if ln.startswith("- **D1**"))
    assert line.endswith(" ".join((LONG + " ## Injected").split()))      # whole, one line
    assert "\n## Injected" not in text


def test_legacy_design_renders_without_reason():
    old = _design(alternatives=[{"what": "patch"}, {"what": "replace"}], chosen="replace")
    text = documents.render_design(old)
    assert "- patch. Not taken: no reason recorded" in text
    assert "## Chosen approach\n\nreplace" in text
    # chosen names none of them: which were not taken is unknown, so none is listed
    assert "Considered and not taken" not in documents.render_design(
        _design(alternatives=[{"what": "patch"}, {"what": "replace"}], chosen="something else"))
    rendered = audit.render(_audit(design=old), fmt="report")
    assert "- - · patch. Not taken: no reason recorded" in rendered


def _audit(design=None, requirements=None, events=(), answered=()):
    projection = {"run_id": "run_x", "status": "completed", "landing": "landed",
                  "reason": None, "human_decisions": 0}
    contract = {"run_id": "run_x", "mode": "isolated", "expected_repos": ["repo"]}
    rows = [{"kind": "run_create", "at": "2026-10-22T00:00:00+00:00", "payload": {}},
            *events, {"kind": "land", "at": "2026-10-22T00:00:30+00:00", "payload": {}}]
    return audit.build(run_id="run_x", projection=projection, events=rows,
                       repos_isolated=1, findings=[], contract=contract, open_checks={},
                       audit_through="COMPLETED", commits={"repo": "a" * 40},
                       design=design, requirements=requirements, answered=answered)


def _report_event(stage, decisions, at):
    row = validation_report.parse(_report(decisions=decisions), ["repo"])[0]
    return {"kind": "validation_report", "at": at,
            "payload": validation_report.event_payload(stage, row, "1" * 40)}


def test_report_has_decisions_and_findings_whole():
    requirements = {
        "analysis": [{"kind": "missing_edge", "refs": ["R1.1"], "note": "old runs",
                      "resolution": "rewritten"}],
        "clarifications": [{"qid": "dec_1", "question": "which profile", "answer": "full",
                            "answered_by": "user"}]}
    events = [
        _report_event("build", [_decision("D1", choice="first try")], "2026-10-22T00:00:10+00:00"),
        # a resubmitted BUILD report replaces the earlier one
        _report_event("build", [_decision("D1", choice="kept the cache")], "2026-10-22T00:00:11+00:00"),
        _report_event("test", [_decision("D1", question="which suite", choice="unit only",
                                         reason=LONG + "\n# heading")], "2026-10-22T00:00:20+00:00"),
    ]
    value = _audit(design=_design(decisions=[_decision("D1", reason="no drift")]),
                   requirements=requirements, events=events,
                   answered=[{"document": "REQUIREMENTS", "request_id": "dec_1",
                              "question": "which profile", "choice": "full",
                              "answered_by": "user"}])
    by = [(row["source"], row["by"], row["choice"]) for row in value["details"]["decisions"]]
    assert by == [("REQUIREMENTS", "user", "full"),
                  ("DESIGN_SPEC", "agent", "the existing documents"),
                  ("BUILD report · repo", "agent", "kept the cache"),
                  ("TEST report · repo", "agent", "unit only")]
    text = audit.render(value, fmt="report")
    section = text.split("## Decisions\n\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert section[0] == "- dec\\_1 · which profile → full (REQUIREMENTS, user)"
    assert section[1] == ("- D1 · which store → the existing documents. Why: no drift "
                          "(DESIGN\\_SPEC, agent)")
    assert "first try" not in text
    whole = " ".join((LONG + " # heading").split())
    assert section[3].startswith("- D1 · which suite → unit only. Why: " + whole)
    assert "\n# heading" not in text
    findings = text.split("## Findings\n\n", 1)[1]
    assert ("- A2 · a new evidence artifact. Not taken: a parallel store for content the "
            "run already holds") in findings
    assert "- missing\\_edge (R1.1): old runs → rewritten" in findings
    # the chat card format is unchanged
    assert "## Decisions" not in audit.render(value, fmt="card")


def test_no_late_stage_check_and_empty_evidence_is_silent():
    late = sorted(spec.check_id for spec in checks.CHECKS.values()
                  if set(spec.stages) & {"deliver", "land", "run_land", "reflect"})
    assert late == ["DV-1", "LG-1", "LG-3", "LG-4", "RF-1", "RF-2", "RF-3", "RF-4", "RF-5"]
    value = _audit(design=_design(alternatives=[{"what": "only"}], chosen="only"))
    assert value["details"]["decisions"] == [] and value["details"]["considered"] == []
    text = audit.render(value, fmt="report")
    assert "## Decisions" not in text and "## Findings" not in text
    assert pse.attention_block("run_x", {"landing": "landed"}, "T") is None


def test_attention_block_lists_agent_decisions(tmp_path):
    value = _audit(design=_design(decisions=[_decision("D1"), _decision("D2", reason=LONG)]),
                   requirements={"clarifications": [{"qid": "dec_1", "question": "q",
                                                     "answer": "a", "answered_by": "user"}]},
                   answered=[{"document": "REQUIREMENTS", "request_id": "dec_1",
                              "question": "q", "answered_by": "user"}])
    (tmp_path / "run_audit.json").write_text(json.dumps(value), encoding="utf-8")
    block = pse.attention_block("run_x", {"landing": "landed", "run_dir": str(tmp_path)}, "T")
    assert [item["kind"] for item in block["items"]] == ["self_answered", "self_answered"]
    assert block["items"][0] == {"kind": "self_answered", "id": "D1", "question": "which store",
                                 "answer": "the existing documents",
                                 "reason": "no second store to drift"}
    assert block["items"][1]["reason"] == LONG.strip()           # whole
    not_landed = pse.attention_block(
        "run_x", {"landing": "not_landed", "reason": "LG-4", "run_dir": str(tmp_path)}, "T")
    assert [item["kind"] for item in not_landed["items"]][0] == "not_landed"
    many = _audit(design=_design(decisions=[_decision(f"D{i}") for i in range(1, 12)]))
    (tmp_path / "run_audit.json").write_text(json.dumps(many), encoding="utf-8")
    capped = pse.attention_block("run_x", {"landing": "landed", "run_dir": str(tmp_path)}, "T")
    assert len(capped["items"]) == pse.MAX_ATTENTION_ITEMS and capped["more"] == 11 - pse.MAX_ATTENTION_ITEMS
    # missing or unreadable audit: the block falls back to what it showed before
    (tmp_path / "run_audit.json").write_text("{not json", encoding="utf-8")
    assert pse.attention_block("run_x", {"landing": "landed", "run_dir": str(tmp_path)}, "T") is None
    assert pse.attention_block("run_x", {"landing": "landed", "run_dir": str(tmp_path / "gone")},
                               "T") is None


def test_decision_credit_comes_from_the_log(tmp_path):
    requirements = {"clarifications": [
        {"qid": "dec_1", "question": "recorded", "answer": "a", "answered_by": "user"},
        # the agent wrote answered_by user, but no person's answer is in the log
        {"qid": "dec_9", "question": "claimed", "answer": "b", "answered_by": "user"},
        {"qid": "dec_2", "question": "after cap", "answer": "c", "answered_by": "evidence"},
        # reuses the id of a DESIGN_SPEC request a person answered: not this document's
        {"qid": "dec_3", "question": "reused id", "answer": "d", "answered_by": "host"}]}
    design = _design(chosen={"alternative": "A1", "decisions": [
        {"request_id": "dec_3", "option_id": "o1", "choice": "keep A1", "answered_by": "host"}]})
    answered = [{"document": "REQUIREMENTS", "request_id": "dec_1", "question": "recorded",
                 "answered_by": "user"},
                {"document": "REQUIREMENTS", "request_id": "dec_2", "question": "after cap",
                 "answered_by": "evidence", "decided_by": "agent_after_cap"},
                {"document": "DESIGN_SPEC", "request_id": "dec_3", "question": "which design",
                 "answered_by": "host"}]
    value = _audit(design=design, requirements=requirements, answered=answered)
    by = [(row["id"], row["source"], row["by"], row["question"])
          for row in value["details"]["decisions"]]
    assert by == [("dec_1", "REQUIREMENTS", "user", "recorded"),
                  ("dec_9", "REQUIREMENTS", "agent", "claimed"),
                  ("dec_2", "REQUIREMENTS", "agent_after_cap", "after cap"),
                  ("dec_3", "REQUIREMENTS", "agent", "reused id"),
                  ("dec_3", "DESIGN_SPEC", "host", "which design")]
    (tmp_path / "run_audit.json").write_text(json.dumps(value), encoding="utf-8")
    block = pse.attention_block("run_x", {"landing": "landed", "run_dir": str(tmp_path)}, "T")
    assert [item["id"] for item in block["items"]] == ["dec_9", "dec_2", "dec_3"]


def test_report_escapes_model_markup():
    markup = '<b>bold</b> [x](http://e) ![](http://u)'
    design = _design(decisions=[_decision(reason=markup)])
    design["alternatives"][1]["why_not"] = markup
    text = audit.render(_audit(design=design), fmt="report")
    tail = text.split("## Decisions", 1)[1]
    assert "<b>" not in tail and "[x](http://e)" not in tail and "![](" not in tail
    assert "&lt;b&gt;bold&lt;/b&gt; \\[x\\](http://e) \\!\\[\\](http://u)" in tail
    assert tail.count("&lt;b&gt;") == 2          # the decision and the not-taken reason


@pytest.mark.timeout(300)
def test_engine_credits_a_recorded_answer_to_the_person(rig):
    """Through the real CLI and engine: a person answers a REQUIREMENTS decision request on
    its card; the audit the engine builds credits that clarification to ``user`` (the
    engine's recorded answers carry their document), not to the agent."""
    r = rig("bugfix")
    r.create()
    r.gate0("WRONG-FRAME", n=1)
    r.gate0("WRONG-FRAME", n=2)
    asked = r.v1("decision-request", "--artifact", rig_decision(r), "--document", "REQUIREMENTS")
    assert asked.get("ok") is True, asked
    assert r.answer_card("a — swap")
    out = r.v1("stage-complete", "--artifact",
               r.artifact("requirements", rig_documents("bugfix")["evaluate"]))
    assert out.get("ok") is True, out
    value = r.engine().build_audit(through="DELIVER", persist=False)
    by = {row["id"]: row["by"] for row in value["details"]["decisions"]
          if row["source"] == "REQUIREMENTS"}
    # q1: the request the person answered on the card; Q1: a clarification the document
    # itself carries, with no recorded answer behind it: the agent's own account
    assert by == {"q1": "user", "Q1": "agent"}, value["details"]["decisions"]


def test_a_field_ending_in_a_full_stop_reads_once():
    design = _design(decisions=[_decision(choice="the existing documents.")])
    design["alternatives"][1]["what"] = "a new evidence artifact."
    for text in (documents.render_design(design), audit.render(_audit(design=design), fmt="report")):
        assert ".." not in text
        assert "a new evidence artifact. Not taken:" in text
        assert "the existing documents. Why:" in text
