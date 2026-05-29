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


class TestAutoRecordStage:
    """Regression: publish --stage must auto-record into run.json.

    Root cause: _append_stage_to_run referenced reg._workspace (does not
    exist — ArtifactRegistry has .workspace_root), raising AttributeError
    that was swallowed by a bare `except: pass` in cmd_publish. Result:
    publish returned a valid artifact_id but run.json stages stayed empty,
    silently breaking the completion gate.
    """

    def test_append_stage_to_run_uses_workspace_root(self, workspace):
        """_append_stage_to_run writes a stage record without AttributeError."""
        from core.artifact_registry import ArtifactRegistry
        from scripts.artifact_cli import _append_stage_to_run

        _create_run(workspace, "TestProject", "run_ar1", "running", stages=[])
        reg = ArtifactRegistry(workspace)

        _append_stage_to_run(
            "TestProject", "run_ar1",
            {"stage": "build", "status": "completed", "artifact_id": "art_x"},
            reg,
        )

        run_file = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_ar1" / "run.json"
        data = _read_run(run_file)
        assert [s["stage"] for s in data["stages"]] == ["build"]

    def test_append_stage_no_duplicate(self, workspace):
        """Re-appending the same stage is a no-op (idempotent)."""
        from core.artifact_registry import ArtifactRegistry
        from scripts.artifact_cli import _append_stage_to_run

        _create_run(workspace, "TestProject", "run_ar2", "running",
                    stages=[{"stage": "build", "status": "completed"}])
        reg = ArtifactRegistry(workspace)

        _append_stage_to_run(
            "TestProject", "run_ar2",
            {"stage": "build", "status": "completed", "artifact_id": "art_y"},
            reg,
        )
        run_file = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_ar2" / "run.json"
        data = _read_run(run_file)
        assert len([s for s in data["stages"] if s["stage"] == "build"]) == 1

    def test_auto_record_failure_is_not_silent(self, workspace, capsys, monkeypatch):
        """When auto-record raises, cmd_publish emits a stderr warning (not silent pass)."""
        import sys
        from pathlib import Path as _P
        # cmd_publish does `from pipeline_validator import ...` assuming its own
        # dir (backend/scripts) is on sys.path — true when run as a script,
        # not when imported as scripts.artifact_cli in pytest. Mirror the CLI.
        _scripts_dir = str(_P(__file__).resolve().parent.parent / "scripts")
        if _scripts_dir not in sys.path:
            sys.path.insert(0, _scripts_dir)
        import scripts.artifact_cli as cli
        from core.artifact_registry import ArtifactRegistry

        _create_run(workspace, "TestProject", "run_ar3", "running", stages=[])
        reg = ArtifactRegistry(workspace)

        # Force the auto-record helper to raise.
        def _boom(*a, **k):
            raise RuntimeError("simulated append failure")
        monkeypatch.setattr(cli, "_append_stage_to_run", _boom)

        # This test isolates the auto-record failure path. Schema validation is
        # a separate concern (tested elsewhere), so stub it to pass — we need
        # execution to reach the auto-record block, not to re-test the schema.
        import pipeline_validator
        monkeypatch.setattr(pipeline_validator, "validate_artifact_data",
                            lambda *a, **k: [])

        class _Args:
            project = "TestProject"
            type = "changeset"
            data = '{"branch":"x","commits":["abc1234"],"files_changed":["f.py"]}'
            producer = "s_autonomous-pipeline"
            summary = "test"
            topic = ""
            stage = "build"
            run_id = "run_ar3"

        try:
            cli.cmd_publish(_Args(), reg)
        except SystemExit:
            pass
        captured = capsys.readouterr()
        # The failure must surface SOMEWHERE visible — not be swallowed silently.
        assert "auto-record" in captured.err.lower() or "simulated append failure" in captured.err.lower()


class TestAdvanceDriftGuard:
    """AC4: advancing past a completed stage with no artifact_id warns."""

    def test_advance_warns_on_missing_artifact(self, workspace, capsys, monkeypatch):
        """A completed stage lacking artifact_id (likely silent publish failure)
        triggers a stderr warning when advancing."""
        import scripts.artifact_cli as cli

        # build stage marked completed but NO artifact_id = silent publish failure
        _create_run(workspace, "TestProject", "run_drift", "running",
                    stages=[{"stage": "build", "status": "completed"}])
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        # Stub the validator subprocess so the test isolates the drift-guard.
        import subprocess as _sp
        monkeypatch.setattr(_sp, "run",
                            lambda *a, **k: type("R", (), {"stdout": '{"valid": true, "warnings": []}', "returncode": 0})())

        cli._auto_validate_before_advance("TestProject", "test")
        captured = capsys.readouterr()
        assert "no artifact_id" in captured.err or "failed silently" in captured.err

    def test_advance_no_warn_for_reflect(self, workspace, capsys, monkeypatch):
        """reflect legitimately has no artifact — no drift warning."""
        import scripts.artifact_cli as cli

        _create_run(workspace, "TestProject", "run_reflect", "running",
                    stages=[{"stage": "reflect", "status": "completed"}])
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        import subprocess as _sp
        monkeypatch.setattr(_sp, "run",
                            lambda *a, **k: type("R", (), {"stdout": '{"valid": true, "warnings": []}', "returncode": 0})())

        cli._auto_validate_before_advance("TestProject", "complete")
        captured = capsys.readouterr()
        assert "no artifact_id" not in captured.err
