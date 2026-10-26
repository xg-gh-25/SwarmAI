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
- a ``.md`` changed in DELIVER still fails LG-1 (the landing rule is unchanged);
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
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"lessons": []}))
    out = r.v1("land", "--message", "fix: x\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    assert out.get("ok") is not True or out.get("state", {}).get("landing") != "landed", out
    assert "LG-1" in json.dumps(out), out
