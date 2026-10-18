"""Cross-file order dependence in ``scripts/pipeline_test.py`` classification.

A failure caused by module state leaked from ANOTHER file earlier in the same shard
passes when its own file is re-run alone. ``classify_failures`` then re-runs the
shard PREFIX (every shard arg up to and including the test's, original order).

Methodology: a throwaway git repo (``rig`` from test_pipeline_test) with one worker
(``_worker_count`` patched to 1) and five tdd files, so ``_shards`` packs
``test_a_leak, test_a_noop, test_b_dep`` into one shard in that order. At HEAD
``test_a_leak`` leaves ``core.s.STATE`` clean; the working-tree edit makes it leak.
Real nested pytest shards and a real HEAD worktree run end to end.

Properties:
- leaked state from another file → ``regression`` with ``order_dependent: true`` and
  the bisected culprit file (never ``flaky``)
- a genuinely flaky test in the same shard (passes in the prefix re-run) stays flaky
- no time left for the prefix re-run → ``unknown`` (never flaky), exit 1
- unit: ``_prefix_plans`` cuts at the furthest failing arg; a test that is its
  shard's first file needs no prefix (the file re-run already covered it)
- stale bytecode: a .pyc compiled in the same second as a changed source is dropped
  before the main run (else a same-size edit is a false green)
"""
from __future__ import annotations

import os
import time

from scripts import pipeline_test as pt
from tests.test_pipeline_test import (  # noqa: F401 — rig is a fixture
    RUN_ID, _evidence, _run, _write, rig)

STATE = "STATE = {'n': 0}\n"
LEAK_HEAD = ("from core import s\n\n\ndef test_leak():\n    s.STATE['n'] += 1\n"
             "    s.STATE['n'] -= 1\n")
LEAK_NOW = "from core import s\n\n\ndef test_leak():\n    s.STATE['n'] = 7\n"
NOOP = "def test_noop():\n    assert True\n"
DEP = '''from pathlib import Path

from core import s


def test_dep():
    assert s.STATE["n"] == 0, "state leaked from another file"


def test_flaky():
    marker = Path(__file__).with_name(".flaky_marker")
    if not marker.exists():
        marker.write_text("1")
        assert False, "fails on the first run only"
'''
DEP_HEAD = DEP.split("def test_flaky():")[0] + "def test_flaky():\n    pass\n"
FILES = ["backend/tests/test_a_leak.py", "backend/tests/test_a_noop.py",
         "backend/tests/test_b_dep.py", "backend/tests/test_unrelated.py",
         "backend/tests/test_x.py"]


def _setup(rig, monkeypatch):
    repo = rig({"backend/core/s.py": STATE, "backend/tests/test_a_leak.py": LEAK_HEAD,
                "backend/tests/test_a_noop.py": NOOP, "backend/tests/test_b_dep.py": DEP_HEAD})
    _write(repo, {"backend/tests/test_a_leak.py": LEAK_NOW,
                  "backend/tests/test_b_dep.py": DEP})
    monkeypatch.setattr(pt, "_worker_count", lambda: 1)
    return repo


def _classes(ev: dict) -> dict:
    return {f["nodeid"].split("::")[-1]: f for f in ev["failures"]}


def test_shards_pack_the_leaker_before_the_victim():
    shards = pt._shards({f: None for f in FILES}, 1)
    assert shards[0] == ["tests/test_a_leak.py", "tests/test_a_noop.py", "tests/test_b_dep.py"]


def test_cross_file_leak_is_order_dependent_regression_and_flaky_stays_flaky(
        rig, capsys, monkeypatch, tmp_path):
    _setup(rig, monkeypatch)
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--files", *FILES)
    assert code == 1, out
    got = _classes(_evidence(tmp_path, "tests/latest-tdd.json"))
    assert set(got) == {"test_dep", "test_flaky"}, got
    dep, flaky = got["test_dep"], got["test_flaky"]
    assert dep["classification"] == "regression", dep
    assert dep["order_dependent"] is True
    assert dep["culprit"] == "backend/tests/test_a_leak.py"
    assert flaky["classification"] == "flaky" and "order_dependent" not in flaky
    printed = {f["nodeid"].split("::")[-1]: f for f in out["failures"]}
    assert printed["test_dep"]["order_dependent"] is True


def test_no_time_for_the_prefix_rerun_is_unknown_never_flaky(rig, capsys, monkeypatch, tmp_path):
    _setup(rig, monkeypatch)
    real = pt.run_pytest_targets

    def starve(ctx, targets, deadline_at, tag, root=None):
        res = real(ctx, targets, deadline_at, tag, root)
        if tag == "rerun":  # the file re-run used up the global deadline
            ctx.deadline_at = time.monotonic() + 1
        return res
    monkeypatch.setattr(pt, "run_pytest_targets", starve)
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "tdd", "--files", *FILES)
    assert code == 1, out
    got = _classes(_evidence(tmp_path, "tests/latest-tdd.json"))
    assert {k: v["classification"] for k, v in got.items()} == {
        "test_dep": "unknown", "test_flaky": "unknown"}, got


def test_prefix_plans_cut_at_the_furthest_failing_arg():
    args = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py::test_1", "tests/test_d.py"]
    res = [{"nodeid": n, "shard": args} for n in (
        "backend/tests/test_a.py::test_0", "backend/tests/test_b.py::test_k",
        "backend/tests/test_c.py::test_1")]
    plans, direct = pt._prefix_plans([r["nodeid"] for r in res], res)
    assert direct == ["backend/tests/test_a.py::test_0"]  # first file: file re-run suffices
    (p,) = plans.values()
    assert p[0] == args and p[1] == 2
    assert p[2] == ["backend/tests/test_b.py::test_k", "backend/tests/test_c.py::test_1"]
    assert pt._arg_index("backend/tests/test_c.py::test_1[x]", args) == 2
    assert pt._arg_index("backend/tests/test_zz.py::t", args) is None
    # no shard data → keeps the file-re-run verdict (no prefix)
    assert pt._prefix_plans(["backend/tests/test_q.py::t"], []) == ({}, ["backend/tests/test_q.py::t"])


def test_same_second_same_size_edit_drops_the_stale_pyc(tmp_path):
    """CPython checks a .pyc by whole-second mtime + size: a same-size edit in the
    compile's second would be served the old bytecode (a false green)."""
    src = tmp_path / "backend" / "core" / "b.py"
    cache = src.parent / "__pycache__"
    cache.mkdir(parents=True)
    src.write_text("def val():\n    return 2\n")
    stale, newer = cache / "b.cpython-312.pyc", cache / "c.cpython-312.pyc"
    stale.write_bytes(b"old")
    (src.parent / "c.py").write_text("x = 1\n")
    newer.write_bytes(b"new")
    sec = int(src.stat().st_mtime)
    os.utime(stale, (sec + 0.5, sec + 0.5))  # compiled in the edit's second
    os.utime(src.parent / "c.py", (sec - 10, sec - 10))
    os.utime(newer, (sec, sec))  # compiled after c.py's last edit
    assert pt._drop_stale_pyc(tmp_path, ["backend/core/b.py", "backend/core/c.py",
                                         "backend/core/missing.py", "../escape.py"]) == 1
    assert not stale.exists() and newer.exists()
