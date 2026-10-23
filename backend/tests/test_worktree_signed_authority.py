"""Worktree authority is signed, never the run.json projection (re-audit 536bb62b /
99f96949).

Methodology: a real isolated Pipeline v1 run (tests/pipeline_v1_rig) in a tmp data dir;
its run.json is then edited the way an agent could, and the readers that decide
whose writes a worktree takes, and what the sweeper may release, must not move:

- ``own_isolated_run`` takes the owner from the signed events, the status from the
  signed lifecycle, the mode from the protected contract, the id/project from the
  run directory;
- a run.json with no protected contract is not a run at all;
- ``worktree_write_gate`` denies a write into a run's worktree from a session that
  owns no run (it used to allow it whenever the caller had none);
- the sweeper keeps a run whose signed state it cannot read.
"""
from __future__ import annotations

import json

import pytest

from tests.pipeline_v1_rig import PROJECT, SESSION, Rig


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made = []

    def make(profile="direct"):
        r = Rig(tmp_path / f"case{len(made)}", monkeypatch, profile=profile)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def _isolated(rig):
    r = rig()
    r.create()
    assert r.worktree is not None, "the direct run starts in BUILD with its worktree"
    return r


def _forge(r, **fields):
    rj = r.engine().run_dir / "run.json"
    rj.write_text(json.dumps({**json.loads(rj.read_text()), **fields}))


def test_a_forged_projection_does_not_change_who_owns_the_worktree(rig):
    from core import run_worktree
    r = _isolated(rig)
    _forge(r, worktree_mode="shared", status="completed", session_id="someone_else",
           id="run_other", project="Nope")
    got = run_worktree.own_isolated_run(SESSION, r.ws)
    assert got is not None and got["id"] == r.run_id and got["project"] == PROJECT
    assert got["worktrees"] and got["status"] == "active"
    assert run_worktree.own_isolated_run("someone_else", r.ws) is None


def test_a_run_json_without_a_protected_contract_is_not_a_run(rig):
    from core import run_worktree
    r = _isolated(rig)
    fake = r.ws / "Projects" / PROJECT / ".artifacts" / "runs" / "run_fakefake"
    fake.mkdir(parents=True)
    (fake / "run.json").write_text(json.dumps({
        "id": "run_fakefake", "status": "active", "session_id": "intruder",
        "worktree_mode": "isolated", "worktrees": []}))
    assert run_worktree.own_isolated_run("intruder", r.ws) is None
    assert "run_fakefake" not in {f.parent.name for f, _d in run_worktree._iter_runs(r.ws)}


def test_a_session_with_no_run_cannot_write_into_a_runs_worktree(rig):
    from core import worktree_write_gate as wg
    r = _isolated(rig)
    target = str(r.worktree / "backend" / "core" / "mod.py")
    v = wg.judge({"tool_name": "Write", "tool_input": {"file_path": target}}, None)
    assert v and v["hookSpecificOutput"]["permissionDecision"] == "deny", v
    # reads stay allowed (reviewers read a run's code)
    assert wg.judge({"tool_name": "Read", "tool_input": {"file_path": target}}, None) is None


def test_the_sweeper_keeps_a_run_whose_signed_state_is_unreadable(rig):
    from core import run_worktree
    r = _isolated(rig)
    _forge(r, status="abandoned")
    key = r.data / "state" / "pipeline_agent_audit" / "runs" / r.run_id / "authority.key"
    key.rename(key.with_name("authority.key.bak"))
    report = run_worktree.sweep(r.ws, owner_active=lambda sid: False,
                                now=1e12, release_orphans=False)
    assert not any(isinstance(x, dict) and x.get("run") == r.run_id
                   for x in report["released"]), report
    assert r.worktree.is_dir()


def test_the_sweeper_rejudges_under_the_run_lock_before_releasing(rig, monkeypatch):
    """Re-audit ca91762f HIGH: staleness was judged from the scan, then the worktree
    was released later; a run that came back to life in between lost its worktree.
    The release decision is made again under the run's operation lock."""
    from core import run_worktree
    r = _isolated(rig)
    calls = {"n": 0}

    def activity(rid, data):
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 1e12      # revived right after the scan
    monkeypatch.setattr(run_worktree, "last_activity", activity)
    report = run_worktree.sweep(r.ws, owner_active=lambda sid: False, now=1e12)
    assert calls["n"] >= 2, "the decision was not made again before release"
    assert not any(isinstance(x, dict) and x.get("run") == r.run_id
                   for x in report["released"]), report
    assert r.worktree.is_dir()


def test_a_forged_integrated_row_is_snapshotted_not_dropped(rig):
    """Re-audit 84b72ec1 HIGH: a row's ``state`` came from run.json, so a terminal
    run's row forged to "integrated" was removed WITHOUT a snapshot. Only the signed
    landing may make a row integrated; the forged one is released like an active one,
    with its ledger snapshot."""
    from core import run_worktree
    r = _isolated(rig)
    (r.worktree / "backend" / "core" / "mod.py").write_text("def add(a, b):\n    return 9\n")
    rj = r.engine().run_dir / "run.json"       # a stale live run: released by the sweep
    data = json.loads(rj.read_text())
    for e in data.get("worktrees") or []:
        # an agent can write the TRUE common dir and integration branch, so the
        # sweeper's on-disk checks pass; only the signed landing may say "integrated"
        import os
        import subprocess
        head = subprocess.run(["git", "-C", str(r.repo), "symbolic-ref", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
        e.update(state="integrated", integration_branch=head,
                 git_common_dir=os.path.realpath(str(r.repo / ".git")))
    rj.write_text(json.dumps(data))
    report = run_worktree.sweep(r.ws, owner_active=lambda sid: False, now=1e12)
    mine = [x for x in report["released"] if isinstance(x, dict) and x.get("run") == r.run_id]
    assert mine, report
    results = mine[0]["results"]
    assert results and all(res.get("integrated") is False for res in results), results
    assert all(res.get("ledger") for res in results), results


def test_the_canvas_classifier_uses_the_contracts_repos(rig):
    """Re-audit 84b72ec1: the display classifier read run id and repos from run.json."""
    from core import run_worktree
    r = _isolated(rig)
    run_worktree._LIVE_SUMMARY.clear()
    roots = run_worktree.active_worktree_roots(r.ws)
    assert any(p == str(r.worktree) for p, _repo in roots), roots
    rj = r.engine().run_dir / "run.json"
    data = json.loads(rj.read_text())
    data["id"] = "run_other"
    data["worktrees"] = [*data["worktrees"], {"repo_name": "intruder", "state": "active"}]
    rj.write_text(json.dumps(data))
    run_worktree._LIVE_SUMMARY.clear()
    roots = run_worktree.active_worktree_roots(r.ws)
    assert all(repo != "intruder" for _p, repo in roots)
    assert any(p == str(r.worktree) for p, _repo in roots), roots
