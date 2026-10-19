"""The change's whole dependency scope is tested ONCE per run (XG 2026-10-20).

What is tested:
- pipeline_test refuses verify / regression / recheck while BUILD, REVIEW or TEST is
  open, allows them at DELIVER, and refuses a third full-scope round there;
- a goal run is exempt (it keeps its own loop), and continuations never count as rounds;
- an identical re-run of a finished, non-cacheable result is NOT re-run: the result
  stands as reported (no silent second try for a lucky pass);
- a continuation re-targets only tests the deadline left unrun, never a failed one;
- DELIVER's push_ready bound needs a verify / regression report on the final code: none,
  or one older than the last edit, caps push_ready (a green tdd does not stand in).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND / "scripts"))
import pipeline_test as pt  # noqa: E402
import artifact_cli as cli  # noqa: E402


def _state(stage: str | None, profile: str = "full") -> dict:
    timing = {"plan": {"started_at": "2026-10-20T01:00:00+00:00",
                       "completed_at": "2026-10-20T01:05:00+00:00"}}
    if stage:
        timing[stage] = {"started_at": "2026-10-20T01:10:00+00:00"}
    return {"profile": profile, "stage_timing": timing}


def _index(tmp_path: Path, rows: list[dict]) -> Path:
    d = tmp_path / "tests"
    d.mkdir(parents=True, exist_ok=True)
    (d / pt.RUN_INDEX).write_text(json.dumps(rows), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("stage", ["build", "review", "test"])
@pytest.mark.parametrize("mode", ["verify", "regression", "recheck"])
def test_full_scope_is_refused_before_deliver(tmp_path, stage, mode):
    out = pt._full_scope_refusal(_index(tmp_path, []), mode, _state(stage))
    assert out and out["full_scope"]["refused"] and "DELIVER" in out["error"]
    assert "--mode tdd" in out["error"]


def test_full_scope_runs_at_deliver_tdd_and_prove_anywhere(tmp_path):
    ev = _index(tmp_path, [])
    assert pt._full_scope_refusal(ev, "regression", _state("deliver")) is None
    for mode in ("tdd", "prove", "probe", "mutation"):
        assert pt._full_scope_refusal(ev, mode, _state("build")) is None


def test_goal_runs_no_open_stage_and_operator_override_are_exempt(tmp_path, monkeypatch):
    ev = _index(tmp_path, [])
    assert pt._full_scope_refusal(ev, "verify", _state(None)) is None
    assert pt._full_scope_refusal(ev, "verify", _state("goal_cycle", "goal")) is None
    monkeypatch.setenv("SWARM_GATE_FORCE", "full_scope")
    assert pt._full_scope_refusal(ev, "verify", _state("build")) is None


def test_a_third_round_is_refused_continuations_and_cache_hits_are_not_rounds(tmp_path):
    run = {"kind": "run", "mode": "regression", "auto_recheck_of": None, "cached": False}
    cont = {"kind": "run", "mode": "recheck", "auto_recheck_of": "regression", "cached": False}
    hit = {**run, "cached": True}
    ev = _index(tmp_path, [run, cont, cont, hit, run])
    assert pt._full_scope_refusal(ev, "regression", _state("deliver"))["full_scope"]["rounds"] == 2
    ev = _index(tmp_path, [run, cont, cont, hit])
    assert pt._full_scope_refusal(ev, "regression", _state("deliver")) is None


def test_continuation_retargets_only_unrun_tests(tmp_path):
    """A failed test is not re-run by a continuation; an unrun one is."""
    prev = {"mode": "regression", "finished_at": "t", "changed_files": {},
            "results": [], "failures": [
                {"nodeid": "backend/tests/test_a.py::test_red", "classification": "regression"},
                {"nodeid": "backend/tests/test_a.py::test_unrun", "classification": "timeout",
                 "deadline": True, "notrun": True}]}
    assert [f["nodeid"] for f in pt._unfinished_failures(prev)] == [
        "backend/tests/test_a.py::test_unrun"]


# ── DELIVER push_ready bound: the one full-scope report on the final code ──────
def _bound(tmp_path, monkeypatch, reports: list[dict], touched: list[str]):
    import pipeline_validator as pv
    monkeypatch.setattr(pv, "load_test_evidence", lambda _d: reports)
    monkeypatch.setattr(pv, "push_ready_upper_bound", lambda *a, **k: (True, []))
    monkeypatch.setattr(cli, "_stage_artifact", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_unverified_gates", lambda _s: [])
    state = {"files_touched": touched, "stages": []}
    return cli._deliver_push_ready_bound(project="P", run_id="r", run_dir=tmp_path,
                                         run_state=state, profile="full", artifact={},
                                         exit_result={"met": True})


def test_push_ready_needs_a_full_scope_report_a_green_tdd_does_not_count(tmp_path, monkeypatch):
    src = tmp_path / "m.py"
    src.write_text("x = 1\n")
    tdd = {"mode": "tdd", "run_id": "r", "counts": {"passed": 3}, "failures": []}
    bound, reasons, _ = _bound(tmp_path, monkeypatch, [tdd], [str(src)])
    assert bound is False and any("no full-scope run" in r for r in reasons)


def test_push_ready_rejects_a_full_scope_report_older_than_the_last_edit(tmp_path, monkeypatch):
    import pipeline_validator as pv
    src = tmp_path / "m.py"
    src.write_text("x = 1\n")
    reg = {"mode": "regression", "run_id": "r", "counts": {"passed": 3}, "failures": []}
    monkeypatch.setattr(pv, "evidence_freshness",
                        lambda ev, ft, roots=(): {"status": "stale", "stale_files": [str(src)]})
    bound, reasons, _ = _bound(tmp_path, monkeypatch, [reg], [str(src)])
    assert bound is False and any("predates later edits" in r for r in reasons)
    monkeypatch.setattr(pv, "evidence_freshness",
                        lambda ev, ft, roots=(): {"status": "fresh", "stale_files": []})
    bound, reasons, _ = _bound(tmp_path, monkeypatch, [reg], [str(src)])
    assert bound is True, reasons


def test_docs_only_change_needs_no_full_scope_run(tmp_path, monkeypatch):
    doc = tmp_path / "README.md"
    doc.write_text("# x\n")
    bound, reasons, _ = _bound(tmp_path, monkeypatch, [], [str(doc)])
    assert bound is True, reasons


def test_whole_scope_criteria_are_judged_at_deliver_only():
    import pipeline_validator as pv
    for wt in ("feature", "refactor", "bugfix", None):
        assert not any(str(c.get("ref", "")).startswith("mode:")
                       for c in pv.default_exit_criteria(wt, ["AC1"])), wt
