"""Tests for pipeline run management in artifact_cli.

Tests cover:
- v1: Creating pipeline runs (run-create)
- v1: Updating pipeline run state (run-update): status, stages, taste decisions, profile
- v1: Reading pipeline runs (run-get): single run, list all
- v1: Edge cases: missing run, duplicate stage update, completed status
- v2: Budget tracking (run-budget): estimates, consumption, checkpoint recommendation
- v2: Historical calibration (run-history): avg token costs from past runs
- v2: Checkpoint (run-checkpoint): atomic pause + checkpoint artifact
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with a project directory."""
    project_dir = tmp_path / "Projects" / "TestProject" / ".artifacts"
    project_dir.mkdir(parents=True)
    return tmp_path


def _run_cli(workspace: Path, *args: str) -> dict:
    """Run artifact_cli command and return parsed JSON output."""
    cli_path = Path(__file__).resolve().parent.parent / "scripts" / "artifact_cli.py"
    # SWARM_TODO_DB isolates checkpoint todos to a temp DB — prevents
    # test runs from polluting the production ~/.swarm-ai/data.db
    todo_db = workspace / ".test-todos.db"
    env = {
        "SWARM_WORKSPACE": str(workspace),
        "SWARM_TODO_DB": str(todo_db),
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


class TestRunCreate:
    def test_creates_run_file(self, workspace):
        result = _run_cli(workspace, "run-create",
                          "--project", "TestProject",
                          "--requirement", "Add retry logic")
        assert "pipeline_id" in result
        assert result["project"] == "TestProject"
        assert result["pipeline_id"].startswith("run_")

        # Verify file on disk
        run_file = Path(result["file"])
        assert run_file.exists()
        state = json.loads(run_file.read_text())
        assert state["requirement"] == "Add retry logic"
        assert state["status"] == "running"
        assert state["stages"] == []
        assert state["taste_decisions"] == []

    def test_creates_run_with_profile(self, workspace):
        result = _run_cli(workspace, "run-create",
                          "--project", "TestProject",
                          "--requirement", "Fix typo",
                          "--profile", "trivial")
        run_file = Path(result["file"])
        state = json.loads(run_file.read_text())
        assert state["profile"] == "trivial"

    def test_creates_run_without_profile(self, workspace):
        result = _run_cli(workspace, "run-create",
                          "--project", "TestProject",
                          "--requirement", "Something")
        run_file = Path(result["file"])
        state = json.loads(run_file.read_text())
        assert state["profile"] is None

    def test_unique_run_ids(self, workspace):
        r1 = _run_cli(workspace, "run-create", "--project", "TestProject",
                       "--requirement", "Task 1")
        r2 = _run_cli(workspace, "run-create", "--project", "TestProject",
                       "--requirement", "Task 2")
        assert r1["pipeline_id"] != r2["pipeline_id"]


class TestRunUpdate:
    @pytest.fixture
    def run_id(self, workspace):
        result = _run_cli(workspace, "run-create",
                          "--project", "TestProject",
                          "--requirement", "Test requirement")
        return result["pipeline_id"]

    def test_update_status(self, workspace, run_id):
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--status", "paused")
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert state["status"] == "paused"

    def test_update_completed_sets_timestamp(self, workspace, run_id):
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--status", "completed")
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert state["status"] == "completed"
        assert state["completed_at"] is not None

    def test_add_stage_record(self, workspace, run_id):
        stage = json.dumps({
            "stage": "evaluate",
            "status": "completed",
            "artifact_id": "art_abc123",
            "escalation_id": None,
            "started_at": "2026-03-24T10:00:00Z",
            "completed_at": "2026-03-24T10:01:00Z",
            "token_cost": 8000,
            "retry_count": 0,
            "notes": "GO: ROI 4.1",
            "decisions": [],
        })
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--stage-json", stage)

        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert len(state["stages"]) == 1
        assert state["stages"][0]["stage"] == "evaluate"
        assert state["stages"][0]["artifact_id"] == "art_abc123"

    def test_update_existing_stage(self, workspace, run_id):
        """Updating a stage with the same name replaces it (retry scenario)."""
        stage_v1 = json.dumps({
            "stage": "build", "status": "running",
            "artifact_id": None, "escalation_id": None,
            "started_at": "2026-03-24T10:00:00Z", "completed_at": None,
            "token_cost": 0, "retry_count": 0, "notes": None, "decisions": [],
        })
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--stage-json", stage_v1)

        stage_v2 = json.dumps({
            "stage": "build", "status": "completed",
            "artifact_id": "art_xyz789", "escalation_id": None,
            "started_at": "2026-03-24T10:00:00Z", "completed_at": "2026-03-24T10:05:00Z",
            "token_cost": 55000, "retry_count": 1, "notes": "Built with retry", "decisions": [],
        })
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--stage-json", stage_v2)

        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert len(state["stages"]) == 1  # replaced, not appended
        assert state["stages"][0]["status"] == "completed"
        assert state["stages"][0]["retry_count"] == 1

    def test_add_taste_decision(self, workspace, run_id):
        decision = json.dumps({
            "stage": "think",
            "description": "Chose approach A over B",
            "classification": "taste",
            "reasoning": "Simpler but less flexible",
        })
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--taste-decision", decision)

        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert len(state["taste_decisions"]) == 1
        assert state["taste_decisions"][0]["classification"] == "taste"

    def test_multiple_taste_decisions_accumulate(self, workspace, run_id):
        for i in range(3):
            _run_cli(workspace, "run-update",
                     "--project", "TestProject", "--run-id", run_id,
                     "--taste-decision", json.dumps({
                         "stage": f"stage_{i}",
                         "description": f"Decision {i}",
                         "classification": "taste",
                         "reasoning": f"Reason {i}",
                     }))

        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert len(state["taste_decisions"]) == 3

    def test_update_profile(self, workspace, run_id):
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--profile", "bugfix")
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert state["profile"] == "bugfix"

    def test_update_nonexistent_run_fails(self, workspace):
        result = subprocess.run(
            [sys.executable,
             str(Path(__file__).resolve().parent.parent / "scripts" / "artifact_cli.py"),
             "run-update", "--project", "TestProject",
             "--run-id", "run_nonexistent", "--status", "paused"],
            capture_output=True, text=True,
            env={
                "SWARM_WORKSPACE": str(workspace),
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
            },
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 1


class TestRunGet:
    def test_get_specific_run(self, workspace):
        created = _run_cli(workspace, "run-create",
                           "--project", "TestProject",
                           "--requirement", "Build feature X")
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject",
                         "--run-id", created["pipeline_id"])
        assert state["requirement"] == "Build feature X"

    def test_list_all_runs(self, workspace):
        _run_cli(workspace, "run-create", "--project", "TestProject",
                 "--requirement", "Task A")
        _run_cli(workspace, "run-create", "--project", "TestProject",
                 "--requirement", "Task B")

        listing = _run_cli(workspace, "run-get", "--project", "TestProject")
        assert listing["count"] == 2
        assert len(listing["runs"]) == 2

    def test_list_empty_project(self, workspace):
        listing = _run_cli(workspace, "run-get", "--project", "TestProject")
        assert listing["count"] == 0

    def test_list_shows_completed_stage_count(self, workspace):
        created = _run_cli(workspace, "run-create",
                           "--project", "TestProject",
                           "--requirement", "Feature Y")
        run_id = created["pipeline_id"]

        # Add 2 completed stages
        for stage_name in ["evaluate", "think"]:
            _run_cli(workspace, "run-update",
                     "--project", "TestProject", "--run-id", run_id,
                     "--stage-json", json.dumps({
                         "stage": stage_name, "status": "completed",
                         "artifact_id": f"art_{stage_name}", "escalation_id": None,
                         "started_at": None, "completed_at": None,
                         "token_cost": 0, "retry_count": 0,
                         "notes": None, "decisions": [],
                     }))

        listing = _run_cli(workspace, "run-get", "--project", "TestProject")
        assert listing["runs"][0]["stages_completed"] == 2


class TestPipelineRunIntegration:
    """End-to-end: create, update stages, add decisions, complete."""

    def test_full_pipeline_lifecycle(self, workspace):
        # Create
        created = _run_cli(workspace, "run-create",
                           "--project", "TestProject",
                           "--requirement", "Add payment retry",
                           "--profile", "full")
        run_id = created["pipeline_id"]

        # Evaluate stage
        _run_cli(workspace, "run-update", "--project", "TestProject",
                 "--run-id", run_id,
                 "--stage-json", json.dumps({
                     "stage": "evaluate", "status": "completed",
                     "artifact_id": "art_eval_001", "escalation_id": None,
                     "started_at": "2026-03-24T10:00:00Z",
                     "completed_at": "2026-03-24T10:01:00Z",
                     "token_cost": 8500, "retry_count": 0,
                     "notes": "GO: ROI 4.2, scope: standard",
                     "decisions": [{"description": "GO based on ROI",
                                    "classification": "mechanical",
                                    "reasoning": "ROI 4.2 > threshold 3.5"}],
                 }))

        # Think stage with taste decision
        _run_cli(workspace, "run-update", "--project", "TestProject",
                 "--run-id", run_id,
                 "--stage-json", json.dumps({
                     "stage": "think", "status": "completed",
                     "artifact_id": "art_research_001", "escalation_id": None,
                     "started_at": "2026-03-24T10:01:00Z",
                     "completed_at": "2026-03-24T10:03:00Z",
                     "token_cost": 35000, "retry_count": 0,
                     "notes": "3 alternatives. Recommending: httpx built-in",
                     "decisions": [],
                 }),
                 "--taste-decision", json.dumps({
                     "stage": "think",
                     "description": "Chose httpx built-in over tenacity",
                     "classification": "taste",
                     "reasoning": "Fewer deps, simpler, matches codebase",
                 }))

        # Complete
        _run_cli(workspace, "run-update", "--project", "TestProject",
                 "--run-id", run_id, "--status", "completed")

        # Verify final state
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert state["status"] == "completed"
        assert state["completed_at"] is not None
        assert len(state["stages"]) == 2
        assert len(state["taste_decisions"]) == 1
        assert state["stages"][0]["decisions"][0]["classification"] == "mechanical"


# ── v2 Tests: Budget, History, Checkpoint ────────────────────────────


class TestRunBudget:
    @pytest.fixture
    def run_id(self, workspace):
        result = _run_cli(workspace, "run-create",
                          "--project", "TestProject",
                          "--requirement", "Budget test",
                          "--profile", "full")
        return result["pipeline_id"]

    def test_new_run_has_budget(self, workspace, run_id):
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert "budget" in state
        assert state["budget"]["session_total"] == 800_000
        assert state["budget"]["remaining"] == 800_000
        assert state["budget"]["consumed"] == 0
        assert "stage_estimates" in state["budget"]
        assert state["budget"]["stage_estimates"]["build"] == 60_000

    def test_budget_check_clean(self, workspace, run_id):
        result = _run_cli(workspace, "run-budget",
                          "--project", "TestProject", "--run-id", run_id)
        assert result["should_checkpoint"] is False
        assert result["next_stage"] == "evaluate"
        assert result["consumed"] == 0
        assert result["pct_consumed"] == 0.0

    def test_budget_tracks_consumption(self, workspace, run_id):
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--stage-json", json.dumps({
                     "stage": "evaluate", "status": "completed",
                     "artifact_id": "art_x", "escalation_id": None,
                     "started_at": None, "completed_at": None,
                     "token_cost": 12000, "retry_count": 0,
                     "notes": None, "decisions": [],
                 }))
        result = _run_cli(workspace, "run-budget",
                          "--project", "TestProject", "--run-id", run_id)
        assert result["consumed"] == 12000
        assert result["next_stage"] == "think"
        assert result["should_checkpoint"] is False

    def test_budget_recommends_checkpoint_when_low(self, workspace, run_id):
        """Simulate high consumption to trigger checkpoint recommendation."""
        # Add stages totaling >70% of budget (>560K)
        for stage_name, cost in [("evaluate", 100_000), ("think", 200_000), ("plan", 300_000)]:
            _run_cli(workspace, "run-update",
                     "--project", "TestProject", "--run-id", run_id,
                     "--stage-json", json.dumps({
                         "stage": stage_name, "status": "completed",
                         "artifact_id": f"art_{stage_name}", "escalation_id": None,
                         "started_at": None, "completed_at": None,
                         "token_cost": cost, "retry_count": 0,
                         "notes": None, "decisions": [],
                     }))

        result = _run_cli(workspace, "run-budget",
                          "--project", "TestProject", "--run-id", run_id)
        assert result["consumed"] == 600_000
        assert result["pct_consumed"] == 75.0
        assert result["should_checkpoint"] is True

    def test_budget_respects_profile(self, workspace):
        """Trivial profile skips think/plan — next stage after evaluate is build."""
        result = _run_cli(workspace, "run-create",
                          "--project", "TestProject",
                          "--requirement", "Trivial fix",
                          "--profile", "trivial")
        run_id = result["pipeline_id"]
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--stage-json", json.dumps({
                     "stage": "evaluate", "status": "completed",
                     "artifact_id": "art_e", "escalation_id": None,
                     "started_at": None, "completed_at": None,
                     "token_cost": 5000, "retry_count": 0,
                     "notes": None, "decisions": [],
                 }))
        budget = _run_cli(workspace, "run-budget",
                          "--project", "TestProject", "--run-id", run_id)
        assert budget["next_stage"] == "build"  # skips think, plan


