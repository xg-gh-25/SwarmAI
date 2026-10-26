"""After a landed run's worktrees are released, run.json says so.

What is tested: the CLI lands a run, REFLECT runs, and ``release_landed_worktrees``
removes the worktree (standalone §4.6). run.json must then list no active worktree
for it, and the daemon's sweeper must find nothing left to release. Before the fix
run.json kept the removed worktree as ``active``; the sweeper retried it on every
pass and logged ``main checkout unknown`` (run_bb7b1850, 2026-10-26).
"""
import json

from core import run_worktree
from tests.pipeline_v1_rig import PROJECT, Rig
from tests.test_pipeline_v1_follow_next import follow


def test_landed_run_json_lists_no_released_worktree(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="direct")
    try:
        r.create()
        state, trail = follow(r)
        assert state["status"] == "completed" and state["landing"] == "landed", trail
        wt = run_worktree.wt_path(r.run_id, "repo")
        assert not wt.exists(), "the landed worktree is removed"
        run_json = r.ws / "Projects" / PROJECT / ".artifacts" / "runs" / r.run_id / "run.json"
        rows = json.loads(run_json.read_text(encoding="utf-8")).get("worktrees") or []
        assert not [row for row in rows if row.get("state") == "active"], rows
        report = run_worktree.sweep(r.ws, owner_active=lambda _session: False)
        assert not report["released"] and not report["errors"], report
    finally:
        r.close()


def test_sweeper_skips_a_row_whose_worktree_is_gone(tmp_path, monkeypatch):
    """A run.json written before the fix still says active for a removed worktree."""
    r = Rig(tmp_path, monkeypatch, profile="direct")
    try:
        r.create()
        state, trail = follow(r)
        assert state["landing"] == "landed", trail
        run_json = r.ws / "Projects" / PROJECT / ".artifacts" / "runs" / r.run_id / "run.json"
        data = json.loads(run_json.read_text(encoding="utf-8"))
        data["worktrees"] = [{"repo_name": "repo", "main": str(r.repo), "state": "active",
                              "path": str(run_worktree.wt_path(r.run_id, "repo"))}]
        run_json.write_text(json.dumps(data), encoding="utf-8")
        report = run_worktree.sweep(r.ws, owner_active=lambda _session: False)
        assert not report["released"] and not report["errors"], report
    finally:
        r.close()
