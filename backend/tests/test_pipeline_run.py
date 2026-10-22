"""Tests for read-only access to historical pipeline run records."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def workspace(tmp_path):
    project_dir = tmp_path / "Projects" / "TestProject" / ".artifacts"
    project_dir.mkdir(parents=True)
    return tmp_path


def _run_cli(workspace: Path, *args: str) -> dict:
    """Run a retained read-only artifact_cli command and parse its JSON output."""
    cli_path = Path(__file__).resolve().parent.parent / "scripts" / "artifact_cli.py"
    env = {
        "SWARM_WORKSPACE": str(workspace),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
    }
    result = subprocess.run(
        [sys.executable, str(cli_path), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    output = result.stdout.strip() or result.stderr.strip()
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pytest.fail(f"CLI output not valid JSON: {output}\nstderr: {result.stderr}")


def test_list_empty_project(workspace):
    listing = _run_cli(workspace, "run-get", "--project", "TestProject")
    assert listing["count"] == 0


def test_empty_history(workspace):
    result = _run_cli(workspace, "run-history", "--project", "TestProject")
    assert result["calibration"] == "defaults"
    assert result["stage_averages"] == {}


def test_empty_status(workspace):
    result = _run_cli(workspace, "run-status")
    assert result["count"] == 0
    assert result["summary"]["running"] == 0


class TestLoadCompletedRunsAnalyticsStatus:
    """Historical analytics includes delivered records regardless of terminal label."""

    def _write_run(self, workspace, run_id, status, stages):
        run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps({
            "id": run_id, "project": "TestProject", "status": status, "stages": stages,
        }))

    def _delivered_stages(self):
        return [{"stage": "build", "status": "completed", "token_cost": 40000},
                {"stage": "reflect", "status": "completed", "token_cost": 3000}]

    def _load(self, workspace):
        scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import scripts.artifact_cli as cli
        from unittest.mock import patch
        with patch.object(cli, "_get_workspace", return_value=workspace):
            return cli._load_completed_runs("TestProject", limit=50)

    def test_delivered_but_abandoned_run_included(self, workspace):
        self._write_run(workspace, "run_deliv_aband", "abandoned", self._delivered_stages())
        ids = [r["id"] for r in self._load(workspace)]
        assert "run_deliv_aband" in ids

    def test_plain_completed_still_included(self, workspace):
        self._write_run(workspace, "run_plain", "completed", self._delivered_stages())
        ids = [r["id"] for r in self._load(workspace)]
        assert "run_plain" in ids

    def test_genuinely_abandoned_empty_shell_excluded(self, workspace):
        self._write_run(workspace, "run_empty", "abandoned",
                        [{"stage": "evaluate", "status": "completed", "token_cost": 5000}])
        ids = [r["id"] for r in self._load(workspace)]
        assert "run_empty" not in ids
