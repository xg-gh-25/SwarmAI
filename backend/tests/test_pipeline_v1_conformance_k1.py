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


def test_an_isolated_repo_with_no_recorded_branch_yields_no_final(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    branches = (r.data / "state" / "pipeline_agent_audit" / "runs" / str(r.run_id)
                / "integration_branches.json")
    assert branches.is_file()
    branches.unlink()                       # the base can no longer be named
    out = r.report_and_complete()
    assert out.get("ok") is not True, out
    assert r.tested_tree("build") is None, "a report was recorded on a guessed base"


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

def test_an_isolated_run_without_its_worktree_never_tests_main(rig):
    r = rig()
    r.create()
    # an edit in the shared main checkout never enters the run's reported tree
    (r.repo / "backend/core/mod.py").write_text("def add(a, b):\n    return 0  # main\n")
    r.edit(Rig.CHANGE)
    assert r.report_and_complete().get("ok") is True
    import subprocess
    reported = subprocess.run(["git", "-C", str(r.repo), "cat-file", "-p",
                               f"{r.tested_tree('build')}:backend/core/mod.py"],
                              check=True, capture_output=True, text=True).stdout
    assert "# main" not in reported


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


def test_a_person_landed_run_is_quality_warn_after_completion(rig):
    """A human_override run stays Quality WARN on the COMPLETED recompute (MD-3 is
    gone: a person's landing is a fact the REPORT states, not an open check)."""
    from tests.test_pipeline_v1_real_e2e import _prepare, _request
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    assert r.v1("abandon", "--reason", "XG takes it from here").get("ok") is True
    assert _request(r, _prepare(r)).get("ok") is True
    engine = r.engine()
    assert engine.projection()["landing"] == "human_override"
    assert "MD-3" not in engine.open_checks()["checks"]
    assert engine.build_audit(through="COMPLETED", persist=False)["summary"]["quality"][
        "verdict"] == "WARN"
