"""Nav reads of pipeline runs see only the last 30 days (design
left-nav-backend-and-visual-system.md B2 / T2 / T3).

What is tested:
- ``run_index.recent()`` returns runs whose run.json changed within
  ``RUN_WINDOW_DAYS`` and never OPENS an older file (the window is applied on the
  stat, before parsing);
- the Pipeline list, the Pipeline analytics and Need You's paused runs all read
  that window: a 31-day-old paused run is not listed by any of them;
- ``run_index.runs()`` (the safety reads: sweeper, gates) still sees every run;
- analytics has one window (30 days): ``window=ytd`` answers as 30d.
"""
from __future__ import annotations

import builtins
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _run(ws: Path, project: str, run_id: str, age_days: float, **data) -> Path:
    d = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True)
    f = d / "run.json"
    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(now - age_days * 86400))
    base = {"id": run_id, "project": project, "requirement": f"req {run_id}",
            "profile": "direct", "created_at": stamp, "updated_at": stamp, "stages": []}
    f.write_text(json.dumps({**base, **data}))
    t = now - age_days * 86400
    os.utime(f, (t, t))
    return f


@pytest.fixture
def ws(tmp_path, monkeypatch):
    from core import run_index
    run_index._clear_for_tests()
    _run(tmp_path, "P", "run_new", 1, status="completed")
    _run(tmp_path, "P", "run_new_paused", 2, status="paused",
         checkpoint={"reason": "pick A or B", "kind": "decision"}, pause_kind="decision")
    _run(tmp_path, "P", "run_old_paused", 31, status="paused",
         checkpoint={"reason": "pick C or D", "kind": "decision"}, pause_kind="decision")
    _run(tmp_path, "Q", "run_old", 200, status="completed")
    yield tmp_path
    run_index._clear_for_tests()


def test_recent_skips_old_files_before_opening_them(ws, monkeypatch):
    from core import run_index

    opened = []
    real_open = builtins.open

    def spy(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    got = sorted(r.path.parent.name for r in run_index.recent(ws))
    assert got == ["run_new", "run_new_paused"]
    assert not any("run_old" in p for p in opened), opened
    assert run_index.RUN_WINDOW_DAYS == 30


def test_safety_reads_still_see_every_run(ws):
    from core import run_index
    names = sorted(r.path.parent.name for r in run_index.runs(ws))
    assert names == ["run_new", "run_new_paused", "run_old", "run_old_paused"]


def test_pipeline_list_and_need_you_read_the_window(ws, monkeypatch):
    from core import attention_authority
    from routers import pipelines

    monkeypatch.setattr(pipelines, "_get_swarmws", lambda: ws)
    ids = sorted(r["id"] for r in pipelines._load_pipeline_runs())
    assert ids == ["run_new", "run_new_paused"]
    paused = [it.id for it in attention_authority._collect_paused_runs()]
    assert all("run_old" not in i for i in paused), paused


def test_analytics_has_one_thirty_day_window(ws, monkeypatch):
    from main import app
    from routers import pipelines

    monkeypatch.setattr(pipelines, "_get_swarmws", lambda: ws)
    client = TestClient(app)
    for w in ("30d", "ytd"):
        d = client.get(f"/api/pipelines/analytics?window={w}").json()
        assert d["window"] == "30d"
