"""The change's whole dependency scope is tested ONCE per run (XG 2026-10-20).

What is tested:
- pipeline_test refuses verify / regression / recheck while BUILD, REVIEW or TEST is
  open, allows them at DELIVER, and refuses a third full-scope round there;
- a goal run is exempt (it keeps its own loop), and continuations never count as rounds;
- an identical re-run of a finished, non-cacheable result is NOT re-run: the result
  stands as reported (no silent second try for a lucky pass);
- a continuation re-targets only tests the deadline left unrun, never a failed one;
- DELIVER's push_ready bound needs a verify / regression report on the final code: none,
  or one older than the last edit, caps push_ready (a green tdd does not stand in);
- the cap and the landing check never lock each other: past the cap a fix is judged by
  ``--mode recheck`` (at most RECHECK_MAX_FIXES rechecks on changed code), and the
  overlay hands the landing check the tree the recheck ran on (push_ready false).
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


# ── the cap and the landing check never lock each other (XG 2026-10-20) ───────
_RUN = {"kind": "run", "mode": "regression", "auto_recheck_of": None, "cached": False}
_FIX = {"kind": "run", "mode": "recheck", "auto_recheck_of": None, "cached": False, "fix": True}
_SAME = {**_FIX, "fix": False}


def test_after_the_cap_a_fix_is_judged_by_recheck_not_another_full_round(tmp_path):
    """Two full rounds ran and the second was red: a third regression stays refused,
    and its message names the way forward, a recheck. The recheck runs (it is not a
    full-scope round), so the red can be cleared on the fixed code."""
    ev = _index(tmp_path, [_RUN, _RUN])
    out = pt._full_scope_refusal(ev, "regression", _state("deliver"))
    assert out and out["full_scope"]["refused"] and "--mode recheck" in out["error"]
    assert pt._full_scope_refusal(ev, "recheck", _state("deliver"), fix=True) is None


def test_a_manual_recheck_does_not_use_up_a_full_scope_round(tmp_path):
    ev = _index(tmp_path, [_RUN, _FIX])
    assert pt._full_scope_refusal(ev, "regression", _state("deliver")) is None


def test_fix_rechecks_are_bounded_and_the_stop_names_its_reason(tmp_path):
    rows = [_RUN, _RUN] + [_FIX] * pt.RECHECK_MAX_FIXES
    ev = _index(tmp_path, rows)
    out = pt._full_scope_refusal(ev, "recheck", _state("deliver"), fix=True)
    assert out and out["full_scope"]["refused"]
    assert out["full_scope"]["fix_rechecks"] == pt.RECHECK_MAX_FIXES
    assert "does not land" in out["error"] and "push_ready false" not in out["error"]
    # one fewer: still allowed
    ev = _index(tmp_path, rows[:-1])
    assert pt._full_scope_refusal(ev, "recheck", _state("deliver"), fix=True) is None


def test_a_recheck_on_unchanged_code_is_a_continuation_not_a_fix(tmp_path):
    """Same code as the report it rechecks: it only finishes what was left; it is
    never refused by the fix budget and does not use it up."""
    rows = [_RUN, _RUN] + [_FIX] * pt.RECHECK_MAX_FIXES + [_SAME] * 5
    ev = _index(tmp_path, rows)
    assert pt._full_scope_refusal(ev, "recheck", _state("deliver"), fix=False) is None
    ev = _index(tmp_path, [_RUN, _RUN] + [_SAME] * 9)
    assert pt._full_scope_refusal(ev, "recheck", _state("deliver"), fix=True) is None


def test_an_old_index_row_without_the_fix_flag_counts_as_a_fix(tmp_path):
    legacy = {k: v for k, v in _FIX.items() if k != "fix"}
    ev = _index(tmp_path, [_RUN, _RUN] + [legacy] * pt.RECHECK_MAX_FIXES)
    assert pt._full_scope_refusal(ev, "recheck", _state("deliver"), fix=True)


def test_rechecks_stay_deliver_only(tmp_path):
    out = pt._full_scope_refusal(_index(tmp_path, []), "recheck", _state("build"), fix=True)
    assert out and "DELIVER" in out["error"]


def test_is_fix_recheck_compares_the_code_with_the_report_it_rechecks():
    base = {"changed_files": {"backend/a.py": "d1", "backend/b.py": "e1"}}
    assert pt._is_fix_recheck(base, {"backend/a.py": "d1", "backend/b.py": "e1"}) is False
    assert pt._is_fix_recheck(base, {"backend/a.py": "d2", "backend/b.py": "e1"}) is True
    assert pt._is_fix_recheck(base, {"backend/a.py": "d1"}) is True          # b reverted
    assert pt._is_fix_recheck(base, {**base["changed_files"], "backend/c.py": "f"}) is True
    assert pt._is_fix_recheck({}, {"backend/a.py": "d1"}) is True             # unknown base


def test_overlay_on_changed_code_records_the_recheck_tree():
    """The landing check binds the change to the tree in the binding report's worktree
    block. A recheck on fixed code must hand it the tree it ran on, else the green
    overlay is refused as 'produced on a different tree' forever (the second lock)."""
    base = {"mode": "regression", "finished_at": "t0", "results": [], "failures": [],
            "runner_errors": [], "layers": {}, "changed_files": {"backend/a.py": "d1"},
            "worktree": {"swarmai": {"tree": "T1", "base": "B", "untracked": []}}}
    re_ev = {"results": [], "failures": [], "runner_errors": [],
             "changed_files": {"backend/a.py": "d2"},
             "worktree": {"swarmai": {"tree": "T2", "base": "B", "untracked": []}}}
    ev = pt._overlay_evidence(Path("."), base, re_ev, [])
    assert ev["worktree"]["swarmai"]["tree"] == "T2"
    assert ev["rechecked_from"]["fix"] is True
    same = {**re_ev, "changed_files": {"backend/a.py": "d1"},
            "worktree": {"swarmai": {"tree": "T1", "base": "B", "untracked": []}}}
    ev = pt._overlay_evidence(Path("."), base, same, [], auto=True)
    assert ev["worktree"]["swarmai"]["tree"] == "T1" and "fix" not in ev["rechecked_from"]


def test_landing_on_a_fix_recheck_lands_with_push_ready_false(tmp_path, monkeypatch):
    import pipeline_worktree as pw
    rep = {"mode": "regression", "rechecked_from": {"finished_at": "t0", "rechecked": 3,
                                                    "auto": False, "fix": True}}
    monkeypatch.setattr(pw, "_binding_report", lambda _d, _r: rep)
    soft = pw._fix_recheck_gap(tmp_path, "r")
    assert soft and "recheck" in soft[0] and "not re-run" in soft[0]
    rep["rechecked_from"].pop("fix")
    assert pw._fix_recheck_gap(tmp_path, "r") == []


def test_the_fix_mark_survives_a_later_recheck_on_unchanged_code(tmp_path, monkeypatch):
    """A fix recheck that hits the deadline is finished by a recheck on the same code.
    That overlay must keep rechecked_from.fix, or the landing loses its push_ready
    reason while most of the scope still ran only on the pre-fix code."""
    import pipeline_worktree as pw
    base = {"mode": "regression", "finished_at": "t0", "results": [], "failures": [],
            "runner_errors": [], "layers": {}, "changed_files": {"backend/a.py": "d1"},
            "worktree": {"swarmai": {"tree": "T1", "base": "B", "untracked": []}}}
    fixed = {"results": [], "failures": [], "runner_errors": [],
             "changed_files": {"backend/a.py": "d2"},
             "worktree": {"swarmai": {"tree": "T2", "base": "B", "untracked": []}}}
    first = pt._overlay_evidence(Path("."), base, fixed, [])
    finish = pt._overlay_evidence(Path("."), first, dict(fixed), [])      # same code
    assert finish["rechecked_from"].get("fix") is True
    monkeypatch.setattr(pw, "_binding_report", lambda _d, _r: finish)
    assert pw._fix_recheck_gap(tmp_path, "r")