class TestRunHistory:
    def test_empty_history(self, workspace):
        result = _run_cli(workspace, "run-history", "--project", "TestProject")
        assert result["calibration"] == "defaults"
        assert result["stage_averages"] == {}

    def test_history_from_completed_runs(self, workspace):
        """Create 2 completed runs, verify history aggregates token costs."""
        for req, eval_cost, think_cost in [("Run A", 8000, 35000), ("Run B", 12000, 45000)]:
            r = _run_cli(workspace, "run-create",
                         "--project", "TestProject", "--requirement", req)
            rid = r["pipeline_id"]
            for stage_name, cost in [("evaluate", eval_cost), ("think", think_cost)]:
                _run_cli(workspace, "run-update",
                         "--project", "TestProject", "--run-id", rid,
                         "--stage-json", json.dumps({
                             "stage": stage_name, "status": "completed",
                             "artifact_id": f"art_{stage_name}", "escalation_id": None,
                             "started_at": None, "completed_at": None,
                             "token_cost": cost, "retry_count": 0,
                             "notes": None, "decisions": [],
                         }))
            _run_cli(workspace, "run-update",
                     "--project", "TestProject", "--run-id", rid,
                     "--status", "completed")

        result = _run_cli(workspace, "run-history", "--project", "TestProject")
        assert result["calibration"] == "historical"
        assert "evaluate" in result["stage_averages"]
        assert "think" in result["stage_averages"]
        # Average of 8000 and 12000 = 10000
        assert result["stage_averages"]["evaluate"]["avg_tokens"] == 10000
        assert result["stage_averages"]["evaluate"]["samples"] == 2
        # Calibrated = avg * 1.2
        assert result["stage_averages"]["evaluate"]["calibrated_estimate"] == 12000


