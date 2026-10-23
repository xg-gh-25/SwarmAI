"""Conformance fixing spec 2026-10-23, K1: every judgment binds the evidence it reads.

Each case drives a real run (``tests/pipeline_v1_rig.py``: the shipped CLIs and hook
functions, no hand-written events) into a state where evidence is present but not
about the code here now, or where a recorded fact could be swapped after it was
validated, and asserts the v13 outcome:

- a refutation whose ref does not resolve is refused (§A3.5);
- LG-5's required set is the BASE validations.json, not the final's own list (§B3.2);
- changing the test selector is a QX-2 push reason (§E2);
- a final from another code tree does not complete BUILD (§A2.3 / §B3.5);
- an isolated repo whose base cannot be named yields no final (§B3.5);
- the decision a person answers is the one its event validated (§A4.4).
"""
from __future__ import annotations

import json

import pytest

from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(300)

FINDING = ('{"severity":"HIGH","kind":"behavior","file":"backend/core/mod.py","line":2,'
           '"claim":"operands swapped","fix":"restore order"}')


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made: list[Rig] = []

    def make(profile: str = "direct", **kwargs) -> Rig:
        r = Rig(tmp_path / f"case{len(made)}", monkeypatch, profile=profile, **kwargs)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def _check(r: Rig, check_id: str) -> tuple[bool, str]:
    from core.run_protocol import checks
    return checks.evaluate(check_id, r.engine().check_context())


def _at_review_with_finding(r: Rig) -> str:
    # >= 50 source lines: REVIEW dispatches a correctness expert (E1 roster)
    r.CHANGE = {"backend/core/mod.py": "def add(a, b):\n    return b + a\n" + "".join(
        f"\n\ndef f{i}():\n    return {i}\n" for i in range(15))}
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    assert started.get("ok") is True, started
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], FINDING if i == 0 else "NO FINDINGS", f"r1-{i}")
    assert [e for e in r.events() if e["kind"] == "finding_occ"], r.kinds()
    from core.run_protocol import review
    engine = r.engine()
    return review.ReviewOrchestrator(engine.store, engine.run_dir).findings()[0]["canonical_id"]


def test_refutation_ref_must_resolve(rig):
    r = rig()
    finding_id = _at_review_with_finding(r)
    addressed = r.v1("review-address", "--artifact", r.artifact("address", {
        "pass": 1, "items": [{"finding_id": finding_id, "hunk": "@@ no change: not a bug"}]}))
    assert addressed.get("ok") is True, addressed
    status = json.dumps({"finding_id": finding_id, "status": "REFUTED", "note": "b + a == a + b"})
    for i, handout in enumerate(addressed["handouts"]):
        r.spawn_reviewer(handout["prompt"], status, f"r2-{i}")
    proof = {"pass": 1, "proofs": [{"finding_id": finding_id, "evidence_type": "refuted",
                                    "refs": ["x"]}]}
    out = r.v1("review-proof", "--artifact", r.artifact("proof-x", proof))
    assert out.get("ok") is False and "x:" in out.get("error", ""), out
    assert not [e for e in r.events() if e["kind"] == "proof"], r.kinds()
    good = {"pass": 1, "proofs": [{"finding_id": finding_id, "evidence_type": "refuted",
                                   "refs": ["backend/core/mod.py:1-2"]}]}
    assert r.v1("review-proof", "--artifact", r.artifact("proof-ok", good)).get("ok") is True


def test_lg5_requires_the_base_manifest_not_the_finals_own_list(rig, monkeypatch):
    smoke = {"id": "smoke", "paths": ["backend/**/*.py"], "cwd": ".",
             "argv": ["python3", "-c", "pass"], "call_type": "build"}
    r = rig(extra={"validations.json": json.dumps({"schema": 1, "entries": [smoke]})})
    r.create()
    r.drive(until="deliver")
    engine = r.engine()
    final = engine.final()
    assert any(v["id"] == "smoke" for row in final["repos"].values()
               for v in row.get("validations", [])), final
    for row in final["repos"].values():
        row["validations"] = []            # the final no longer shows the required run
    monkeypatch.setattr(type(engine), "final", lambda self: final)
    from core.run_protocol import checks
    ok, detail = checks.evaluate("LG-5", engine.check_context())
    assert ok is False, detail


def test_changing_the_test_selector_is_a_qx2_push_reason(rig):
    r = rig()
    r.create()
    r.drive(until="review")
    r.edit({"backend/scripts/pipeline_test.py": "SELECT_NOTHING = True\n"})
    ok, detail = _check(r, "QX-2")
    assert ok is False and "pipeline_test.py" in detail, detail


