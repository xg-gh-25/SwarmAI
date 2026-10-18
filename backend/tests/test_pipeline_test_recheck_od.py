"""Review fixes in ``scripts/pipeline_test.py``: overlay on compact evidence, order-
dependent rechecks, the run index, prefix deselects, direct tests, culprit budget and
referenced-nodeid capping.

Methodology: unit tests call the runner's helpers on hand-built evidence / shard args
(fake ``_run_prefixes`` where a probe would run pytest); end-to-end tests run the real
CLI in the ``rig`` tmp git repo from test_pipeline_test (real nested pytest shards, a
real HEAD worktree) with the cross-file leak fixture of test_pipeline_test_order.
Properties:
- an overlay on a base whose passes are compact (no ``file``) with a whole-file AC ref
  computes the AC layer instead of raising KeyError
- recheck of an ``order_dependent`` failure re-runs its recorded prefix in order, so
  the failure reproduces and the overlay keeps it; a pass not run in context never
  replaces it
- ``tests/run_index.json`` gets one entry per evidence write (``kind`` run / overlay),
  is append-only and survives pruning
- prefix re-runs carry the shard's ``--deselect=`` tokens; ``_arg_index`` skips them
- a test with no other file's arg before it needs no prefix job
- the culprit bisect runs once per shard within min(60 s, left / 3)
- a long nodeid named by an exit_criteria / ac_coverage ref is not capped
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pipeline_test as pt
from tests.test_pipeline_test import (  # noqa: F401 — rig is a fixture
    PROJECT, RUN_ID, _evidence, _run, _write, rig)
from tests.test_pipeline_test_order import LEAK_HEAD, LEAK_NOW, STATE


# Nested runners (verify + recheck + a HEAD baseline in one test): like test_pipeline_test,
# they outlast the runner's 30 s per-test timeout under load; 120 s still catches a hang.
pytestmark = pytest.mark.timeout(120)

F = "backend/tests/test_x.py"


# ── HIGH 1: compact passes in the overlay base ──────────────────────────────
def test_overlay_on_compact_passes_with_whole_file_ac_ref(tmp_path):
    (tmp_path / "backend" / "tests").mkdir(parents=True)
    (tmp_path / F).write_text("def test_a():\n    pass\n")
    compact = pt._evidence_result({"nodeid": F + "::test_a", "outcome": "passed", "file": F,
                                   "duration": 0.01})
    assert "file" not in compact
    base = {"mode": "regression", "finished_at": "t0", "results": [compact],
            "failures": [{"nodeid": F + "::test_b", "classification": "unknown", "outcome": "timeout"}],
            "layers": {"ac_driven": {"run": True, "pass": 0, "fail": 1}}}
    re_ev = {"results": [pt._evidence_result({"nodeid": F + "::test_b", "outcome": "passed",
                                              "file": F})], "failures": []}
    ev = pt._overlay_evidence(tmp_path, base, re_ev, [[F], [F + "::test_b"]])
    assert ev["layers"]["ac_driven"] == {"run": True, "pass": 2, "fail": 0}
    assert ev["failures"] == [] and ev["counts"]["passed"] == 2
    assert pt._exit_code(ev) == 0
    out = pt._test_output({**ev, "failures": [{"nodeid": "n"}]}, tmp_path / "e.json")
    assert out["failures"][0]["classification"] == "unknown"  # tolerant of thin entries


# ── MED 2: order_dependent failures through recheck ─────────────────────────
DEP = "from core import s\n\n\ndef test_dep():\n    assert s.STATE['n'] == 0, 'leaked'\n"
DEP_NID = "backend/tests/test_b_dep.py::test_dep"


def test_overlay_holds_an_order_dependent_failure_without_context(tmp_path):
    fail = {"nodeid": DEP_NID, "classification": "regression", "outcome": "failed",
            "order_dependent": True, "culprit": "backend/tests/test_a_leak.py"}
    base = {"mode": "verify", "finished_at": "t0", "failures": [fail],
            "results": [{"nodeid": DEP_NID, "outcome": "failed", "file": DEP_NID.split("::")[0]}]}
    alone = {"results": [{"nodeid": DEP_NID, "outcome": "passed"}], "failures": []}
    ev = pt._overlay_evidence(tmp_path, base, alone, [])
    assert ev["failures"] == [fail] and ev["counts"]["failed"] == 1
    assert ev["rechecked_from"]["held_order_dependent"] == [DEP_NID]
    in_ctx = {**alone, "od_in_context": [DEP_NID]}  # re-ran after its prefix and passed
    assert pt._overlay_evidence(tmp_path, base, in_ctx, [])["failures"] == []


def test_od_context_args_prefers_prefix_then_culprit_and_rejects_escapes(tmp_path):
    for f in ("test_a_leak.py", "test_b_dep.py"):
        (tmp_path / "backend" / "tests").mkdir(parents=True, exist_ok=True)
        (tmp_path / "backend" / "tests" / f).write_text("")
    pre = ["tests/test_a_leak.py", "tests/test_b_dep.py", "--deselect=tests/test_b_dep.py::t_ac"]
    f = {"nodeid": DEP_NID, "order_dependent": True, "prefix": pre,
         "culprit": "backend/tests/test_a_leak.py"}
    assert pt._od_context_args(tmp_path, f) == pre
    assert pt._od_context_args(tmp_path, {**f, "prefix": None}) == [
        "tests/test_a_leak.py", "tests/test_b_dep.py::test_dep"]
    assert pt._od_context_args(tmp_path, {**f, "prefix": ["../../etc/x.py", "tests/test_b_dep.py"]}) is None
    assert pt._od_context_args(tmp_path, {**f, "prefix": ["tests/test_a_leak.py"]}) is None
    assert pt._od_context_args(tmp_path, {"nodeid": DEP_NID, "order_dependent": True}) is None


def test_recheck_reruns_an_order_dependent_failure_after_its_prefix(rig, capsys, monkeypatch, tmp_path):
    """verify: test_a_leak (changed to leak) runs before test_b_dep in one shard →
    test_dep is an order_dependent regression. A recheck used to re-run test_dep alone,
    where it passes, and the overlay turned latest-verify.json green."""
    repo = rig({"backend/core/s.py": STATE, "backend/tests/test_a_leak.py": LEAK_HEAD,
                "backend/tests/test_b_dep.py": DEP})
    _write(repo, {"backend/tests/test_a_leak.py": LEAK_NOW,  # three changed test files:
                  "backend/tests/test_b_dep.py": DEP + "# touched\n",  # one worker packs
                  "backend/tests/test_x.py": (repo / F).read_text() + "# touched\n"})  # a, b
    monkeypatch.setattr(pt, "_worker_count", lambda: 1)
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "verify")
    assert code == 1, out
    (f,) = _evidence(tmp_path, "tests/latest-verify.json")["failures"]
    assert f["nodeid"] == DEP_NID and f["order_dependent"] is True, f
    assert f["prefix"] == ["tests/test_a_leak.py", "tests/test_b_dep.py"], f
    out, code = _run(capsys, "--run-id", RUN_ID, "--mode", "recheck")
    assert code == 1 and out.get("overlaid_onto") == "verify", out
    rec = _evidence(tmp_path, "tests/latest-recheck.json")
    assert rec["od_in_context"] == [DEP_NID]
    assert {r["nodeid"]: r["outcome"] for r in rec["results"]}[DEP_NID] == "failed"
    (g,) = _evidence(tmp_path, "tests/latest-verify.json")["failures"]
    assert g["nodeid"] == DEP_NID and g["order_dependent"] is True, g
    assert g["classification"] in ("regression", "unknown"), g  # never flaky / gone
    idx = _evidence(tmp_path, "tests/run_index.json")
    assert [(e["kind"], e["mode"]) for e in idx] == [
        ("run", "verify"), ("run", "recheck"), ("overlay", "verify")], idx
    assert idx[1]["rechecked_from"] == idx[0]["finished_at"] and idx[2]["auto_recheck_of"] is None


# ── MED 3: run index survives retention ─────────────────────────────────────
def test_run_index_records_every_write_and_survives_pruning(tmp_path):
    for i in range(6):
        pt._write_evidence(tmp_path, "verify", {"finished_at": f"t{i}", "cached": i == 5})
    pt._write_evidence(tmp_path, "verify", {"finished_at": "t6"}, kind="overlay",
                       auto_recheck_of="verify", rechecked_from="t5")
    t = tmp_path / "tests"
    old = time.time() - 3600
    for p in t.glob("verify-*.json"):
        os.utime(p, (old, old))
    pt._prune_evidence(t)
    assert len(list(t.glob("verify-*.json"))) == pt.EVIDENCE_KEEP
    idx = json.loads((t / pt.RUN_INDEX).read_text())
    assert [e["kind"] for e in idx] == ["run"] * 6 + ["overlay"]
    assert set(idx[0]) == {"kind", "mode", "finished_at", "ts", "evidence", "cached",
                           "auto_recheck_of", "rechecked_from"}
    assert idx[5]["cached"] is True and idx[6]["rechecked_from"] == "t5"
    assert idx[6]["auto_recheck_of"] == "verify" and all(isinstance(e["ts"], float) for e in idx)
    assert len({e["evidence"] for e in idx}) == 7  # each names its own timestamped file


# ── LOW 4 / 5: prefix deselects, direct tests ───────────────────────────────
SHARD = ["tests/test_a.py", "--deselect=tests/test_a.py::t_ac", "tests/test_b.py",
         "--deselect=tests/test_b.py::t_ac2"]


def test_prefix_jobs_keep_the_shard_deselects(tmp_path, monkeypatch):
    ctx = SimpleNamespace(workdir=tmp_path, python=sys.executable, plugin_dir=tmp_path, seq=0,
                          repo=tmp_path, workers=1)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    for f in ("test_a.py", "test_b.py"):  # a target whose file is absent never reaches argv
        (tmp_path / "tests" / f).write_text("")
    job = pt._pytest_job(ctx, "j", SHARD, tmp_path, False)
    assert job.meta["args"] == ["tests/test_a.py", "tests/test_b.py"]
    assert job.meta["shard_args"] == SHARD
    monkeypatch.setattr(pt, "_parse_pytest", lambda j: [pt._res("backend/tests/test_b.py::t", "failed")])
    (r,) = pt._parse_job(job)
    assert r["shard"] == SHARD  # tagged with the full args, deselects included
    assert pt._arg_index("backend/tests/test_b.py::t", SHARD) == 2
    assert pt._arg_index("backend/tests/test_a.py::t_ac", ["--deselect=tests/test_a.py::t_ac"]) is None
    plans, direct = pt._prefix_plans([r["nodeid"]], [r])
    assert direct == [] and list(plans.values())[0][1] == 2
    for f in ("test_a.py", "test_b.py"):
        (tmp_path / "backend" / "tests").mkdir(parents=True, exist_ok=True)
        (tmp_path / "backend" / "tests" / f).write_text("")
    seen = []
    monkeypatch.setattr(pt, "_pytest_job", lambda c, n, args, b, iso: seen.append(args) or job)
    monkeypatch.setattr(pt, "_run_jobs", lambda *a: None)
    monkeypatch.setattr(pt, "_parse_job", lambda j: [])
    pt._run_prefixes(ctx, plans, time.monotonic() + 5, "prefix")
    assert seen == [["tests/test_a.py", "tests/test_b.py", "--deselect=tests/test_a.py::t_ac",
                     "--deselect=tests/test_b.py::t_ac2"]], seen  # deselects travel along


def test_a_test_with_no_other_file_before_it_needs_no_prefix():
    own = ["tests/test_a.py::t1", "tests/test_a.py::t2", "tests/test_z.py"]
    mixed = ["tests/test_z.py::k", "tests/test_a.py::t2"]
    res = [{"nodeid": "backend/tests/test_a.py::t2", "shard": own}]
    assert pt._prefix_plans(["backend/tests/test_a.py::t2"], res) == ({}, ["backend/tests/test_a.py::t2"])
    res = [{"nodeid": "backend/tests/test_a.py::t2", "shard": mixed}]
    plans, direct = pt._prefix_plans(["backend/tests/test_a.py::t2"], res)
    assert direct == [] and list(plans.values())[0][1] == 1


# ── LOW 6: culprit bisect budget, one per shard ─────────────────────────────
def test_culprit_bisect_is_one_per_shard_and_time_capped(monkeypatch):
    args = ["tests/t1.py", "tests/t2.py", "tests/t3.py", "tests/t4.py",
            "tests/test_v.py::a", "tests/test_w.py::b"]
    n1, n2 = "backend/tests/test_v.py::a", "backend/tests/test_w.py::b"
    calls = []

    def fake(ctx, plans, deadline_at, tag, root=None):
        sel, _, ns = plans["c"]
        calls.append((deadline_at, list(sel)))
        return {n: "failing" if "tests/t3.py" in sel else "passed" for n in ns}
    monkeypatch.setattr(pt, "_run_prefixes", fake)
    for left, cap in ((1000.0, pt.CULPRIT_BUDGET_S), (30.0, 10.0)):
        calls.clear()
        ctx = SimpleNamespace(left=lambda v=left: v)
        t0 = time.monotonic()
        got = pt._culprits(ctx, [n1, n2], {"k": [args, 5, [n1, n2]]})
        assert got == {n1: "backend/tests/t3.py", n2: "backend/tests/t3.py"}
        assert len(calls) == 4, calls  # 3 bisect probes for n1 + one confirm for n2
        assert calls[-1][1] == ["tests/t3.py", "tests/test_w.py::b"]  # confirm for n2 only
        assert all(d <= t0 + cap + 1 for d, _ in calls), (cap, calls)


def test_culprit_bisect_stops_when_its_budget_is_spent(monkeypatch):
    args = ["tests/t1.py", "tests/t2.py", "tests/t3.py", "tests/test_v.py::a"]
    monkeypatch.setattr(pt, "CULPRIT_BUDGET_S", 0.0)
    monkeypatch.setattr(pt, "_run_prefixes", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    ctx = SimpleNamespace(left=lambda: 1000.0)
    assert pt._culprits(ctx, ["backend/tests/test_v.py::a"],
                        {"k": [args, 3, ["backend/tests/test_v.py::a"]]}) == {}


# ── LOW 7: never cap a nodeid an explicit ref names ─────────────────────────
G = "backend/tests/test_g.py"
LONG = f"{G}::test_g[{'x' * 500}]"
OTHER = f"{G}::test_g[{'y' * 500}]"


def test_long_nodeid_named_by_exit_criteria_is_not_capped(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "backend" / "tests").mkdir(parents=True)
    (repo / G).write_text("")
    (repo / F).write_text("")
    arts = tmp_path / "ws" / "Projects" / PROJECT / ".artifacts"
    arts.mkdir(parents=True)
    (arts / "manifest.json").write_text(json.dumps({"artifacts": [
        {"id": "p1", "file": "plan.json"}, {"id": "b1", "file": "build.json"}]}))
    (arts / "plan.json").write_text(json.dumps({"data": {"exit_criteria": [
        {"id": "X1", "kind": "test", "ref": LONG, "expect": "pass"},
        {"id": "X2", "kind": "probe", "ref": "fuzz", "expect": "no_new_findings"}]}}))
    (arts / "build.json").write_text(json.dumps({"ac_coverage": [{"test": "test_x.py::test_add"}]}))
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path / "ws"))
    run_state = {"stages": [{"stage": "plan", "artifact_id": "p1"},
                            {"stage": "build", "artifact_id": "b1"}]}
    refs = pt._ref_nodeids(repo, PROJECT, run_state, [])
    assert refs == [f"{F}::test_add", LONG], refs
    assert pt._capped_id(LONG, refs) == {"nodeid": LONG}  # referenced: kept whole
    assert pt._capped_id(OTHER, refs).get("nodeid_truncated") is True  # others still capped
    short = [f"{G}::test_g"]  # a function ref still matches the capped id: cap it
    assert pt._capped_id(LONG, short).get("nodeid_truncated") is True
    e = pt._evidence_result({"nodeid": LONG, "outcome": "passed", "file": G}, refs)
    assert e["nodeid"] == LONG and "nodeid_truncated" not in e