class TestRunCheckpoint:
    @pytest.fixture
    def run_with_stages(self, workspace):
        """Create a run with evaluate completed."""
        r = _run_cli(workspace, "run-create",
                     "--project", "TestProject",
                     "--requirement", "Checkpoint test feature")
        rid = r["pipeline_id"]
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", rid,
                 "--stage-json", json.dumps({
                     "stage": "evaluate", "status": "completed",
                     "artifact_id": "art_eval", "escalation_id": None,
                     "started_at": None, "completed_at": None,
                     "token_cost": 9000, "retry_count": 0,
                     "notes": "GO", "decisions": [],
                 }))
        return rid

    def test_checkpoint_pauses_run(self, workspace, run_with_stages):
        result = _run_cli(workspace, "run-checkpoint",
                          "--project", "TestProject",
                          "--run-id", run_with_stages,
                          "--stage", "think",
                          "--reason", "L2 BLOCK: ambiguous scope")
        assert result["status"] == "paused"
        assert result["next_stage"] == "think"

        # Verify run file is paused
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject",
                         "--run-id", run_with_stages)
        assert state["status"] == "paused"
        assert state["checkpoint"]["reason"] == "L2 BLOCK: ambiguous scope"
        assert state["checkpoint"]["completed_stages"] == ["evaluate"]

    def test_checkpoint_publishes_artifact(self, workspace, run_with_stages):
        result = _run_cli(workspace, "run-checkpoint",
                          "--project", "TestProject",
                          "--run-id", run_with_stages,
                          "--stage", "build",
                          "--reason", "Budget exhausted")
        assert result["checkpoint_artifact"] is not None
        assert result["checkpoint_artifact"].startswith("art_")

        # Verify artifact is discoverable
        artifacts = _run_cli(workspace, "discover",
                             "--project", "TestProject",
                             "--types", "checkpoint")
        assert artifacts["count"] >= 1

    def test_checkpoint_without_db_still_works(self, workspace, run_with_stages):
        """Checkpoint should succeed even if todo DB doesn't exist (no Radar todo)."""
        # The workspace tmp_path won't have ~/.swarm-ai/data.db
        result = _run_cli(workspace, "run-checkpoint",
                          "--project", "TestProject",
                          "--run-id", run_with_stages,
                          "--stage", "plan",
                          "--reason", "Test without DB")
        assert result["status"] == "paused"
        # radar_todo may be None or have an error — that's fine
        assert result["checkpoint_artifact"] is not None


