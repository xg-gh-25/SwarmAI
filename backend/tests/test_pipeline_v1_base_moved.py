"""Main moving does not use up the run's own landing attempts (design 2026-10-26-
pipeline-review-closure-and-visibility §5.2 point 2, step 11).

Before: the third failed LG-1 / LG-4 made the run final_stale (not landed), whether the
run was at fault or main had moved under it three times while the TEST report was fresh.

What is tested:
- lifecycle: a ``base_moved:`` LG-1 failure counts apart (cap 10 → base_kept_moving,
  a Quality BLOCK reason mapped to the cap ``base_moved``); other LG-1 / LG-4 failures
  still make final_stale / land_conflict at 3, not counting the base-moved ones;
- through real runs: main moves while the TEST report is fresh → the detail starts with
  ``base_moved:`` and names the last TEST command and the run files main also changed;
  three such moves, then the run lands on the fourth attempt; a run that changed its own
  code after TEST gets no prefix (its report was stale before the sync).
"""
from __future__ import annotations

import json

import pytest

from core.run_protocol import lifecycle
from tests.pipeline_v1_rig import SWARM, Rig

pytestmark = pytest.mark.timeout(600)


def _run() -> dict:
    return {"open_attempt": None, "pending_landing": None, "reason": None,
            "base_moved_failures": 0, "attempt_base": {}, "stages": {}, "stage": "deliver"}


def _fail_land(run: dict, number: int, details: dict, causes: dict | None = None) -> None:
    run["open_attempt"] = {"stage": "land", "attempt": number, "details": details,
                           "causes": causes or {}}
    lifecycle._failed_stage(run, {"event_id": f"e{number}"}, set(details))


# what the fold holds for a detail that was moved into a blob (str of the event's value)
BLOB = "{'blob': '" + "0" * 64 + "'}"


def test_a_long_base_moved_detail_counts_apart_by_its_recorded_cause():
    """Bugfix: the base-moved note names every TEST command, so in a real run it passes
    the inline limit and is stored as a blob; the fold read the prefix out of the blob
    reference, found none, and ended the run final_stale at the third move."""
    run = _run()
    for n in range(1, 6):
        _fail_land(run, n, {"LG-1": BLOB, "LG-4": BLOB},
                   {"LG-1": "base_moved", "LG-4": "not_evaluated"})
    assert run["pending_landing"] is None and run["base_moved_failures"] == 5, run


def test_a_long_merge_conflict_detail_ends_land_conflict_by_its_recorded_cause():
    run = _run()
    for n in (1, 2, 3):
        _fail_land(run, n, {"LG-1": BLOB, "LG-4": BLOB}, {"LG-4": "merge_conflict"})
    assert run["pending_landing"]["reason"] == "land_conflict", run


def test_an_older_event_without_a_cause_is_classified_by_its_inline_prefix():
    run = _run()
    _fail_land(run, 1, {"LG-1": "base_moved: main moved", "LG-4": "not evaluated: x"})
    assert run["base_moved_failures"] == 1 and run["pending_landing"] is None, run


def test_base_moved_failures_count_apart_up_to_their_cap():
    run = _run()
    for n in range(1, lifecycle.BASE_MOVED_CAP):
        _fail_land(run, n, {"LG-1": "base_moved: main moved", "LG-4": "not evaluated: x"})
        assert run["pending_landing"] is None, (n, run)
    _fail_land(run, lifecycle.BASE_MOVED_CAP, {"LG-1": "base_moved: main moved"})
    assert run["pending_landing"]["reason"] == "base_kept_moving"
    assert "base_kept_moving" in lifecycle.QUALITY_BLOCK_REASONS
    assert lifecycle.REASON_CAPS["base_kept_moving"] == "base_moved" in lifecycle.CAPS


def test_own_failures_still_end_at_three_not_counting_base_moves():
    run = _run()
    for n in (1, 2):
        _fail_land(run, n, {"LG-1": "base_moved: main moved"})
    _fail_land(run, 3, {"LG-1": "no TEST report on the final tree"})
    _fail_land(run, 4, {"LG-4": "merge_conflict: x"})
    assert run["pending_landing"] is None, run
    _fail_land(run, 5, {"LG-1": "no TEST report on the final tree"})
    assert run["pending_landing"]["reason"] == "final_stale"


