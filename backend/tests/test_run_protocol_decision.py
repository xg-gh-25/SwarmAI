"""Pipeline v1 human-decision tests (v13 §B5.2, DC-1/DC-2, §D4).

Methodology: pure decisions against an explicit current-run reference index. Tests lock
question kinds, options/consequences, evidence insufficiency, reference requirements,
high-risk tradeoffs, answer identity and the bounded agent-after-cap path.
"""
from __future__ import annotations

import copy

import pytest

from core.run_protocol import decision as dc

REFS = {
    "analysis-1": {"kind": "analysis", "resolution": "asked"},
    "R1.1": {"kind": "requirement"},
    "event-1": {"kind": "answer_event"},
}


def options(*, all_safe=False):
    return [
        {"id": "A", "choice": "keep API", "consequence": "compatible",
         "reversible": True, "risk": "low"},
        {"id": "B", "choice": "replace API", "consequence": "consumers migrate",
         "reversible": True if all_safe else False,
         "risk": "low" if all_safe else "high"},
    ]


def requirement_decision():
    return {"request_id": "q1", "kind": "requirement_ambiguity",
            "question": "Which behavior is intended?", "options": options(),
            "why_evidence_insufficient": "both readings fit the existing code",
            "refs": ["analysis-1", "R1.1"]}


def tradeoff_decision():
    doc = requirement_decision()
    doc.update(kind="design_tradeoff", refs=["R1.1"])
    return doc


def issue_paths(doc, refs=REFS):
    return {issue.path for issue in dc.validate(doc, refs)}


def test_valid_requirement_ambiguity_and_design_tradeoff():
    assert dc.validate(requirement_decision(), REFS) == []
    assert dc.validate(tradeoff_decision(), REFS) == []


@pytest.mark.parametrize("field", ["request_id", "question", "why_evidence_insufficient"])
def test_required_decision_text(field):
    doc = requirement_decision()
    doc[field] = ""
    assert f"$.{field}" in issue_paths(doc)


@pytest.mark.parametrize("count", [0, 1, 4])
def test_decision_has_two_or_three_options(count):
    doc = requirement_decision()
    doc["options"] = options()[:1] * count
    assert "$.options" in issue_paths(doc)


@pytest.mark.parametrize("mutation,path", [
    (lambda d: d["options"][0].update(choice=""), "options[0].choice"),
    (lambda d: d["options"][0].update(consequence=""), "options[0].consequence"),
    (lambda d: d["options"][0].update(reversible="yes"), "options[0].reversible"),
    (lambda d: d["options"][0].update(risk="medium"), "options[0].risk"),
    (lambda d: d["options"][1].update(id="A"), "$.options"),
    (lambda d: d["options"][0].update(extra=True), "options[0].extra"),
])
def test_option_shape_is_strict(mutation, path):
    doc = requirement_decision()
    mutation(doc)
    assert path in issue_paths(doc)


def test_requirement_ambiguity_must_reference_an_asked_analysis():
    doc = requirement_decision()
    doc["refs"] = ["R1.1"]
    issues = dc.validate(doc, REFS)
    assert any("resolution=asked" in issue.message for issue in issues)
    not_asked = {**REFS, "analysis-2": {"kind": "analysis", "resolution": "confirmed"}}
    doc["refs"] = ["analysis-2"]
    assert any("resolution=asked" in issue.message for issue in dc.validate(doc, not_asked))


def test_design_tradeoff_needs_an_irreversible_or_high_risk_option():
    doc = tradeoff_decision()
    doc["options"] = options(all_safe=True)
    assert any("irreversible or high-risk" in issue.message for issue in dc.validate(doc, REFS))


def test_refs_must_be_nonempty_and_resolve_in_this_run():
    doc = requirement_decision()
    doc["refs"] = []
    assert "$.refs" in issue_paths(doc)
    doc["refs"] = ["another-run-event"]
    assert "refs[0]" in issue_paths(doc)


def test_unknown_decision_fields_and_kinds_fail():
    doc = requirement_decision()
    doc["kind"] = "mechanical_failure"
    doc["retry_count"] = 3
    assert {"$.kind", "$.retry_count"} <= issue_paths(doc)


def test_decision_cap_is_numeric_and_deterministic():
    assert dc.may_ask(0, 2) and dc.may_ask(1, 2)
    assert not dc.may_ask(2, 2) and not dc.may_ask(3, 2)
    for bad in ((-1, 2), (0, -1), (True, 2)):
        with pytest.raises(dc.DecisionError):
            dc.may_ask(*bad)


def test_answer_binds_current_request_and_declared_option():
    doc = requirement_decision()
    answer = dc.answer(doc, request_id="q1", option_id="B")
    assert answer == {"request_id": "q1", "option_id": "B",
                      "choice": "replace API", "answered_by": "user"}
    with pytest.raises(dc.DecisionError, match="current"):
        dc.answer(doc, request_id="q2", option_id="B")
    with pytest.raises(dc.DecisionError, match="declared"):
        dc.answer(doc, request_id="q1", option_id="C")


def test_agent_after_cap_needs_evidence_and_marks_dc2_failed():
    doc = requirement_decision()
    result = dc.resolve_after_cap(doc, option_id="A", evidence_refs=["R1.1"],
                                  known_refs=REFS, decisions_answered=2, cap=2)
    assert result["answered_by"] == "evidence"
    assert result["decided_by"] == "agent_after_cap" and result["dc2_passed"] is False
    with pytest.raises(dc.DecisionError, match="evidence"):
        dc.resolve_after_cap(doc, option_id="A", evidence_refs=["missing"], known_refs=REFS,
                             decisions_answered=2, cap=2)
    with pytest.raises(dc.DecisionError, match="only after"):
        dc.resolve_after_cap(doc, option_id="A", evidence_refs=["R1.1"], known_refs=REFS,
                             decisions_answered=1, cap=2)


def test_validation_does_not_mutate_the_decision():
    doc = requirement_decision()
    before = copy.deepcopy(doc)
    dc.validate(doc, REFS)
    assert doc == before
