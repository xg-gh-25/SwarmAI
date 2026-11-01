"""One REVIEW visit: two rounds, a third only for HIGH, and a lost answer asked again
(design docs/design/2026-10-31-pipeline-review-close-and-landing-route.md §3 changes 1-2).

What is tested, through real runs (``tests/pipeline_v1_rig.py``: CLI + hooks):

- after the visit's R2, open MEDIUM / LOW findings read as residual risks, a fixing
  pass on them is refused, and ``next`` completes REVIEW (T1);
- a HIGH still open after R2 gets one more pass, for HIGH findings only; after its R2
  the visit is closed (T2);
- an edit after the closed review is code no review sees: ``review-start`` hands out
  nothing and names the restore (T3);
- a review TEST reopened is a new visit with its own fixing pass (T4);
- a first-round answer that could not be read is asked again once, before REVIEW can
  complete, with the parser's reasons; never a third time (T5, T6).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig

pytestmark = pytest.mark.timeout(300)

TEST_CMD = "python -m pytest backend/tests/test_x.py"
BIG = "def add(a, b):\n    return b + a\n" + "".join(
    f"\n\ndef f{i}():\n    return {i}\n" for i in range(15))       # >= 50 lines


def _finding(severity: str, line: int, claim: str) -> str:
    return json.dumps({"severity": severity, "kind": "behavior", "file": "backend/core/mod.py",
                       "line": line, "claim": claim, "fix": "fix it"})


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


def _r1(r: Rig, answer: str) -> list[str]:
    """Create, drive to REVIEW, answer the first handout with *answer*."""
    r.CHANGE = {"backend/core/mod.py": BIG}
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], answer if i == 0 else "NO FINDINGS", f"r1-{i}")
    return [row["canonical_id"] for row in _rows(r)]


def _rows(r: Rig) -> list[dict]:
    from core.run_protocol import review
    engine = r.engine()
    return review.ReviewOrchestrator(engine.store, engine.run_dir).findings()


def _fix_and_address(r: Rig, ids: list[str], marker: str) -> dict:
    r.edit({"backend/core/mod.py": BIG.replace("b + a", f"a + b  # {marker}")})
    r.v1("report-tests", "--artifact", r.artifact(f"rep-{marker}", r.report()))
    return r.v1("review-address", "--artifact", r.artifact(f"addr-{marker}", {
        "pass": 1, "items": [{"finding_id": cid, "hunk": "@@ -2 +2 @@ return a + b",
                              "proof": {"evidence_type": "reported_test",
                                        "command": TEST_CMD}} for cid in ids]}))


def _r2(r: Rig, out: dict, statuses: dict[str, str], extra: str = "", tag: str = "r2") -> None:
    lines = [json.dumps({"finding_id": cid, "status": word, "note": "n"})
             for cid, word in statuses.items()]
    for i, handout in enumerate(out["handouts"]):
        r.spawn_reviewer(handout["prompt"], "\n".join(lines + ([extra] if extra else [])),
                         f"{tag}-{i}")


def _findings(r: Rig) -> dict[str, str]:
    out = r.v1("findings")
    return {row["id"]: row["close_with"] for row in out["findings"]}


def test_after_r2_with_no_open_high_the_rest_is_residual(rig):
    r = rig()
    (low,) = _r1(r, _finding("LOW", 2, "the order is surprising"))
    out = _fix_and_address(r, [low], "one")
    assert out.get("ok") is True, out
    _r2(r, out, {low: "TEST_INADEQUATE"}, _finding("LOW", 3, "a new nit"))
    lines = _findings(r)
    assert len(lines) == 2 and all(text.startswith("residual") for text in lines.values()), lines
    again = r.v1("review-address", "--artifact", r.artifact("addr-two", {
        "pass": 2, "items": [{"finding_id": cid, "hunk": "@@ -2 +2 @@ x"} for cid in lines]}))
    assert again.get("ok") is False and "fixing passes are done" in again["error"], again
    step = r.v1("status")["next"]
    assert "stage-complete" in step["command"], step
    assert r.v1("stage-complete").get("ok") is True


def test_an_open_high_after_r2_gets_one_more_pass_for_high_only(rig):
    r = rig()
    (high,) = _r1(r, _finding("HIGH", 2, "operands swapped"))
    out = _fix_and_address(r, [high], "one")
    # far from the HIGH (a ±10-line cluster is fixed as one pass)
    _r2(r, out, {high: "TEST_INADEQUATE"}, _finding("LOW", 40, "a new nit"))
    lines = _findings(r)
    (low,) = [cid for cid in lines if cid != high]
    assert lines[low].startswith("residual") and lines[high].startswith("fix it"), lines
    refused = _fix_and_address(r, [high, low], "two")
    assert refused.get("ok") is False and "HIGH / CRITICAL findings (and their cluster) only" in refused["error"]
    out = _fix_and_address(r, [high], "three")
    assert out.get("ok") is True, out
    _r2(r, out, {high: "TEST_INADEQUATE"}, tag="r2b")
    assert _findings(r)[high].startswith("this review's fixing passes are done")
    last = _fix_and_address(r, [high], "four")
    assert last.get("ok") is False and "fixing passes are done" in last["error"], last


def test_a_second_pass_before_the_first_r2_answers_is_refused(rig):
    r = rig()
    (low,) = _r1(r, _finding("LOW", 2, "nit"))
    out = _fix_and_address(r, [low], "one")
    assert out.get("ok") is True, out
    again = _fix_and_address(r, [low], "two")
    assert again.get("ok") is False and "has not answered yet" in again["error"], again


def test_an_edit_after_the_closed_review_must_be_restored(rig):
    r = rig()
    (low,) = _r1(r, _finding("LOW", 2, "nit"))
    out = _fix_and_address(r, [low], "one")
    _r2(r, out, {low: "ADEQUATE"})
    r.edit({"backend/core/mod.py": BIG.replace("b + a", "a + b  # later")})
    step = r.v1("status")["next"]
    assert "Restore the reviewed code" in step["why"] and "stage-complete" in step["command"]
    started = r.v1("review-start")
    assert started.get("ok") is False and not started.get("handouts"), started
    assert not [e for e in r.events() if e["kind"] == "handout"
                and e["payload"].get("incremental_from")], "no incremental review"


def test_a_review_test_reopened_is_a_new_visit_with_its_own_pass(rig):
    r = rig()
    (low,) = _r1(r, _finding("LOW", 40, "nit"))      # far from line 2: its own cluster
    out = _fix_and_address(r, [low], "one")
    _r2(r, out, {low: "TEST_INADEQUATE"})            # left open: the first visit's residual
    assert r.v1("stage-complete").get("ok") is True
    r.edit({"backend/core/mod.py": BIG.replace("b + a", "a + b  # in test")})
    assert r.report_and_complete().get("reopened") == "review"
    started = r.v1("review-start")
    assert started.get("incremental") is True, started
    r.spawn_reviewer(started["handouts"][0]["prompt"], _finding("MEDIUM", 2, "regressed"),
                     "inc-0")
    new = [row["canonical_id"] for row in _rows(r) if row["canonical_id"] != low]
    lines = _findings(r)
    assert lines[new[0]].startswith("fix it") and lines[low].startswith("residual"), lines
    # a finding of the first visit is that visit's residual, not this one's to fix
    old = r.v1("review-address", "--artifact", r.artifact("addr-old", {
        "pass": 2, "items": [{"finding_id": low, "hunk": "@@ -2 +2 @@ x"}]}))
    assert old.get("ok") is False and "earlier review" in old["error"], old
    out = _fix_and_address(r, new, "two")
    assert out.get("ok") is True, out


BROKEN = ('Two problems.\n{"finding_id":"CF-00000000","status":"RESOLVED","note":"x"}\n'
          + _finding("MEDIUM", 2, "lost in the first answer"))


def test_an_unreadable_answer_is_asked_again_before_review_completes(rig):
    r = rig()
    assert _r1(r, BROKEN) == []                  # nothing of it was recorded
    step = r.v1("status")["next"]
    assert "review-start" in step["command"] and "asks it once more" in step["why"], step
    again = r.v1("review-start")
    (handout,) = again["handouts"]
    text = Path(handout["path"]).read_text(encoding="utf-8")
    assert "COULD NOT BE READ" in text and "status row is only valid in R2" in text
    assert "INCREMENTAL REVIEW" not in text and "backend/core/mod.py" in text
    r.spawn_reviewer(handout["prompt"], _finding("MEDIUM", 2, "lost in the first answer"),
                     "r1-again")
    assert [row["severity"] for row in _rows(r)] == ["MEDIUM"]


def test_an_answer_unreadable_twice_is_not_asked_a_third_time(rig):
    r = rig()
    _r1(r, BROKEN)
    again = r.v1("review-start")
    r.spawn_reviewer(again["handouts"][0]["prompt"], BROKEN, "r1-again")
    step = r.v1("status")["next"]
    assert "stage-complete" in step["command"], step
    third = r.v1("review-start")
    assert not third.get("handouts"), third