# ── v3 Tests: Status, Resume, Multi-Project ─────────────────────────


class TestRunStatus:
    def test_empty_status(self, workspace):
        result = _run_cli(workspace, "run-status")
        assert result["count"] == 0
        assert result["summary"]["running"] == 0

    def test_status_shows_active_runs(self, workspace):
        _run_cli(workspace, "run-create", "--project", "TestProject",
                 "--requirement", "Active task", "--profile", "full")
        result = _run_cli(workspace, "run-status")
        assert result["count"] == 1
        assert result["pipelines"][0]["status"] == "running"
        assert result["pipelines"][0]["progress"] == "0/8"

    def test_status_active_only_filter(self, workspace):
        # Create running + completed runs
        r1 = _run_cli(workspace, "run-create", "--project", "TestProject",
                       "--requirement", "Running")
        r2 = _run_cli(workspace, "run-create", "--project", "TestProject",
                       "--requirement", "Done")
        _run_cli(workspace, "run-update", "--project", "TestProject",
                 "--run-id", r2["pipeline_id"], "--status", "completed")

        all_result = _run_cli(workspace, "run-status")
        active_result = _run_cli(workspace, "run-status", "--active-only")

        assert all_result["count"] == 2
        assert active_result["count"] == 1
        assert active_result["pipelines"][0]["status"] == "running"

    def test_status_multi_project(self, workspace):
        """Status spans all projects."""
        # Create second project
        proj2 = workspace / "Projects" / "OtherProject" / ".artifacts"
        proj2.mkdir(parents=True)

        _run_cli(workspace, "run-create", "--project", "TestProject",
                 "--requirement", "Task A")
        _run_cli(workspace, "run-create", "--project", "OtherProject",
                 "--requirement", "Task B")

        result = _run_cli(workspace, "run-status")
        assert result["count"] == 2
        projects = {p["project"] for p in result["pipelines"]}
        assert projects == {"TestProject", "OtherProject"}

    def test_status_summary_counts(self, workspace):
        r1 = _run_cli(workspace, "run-create", "--project", "TestProject",
                       "--requirement", "R1")
        r2 = _run_cli(workspace, "run-create", "--project", "TestProject",
                       "--requirement", "R2")
        _run_cli(workspace, "run-update", "--project", "TestProject",
                 "--run-id", r2["pipeline_id"], "--status", "paused")

        result = _run_cli(workspace, "run-status")
        assert result["summary"]["running"] == 1
        assert result["summary"]["paused"] == 1


