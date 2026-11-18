"""EVALUATE decides one run or several (requirements.json ``split``).

The orchestrator records at EVALUATE whether the requirement's parts are one run or
separate runs. The schema checks the record's shape against the document's own
requirement ids; the next step on the stage after EVALUATE states a ``separate``
decision before any design is spent; REQUIREMENTS.md renders it; and the late
``split_hint`` after PLAN also offers running the groups as Hive children.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from core.run_protocol import documents, next_step
from core.run_protocol import schemas as sc
from tests.test_run_protocol_schemas import ac, full_requirements


def _two_requirements(split=None):
    doc = full_requirements()
    doc["requirements"].append({"id": "R2", "story": "As an operator",
                                "acceptance": [ac("R2.1")]})
    if split is not None:
        doc["split"] = split
    return doc


def _issues(doc):
    return sc.validate_requirements(doc, variant="full", profile="full")


GOOD = {"decision": "separate", "parts": [["R1"], ["R2"]],
        "reason": "R2 changes another module and is useful alone"}


@pytest.mark.parametrize("split, path", [
    ({**GOOD, "parts": [["R1"], ["R9"]]}, "split.parts[1]"),
    ({**GOOD, "decision": "one", "parts": [["R1"]]}, "split.parts"),  # R2 in no part
    ({**GOOD, "parts": [["R1", "R2"], ["R2"]]}, "split.parts"),   # R2 twice
    ({**GOOD, "decision": "separate", "parts": [["R1", "R2"]]}, "split.parts"),
    ({**GOOD, "decision": "maybe"}, "split.decision"),
    ({**GOOD, "reason": " "}, "split.reason"),
    ({**GOOD, "parts": [[]]}, "split.parts[0]"),
    ({**GOOD, "extra": 1}, "split"),
])
def test_split_shapes(split, path):
    assert _issues(_two_requirements(GOOD)) == []
    assert _issues(_two_requirements({**GOOD, "decision": "one"})) == []
    assert _issues(_two_requirements({"decision": "one", "parts": [["R1", "R2"]],
                                      "reason": "R2 needs R1's new field"})) == []
    issues = _issues(_two_requirements(split))
    assert issues and all(i.check_id == "RQ-1" for i in issues), issues
    assert any(i.path.startswith(path) for i in issues), [i.path for i in issues]


def test_malformed_requirements_with_split_is_reported_not_raised():
    for value in (5, True, "R1"):
        issues = sc.validate_requirements({"requirements": value, "split": GOOD},
                                          variant="full", profile="full")
        assert issues and all(i.check_id.startswith("RQ") for i in issues)


def test_no_split_is_unchanged():
    assert _issues(_two_requirements()) == []
    assert _issues(full_requirements()) == []
    # lite (direct) does not accept it: no EVALUATE there
    lite = {"requirements": [{"id": "R1", "acceptance": [ac()]}], "split": GOOD}
    assert sc.validate_requirements(lite, variant="lite", profile="direct")


def _engine(requirements, design=None):
    return SimpleNamespace(check_context=lambda: SimpleNamespace(requirements=requirements,
                                                                 design=design))


def test_advice_after_evaluate():
    out = {"why": "every think check passes"}
    next_step._note_split_at_evaluate(_engine(_two_requirements(GOOD)), out)
    assert out["split_advice"] == [["R1"], ["R2"]]
    assert "R1; R2" in out["why"] and "Hive" in out["why"] and GOOD["reason"] in out["why"]
    for doc in (_two_requirements({"decision": "one", "parts": [["R1", "R2"]],
                                   "reason": "coupled"}),
                # two parts kept as one run (the person asked for one run)
                _two_requirements({"decision": "one", "parts": [["R1"], ["R2"]],
                                   "reason": "the person asked for one run"}),
                _two_requirements(), {}, {"split": "separate"},
                {"split": {"decision": "separate", "parts": "R1"}}):
        out = {"why": "x"}
        next_step._note_split_at_evaluate(_engine(doc), out)
        assert out == {"why": "x"}, doc

    # once PLAN's design is written the design is spent: no more advice
    out = {"why": "x"}
    next_step._note_split_at_evaluate(_engine(_two_requirements(GOOD), design={"tasks": []}), out)
    assert out == {"why": "x"}

    def broken():
        raise OSError("unreadable")
    out = {"why": "x"}
    next_step._note_split_at_evaluate(SimpleNamespace(check_context=broken), out)
    assert out == {"why": "x"}


def test_next_step_states_it_on_the_stage_after_evaluate(monkeypatch):
    """Wired into next_step on every THINK and PLAN response (a later `status` too, so a
    missed reply does not lose it); the helper itself stops once the design exists."""
    calls = []
    monkeypatch.setattr(next_step, "_note_split_at_evaluate",
                        lambda engine, out: calls.append(out.get("stage")))
    monkeypatch.setattr(next_step, "blocking_checks", lambda engine, state: [])
    monkeypatch.setattr(next_step, "_note_children", lambda engine, out: None)
    monkeypatch.setattr(next_step, "_cmd", lambda engine, rest: rest)
    for stage in ("think", "plan"):
        engine = SimpleNamespace(
            projection=lambda stage=stage: {"status": "active", "stage": stage,
                                            "profile": "full" if stage == "think" else "trivial"},
            contract={"profile": "full"}, check_context=lambda: None)
        next_step.next_step(engine, previous_stage="evaluate")
        next_step.next_step(engine, previous_stage=None)
    assert calls == ["think", "think", "plan", "plan"]
    calls.clear()
    review = SimpleNamespace(projection=lambda: {"status": "active", "stage": "reflect",
                                                 "profile": "full"},
                             contract={"profile": "full"}, check_context=lambda: None)
    next_step.next_step(review, previous_stage="review")
    assert calls == []


def test_rendered_and_hinted():
    text = documents.render_requirements(_two_requirements(GOOD))
    assert "## Split" in text and "**separate**" in text and GOOD["reason"] in text
    assert "- Part 1: R1" in text and "- Part 2: R2" in text
    assert "## Split" not in documents.render_requirements(_two_requirements())
    wide = [{"id": f"T{i}", "file": f"{('backend', 'desktop', 'docs')[i % 3]}/f{i}.py",
             "depends_on": []} for i in range(45)]
    out: dict = {"why": "x."}
    next_step._note_split(SimpleNamespace(
        check_context=lambda: SimpleNamespace(design={"tasks": copy.deepcopy(wide)})), out)
    assert "Swarm Run" in out["why"] and "abandon this one" in out["why"]
