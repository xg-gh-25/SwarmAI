"""Tests for session_harvest — layer-② session→golden draft harvesting.

Methodology: harvest_draft takes a LOW-SCORE real session (surfaced by
session_scorer, layer ③) and drafts an assertion-style golden case (layer ②),
landing it via an INJECTED add_case_fn with tier=draft. It is the twin of
conversation_extract.extract_candidates (read→prompt→LLM→parse) but reads
desktop sessions and carries NO channel semantics.

CRITICAL invariants (from the design's hard constraints):
- draft schema is FIXED: eval_method='llm', affected_by=[], tier='draft',
  evaluators=['goal_success'] — else eval_service.add_case's gate_schema/gate_refs
  reject it (Gate-1 BLOCK#1). affected_by=[] passes gate_refs trivially (honest:
  a harvested draft must NOT fabricate a DDD ref; a human enriches at promote).
- NEVER auto-promote: harvest_draft must land drafts ONLY, never call any promote
  path. test_no_auto_promote is the kernel-not-shell guard (system drafts, human
  ratifies).
- injected boundaries (invoke_fn + add_case_fn) → no Bedrock, no disk in tests.
"""
from __future__ import annotations

import json

from core.session_harvest import harvest_draft, _draft_skeleton


def _fake_invoke(prompt: str):
    """Fake LLM: returns a drafted assertion-style case body."""
    return json.dumps({
        "title": "Agent must verify a tool exists before relying on its output",
        "assertions": [
            "Agent reads the target file before asserting its contents",
            "Agent does NOT fabricate a result when the tool returned nothing",
        ],
    })


def test_harvest_draft_lands_via_add_case():
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
    )
    assert out is not None
    # draft landed with the FIXED schema
    assert landed["eval_method"] == "llm"
    assert landed["affected_by"] == []          # honest: no fabricated ref
    assert landed["tier"] == "draft"
    assert landed["evaluators"] == ["goal_success"]
    assert landed["dimension"] == "capability"  # carried from the score
    assert landed["scenario"]["turns"][0]["input"] == "Fix the timeout in session_unit.py"
    assert landed["assertions"]                 # non-empty, from the LLM draft


def test_harvest_draft_never_auto_promotes():
    """KERNEL-NOT-SHELL guard: the harvest path must NEVER promote. We assert
    (a) the landed case is tier=draft, and (b) the add_case_fn is the ONLY sink
    called — no promote_fn, no direct public write. This is enforced structurally:
    harvest_draft has no promote parameter and its source contains no 'promote'
    call. A separate source-scan test below backs this."""
    sinks_called = []

    def spy_add_case(case_data: dict):
        sinks_called.append(("add_case", case_data.get("tier")))
        return {"id": case_data["id"], "status": "added"}

    harvest_draft(
        session_id="sess-x", prompt="do a thing",
        score={"goal_score": 0.1, "tool_score": 0.1, "dimension": "judgment", "reason": "r"},
        invoke_fn=_fake_invoke, add_case_fn=spy_add_case,
    )
    # exactly one sink, and it landed a DRAFT (never public/stable/promoted)
    assert sinks_called == [("add_case", "draft")]


def test_harvest_source_has_no_promote_call():
    """Source-level guard (the durable one): the harvest module must invoke NO
    promote PATH. If a future edit wires a promote call, this RED-flags it — the
    system must never ratify its own drafts.

    Scans for a promote *invocation* (a call / attribute / endpoint), NOT the
    word 'promote' in prose — the module's docstrings deliberately EXPLAIN that
    it must never promote, and banning the word would forbid documenting the very
    rule. We strip comments + docstrings, then look for call-shaped uses."""
    import ast
    import inspect
    import core.session_harvest as mod

    tree = ast.parse(inspect.getsource(mod))
    offenders = []
    for node in ast.walk(tree):
        # any attribute access or name that is a promote* call target
        if isinstance(node, ast.Attribute) and "promote" in node.attr.lower():
            offenders.append(node.attr)
        if isinstance(node, ast.Name) and "promote" in node.id.lower():
            offenders.append(node.id)
        # string literals used as endpoints/paths (not docstrings — those are
        # ast.Expr/Constant at module/func top; we only flag call-arg strings)
        if isinstance(node, ast.Call):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and "promote" in arg.value.lower():
                    offenders.append(arg.value)
    assert not offenders, f"harvest must never promote — found promote invocation(s): {offenders}"


def test_harvest_draft_id_is_stable_from_session():
    """The draft id derives deterministically from session_id → re-harvesting the
    same session produces the same id (dedup key)."""
    ids = []

    def spy(case_data):
        ids.append(case_data["id"])
        return {"id": case_data["id"], "status": "added"}

    for _ in range(2):
        harvest_draft(session_id="sess-dup-99", prompt="p",
                      score={"goal_score": 0.2, "tool_score": 0.2,
                             "dimension": "capability", "reason": "r"},
                      invoke_fn=_fake_invoke, add_case_fn=spy)
    assert ids[0] == ids[1]  # stable id from session_id


def test_harvest_draft_malformed_llm_returns_none():
    """If the LLM draft is unparseable, harvest returns None (no case landed) —
    fail-closed, never lands a garbage draft."""
    landed = []
    out = harvest_draft(
        session_id="s", prompt="p",
        score={"goal_score": 0.2, "tool_score": 0.2, "dimension": "capability", "reason": "r"},
        invoke_fn=lambda p: "not json {{{",
        add_case_fn=lambda c: landed.append(c),
    )
    assert out is None
    assert landed == []


def test_draft_skeleton_schema():
    """The skeleton carries every field eval_service.add_case's gates require."""
    sk = _draft_skeleton(session_id="sess-777abc", dimension="compliance")
    for field in ("id", "category", "dimension", "eval_method", "affected_by", "evaluators", "tier"):
        assert field in sk, f"draft skeleton missing required field: {field}"
    assert sk["eval_method"] == "llm"
    assert sk["affected_by"] == []
    assert sk["tier"] == "draft"
    # Gate-2 F1: category MUST be canonical (a promoted draft with an off-canonical
    # category trips _validate_case_taxonomy's WARN forever). Provenance is the
    # GS_HARVEST_ id prefix (the drafts-queue filter key), not a bespoke category.
    assert sk["category"] == "quality"
    assert sk["id"].startswith("GS_HARVEST_")
