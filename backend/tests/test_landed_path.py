"""core/run_worktree.landed_path and its one consumer, GET /workspace/file.

A Canvas row recorded inside a pipeline run's worktree keeps opening after the run
landed and its worktree was released: the missing path is mapped to the same file in
the main checkout, but only for a run whose run.json says that worktree was
integrated (pipeline-run-worktree §6.4).

Methodology: real git in tmp_path (the "main checkout"), a hand-written run.json in a
temp SwarmWS, SWARM_WORKTREE_ROOT pointing at an empty temp dir (the released
worktree simply does not exist), HOME set to tmp_path so the home-only guard holds.
No real repository, run or workspace is read.

Invariants:
- an integrated run maps the missing worktree path to the main checkout's file;
- not integrated, a path that still exists, a path outside the worktree root, a file
  the main checkout lacks, or an escape with ``..`` is None;
- the endpoint serves the mapped file (200) and 404s an unintegrated run's path.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core import run_worktree as rw

RUN = "run_landed1"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    home = Path(os.path.realpath(tmp_path))
    monkeypatch.setenv("HOME", str(home))
    main = home / "proj"
    main.mkdir()
    _git(main, "init", "-q")
    (main / "docs").mkdir()
    (main / "docs" / "a.md").write_text("landed\n")
    wts = home / "wts"
    wts.mkdir()
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(wts))
    ws = home / "ws"
    run_dir = ws / "Projects" / "P" / ".artifacts" / "runs" / RUN
    run_dir.mkdir(parents=True)

    def write_run(integrated: bool):
        entry = {"repo_name": "proj", "main": str(main), "git_common_dir": str(main / ".git"),
                 "state": "released"}
        if integrated:
            entry["integrated"] = {"sha": "a" * 40}
        (run_dir / "run.json").write_text(json.dumps({"id": RUN, "worktrees": [entry]}))

    write_run(True)
    recorded = wts / RUN / "proj" / "docs" / "a.md"   # the released worktree: gone
    return {"home": home, "main": main, "ws": ws, "recorded": recorded, "write_run": write_run}


def test_an_integrated_run_maps_to_the_main_checkout(setup):
    s = setup
    assert rw.landed_path(s["recorded"], s["ws"]) == s["main"] / "docs" / "a.md"


def test_no_mapping_without_a_landed_file(setup):
    s = setup
    assert rw.landed_path(s["recorded"].parent / "missing.md", s["ws"]) is None      # main lacks it
    assert rw.landed_path(s["home"] / "elsewhere" / "a.md", s["ws"]) is None          # not a worktree path
    assert rw.landed_path(str(s["recorded"].parent) + "/../../../proj/docs/a.md", s["ws"]) is None
    assert rw.landed_path(s["main"] / "docs" / "a.md", s["ws"]) is None               # exists: no mapping
    s["write_run"](False)
    assert rw.landed_path(s["recorded"], s["ws"]) is None                             # not integrated


def test_get_workspace_file_serves_the_landed_file(setup, client):
    s = setup
    with patch("routers.workspace_api._get_workspace_path", new_callable=AsyncMock,
               return_value=str(s["ws"])):
        r = client.get("/api/workspace/file", params={"path": str(s["recorded"])})
        assert r.status_code == 200 and r.json()["content"] == "landed\n"
        s["write_run"](False)
        r = client.get("/api/workspace/file", params={"path": str(s["recorded"])})
        assert r.status_code == 404
