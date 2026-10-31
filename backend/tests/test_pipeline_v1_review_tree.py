"""S6-2: review binds the code it saw (design usable-by-anyone §3.3.3, r14).

What is tested, through real runs (``tests/pipeline_v1_rig.py``: CLI + hooks):

- REVIEW's completion records the code trees and patch ids it signed off on;
- code changed in TEST sends the run back to REVIEW; review-start then hands out an
  incremental review of exactly diff(last reviewed tree, current tree) to correctness;
  after it the run lands;
- RV-4: an edit after the last review round keeps REVIEW from completing;
- LG-1: a change no REVIEW completed cannot land, even with a TEST report on it;
- the reopen cap: past two reopens, TEST's attempt fails instead;
- reported_test: a finding's fix proven by a passing command of a validation report on
  the current code (report-tests in REVIEW records one); R2 ADEQUATE closes it,
  TEST_INADEQUATE reopens it as invalid_test.
"""
from __future__ import annotations

import pytest

from tests.pipeline_v1_rig import SWARM, Rig

pytestmark = pytest.mark.timeout(300)

FIX = {"backend/core/mod.py": "def add(a, b):\n    return b + a  # checked\n"}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    rigs: list[Rig] = []

    def make(profile: str = "direct", **kwargs) -> Rig:
        r = Rig(tmp_path / f"case{len(rigs)}", monkeypatch, profile=profile, **kwargs)
        rigs.append(r)
        return r
    yield make
    for r in rigs:
        if r.run_id:
            assert r.v1("status").get("ok") is True, "the event log no longer folds"
        r.close()


def _review_completions(r: Rig) -> list[dict]:
    return [e["payload"] for e in r.events()
            if e["kind"] == "stage_complete" and e.get("stage") == "review"]


def test_review_completion_records_the_code_it_signed_off_on(rig):
    r = rig()
    r.create()
    r.drive(until="test")
    (done,) = _review_completions(r)
    assert set(done["code_trees"]) == {"repo"} and set(done["patch_ids"]) == {"repo"}
    assert done["code_trees"]["repo"] == r.engine().check_context().run["code_trees"]["repo"]


def test_a_change_in_test_goes_back_to_review_for_an_incremental_review(rig):
    r = rig()
    r.create()
    r.drive(until="test")
    reviewed = _review_completions(r)[0]["code_trees"]["repo"]
    r.edit(FIX)
    out = r.report_and_complete()
    assert out.get("reopened") == "review" and r.state()["stage"] == "review", out
    assert "review-start" in out["next"]["command"]
    started = r.v1("review-start")
    assert started.get("incremental") is True, started
    (handout,) = started["handouts"]
    diff = __import__("pathlib").Path(handout["path"]).read_text(encoding="utf-8").split("INCREMENTAL REVIEW", 1)[1]
    # exactly the change since the last review: the fix, not the original swap
    assert "+    return b + a  # checked" in diff and "-    return a + b" not in diff
    event = next(e for e in r.events() if e["kind"] == "handout"
                 and e["payload"].get("incremental_from"))
    assert event["payload"]["incremental_from"] == {"repo": reviewed}
    assert event["payload"]["expert_id"] == "correctness"
    r.spawn_reviewer(handout["prompt"], "NO FINDINGS", "incremental-0")
    assert r.v1("stage-complete").get("ok") is True
    assert len(_review_completions(r)) == 2
    from tests.pipeline_v1_e2e_common import _landed_clean
    _landed_clean(r.drive())


def test_an_edit_after_the_last_review_keeps_review_open(rig):
    r = rig()
    r.create()
    r.drive(until="test")
    r.edit(FIX)
    r.report_and_complete()                         # back to REVIEW
    started = r.v1("review-start")
    r.spawn_reviewer(started["handouts"][0]["prompt"], "NO FINDINGS", "inc-0")
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return a + b  # again\n"})
    out = r.v1("stage-complete")
    assert out.get("ok") is False and "RV-4" in str(out.get("checks")), out
    # the visit's first round answered with nothing to fix: its review is closed, so an
    # edit now is code no review will see; restore it (design 2026-10-31 §3 change 1)
    assert "Restore the reviewed code" in out["next"]["why"], out["next"]
    again = r.v1("review-start")
    assert again.get("ok") is False and not again.get("handouts"), again
    assert "git -C" in again["error"] and "restore --source=" in again["error"]


