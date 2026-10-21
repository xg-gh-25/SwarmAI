"""Tests for ``artifact_cli.py run-get --brief`` and ``--field``.

Methodology: a run.json and evidence/tests/latest-*.json are written into a temp
workspace, the CLI runs as a subprocess (as agents call it), and the single-line JSON
is parsed. Properties checked:
- --brief carries status, the profile's stage plan with each stage's status, the first
  unfinished stage, files touched, and per-mode test counts + failing node ids;
- it leaves out the bulky parts (stage decisions, token bookkeeping);
- --field returns only the named dot paths, list indexes included, and marks a path
  that does not exist as {"missing": path} instead of a null;
- --brief / --field without --run-id is an error; plain run-get is unchanged.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
RUN_ID = "run_abcd1234"


def _cli(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        "SWARM_WORKSPACE": str(workspace),
        "SWARM_TODO_DB": str(workspace / ".test-todos.db"),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(BACKEND),
    }
    return subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "artifact_cli.py"), "run-get",
         "--project", "TestProject", *args],
        capture_output=True, text=True, env=env, cwd=str(BACKEND), timeout=60,
    )


@pytest.fixture
def workspace(tmp_path):
    run_dir = tmp_path / "Projects" / "TestProject" / ".artifacts" / "runs" / RUN_ID
    (run_dir / "evidence" / "tests").mkdir(parents=True)
    state = {
        "id": RUN_ID, "project": "TestProject", "requirement": "r", "profile": "full",
        "status": "running", "created_at": "2026-10-21T00:00:00+00:00",
        "updated_at": "2026-10-21T01:00:00+00:00",
        "stages": [
            {"stage": "evaluate", "status": "completed", "summary": "GO",
             "decisions": [{"description": "BULKY-DECISION"}], "token_usage": {"in": 1}},
            {"stage": "think", "status": "completed", "summary": "x" * 400},
        ],
        "files_touched": ["backend/a.py"],
        "worktree_mode": "isolated",
    }
    (run_dir / "run.json").write_text(json.dumps(state), encoding="utf-8")
    (run_dir / "evidence" / "tests" / "latest-tdd.json").write_text(json.dumps({
        "mode": "tdd", "finished_at": "2026-10-21T00:30:00Z",
        "counts": {"passed": 3, "failed": 1},
        "failures": [{"nodeid": "backend/tests/test_a.py::test_x", "summary": "boom"}],
    }), encoding="utf-8")
    return tmp_path


def test_brief_has_plan_current_stage_files_and_tests(workspace):
    r = _cli(workspace, "--run-id", RUN_ID, "--brief")
    assert r.returncode == 0, r.stderr
    assert "\n" not in r.stdout.strip()
    b = json.loads(r.stdout)
    assert b["status"] == "running"
    rows = {s["stage"]: s for s in b["stages"]}
    assert rows["evaluate"]["status"] == "completed" and rows["evaluate"]["summary"] == "GO"
    assert rows["plan"]["status"] == "pending"
    assert b["current_stage"] == "plan"
    assert len(rows["think"]["summary"]) == 160 and rows["think"]["summary"].endswith("…")
    assert b["files_touched"] == ["backend/a.py"]
    assert b["tests"] == [{
        "mode": "tdd", "finished_at": "2026-10-21T00:30:00Z",
        "counts": {"passed": 3, "failed": 1},
        "failures": ["backend/tests/test_a.py::test_x"], "more_failures": 0,
        "evidence": "evidence/tests/latest-tdd.json",
    }]
    assert "BULKY-DECISION" not in r.stdout and "token_usage" not in r.stdout


def test_field_paths_and_missing(workspace):
    r = _cli(workspace, "--run-id", RUN_ID, "--field", "status",
             "--field", "stages.1.stage", "--field", "stages.9.stage")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {
        "status": "running", "stages.1.stage": "think",
        "stages.9.stage": {"missing": "stages.9.stage"},
    }


def test_brief_needs_run_id(workspace):
    r = _cli(workspace, "--brief")
    assert r.returncode == 1
    assert "--run-id" in r.stderr


def test_plain_run_get_unchanged(workspace):
    r = _cli(workspace, "--run-id", RUN_ID)
    assert json.loads(r.stdout)["stages"][0]["decisions"] == [{"description": "BULKY-DECISION"}]
