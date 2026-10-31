"""A TEST change of doc-class files only does not send the run back through an empty
review (design 2026-10-26-pipeline-review-closure-and-visibility §5.2 point 1, step 10).

Before: any change in TEST (a patch id the REVIEW completion did not record) reopened
REVIEW, also when only ``.md/.txt/.rst`` files changed, where the incremental review has
nothing to hand out: review-start, two stage-completes and a second TEST report for a
one-line note.

What is tested, through real runs (``tests/pipeline_v1_rig.py``):
- TEST changes only a ``.txt``: one stage-complete reopens and completes REVIEW itself
  (the new patch id recorded), completes TEST on the report it was given and the run
  lands (LG-1 passes);
- TEST changes a code file: REVIEW reopens as before;
- a ``.md`` changed in DELIVER still fails LG-1 (the landing rule is unchanged); the
  landing completes REVIEW itself and the run goes back to TEST;
- the Run Audit lists the doc-class paths no review was sent.
"""
from __future__ import annotations

import json

import pytest

from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(600)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _completions(r: Rig) -> list[dict]:
    return [e["payload"] for e in r.events()
            if e["kind"] == "stage_complete" and e.get("stage") == "review"]


def test_a_doc_only_change_in_test_completes_review_itself_and_lands(rig):
    r = rig
    r.create()
    r.drive(until="test")
    before = _completions(r)[-1]["patch_ids"]["repo"]
    r.edit({"NOTES.txt": "a release note\n"})
    out = r.report_and_complete()
    assert out.get("ok") is True and r.state()["stage"] == "deliver", out
    assert [e for e in r.events() if e["kind"] == "review_reopen"], r.kinds()
    after = _completions(r)[-1]["patch_ids"]["repo"]
    assert after != before                         # the new change is the reviewed one
    state = r.drive()
    assert state["status"] == "completed" and state.get("landing") == "landed", state
    audit = json.loads((r.engine().run_dir / "run_audit.json").read_text())
    assert audit["details"]["doc_paths_not_reviewed"] == {"repo": ["NOTES.txt"]}, audit["details"]
    assert "NOTES.txt" in (r.engine().run_dir / "REPORT.md").read_text()


def test_a_code_change_in_test_still_reopens_review(rig):
    r = rig
    r.create()
    r.drive(until="test")
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return b + a  # checked\n"})
    out = r.report_and_complete()
    assert out.get("reopened") == "review" and r.state()["stage"] == "review", out


def test_a_doc_change_in_deliver_still_fails_lg1(rig):
    r = rig
    r.create()
    r.drive(until="deliver")
    r.edit({"README.md": "late note\n"})
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
    out = r.v1("land", "--message", "fix: x\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    assert out.get("ok") is not True or out.get("state", {}).get("landing") != "landed", out
    # LG-1 still refuses it; the landing completes that REVIEW itself and the run is
    # back in TEST for the new tree (design 2026-10-31 §3 change 3)
    lg1 = [e["payload"] for e in r.events() if e["kind"] == "check"
           and e["payload"].get("check_id") == "LG-1"][-1]
    assert lg1["passed"] is False and out.get("reopened") == "test", out


def test_doc_only_reopens_do_not_use_up_the_reopen_cap(rig):
    """Review fix: doc-only TEST changes each complete REVIEW themselves, however many;
    a doc-only reopen is not one of the REVIEW_REOPEN_CAP code reopens. (A failing
    TEST report keeps the run in TEST between them.)"""
    from core.run_protocol import lifecycle
    r = rig
    r.create()
    r.drive(until="test")
    for i in range(lifecycle.REVIEW_REOPEN_CAP + 1):
        r.edit({"NOTES.txt": f"note {i}\n"})
        out = r.report_and_complete("fail")
        assert r.state()["stage"] == "test", (i, out)
        assert "TS-1" not in json.dumps(out.get("forced") or {}), out
    r.edit({"NOTES.txt": "last note\n"})
    out = r.report_and_complete()
    assert out.get("ok") is True and r.state()["stage"] == "deliver", out


def test_a_failing_review_check_leaves_no_failed_review_attempt(rig, monkeypatch):
    """Review fix: when REVIEW's own checks would not pass, the engine records no
    REVIEW attempt for the agent's window of 3; the run is back in REVIEW as usual."""
    from core.run_protocol import checks
    r = rig
    r.create()
    r.drive(until="test")
    engine = r.engine()
    real = checks.evaluate
    monkeypatch.setattr(checks, "evaluate", lambda cid, ctx: (False, "forced")
                        if cid == "RV-4" else real(cid, ctx))
    r.edit({"NOTES.txt": "a note\n"})
    out = engine.stage_complete(r.report())
    assert out.get("reopened") == "review", out
    attempts = [e for e in r.events() if e["kind"] == "stage_attempt" and e.get("stage") == "review"]
    before = len([e for e in attempts if e["payload"].get("check_ids")])
    assert r.state()["stage"] == "review"
    assert not [e for e in r.events() if e["kind"] == "check" and e.get("stage") == "review"
                and not e["payload"]["passed"]], "a failed REVIEW attempt was recorded"
    assert before >= 1


def test_a_repo_no_review_saw_is_never_doc_only(monkeypatch):
    """Review fix: in a multi-repo run, a repo with a tree now and none at the last
    review fails closed (its code was never reviewed), whatever the other repo changed."""
    from types import SimpleNamespace
    from core.run_protocol import engine as engine_module
    fake = SimpleNamespace(effective_repos=lambda: {"x": "/nowhere-x", "y": "/nowhere-y"})
    # changed_since_review: the review facts' paths (engine._review_facts)
    seen_both = SimpleNamespace(run={"last_reviewed_trees": {"x": "t8", "y": "t1"},
                                     "code_trees": {"x": "t8", "y": "t2"},
                                     "changed_since_review": {"y": ["README.md"]}})
    assert engine_module.RunEngine._doc_paths_since_review(fake, seen_both) == {
        "y": ["README.md"]}
    x_unseen = SimpleNamespace(run={"last_reviewed_trees": {"y": "t1"},
                                    "code_trees": {"x": "t9", "y": "t2"},
                                    "changed_since_review": {"y": ["README.md"]}})
    assert engine_module.RunEngine._doc_paths_since_review(fake, x_unseen) == {}
