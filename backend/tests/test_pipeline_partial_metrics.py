"""G2 (run_f8494370): partial METRICS for paused/abandoned pipeline runs.

The retro-analytics dashboard must show cycle-time-so-far + tokens-so-far for
runs that never completed (paused / abandoned). `_try_generate_metrics` only
fires at status==completed (artifact_cli.py:1541), so those runs have no
METRICS.json. But `cmd_run_metrics` / `_extract_run_metrics` are NOT
status-gated — they already produce a partial, None-safe metrics dict for any
run. These tests pin that contract so the endpoint can rely on on-read
generation, and prove a partial run does NOT crash the extractor.
"""
import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "Projects" / "TestProject" / ".artifacts" / "runs").mkdir(parents=True)
    return tmp_path


def _write_run(workspace, run_id, *, status, stages, created_at, completed_at=None):
    run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": run_id, "project": "TestProject", "requirement": "Do a thing",
        "profile": "goal", "status": status, "stages": stages,
        "taste_decisions": [], "created_at": created_at,
        "updated_at": completed_at or created_at,
    }
    if completed_at:
        data["completed_at"] = completed_at
    (run_dir / "run.json").write_text(json.dumps(data), encoding="utf-8")
    return run_dir


def _run_metrics(workspace, run_id):
    from scripts.artifact_cli import cmd_run_metrics
    args = Namespace(project="TestProject", run_id=run_id)
    with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
        cmd_run_metrics(args, reg=None)
    return workspace / "Projects/TestProject/.artifacts/runs" / run_id / "METRICS.json"


class TestPartialMetricsForNonCompleted:
    def test_paused_run_generates_partial_metrics(self, workspace):
        # A paused run: 2 stages recorded, NO completed_at.
        _write_run(
            workspace, "run_paused", status="paused",
            stages=[
                {"stage": "evaluate", "status": "completed", "token_cost": 9000},
                {"stage": "think", "status": "completed", "token_cost": 8000},
            ],
            created_at="2026-08-02T00:00:00+00:00",  # no completed_at
        )
        mf = _run_metrics(workspace, "run_paused")
        assert mf.exists(), "partial METRICS.json must be generated for a paused run"
        m = json.loads(mf.read_text())
        # status is preserved (the dashboard shows 'paused', not fabricated 'completed')
        assert m["status"] == "paused"
        # duration is None-safe (no completed_at → no crash, just null)
        assert m["duration_minutes"] is None
        # tokens-so-far are captured from recorded stages
        assert m["stage_tokens"].get("evaluate") == 9000
        assert m["stage_tokens"].get("think") == 8000
        assert m["stages_completed"] == 2

    def test_abandoned_run_does_not_crash_extractor(self, workspace):
        # An abandoned run with a half-written stage (status not completed).
        _write_run(
            workspace, "run_abandoned", status="abandoned",
            stages=[{"stage": "evaluate", "status": "recorded"}],
            created_at="2026-08-02T00:00:00+00:00",
        )
        mf = _run_metrics(workspace, "run_abandoned")
        assert mf.exists()
        m = json.loads(mf.read_text())
        assert m["status"] == "abandoned"
        assert m["duration_minutes"] is None
        # a 'recorded' (not completed) stage is not counted as completed
        assert m["stages_completed"] == 0

    def test_completed_run_still_gets_full_duration(self, workspace):
        # Regression guard: a completed run with completed_at still computes duration.
        _write_run(
            workspace, "run_done", status="completed",
            stages=[{"stage": "evaluate", "status": "completed", "token_cost": 5000}],
            created_at="2026-08-02T00:00:00+00:00",
            completed_at="2026-08-02T00:10:00+00:00",
        )
        mf = _run_metrics(workspace, "run_done")
        m = json.loads(mf.read_text())
        assert m["status"] == "completed"
        assert m["duration_minutes"] == 10.0
