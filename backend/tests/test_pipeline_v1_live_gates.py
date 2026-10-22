"""Live gates and the worktree sweeper must recognise a non-terminal Pipeline v1 run.

Regression for the cutover gap where run_index / run_worktree / security_hooks only knew
the legacy ``running``/``paused`` statuses: an ``active`` v1 run looked finished, so the
hourly sweeper released its worktree and the worktree write gate saw no owning run.
Real git repo, real RunEngine, real linked worktree.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core import run_worktree, worktree_write_gate as wg
from tests.test_main_dirty_scan_v1 import _start_v1_run


def test_sweep_keeps_an_active_v1_runs_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _repo, engine = _start_v1_run(tmp_path, monkeypatch)
    wt = Path(engine.effective_repos()["repo"])
    assert engine.projection()["status"] == "active" and wt.is_dir()
    report = run_worktree.sweep(engine.workspace, lambda _sid: False, release_orphans=True)
    assert wt.is_dir(), report
    assert not any(engine.run_id in str(item) for item in report.get("released", []))


def test_write_gate_sees_the_v1_run_as_this_sessions_run(tmp_path, monkeypatch):
    repo, engine = _start_v1_run(tmp_path, monkeypatch)
    own = run_worktree.own_isolated_run("session-v1", engine.workspace)
    assert own is not None and own["id"] == engine.run_id
    wt = Path(engine.effective_repos()["repo"])
    owner = wg._Own(own["id"], own["worktrees"], project=own.get("project"))
    # main-checkout write: redirected to this run's own worktree twin
    verdict = wg.judge({"tool_name": "Edit", "tool_input": {"file_path": str(repo / "app.py")}},
                       owner)
    why = verdict["hookSpecificOutput"]["permissionDecisionReason"]
    assert str(wt / "app.py") in why
    # its own worktree: allowed
    assert wg.judge({"tool_name": "Edit", "tool_input": {"file_path": str(wt / "app.py")}},
                    owner) is None
