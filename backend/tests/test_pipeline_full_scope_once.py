"""The change's whole dependency scope runs a bounded number of times per run.

What is tested:
- the bounds are the run contract's ``regression_round_cap`` and ``recheck_cap``
  (conformance F17); a contract with unreadable caps refuses every full-scope call, a
  legacy run (no contract) is left to the circuit breaker; continuations and cache
  hits never count as rounds;
- an identical re-run of a finished, non-cacheable result is NOT re-run: the result
  stands as reported (no silent second try for a lucky pass);
- a continuation re-targets only tests the deadline left unrun, never a failed one;
- DELIVER's push_ready bound needs a verify / regression report on the final code: none,
  or one older than the last edit, caps push_ready (a green tdd does not stand in);
- the cap and the landing check never lock each other: past the cap a fix is judged by
  ``--mode recheck`` (at most ``recheck_cap`` rechecks on changed code), and the
  overlay hands the landing check the tree the recheck ran on (push_ready false).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND / "scripts"))
import pipeline_test as pt  # noqa: E402


CONTRACT = {"regression_round_cap": 2, "recheck_cap": 3}
FIXES = CONTRACT["recheck_cap"]


def _index(tmp_path: Path, rows: list[dict]) -> Path:
    d = tmp_path / "tests"
    d.mkdir(parents=True, exist_ok=True)
    (d / pt.RUN_INDEX).write_text(json.dumps(rows), encoding="utf-8")
    return tmp_path


def test_only_a_v1_contract_caps_full_scope_calls(tmp_path):
    for mode in ("verify", "regression", "recheck"):
        assert pt._full_scope_refusal(_index(tmp_path, []), mode, None) is None   # legacy
        out = pt._full_scope_refusal(_index(tmp_path, []), mode, {"regression_round_cap": "2"})
        assert out and out["full_scope"]["no_caps"] is True
    for mode in ("tdd", "prove", "probe", "mutation"):
        assert pt._full_scope_refusal(_index(tmp_path, []), mode, CONTRACT) is None


def test_the_operator_override_is_exempt(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_GATE_FORCE", "full_scope")
    assert pt._full_scope_refusal(_index(tmp_path, []), "verify", None) is None


def test_the_caps_are_the_contracts(tmp_path):
    run = {"kind": "run", "mode": "regression", "auto_recheck_of": None, "cached": False}
    ev = _index(tmp_path, [run])
    assert pt._full_scope_refusal(ev, "regression", {**CONTRACT, "regression_round_cap": 1})
    assert pt._full_scope_refusal(ev, "regression", CONTRACT) is None


def test_a_third_round_is_refused_continuations_and_cache_hits_are_not_rounds(tmp_path):
    run = {"kind": "run", "mode": "regression", "auto_recheck_of": None, "cached": False}
    cont = {"kind": "run", "mode": "recheck", "auto_recheck_of": "regression", "cached": False}
    hit = {**run, "cached": True}
    ev = _index(tmp_path, [run, cont, cont, hit, run])
    assert pt._full_scope_refusal(ev, "regression", CONTRACT)["full_scope"]["rounds"] == 2
    ev = _index(tmp_path, [run, cont, cont, hit])
    assert pt._full_scope_refusal(ev, "regression", CONTRACT) is None


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


# ── the cap and the landing check never lock each other (XG 2026-10-20) ───────
_RUN = {"kind": "run", "mode": "regression", "auto_recheck_of": None, "cached": False}
_FIX = {"kind": "run", "mode": "recheck", "auto_recheck_of": None, "cached": False, "fix": True}
_SAME = {**_FIX, "fix": False}


def test_after_the_cap_a_fix_is_judged_by_recheck_not_another_full_round(tmp_path):
    """Two full rounds ran and the second was red: a third regression stays refused,
    and its message names the way forward, a recheck. The recheck runs (it is not a
    full-scope round), so the red can be cleared on the fixed code."""
    ev = _index(tmp_path, [_RUN, _RUN])
    out = pt._full_scope_refusal(ev, "regression", CONTRACT)
    assert out and out["full_scope"]["refused"] and "--mode recheck" in out["error"]
    assert pt._full_scope_refusal(ev, "recheck", CONTRACT, fix=True) is None


def test_a_manual_recheck_does_not_use_up_a_full_scope_round(tmp_path):
    ev = _index(tmp_path, [_RUN, _FIX])
    assert pt._full_scope_refusal(ev, "regression", CONTRACT) is None


def test_fix_rechecks_are_bounded_and_the_stop_names_its_reason(tmp_path):
    rows = [_RUN, _RUN] + [_FIX] * FIXES
    ev = _index(tmp_path, rows)
    out = pt._full_scope_refusal(ev, "recheck", CONTRACT, fix=True)
    assert out and out["full_scope"]["refused"]
    assert out["full_scope"]["fix_rechecks"] == FIXES
    assert "does not land" in out["error"] and "push_ready false" not in out["error"]
    # one fewer: still allowed
    ev = _index(tmp_path, rows[:-1])
    assert pt._full_scope_refusal(ev, "recheck", CONTRACT, fix=True) is None


def test_a_recheck_on_unchanged_code_is_a_continuation_not_a_fix(tmp_path):
    """Same code as the report it rechecks: it only finishes what was left; it is
    never refused by the fix budget and does not use it up."""
    rows = [_RUN, _RUN] + [_FIX] * FIXES + [_SAME] * 5
    ev = _index(tmp_path, rows)
    assert pt._full_scope_refusal(ev, "recheck", CONTRACT, fix=False) is None
    ev = _index(tmp_path, [_RUN, _RUN] + [_SAME] * 9)
    assert pt._full_scope_refusal(ev, "recheck", CONTRACT, fix=True) is None


def test_an_old_index_row_without_the_fix_flag_counts_as_a_fix(tmp_path):
    legacy = {k: v for k, v in _FIX.items() if k != "fix"}
    ev = _index(tmp_path, [_RUN, _RUN] + [legacy] * FIXES)
    assert pt._full_scope_refusal(ev, "recheck", CONTRACT, fix=True)


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