BIG = "".join(f"value_{i} = {i}\n" for i in range(40))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct",
            extra={"backend/core/big.py": BIG})
    r.CHANGE = {**Rig.CHANGE, "backend/core/big.py": BIG.replace("value_1 = 1", "value_1 = 10")}
    yield r
    if r.run_id:
        assert r.v1("status").get("ok") is True, "the event log no longer folds"
    r.close()


def _land(r: Rig) -> dict:
    return r.v1("land", "--message", "fix: swap the operands" + SWARM)


def _lg1(out: dict) -> str:
    return str((out.get("failed") or {}).get("LG-1") or "")


def test_main_moving_three_times_then_the_run_lands(rig):
    r = rig
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
    for i in range(3):
        r.commit_on_main({f"other{i}.py": f"x = {i}\n", "backend/core/big.py":
                          BIG.replace("value_38 = 38", "value_38 = 380")}
                         if i == 0 else {f"other{i}.py": f"x = {i}\n"})
        out = _land(r)
        assert out.get("ok") is False and _lg1(out).startswith("base_moved:"), out
        assert "python -m pytest backend/tests/test_x.py" in _lg1(out), out
        if i == 0:
            assert "backend/core/big.py" in _lg1(out), out
        assert r.state()["status"] == "active" and r.state().get("pending_landing") is None
        assert "report-tests" in json.dumps(r.v1("status")["next"])
        r.v1("report-tests", "--artifact", r.artifact(f"t{i}", r.report()))
    out = _land(r)
    assert out.get("ok") is True or r.state().get("landing") == "landed", out


def test_a_report_stale_before_the_sync_gets_no_prefix(rig):
    r = rig
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
    r.edit({"backend/core/other.py": "def other():\n    return 2\n"})   # after TEST
    r.commit_on_main({"elsewhere.py": "y = 1\n"})
    out = _land(r)
    # an edit no review saw: back to REVIEW (design 2026-10-31 §3 change 3), never a
    # base_moved failure
    lg1 = [e["payload"] for e in r.events() if e["kind"] == "check"
           and e["payload"].get("check_id") == "LG-1"][-1]
    assert out.get("ok") is False and out.get("reopened") == "review", out
    assert lg1.get("cause") == "review_reopened", lg1


def test_a_failing_report_before_the_sync_gets_no_prefix(rig):
    """Review fix: a TEST report with a failed command is not fresh evidence."""
    r = rig
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))
    r.v1("report-tests", "--artifact", r.artifact("red", r.report("fail")))
    r.commit_on_main({"elsewhere.py": "y = 1\n"})
    out = _land(r)
    assert out.get("ok") is False and _lg1(out) and not _lg1(out).startswith("base_moved"), out


def test_main_moving_three_times_with_long_test_commands_still_lands(rig):
    """The same three moves with a TEST report whose commands make the base-moved note
    longer than an event keeps inline (every real run's): the run still lands."""
    r = rig
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"delivered": [], "decisions": [], "corrections": [], "practices": [], "gaps": []}))

    def long_report() -> dict:
        report = r.report()
        report["reports"][0]["commands"][0]["command"] = (
            "python -m pytest backend/tests/test_x.py " + " ".join(
                f"backend/tests/test_x.py::test_case_{i}" for i in range(40)))
        return report

    r.v1("report-tests", "--artifact", r.artifact("long0", long_report()))
    for i in range(3):
        r.commit_on_main({f"other{i}.py": f"x = {i}\n"})
        out = _land(r)
        assert out.get("ok") is False and _lg1(out).startswith("base_moved:"), out
        assert len(_lg1(out).encode()) > 1024, len(_lg1(out))
        assert r.state()["status"] == "active" and r.state().get("pending_landing") is None
        r.v1("report-tests", "--artifact", r.artifact(f"long{i + 1}", long_report()))
    out = _land(r)
    assert out.get("ok") is True or r.state().get("landing") == "landed", out
