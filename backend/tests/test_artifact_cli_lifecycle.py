"""Tests for pipeline run lifecycle management: auto-abandon and cleanup.

Tests AC1-AC5 of pipeline run_fd0064e6:
- AC1: auto-abandon stale same-project running runs (>2h) on new run start
- AC2: cleanup-orphans subcommand for batch cleanup
- AC3: one-time data fix reclassifies 14 failed + 6 orphan
- AC4: legitimate concurrent runs (<2h) NOT touched
- AC5: all changes have passing tests (this file)
"""

import json
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def workspace(tmp_path):
    """Create a fake workspace with pipeline runs."""
    project_dir = tmp_path / "Projects" / "TestProject" / ".artifacts" / "runs"
    project_dir.mkdir(parents=True)
    return tmp_path


def _create_run(workspace, project, run_id, status="running", hours_ago=0, stages=None):
    """Helper: create a run.json with given status and age."""
    run_dir = workspace / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    created = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    updated = created

    run_data = {
        "id": run_id,
        "project": project,
        "requirement": f"Test requirement for {run_id}",
        "profile": "full",
        "status": status,
        "stages": stages or [],
        "created_at": created.isoformat(),
        "updated_at": updated.isoformat(),
    }
    run_file = run_dir / "run.json"
    run_file.write_text(json.dumps(run_data, indent=2))
    return run_file


def _read_run(run_file):
    """Read and parse a run.json."""
    return json.loads(run_file.read_text())


class TestAutoAbandonOnNewRun:
    """AC1: When a new run starts, stale same-project running runs get abandoned."""

    def test_stale_run_gets_abandoned(self, workspace):
        """A run that's been 'running' for >2h gets auto-abandoned."""
        from scripts.artifact_cli import _auto_abandon_stale_runs

        # Create a stale run (3 hours old)
        stale_file = _create_run(workspace, "TestProject", "run_old123", "running", hours_ago=3)

        # Trigger auto-abandon
        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            abandoned = _auto_abandon_stale_runs("TestProject", "run_new456")

        # Verify stale run was abandoned
        stale_data = _read_run(stale_file)
        assert stale_data["status"] == "abandoned"
        assert "superseded_by_run_new456" in stale_data.get("abandon_reason", "")
        assert abandoned == 1

    def test_fresh_run_not_touched(self, workspace):
        """AC4: A run that's <2h old is NOT abandoned."""
        from scripts.artifact_cli import _auto_abandon_stale_runs

        # Create a fresh run (30 min old)
        fresh_file = _create_run(workspace, "TestProject", "run_fresh", "running", hours_ago=0.5)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            abandoned = _auto_abandon_stale_runs("TestProject", "run_new456")

        # Fresh run should still be running
        fresh_data = _read_run(fresh_file)
        assert fresh_data["status"] == "running"
        assert abandoned == 0

    def test_different_project_not_touched(self, workspace):
        """AC4: Runs in other projects are never touched."""
        from scripts.artifact_cli import _auto_abandon_stale_runs

        # Create stale run in a DIFFERENT project
        _create_run(workspace, "OtherProject", "run_other", "running", hours_ago=5)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            abandoned = _auto_abandon_stale_runs("TestProject", "run_new456")

        assert abandoned == 0

    def test_completed_run_not_touched(self, workspace):
        """Only 'running' status gets abandoned — completed/failed stay."""
        from scripts.artifact_cli import _auto_abandon_stale_runs

        completed_file = _create_run(workspace, "TestProject", "run_done", "completed", hours_ago=10)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            abandoned = _auto_abandon_stale_runs("TestProject", "run_new456")

        completed_data = _read_run(completed_file)
        assert completed_data["status"] == "completed"
        assert abandoned == 0


class TestCleanupOrphans:
    """AC2: cleanup-orphans subcommand marks all >2h stale running runs."""

    def test_cleanup_marks_all_stale_across_projects(self, workspace):
        """Batch mode cleans up all projects."""
        from scripts.artifact_cli import cleanup_orphans

        # Stale runs across 2 projects
        stale1 = _create_run(workspace, "ProjectA", "run_a1", "running", hours_ago=5)
        stale2 = _create_run(workspace, "ProjectB", "run_b1", "running", hours_ago=3)
        # Fresh run should survive
        fresh = _create_run(workspace, "ProjectA", "run_a2", "running", hours_ago=1)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            result = cleanup_orphans(threshold_hours=2.0)

        assert result["abandoned_count"] == 2
        assert _read_run(stale1)["status"] == "abandoned"
        assert _read_run(stale2)["status"] == "abandoned"
        assert _read_run(fresh)["status"] == "running"

    def test_cleanup_adds_reason(self, workspace):
        """Abandoned runs get a reason field."""
        from scripts.artifact_cli import cleanup_orphans

        stale = _create_run(workspace, "ProjectA", "run_stale", "running", hours_ago=4)

        with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
            cleanup_orphans(threshold_hours=2.0)

        data = _read_run(stale)
        assert "stale_orphan" in data.get("abandon_reason", "")
        assert "abandoned_at" in data


class TestOneTimeCleanup:
    """AC3: Reclassify zero-stage failed + orphan running."""

    def test_zero_stage_failed_becomes_abandoned(self, workspace):
        """Failed runs with no stages = never executed = abandoned."""
        from scripts.pipeline_cleanup import reclassify_stale_runs

        # Create a "failed" run with zero stages (session crash)
        failed_file = _create_run(workspace, "TestProject", "run_crash", "failed", hours_ago=24, stages=[])

        # Pass workspace directly — no mock needed
        result = reclassify_stale_runs(workspace)

        data = _read_run(failed_file)
        assert data["status"] == "abandoned"
        assert "zero_stages_reclassified" in data.get("abandon_reason", "")
        assert result["reclassified_failed"] >= 1

    def test_failed_with_stages_not_touched(self, workspace):
        """Failed runs that actually executed stages stay 'failed'."""
        from scripts.pipeline_cleanup import reclassify_stale_runs

        # Create a "failed" run with actual stages
        stages = [{"stage": "evaluate", "status": "done"}]
        failed_file = _create_run(workspace, "TestProject", "run_real_fail", "failed", hours_ago=24, stages=stages)

        # Pass workspace directly — no mock needed
        result = reclassify_stale_runs(workspace)

        data = _read_run(failed_file)
        assert data["status"] == "failed"  # NOT changed