class TestRunResume:
    @pytest.fixture
    def paused_run(self, workspace):
        r = _run_cli(workspace, "run-create", "--project", "TestProject",
                     "--requirement", "Resume test")
        rid = r["pipeline_id"]
        _run_cli(workspace, "run-update", "--project", "TestProject",
                 "--run-id", rid,
                 "--stage-json", json.dumps({
                     "stage": "evaluate", "status": "completed",
                     "artifact_id": "art_e", "escalation_id": None,
                     "started_at": None, "completed_at": None,
                     "token_cost": 8000, "retry_count": 0,
                     "notes": "GO", "decisions": [],
                 }))
        _run_cli(workspace, "run-checkpoint", "--project", "TestProject",
                 "--run-id", rid, "--stage", "think",
                 "--reason", "Test checkpoint for resume")
        return rid

    def test_resume_sets_running(self, workspace, paused_run):
        result = _run_cli(workspace, "run-resume",
                          "--project", "TestProject", "--run-id", paused_run)
        assert result["status"] == "running"
        assert result["resumed_from"] == "think"
        assert "evaluate" in result["completed_stages"]

    def test_resume_resets_budget(self, workspace, paused_run):
        result = _run_cli(workspace, "run-resume",
                          "--project", "TestProject", "--run-id", paused_run)
        assert result["budget"]["session_total"] == 800_000
        assert result["budget"]["remaining"] == 800_000

    def test_resume_non_paused_fails(self, workspace):
        r = _run_cli(workspace, "run-create", "--project", "TestProject",
                     "--requirement", "Not paused")
        import subprocess, sys
        cli_path = Path(__file__).resolve().parent.parent / "scripts" / "artifact_cli.py"
        proc = subprocess.run(
            [sys.executable, str(cli_path), "run-resume",
             "--project", "TestProject", "--run-id", r["pipeline_id"]],
            capture_output=True, text=True,
            env={"SWARM_WORKSPACE": str(workspace), "PATH": "/usr/bin:/bin",
                 "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert proc.returncode == 1

    def test_resume_marks_checkpoint_resolved(self, workspace, paused_run):
        _run_cli(workspace, "run-resume",
                 "--project", "TestProject", "--run-id", paused_run)
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", paused_run)
        assert state["checkpoint"]["resumed_at"] is not None
