"""Break-test proofs: ``pipeline_test.py --mode snapshot`` / ``--mode prove``
(Knowledge/Designs/2026-10-19-pipeline-deterministic-convergence.md §4; run_600db8ac
AC1-AC4).

Methodology: a temp main repo + a real run worktree; the round snapshot is taken on
the buggy code, then the fix and the break tests are written, and ``run_prove`` runs
real nested pytest processes on the overlaid snapshot and on the current worktree.
Invariants: snapshotting never touches the worktree's files, index or branch (I2); a
proof is valid only for two assertion failures on the snapshot plus a pass now (I1);
an ImportError / TypeError / skip / missing nodeid / flaky / green-on-snapshot test is
invalid with a named reason (AC3); old-side outcomes are cached by the overlay tree
and a deadline between the two old runs loses nothing (AC4, F2).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core import run_worktree as rw
from scripts import pipeline_test as pt

RID = "run_prv1"
MOD = "prove_target_mod"
BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n\n\ndef added_by_fix():\n    return 1\n"

BREAK_TESTS = f'''import os
import pytest
import {MOD} as m


def test_assert():
    assert m.add(2, 2) == 4


def test_pytest_fail():
    if m.add(1, 1) != 2:
        pytest.fail("add is wrong")


def test_did_not_raise():
    with pytest.raises(ZeroDivisionError):
        1 / (m.add(1, -1) if m.add(2, 2) == 4 else 1)


def test_import_error():
    from {MOD} import added_by_fix
    assert added_by_fix() == 1


def test_attr_error():
    assert m.added_by_fix() == 1


def test_green_on_old():
    assert m.add(0, 0) == 0


def test_skipped():
    pytest.skip("not here")


def test_flaky():
    p = os.environ["PT_PROVE_FLAKY"]
    n = int(open(p).read()) if os.path.exists(p) else 0
    open(p, "w").write(str(n + 1))
    assert n >= 1
'''


def _g(cwd, *a):
    p = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


@pytest.fixture
def run(tmp_path, monkeypatch):
    tmp = Path(os.path.realpath(tmp_path))
    main = tmp / "swarmai"
    (main / "backend" / "tests").mkdir(parents=True)
    _g(main, "init", "-q", "-b", "main")
    for k, v in (("user.name", "T"), ("user.email", "t@e.com"), ("core.hooksPath", str(tmp))):
        _g(main, "config", k, v)
    (main / "backend" / f"{MOD}.py").write_text(BUGGY)
    (main / "backend" / "tests" / "test_existing.py").write_text("def test_ok():\n    pass\n")
    _g(main, "add", "-A")
    _g(main, "commit", "-q", "-m", "i")
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp / "wt"))
    monkeypatch.setenv("SWARM_TEST_REPO_ROOT", str(main))
    monkeypatch.setenv("SWARM_TEST_PYTHON", sys.executable)
    monkeypatch.setenv("PT_PROVE_FLAKY", str(tmp / "flaky.count"))
    monkeypatch.setattr(pt, "_WORKTREE", {})
    monkeypatch.delenv("PIPELINE_TEST_STRIP_ROOT", raising=False)
    e = rw.create(RID, "swarmai", main)
    wt = rw.wt_path(RID, "swarmai")
    repo, err = pt._target_repo(RID, {"worktree_mode": "isolated", "worktrees": [e]})
    assert err is None and repo == wt
    ev_root = tmp / "evidence"
    cache = tmp / "cache"
    yield {"main": main, "wt": wt, "ev": ev_root, "cache": cache, "tmp": tmp}
    os.environ.pop("PIPELINE_TEST_STRIP_ROOT", None)


def _ctx(run, deadline=90.0):
    c = pt._Ctx(run["wt"], run["cache"], deadline)
    c.workers = 2
    return c


def _ns(rnd="R1", files=()):
    return argparse.Namespace(run_id=RID, round=rnd, files=list(files), project="SwarmAI")


def _state(wt):
    idx = Path(_g(wt, "rev-parse", "--git-path", "index"))
    idx = idx if idx.is_absolute() else wt / idx
    return (_g(wt, "rev-parse", "HEAD"), _g(wt, "symbolic-ref", "HEAD"),
            _g(wt, "status", "--porcelain"), hashlib.sha256(idx.read_bytes()).hexdigest())


def _fix_and_write_tests(run):
    (run["wt"] / "backend" / f"{MOD}.py").write_text(FIXED)
    (run["wt"] / "backend" / "tests" / "test_brk.py").write_text(BREAK_TESTS)


def _nid(name):
    return f"backend/tests/test_brk.py::{name}"


# ── AC1 / I2: snapshot ───────────────────────────────────────────────────────
def test_snapshot_freezes_the_change_set_and_touches_nothing(run):
    (run["wt"] / "backend" / f"{MOD}.py").write_text(BUGGY + "# edited\n")
    before = _state(run["wt"])
    out, code = pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    assert code == 0 and not out.get("error")
    assert _state(run["wt"]) == before
    assert _g(run["wt"], "show", f"{out['sha']}:backend/{MOD}.py") == (BUGGY + "# edited").strip()
    assert _g(run["wt"], "rev-parse", f"{out['sha']}^") == out["base"] == before[0]
    assert _g(run["wt"], "rev-parse", rw.snapshot_ref(RID, "R1")) == out["sha"]
    rec = json.loads((run["ev"] / "proofs" / "snapshots.json").read_text())["R1"]
    assert {"sha", "tree", "base", "at"} <= set(rec)


def test_a_round_is_snapshotted_once(run):
    first, _ = pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    (run["wt"] / "backend" / f"{MOD}.py").write_text(FIXED)
    again, code = pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    assert code == 0 and again["already"] is True and again["sha"] == first["sha"]


def test_overlay_is_content_addressed_and_leaves_the_worktree(run):
    snap, _ = pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    _fix_and_write_tests(run)
    before = _state(run["wt"])
    a = rw.overlay_commit(run["wt"], snap["sha"], ["backend/tests/test_brk.py"])
    b = rw.overlay_commit(run["wt"], snap["sha"], ["backend/tests/test_brk.py"])
    assert a["sha"] == b["sha"] != snap["sha"]
    assert _g(run["wt"], "show", f"{a['sha']}:backend/{MOD}.py") == BUGGY.strip()  # code: snapshot
    assert "test_flaky" in _g(run["wt"], "show", f"{a['sha']}:backend/tests/test_brk.py")
    assert _state(run["wt"]) == before


def test_overlay_refuses_a_path_outside_the_worktree(run):
    snap, _ = pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    with pytest.raises(rw.WorktreeError):
        rw.overlay_commit(run["wt"], snap["sha"], ["../escape.py"])


# ── AC2 / AC3: prove verdicts ────────────────────────────────────────────────
def test_prove_classifies_each_break_test(run):
    pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    _fix_and_write_tests(run)
    names = ["test_assert", "test_pytest_fail", "test_did_not_raise", "test_import_error",
             "test_attr_error", "test_green_on_old", "test_skipped", "test_flaky",
             "test_does_not_exist"]
    out, code = pt.run_prove(_ctx(run), _ns("R1", [_nid(n) for n in names]), run["ev"])
    assert not out.get("error"), out
    rows = {r["nodeid"].split("::")[1]: r for r in out["proofs"]}
    for n in ("test_assert", "test_pytest_fail", "test_did_not_raise"):
        assert rows[n]["verdict"] == "valid" and rows[n]["old"] == ["assert_fail"] * 2, rows[n]
        assert rows[n]["new"] == "pass"
    assert rows["test_import_error"]["code"] == "old_error"
    assert "ImportError" in rows["test_import_error"]["reason"]
    assert rows["test_attr_error"]["code"] == "old_error"
    assert "AttributeError" in rows["test_attr_error"]["reason"]
    assert rows["test_green_on_old"]["code"] == "old_pass"
    assert rows["test_skipped"]["code"] == "old_skip"
    assert rows["test_flaky"]["code"] == "old_flaky"
    assert rows["test_flaky"]["old"] == ["assert_fail", "pass"]
    assert rows["test_does_not_exist"]["code"] == "old_error"
    assert code == 1 and "continue" not in out


def test_new_side_red_is_invalid(run):
    pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    (run["wt"] / "backend" / "tests" / "test_brk.py").write_text(BREAK_TESTS)  # no fix
    out, _ = pt.run_prove(_ctx(run), _ns("R1", [_nid("test_assert")]), run["ev"])
    assert out["proofs"][0]["code"] == "new_not_pass" and out["proofs"][0]["new"] == "assert_fail"


def test_prove_needs_the_rounds_snapshot(run):
    _fix_and_write_tests(run)
    out, code = pt.run_prove(_ctx(run), _ns("R2", [_nid("test_assert")]), run["ev"])
    assert code == 2 and "--mode snapshot --round R2" in out["error"]


def test_prove_refuses_a_whole_file(run):
    pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    _fix_and_write_tests(run)
    out, code = pt.run_prove(_ctx(run), _ns("R1", ["backend/tests/test_brk.py"]), run["ev"])
    assert code == 2 and "nodeid" in out["error"]


# ── AC4: cache + continuation ────────────────────────────────────────────────
def _spy(monkeypatch, ctl=None):
    """Record each runner batch's tag; ``ctl["drop_old"] = n`` makes the n-th old-side
    batch come back empty, as when the deadline cuts it."""
    calls = []
    ctl = {} if ctl is None else ctl
    real = pt.run_pytest_targets

    def wrapped(ctx, targets, deadline_at, tag, root=None, requeue=False):
        calls.append(tag)
        if tag == "old" and ctl.get("drop_old") == sum(t == "old" for t in calls):
            return []  # this batch hit the deadline: nothing finished
        return real(ctx, targets, deadline_at, tag, root=root, requeue=requeue)
    monkeypatch.setattr(pt, "run_pytest_targets", wrapped)
    return calls


def test_second_call_reruns_only_the_new_side(run, monkeypatch):
    pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    _fix_and_write_tests(run)
    first, _ = pt.run_prove(_ctx(run), _ns("R1", [_nid("test_assert")]), run["ev"])
    calls = _spy(monkeypatch)
    again, code = pt.run_prove(_ctx(run), _ns("R1", [_nid("test_assert")]), run["ev"])
    assert calls == ["new"] and code == 0
    assert again["proofs"][0]["verdict"] == first["proofs"][0]["verdict"] == "valid"


def test_a_changed_break_test_is_rerun_on_the_old_side(run, monkeypatch):
    pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    _fix_and_write_tests(run)
    pt.run_prove(_ctx(run), _ns("R1", [_nid("test_assert")]), run["ev"])
    p = run["wt"] / "backend" / "tests" / "test_brk.py"
    p.write_text(p.read_text() + "\n# touched\n")
    calls = _spy(monkeypatch)
    pt.run_prove(_ctx(run), _ns("R1", [_nid("test_assert")]), run["ev"])
    assert calls.count("old") == 2


def test_deadline_between_old_runs_loses_nothing(run, monkeypatch):
    pt.run_snapshot(_ctx(run), _ns("R1"), run["ev"])
    _fix_and_write_tests(run)
    ctl = {"drop_old": 2}
    calls = _spy(monkeypatch, ctl)
    out, code = pt.run_prove(_ctx(run), _ns("R1", [_nid("test_assert")]), run["ev"])
    assert out.get("continue") is True and out["proofs"][0]["verdict"] == "pending"
    assert out["proofs"][0]["old"] == ["assert_fail"] and code == 1
    calls.clear()
    ctl.clear()
    out, code = pt.run_prove(_ctx(run), _ns("R1", [_nid("test_assert")]), run["ev"])
    assert calls == ["old", "new"] and out["proofs"][0]["verdict"] == "valid" and code == 0


# ── AC3: the classifier on runner results the nested runs cannot produce cheaply ──
@pytest.mark.parametrize("res,label", [
    ({"outcome": "failed", "exc_kind": "assert", "exc": "builtins.AssertionError"}, "assert_fail"),
    ({"outcome": "failed", "exc_kind": "fail", "exc": "Failed"}, "assert_fail"),
    ({"outcome": "failed", "exc_kind": "error", "exc": "builtins.TypeError"}, "error"),
    ({"outcome": "failed"}, "error"),  # no exception kind recorded: never a proof
    ({"outcome": "error", "summary": ["fixture 'x' not found"]}, "error"),
    ({"outcome": "timeout", "summary": ["+ Timeout +"]}, "timeout"),
    ({"outcome": "timeout", "deadline": True, "notrun": True}, None),
    ({"outcome": "timeout", "deadline": True}, None),
    ({"outcome": "skipped"}, "skip"),
    ({"outcome": "passed"}, "pass"),
    (None, None),
])
def test_proof_outcome_labels(res, label):
    assert pt.proof_outcome(res)[0] == label


def test_proof_modes_need_an_isolated_run(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, "_WORKTREE", {})
    ws = Path(os.path.realpath(tmp_path)) / "ws"
    rd = ws / "Projects" / "P" / ".artifacts" / "runs" / RID
    rd.mkdir(parents=True)
    (rd / "run.json").write_text(json.dumps({"worktree_mode": "shared"}))
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    monkeypatch.setenv("SWARM_TEST_CACHE_DIR", str(tmp_path / "c"))
    ns = pt._parser().parse_args(["--project", "P", "--run-id", RID, "--mode", "snapshot",
                                  "--round", "R1"])
    out, code = pt.execute(ns)
    assert code == 2 and "isolated" in out["error"]
    ns = pt._parser().parse_args(["--project", "P", "--run-id", RID, "--mode", "prove"])
    out, code = pt.execute(ns)
    assert code == 2 and "--round" in out["error"]