def test_a_change_no_review_completed_cannot_land(rig):
    r = rig()
    r.create()
    r.drive(until="deliver")
    r.edit(FIX)                                     # edited in DELIVER
    r.v1("report-tests", "--artifact", r.artifact("report", r.report()))
    out = r.v1("land", "--message", "fix: swap" + SWARM)
    assert out.get("ok") is False and "never reviewed" in str(out.get("failed")), out


def test_past_the_reopen_cap_test_fails_instead(rig):
    r = rig()
    r.create()
    r.drive(until="test")
    for i in range(2):
        r.edit({"backend/core/mod.py": f"def add(a, b):\n    return b + a  # {i}\n"})
        assert r.report_and_complete().get("reopened") == "review"
        started = r.v1("review-start")
        r.spawn_reviewer(started["handouts"][0]["prompt"], "NO FINDINGS", f"inc-{i}")
        assert r.v1("stage-complete").get("ok") is True
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return b + a  # 3\n"})
    out = r.report_and_complete()
    assert out.get("ok") is False and "reopened" not in out, out
    assert r.state()["stage"] == "test"
    assert any(e["kind"] == "metric" and e["payload"]["name"] == "cap_exceeded.review_reopen"
               for e in r.events())


# ── reported_test: a finding closed by a test the agent ran and reported ─────────
FINDING = ('{"severity":"HIGH","kind":"behavior","file":"backend/core/mod.py","line":2,'
           '"claim":"operands swapped","fix":"restore order"}')
TEST_CMD = "python -m pytest backend/tests/test_x.py"


def _finding_at_review(r: Rig) -> str:
    r.CHANGE = {"backend/core/mod.py": "def add(a, b):\n    return b + a\n" + "".join(
        f"\n\ndef f{i}():\n    return {i}\n" for i in range(15))}  # >= 50 lines: an expert
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], FINDING if i == 0 else "NO FINDINGS", f"r1-{i}")
    from core.run_protocol import review
    engine = r.engine()
    return review.ReviewOrchestrator(engine.store, engine.run_dir).findings()[0]["canonical_id"]


def _address(r: Rig, finding_id: str, command: str = TEST_CMD) -> dict:
    return r.v1("review-address", "--artifact", r.artifact("address", {"pass": 1, "items": [
        {"finding_id": finding_id, "hunk": "@@ -2 +2 @@ return a + b",
         "proof": {"evidence_type": "reported_test", "command": command}}]}))


def _status(r: Rig, finding_id: str) -> str:
    from core.run_protocol import review
    engine = r.engine()
    rows = review.ReviewOrchestrator(engine.store, engine.run_dir).findings()
    return next(row["status"] for row in rows if row["canonical_id"] == finding_id)


def test_reported_test_needs_a_passing_command_on_the_current_code(rig):
    r = rig()
    finding_id = _finding_at_review(r)
    r.edit({"backend/core/mod.py": r.CHANGE["backend/core/mod.py"].replace("b + a", "a + b")})
    out = _address(r, finding_id)                  # the BUILD report is for the old code
    assert out.get("ok") is False and "report-tests" in out.get("error", ""), out
    assert not [e for e in r.events() if e["kind"] == "address"], "no partial pass"
    r.v1("report-tests", "--artifact", r.artifact("fix", r.report()))
    assert _address(r, finding_id, "pytest -k other").get("ok") is False
    out = _address(r, finding_id)
    assert out.get("ok") is True, out
    proof = next(e for e in r.events() if e["kind"] == "proof")["payload"]
    assert proof["evidence_type"] == "reported_test" and proof["command"] == TEST_CMD
    # proven, waiting for R2 to judge the test (below); not closed until ADEQUATE
    # (review-closure invariant 3: waiting never shows it as open)
    assert _status(r, finding_id) == "closed_proven"
    (handout,) = out["handouts"]
    full = __import__("pathlib").Path(handout["path"]).read_text(encoding="utf-8")
    assert TEST_CMD in full and "test_x fails before" in full


@pytest.mark.parametrize("verdict,expected", [("ADEQUATE", "closed_proven"),
                                              ("TEST_INADEQUATE", "invalid_test")])
def test_r2_decides_whether_the_reported_test_shows_the_fix(rig, verdict, expected):
    import json
    r = rig()
    finding_id = _finding_at_review(r)
    r.edit({"backend/core/mod.py": r.CHANGE["backend/core/mod.py"].replace("b + a", "a + b")})
    r.v1("report-tests", "--artifact", r.artifact("fix", r.report()))
    out = _address(r, finding_id)
    status = json.dumps({"finding_id": finding_id, "status": verdict, "note": "n"})
    r.spawn_reviewer(out["handouts"][0]["prompt"], status, "r2-0")
    assert _status(r, finding_id) == expected
