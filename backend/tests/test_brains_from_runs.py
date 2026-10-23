"""Brain Hub reads a project's last landing and in-progress state from its runs,
not from git (design left-nav-backend-and-visual-system.md B3 / T4).

What is tested (real hermetic DDD workspace, real router):
- GET /api/ddd/brains starts no subprocess at all;
- ``lastChangeRelative`` is the newest LANDED run in the 30-day window: a legacy
  run with ``commits`` (its ``completed_at``) or a v1 run whose ``landing`` is
  landed / human_override (its ``updated_at``); a project without one shows "--";
- ``liveRun`` is true only for a project with a run in ``run_index.LIVE_STATUSES``;
- a landed run older than the window does not count.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._brain_workspace import hermetic_workspace  # noqa: F401 — pytest fixture


def _stamp(days: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - days * 86400))


def _run(ws: Path, project: str, run_id: str, age_days: float, **data) -> None:
    d = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True)
    f = d / "run.json"
    f.write_text(json.dumps({"id": run_id, "project": project, "stages": [],
                             "created_at": _stamp(age_days), "updated_at": _stamp(age_days),
                             **data}))
    t = time.time() - age_days * 86400
    os.utime(f, (t, t))


@pytest.fixture
def client(hermetic_workspace, monkeypatch):  # noqa: F811
    from core import run_index
    from routers import pipelines
    from routers.ddd_brain import router

    run_index._clear_for_tests()
    monkeypatch.setattr(pipelines, "_get_swarmws", lambda: hermetic_workspace)
    app = FastAPI()
    app.include_router(router, prefix="/api/ddd")
    yield TestClient(app)
    run_index._clear_for_tests()


def _no_subprocess(monkeypatch):
    def boom(*a, **k):
        raise AssertionError(f"subprocess started: {a[:1]}")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)


def _brains(client) -> dict:
    return {b["name"]: b["health"] for b in client.get("/api/ddd/brains").json()["brains"]}


def test_gallery_starts_no_subprocess_and_reads_runs(client, hermetic_workspace, monkeypatch):
    ws = hermetic_workspace
    _run(ws, "SwarmAI", "run_landed", 3, status="completed", completed_at=_stamp(3),
         commits=[{"repo": "/r", "sha": "abc1234", "files": ["x.py"]}])
    _run(ws, "SwarmAI", "run_unlanded", 1, status="completed", completed_at=_stamp(1))
    _run(ws, "SwarmAI", "run_live", 0.5, status="active", decision_cap=3)
    _run(ws, "Other", "run_old_landed", 40, status="completed", completed_at=_stamp(40),
         commits=[{"repo": "/r", "sha": "def5678", "files": ["y.py"]}])
    _no_subprocess(monkeypatch)

    h = _brains(client)
    assert h["SwarmAI"]["lastChangeRelative"] == "3d ago"   # the landed run, not the newer unlanded one
    assert h["SwarmAI"]["liveRun"] is True
    assert h["Other"]["lastChangeRelative"] == "--"          # its only landing is outside the window
    assert h["Other"]["liveRun"] is False
    assert "uncommitted" not in h["SwarmAI"]


def test_v1_landing_counts(client, hermetic_workspace, monkeypatch):
    _run(hermetic_workspace, "Other", "run_v1", 2, status="completed", decision_cap=3,
         landing="landed")
    _no_subprocess(monkeypatch)
    assert _brains(client)["Other"]["lastChangeRelative"] == "2d ago"
