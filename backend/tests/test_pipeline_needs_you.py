"""Pipeline "Needs you": one definition for the count and the rows (design
left-nav-backend-and-visual-system.md S5b; the 2026-10-23 screenshot showed
"Needs you 0" above a listed stopped run).

What is tested (real analytics endpoint over a temp workspace):
- a run needs you when it is abandoned or its pause kind is decision / stopped /
  worktree / idle (the v1 human loop's kinds), and each run summary carries that
  as ``needs_you``;
- ``overall.aborted_count`` and each group's ``aborted_count`` count exactly the
  runs whose ``needs_you`` is true;
- an older needs-you run past the newest-20 cap is still listed.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _run(ws, project, run_id, days, **data):
    d = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True)
    base = {"id": run_id, "project": project, "requirement": f"r {run_id}", "profile": "direct",
            "created_at": _ago(days), "updated_at": _ago(days),
            "stages": [{"stage": "deliver", "status": "completed"}]}
    (d / "run.json").write_text(json.dumps({**base, **data}))


@pytest.fixture
def client(tmp_path, monkeypatch):
    from core import run_index
    from main import app
    from routers import pipelines

    run_index._clear_for_tests()
    monkeypatch.setattr(pipelines, "_get_swarmws", lambda: tmp_path)
    # these runs are bare projections: treat each as having a v1 contract
    monkeypatch.setattr(pipelines.pm, "is_v1", lambda run_id: True)
    yield TestClient(app), tmp_path
    run_index._clear_for_tests()


def test_count_and_rows_share_one_definition(client):
    c, ws = client
    for i in range(22):
        _run(ws, "P", f"run_done{i:02d}", 1 + i * 0.01, status="completed",
             completed_at=_ago(1 + i * 0.01))
    # v1 run stopped by the user (pause kind 'stopped'), the oldest: past the cap
    _run(ws, "P", "run_v1_stopped", 5, status="paused", decision_cap=3, session_id="s",
         reason="user_stop", stages={"BUILD": "active"})
    _run(ws, "P", "run_v1_decision", 2, status="awaiting_human", decision_cap=3,
         session_id="s", stages={"BUILD": "active"})

    d = c.get("/api/pipelines/analytics").json()
    g = next(x for x in d["by_project"] if x["project"] == "P")
    rows = {r["id"]: r for r in g["runs"]}
    assert rows["run_v1_stopped"]["needs_you"] is True
    assert rows["run_v1_decision"]["needs_you"] is True
    assert rows["run_done00"]["needs_you"] is False
    shown = sum(1 for r in g["runs"] if r["needs_you"])
    assert g["aborted_count"] == shown == 2
    assert d["overall"]["aborted_count"] == 2
