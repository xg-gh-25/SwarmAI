"""Tests for session_harvest — layer-② session→golden harvesting, TEETH-GATED.

Methodology (run_1bfd3cf9, option-D): harvest_draft takes a LOW-SCORE real
session, has the LLM generate {full assertion case + negative_example}, runs the
TEETH GATE (gate_by_knockout: the pinned judge must FAIL the negative), and then
either lands it via add_case_fn (tier=active) or discards it via discard_fn
(recoverable archive). There is NO tier=draft middle state anymore.

CRITICAL invariants:
- FIXED schema: eval_method='llm', affected_by=[], tier='active' (post-gate),
  evaluators=['goal_success'], + a REQUIRED negative_example.
- Gate is the bar: a case that FAILS the teeth gate is discarded, never landed.
- NEVER auto-promote (kernel-not-shell): system generates + gates; the gate — not
  a human, not the generator — decides admission.
- injected boundaries (invoke_fn + add_case_fn + gate_fn + discard_fn) → no
  Bedrock, no disk in tests.
"""
from __future__ import annotations

import json

from core.session_harvest import harvest_draft, _draft_skeleton


def _fake_invoke(prompt: str):
    """Fake LLM: returns a full case body WITH a negative_example."""
    return json.dumps({
        "title": "Agent must verify a tool exists before relying on its output",
        "assertions": [
            "Agent reads the target file before asserting its contents",
            "Agent does NOT fabricate a result when the tool returned nothing",
        ],
        "negative_example": "Sure, the file says X (fabricated without reading it).",
    })


def _pass_gate(case):
    return (True, "teeth: negative correctly failed by pinned judge")


def _fail_gate(case):
    return (False, "tautology: negative_example also PASSED the judge — no teeth")


def test_harvest_lands_active_when_gate_passes():
    landed = {}

    def spy_add_case(case_data: dict):
        landed.update(case_data)
        return {"id": case_data["id"], "status": "added"}

    out = harvest_draft(
        session_id="sess-abc12345",
        prompt="Fix the timeout in session_unit.py",
        score={"goal_score": 0.3, "tool_score": 0.4, "dimension": "capability",
               "reason": "agent didn't read the file"},
        invoke_fn=_fake_invoke,
        add_case_fn=spy_add_case,
        gate_fn=_pass_gate,
        discard_fn=lambda c, r: None,
    )
    assert out is not None
    assert landed["eval_method"] == "llm"
    assert landed["affected_by"] == []
    assert landed["tier"] == "active"              # post-gate: earned active, no draft
    assert landed["evaluators"] == ["goal_success"]
    assert landed["dimension"] == "capability"
    assert landed["negative_example"]              # the knockout input, present
    assert landed["scenario"]["turns"][0]["input"] == "Fix the timeout in session_unit.py"
    assert landed["assertions"]


def test_harvest_discards_when_gate_fails():
    """A case whose negative_example is NOT failed (tautology) is discarded via
    discard_fn and NEVER reaches add_case_fn."""
    added = []
    discarded = []

    out = harvest_draft(
        session_id="sess-taut", prompt="do a thing",
        score={"goal_score": 0.1, "tool_score": 0.1, "dimension": "judgment", "reason": "r"},
        invoke_fn=_fake_invoke,
        add_case_fn=lambda c: added.append(c),
        gate_fn=_fail_gate,
        discard_fn=lambda c, r: discarded.append((c["id"], r)),
    )
    assert out is None
    assert added == [], "a gate-failed case must NEVER be landed"
    assert len(discarded) == 1, "a gate-failed case must be discarded to the archive"
    assert "tautolog" in discarded[0][1].lower()


def test_harvest_missing_negative_returns_none():
    """LLM omits negative_example → parse fail-closed → nothing landed/gated."""
    added = []
    out = harvest_draft(
        session_id="s", prompt="p",
        score={"goal_score": 0.2, "tool_score": 0.2, "dimension": "capability", "reason": "r"},
        invoke_fn=lambda p: json.dumps({"title": "t", "assertions": ["a"]}),  # no negative
        add_case_fn=lambda c: added.append(c),
        gate_fn=_pass_gate,
        discard_fn=lambda c, r: None,
    )
    assert out is None
    assert added == []


def test_harvest_never_auto_promotes():
    """KERNEL-NOT-SHELL: only add_case_fn is the land sink; tier is active (earned
    via gate), never a promote path."""
    sinks = []

    def spy_add_case(case_data: dict):
        sinks.append(("add_case", case_data.get("tier")))
        return {"id": case_data["id"], "status": "added"}

    harvest_draft(
        session_id="sess-x", prompt="do a thing",
        score={"goal_score": 0.1, "tool_score": 0.1, "dimension": "judgment", "reason": "r"},
        invoke_fn=_fake_invoke, add_case_fn=spy_add_case,
        gate_fn=_pass_gate, discard_fn=lambda c, r: None,
    )
    assert sinks == [("add_case", "active")]


def test_harvest_source_has_no_promote_call():
    """Source-level guard: the harvest module must invoke NO promote path."""
    import ast
    import inspect
    import core.session_harvest as mod

    tree = ast.parse(inspect.getsource(mod))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and "promote" in node.attr.lower():
            offenders.append(node.attr)
        if isinstance(node, ast.Name) and "promote" in node.id.lower():
            offenders.append(node.id)
        if isinstance(node, ast.Call):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and "promote" in arg.value.lower():
                    offenders.append(arg.value)
    assert not offenders, f"harvest must never promote — found: {offenders}"


def test_harvest_id_is_stable_from_session():
    ids = []

    def spy(case_data):
        ids.append(case_data["id"])
        return {"id": case_data["id"], "status": "added"}

    for _ in range(2):
        harvest_draft(session_id="sess-dup-99", prompt="p",
                      score={"goal_score": 0.2, "tool_score": 0.2,
                             "dimension": "capability", "reason": "r"},
                      invoke_fn=_fake_invoke, add_case_fn=spy,
                      gate_fn=_pass_gate, discard_fn=lambda c, r: None)
    assert ids[0] == ids[1]


def test_harvest_malformed_llm_returns_none():
    landed = []
    out = harvest_draft(
        session_id="s", prompt="p",
        score={"goal_score": 0.2, "tool_score": 0.2, "dimension": "capability", "reason": "r"},
        invoke_fn=lambda p: "not json {{{",
        add_case_fn=lambda c: landed.append(c),
        gate_fn=_pass_gate, discard_fn=lambda c, r: None,
    )
    assert out is None
    assert landed == []


def test_draft_skeleton_schema():
    """The skeleton carries every field add_case's gates require; tier=active."""
    sk = _draft_skeleton(session_id="sess-777abc", dimension="compliance")
    for field in ("id", "category", "dimension", "eval_method", "affected_by", "evaluators", "tier"):
        assert field in sk, f"skeleton missing required field: {field}"
    assert sk["eval_method"] == "llm"
    assert sk["affected_by"] == []
    assert sk["tier"] == "active"           # post-gate landing tier, no draft
    assert sk["category"] == "quality"
    assert sk["id"].startswith("GS_HARVEST_")