def test_a_final_from_another_code_tree_does_not_complete_build(rig):
    r = rig("trivial")
    r.create()
    r.drive(until="build")
    # another session's test call: it never completes the stage itself (r6 §2.3)
    r.pt("tdd", "--files", "backend/tests/test_x.py", session="someone-else")
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return a - b\n"})   # after the run
    out = r.v1("stage-complete")
    assert out.get("ok") is False and r.state()["stage"] == "build", out


def test_an_isolated_repo_with_no_recorded_branch_yields_no_final(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    branches = (r.data / "state" / "pipeline_agent_audit" / "runs" / str(r.run_id)
                / "integration_branches.json")
    assert branches.is_file()
    branches.unlink()                       # the base can no longer be named
    out = r.pt("tdd", "--files", "backend/tests/test_x.py")
    assert out.get("ok") is not True, out
    assert r.engine().final() == {}, "a final was written on a guessed base"


def test_the_answered_decision_is_the_one_its_event_validated(rig):
    r = rig("trivial")
    r.create()
    r.drive(until="plan")
    decision = {
        "request_id": "q1", "kind": "design_tradeoff", "question": "Swap or rewrite?",
        "why_evidence_insufficient": "both fit", "refs": [r.events()[0]["event_id"]],
        "options": [
            {"id": "a", "choice": "swap", "consequence": "small", "reversible": True,
             "risk": "low"},
            {"id": "b", "choice": "rewrite", "consequence": "large", "reversible": False,
             "risk": "high"}]}
    asked = r.v1("decision-request", "--artifact", r.artifact("d1", decision),
                 "--document", "DESIGN_SPEC")
    assert asked.get("ok") is True, asked
    run_dir = r.engine().run_dir
    swapped = {**decision, "question": "Delete production data?"}
    (run_dir / "artifacts" / "decision-q1.json").write_text(json.dumps(swapped))
    assert r.engine().pending_decision()["question"] == "Swap or rewrite?"


# ── review round 1: the binding must not fail a legitimate run, nor leak ────

def test_an_unchanged_run_is_qx2_clean(rig):
    """A run that changed no pipeline code and whose final ran on the contract's
    pipeline code passes QX-2 (the hash is read from the final's provenance)."""
    r = rig()
    r.create()
    r.drive(until="review")
    ok, detail = _check(r, "QX-2")
    assert ok is True, detail


def test_an_isolated_run_without_its_worktree_never_tests_main(rig):
    r = rig()
    r.create()
    out = r.pt("tdd", "--files", "backend/tests/test_x.py", repo=r.repo)   # main, not its own
    assert out.get("ok") is not True, out
    assert r.engine().final() == {}, "a final was made on the shared main checkout"


def test_a_refutation_does_not_survive_the_tree_it_was_judged_on(rig):
    r = rig()
    finding_id = _at_review_with_finding(r)
    addressed = r.v1("review-address", "--artifact", r.artifact("address", {
        "pass": 1, "items": [{"finding_id": finding_id, "hunk": "@@ not a bug"}]}))
    status = json.dumps({"finding_id": finding_id, "status": "REFUTED", "note": "commutes"})
    for i, handout in enumerate(addressed["handouts"]):
        r.spawn_reviewer(handout["prompt"], status, f"r2-{i}")
    good = {"pass": 1, "proofs": [{"finding_id": finding_id, "evidence_type": "refuted",
                                   "refs": ["backend/core/mod.py:1-2"]}]}
    assert r.v1("review-proof", "--artifact", r.artifact("proof", good)).get("ok") is True
    assert _check(r, "RV-2")[0] is True
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return a - b\n"})
    ok, detail = _check(r, "RV-2")
    assert ok is False and finding_id in detail, detail


def test_a_person_landed_run_stays_not_push_ready_after_completion(rig):
    """Review round 2: MD-3 follows the folded landing, so the COMPLETED recompute
    of a human_override run keeps push_ready false."""
    from tests.test_pipeline_v1_real_e2e import _prepare, _request
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    assert r.v1("abandon", "--reason", "XG takes it from here").get("ok") is True
    assert _request(r, _prepare(r)).get("ok") is True
    engine = r.engine()
    assert engine.projection()["landing"] == "human_override"
    push = engine.push_status()
    assert push["push_ready"] is False and "MD-3" in push["reasons"], push
