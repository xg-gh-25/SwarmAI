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


def _publish_valid_deliver(workspace: Path, run_id: str) -> str:
    """Publish a REAL, loadable deliver artifact and return its artifact_id.

    The completion gate deep-loads the deliver artifact (pipeline_validator
    L1509): an artifact_id that points at no published artifact is a BLOCK —
    "could not be loaded". Fabricating `artifact_id="art_deliver"` without a
    real publish used to slip through because the completion gate's
    _INFRA_PHRASES filter masked the load error; that fail-open seam was
    removed (run_95fc9b6a). Tests must now publish a genuine deliver artifact.

    Fields are the minimal valid deliver artifact:
      - title + quality (schema required)
      - quality.push_ready=true, tests_pass, regressions=0 (push-ready gate)
      - adversarial_review{profile_tier, findings} (depth check, full/bugfix)
      - completion_audit.all_green + ac_verification.status (depth check)
    """
    data = {
        "title": "Test delivery",
        "quality": {"push_ready": True, "tests_pass": True, "regressions": 0,
                    "smoke_pass": True},
        "adversarial_review": {
            "spawned": True, "profile_tier": "full",
            "findings_total": 0, "findings_fixed": 0, "findings": [],
            "evidence": "Agent tool: adversarial reviewer",
        },
        "completion_audit": {"all_green": True},
        "ac_verification": {"status": "verified"},
        "meta_review": {"blind_spots": [], "verdict": "none found"},
        "convergence": {"iterations": 1, "final_status": "push-ready"},
    }
    result = _run_cli(
        workspace, "publish", "--project", "TestProject", "--run-id", run_id,
        "--type", "delivery", "--producer", "test", "--summary", "test deliver",
        "--stage", "deliver", "--data", json.dumps(data),
    )
    return result["artifact_id"]


def _complete_all_stages(workspace: Path, run_id: str, profile: str = "full",
                         skip_existing: bool = True):
    """Add all profile stages as completed so the run can be marked done.

    If skip_existing=True, won't overwrite stages already in run.json.
    Also creates REPORT.md (required by completion gate).
    The deliver stage gets a REAL published artifact (completion gate
    deep-loads it — a fabricated id is now a BLOCK, see _publish_valid_deliver).
    """
    from core.pipeline_profiles import PIPELINE_PROFILES
    profiles = PIPELINE_PROFILES
    # Check which stages already exist if skipping
    existing_stages: set = set()
    if skip_existing:
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        existing_stages = {
            s.get("stage", s.get("name", "?"))
            for s in state.get("stages", [])
            if s.get("status") in ("completed", "done")
        }

    for stg in profiles.get(profile, profiles["full"]):
        if stg in existing_stages:
            continue
        # deliver needs a genuinely loadable artifact (completion gate deep-loads it)
        if stg == "deliver":
            deliver_aid = _publish_valid_deliver(workspace, run_id)
        stage_json: dict = {
            "stage": stg, "status": "completed",
            "stage_doc_consumed": True,
            "token_cost": 2000,
            "artifact_id": (
                deliver_aid if stg == "deliver"
                else f"art_{stg}" if stg not in ("reflect", "goal_cycle")
                else None
            ),
            "decisions": [],
        }
        if stg == "reflect":
            stage_json["lessons"] = ["Substantive lesson learned from this pipeline run"]
        if stg == "goal_cycle":
            stage_json["adversarial_review"] = True
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--stage-json", json.dumps(stage_json))

    # Create REPORT.md (required by completion gate)
    run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
    report = run_dir / "REPORT.md"
    if not report.exists():
        report.write_text(
            "# Pipeline Report\n\nTest pipeline run completed successfully.\n"
            "Stages executed, decisions made, quality verified.\n" + "x" * 400
        )


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
        # Smoke-test that run-update WRITES a CHANGED status to disk. We use
        # `cancelled` deliberately: it is (a) NOT the create-time default
        # ("running", artifact_cli.py:843) so the assertion proves an actual
        # change — not a no-op self-write — and (b) NOT behind the confabulation
        # pause-guard (only `--status paused` is refused; artifact_cli.py:882,
        # commit 5a9522d0 / run_a822b3e8). The paused-door is covered exhaustively
        # by test_runupdate_paused_blocked_without_force + _allowed_with_force.
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--status", "cancelled")
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert state["status"] == "cancelled"

    def test_update_completed_sets_timestamp(self, workspace, run_id):
        # Completion gate: ALL profile stages must be done
        _complete_all_stages(workspace, run_id, "full")
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--status", "completed")
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert state["status"] == "completed"
        assert state["completed_at"] is not None

    def test_completion_gate_blocks_without_all_stages(self, workspace, run_id):
        """ALL profile stages must be done or explicitly skipped before completion."""
        result = _run_cli(workspace, "run-update",
                          "--project", "TestProject", "--run-id", run_id,
                          "--status", "completed")
        assert "error" in result
        assert "missing_stages" in result
        assert len(result["missing_stages"]) > 0
        # Run should still be running
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert state["status"] == "running"

    def test_completion_gate_allows_skipped_with_reason(self, workspace, run_id):
        """Skipped stages with explicit reason pass the gate."""
        from core.pipeline_profiles import PIPELINE_PROFILES
        full_stages = PIPELINE_PROFILES["full"]
        for stg in full_stages:
            if stg == "think":
                # Simulate explicit skip with reason (think is skippable)
                _run_cli(workspace, "run-update",
                         "--project", "TestProject", "--run-id", run_id,
                         "--stage-json", json.dumps({
                             "stage": stg, "status": "skipped",
                             "skip_reason": "User override: approach already known",
                             "stage_doc_consumed": True,
                             "decisions": [],
                         }))
            else:
                # deliver needs a real published artifact (completion gate deep-loads it)
                aid = (_publish_valid_deliver(workspace, run_id) if stg == "deliver"
                       else f"art_{stg}" if stg != "reflect" else None)
                _run_cli(workspace, "run-update",
                         "--project", "TestProject", "--run-id", run_id,
                         "--stage-json", json.dumps({
                             "stage": stg, "status": "completed",
                             "stage_doc_consumed": True,
                             "token_cost": 1000,
                             "artifact_id": aid,
                             "lessons": ["Skipped stages with explicit reason pass the completion gate"] if stg == "reflect" else None,
                             "decisions": [],
                         }))
        # Create REPORT.md (required by gate)
        run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
        (run_dir / "REPORT.md").write_text("# Report\n" + "x" * 600)
        result = _run_cli(workspace, "run-update",
                          "--project", "TestProject", "--run-id", run_id,
                          "--status", "completed")
        assert "error" not in result
        assert result.get("updated") is True

    def test_reflect_quality_gate_blocks_trivial_lessons(self, workspace, run_id):
        """REFLECT lessons must be >20 chars — no 'done' or '3 lessons captured'."""
        from core.pipeline_profiles import PIPELINE_PROFILES
        # Add all stages with proper fields
        for stg in PIPELINE_PROFILES["full"]:
            if stg == "reflect":
                continue
            _run_cli(workspace, "run-update",
                     "--project", "TestProject", "--run-id", run_id,
                     "--stage-json", json.dumps({
                         "stage": stg, "status": "completed",
                         "stage_doc_consumed": True,
                         "token_cost": 2000, "decisions": [],
                     }))
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--stage-json", json.dumps({
                     "stage": "reflect", "status": "completed",
                     "stage_doc_consumed": True,
                     "token_cost": 2000, "lessons": ["done", "3 captured"],
                     "decisions": [],
                 }))
        # Create REPORT.md so we hit the reflect gate (not the report gate)
        run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
        (run_dir / "REPORT.md").write_text("# Report\n" + "x" * 600)
        result = _run_cli(workspace, "run-update",
                          "--project", "TestProject", "--run-id", run_id,
                          "--status", "completed")
        assert "error" in result
        assert "substantive" in result["error"].lower()

    def test_add_stage_record(self, workspace, run_id):
        stage = json.dumps({
            "stage": "evaluate",
            "status": "completed",
            "stage_doc_consumed": True,
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
            "stage_doc_consumed": True,
            "artifact_id": None, "escalation_id": None,
            "started_at": "2026-03-24T10:00:00Z", "completed_at": None,
            "token_cost": 0, "retry_count": 0, "notes": None, "decisions": [],
        })
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--stage-json", stage_v1)

        stage_v2 = json.dumps({
            "stage": "build", "status": "completed",
            "stage_doc_consumed": True,
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
                         "stage_doc_consumed": True,
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
                     "artifact_id": "art_eval_001", "stage_doc_consumed": True, "escalation_id": None,
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
                     "artifact_id": "art_research_001", "stage_doc_consumed": True, "escalation_id": None,
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

        # Remaining stages (completion gate requires ALL profile stages)
        for stg in ["plan", "build", "review", "test", "deliver", "reflect"]:
            # deliver needs a real published artifact (completion gate deep-loads it)
            aid = (_publish_valid_deliver(workspace, run_id) if stg == "deliver"
                   else f"art_{stg}" if stg != "reflect" else None)
            _run_cli(workspace, "run-update", "--project", "TestProject",
                     "--run-id", run_id,
                     "--stage-json", json.dumps({
                         "stage": stg, "status": "completed",
                         "stage_doc_consumed": True,
                         "token_cost": 3000,
                         "artifact_id": aid,
                         "lessons": ["httpx built-in retry is simpler than tenacity"] if stg == "reflect" else None,
                         "decisions": [],
                     }))

        # Create REPORT.md (required by gate)
        run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
        (run_dir / "REPORT.md").write_text("# Report\n" + "x" * 600)

        # Complete
        _run_cli(workspace, "run-update", "--project", "TestProject",
                 "--run-id", run_id, "--status", "completed")

        # Verify final state
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_id)
        assert state["status"] == "completed"
        assert state["completed_at"] is not None
        assert len(state["stages"]) == 8  # all full-profile stages
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
                     "stage_doc_consumed": True,
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
                         "stage_doc_consumed": True,
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
        """Trivial profile skips plan — next stage after evaluate is think."""
        result = _run_cli(workspace, "run-create",
                          "--project", "TestProject",
                          "--requirement", "Trivial fix",
                          "--profile", "trivial")
        run_id = result["pipeline_id"]
        _run_cli(workspace, "run-update",
                 "--project", "TestProject", "--run-id", run_id,
                 "--stage-json", json.dumps({
                     "stage": "evaluate", "status": "completed",
                     "stage_doc_consumed": True,
                     "artifact_id": "art_e", "escalation_id": None,
                     "started_at": None, "completed_at": None,
                     "token_cost": 5000, "retry_count": 0,
                     "notes": None, "decisions": [],
                 }))
        budget = _run_cli(workspace, "run-budget",
                          "--project", "TestProject", "--run-id", run_id)
        assert budget["next_stage"] == "think"  # trivial: evaluate→think→build→...


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
                             "stage_doc_consumed": True,
                             "artifact_id": f"art_{stage_name}", "escalation_id": None,
                             "started_at": None, "completed_at": None,
                             "token_cost": cost, "retry_count": 0,
                             "notes": None, "decisions": [],
                         }))
            # Completion gate: all profile stages must be present
            # skip_existing=True preserves our custom token_cost values above
            _complete_all_stages(workspace, rid, "full", skip_existing=True)
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
                     "artifact_id": "art_eval", "stage_doc_consumed": True, "escalation_id": None,
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
        """Checkpoint should succeed even if todo DB doesn't exist (no Radar todo).

        Uses --force-checkpoint: this is a MECHANISM test (not a real decay event),
        and the reason 'Test without DB' has no true-trigger, so the confabulation
        guard (run_a822b3e8) would otherwise block it. Forcing is the correct,
        auditable way to checkpoint deliberately in a test.
        """
        # The workspace tmp_path won't have ~/.swarm-ai/data.db
        result = _run_cli(workspace, "run-checkpoint",
                          "--project", "TestProject",
                          "--run-id", run_with_stages,
                          "--stage", "plan",
                          "--reason", "Test without DB",
                          "--force-checkpoint")
        assert result["status"] == "paused"
        # radar_todo may be None or have an error — that's fine
        assert result["checkpoint_artifact"] is not None

    # ── Confabulation guard (run_a822b3e8): warning -> hard block ──

    def test_checkpoint_blocked_on_confabulation_reason(self, workspace, run_with_stages):
        """A fatigue/'fresh-context' reason at should_checkpoint=false is REFUSED
        (exit 2, run NOT paused). This is the exact bug from run_1e2e663b."""
        result = _run_cli(workspace, "run-checkpoint",
                          "--project", "TestProject",
                          "--run-id", run_with_stages,
                          "--stage", "build",
                          "--reason", "Fresh-context BUILD — clean attention, session long")
        assert result.get("blocked") is True
        assert "CHECKPOINT REFUSED" in result["error"]
        assert "measurement" in result
        # Run must NOT be paused — the block happens before any mutation.
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_with_stages)
        assert state["status"] != "paused"

    def test_checkpoint_blocked_when_no_trigger_and_budget_ok(self, workspace, run_with_stages):
        """No true-trigger + should_checkpoint=false (tiny consumed) -> blocked,
        even without a denylist word."""
        result = _run_cli(workspace, "run-checkpoint",
                          "--project", "TestProject",
                          "--run-id", run_with_stages,
                          "--stage", "build",
                          "--reason", "just pausing here")
        assert result.get("blocked") is True

    def test_checkpoint_allowed_on_true_trigger(self, workspace, run_with_stages):
        """A true-trigger reason (L2 block) checkpoints normally even at low budget."""
        result = _run_cli(workspace, "run-checkpoint",
                          "--project", "TestProject",
                          "--run-id", run_with_stages,
                          "--stage", "build",
                          "--reason", "L2 BLOCK: genuine judgment-class decision")
        assert result["status"] == "paused"

    def test_runupdate_paused_blocked_without_force(self, workspace, run_with_stages):
        """The SECOND pause door — run-update --status paused — honors the same
        guard (adversarial run_a822b3e8 finding F: COE10 dual-write class)."""
        result = _run_cli(workspace, "run-update",
                          "--project", "TestProject",
                          "--run-id", run_with_stages,
                          "--status", "paused")
        assert result.get("blocked") is True
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_with_stages)
        assert state["status"] != "paused"

    def test_runupdate_paused_allowed_with_force(self, workspace, run_with_stages):
        """run-update --status paused --force-checkpoint is allowed (deliberate)."""
        _run_cli(workspace, "run-update",
                 "--project", "TestProject",
                 "--run-id", run_with_stages,
                 "--status", "paused", "--force-checkpoint")
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_with_stages)
        assert state["status"] == "paused"

    def test_checkpoint_keyword_stuffing_blocked(self, workspace, run_with_stages):
        """A confabulation reason that merely CONTAINS a trigger substring
        ('blocked' / 'budget hygiene') no longer rides through (word-boundary +
        denylist precedence — adversarial finding B)."""
        result = _run_cli(workspace, "run-checkpoint",
                          "--project", "TestProject",
                          "--run-id", run_with_stages,
                          "--stage", "build",
                          "--reason", "I feel mentally blocked and need a fresh start")
        assert result.get("blocked") is True

    def test_checkpoint_force_overrides_block(self, workspace, run_with_stages):
        """--force-checkpoint bypasses the guard even on a confabulation reason,
        and the override is recorded as auditable."""
        result = _run_cli(workspace, "run-checkpoint",
                          "--project", "TestProject",
                          "--run-id", run_with_stages,
                          "--stage", "build",
                          "--reason", "fatigue — I feel this is getting long",
                          "--force-checkpoint")
        assert result["status"] == "paused"
        state = _run_cli(workspace, "run-get",
                         "--project", "TestProject", "--run-id", run_with_stages)
        assert state["checkpoint"]["forced"] is True


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
        # Completion gate: all stages must be present
        _complete_all_stages(workspace, r2["pipeline_id"], "full")
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
        # Pause r2 via the SANCTIONED path (run-checkpoint --force-checkpoint),
        # not a bare `run-update --status paused` — the latter is refused by the
        # confabulation guard on a fresh 0-stage run (should_checkpoint=false),
        # which would silently leave r2 running and mask the paused-count check.
        # This keeps real coverage of the running-vs-paused summary split.
        _run_cli(workspace, "run-checkpoint", "--project", "TestProject",
                 "--run-id", r2["pipeline_id"], "--stage", "think",
                 "--reason", "pause for summary-count coverage",
                 "--force-checkpoint")

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
                     "stage_doc_consumed": True,
                     "artifact_id": "art_e", "escalation_id": None,
                     "started_at": None, "completed_at": None,
                     "token_cost": 8000, "retry_count": 0,
                     "notes": "GO", "decisions": [],
                 }))
        _run_cli(workspace, "run-checkpoint", "--project", "TestProject",
                 "--run-id", rid, "--stage", "think",
                 "--reason", "Test checkpoint for resume",
                 "--force-checkpoint")  # mechanism fixture — guard would block otherwise
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


class TestLoadCompletedRunsAnalyticsStatus:
    """D8 (run_57929039): _load_completed_runs (calibration/history/analytics source)
    filtered raw status=='completed' only, so a run that DELIVERED (completed
    reflect/deliver stage) but was later stamped 'abandoned'/'paused' (superseded /
    crash-after-the-fact) was EXCLUDED from budget calibration — exactly the
    delivered-but-mislabeled runs _effective_analytics_status exists to recover. Now
    it buckets via _effective_analytics_status."""

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
        import sys as _s
        from pathlib import Path as _P
        _d = str(_P(__file__).resolve().parent.parent / "scripts")
        if _d not in _s.path:
            _s.path.insert(0, _d)
        import scripts.artifact_cli as cli
        from unittest.mock import patch
        with patch.object(cli, "_get_workspace", return_value=workspace):
            return cli._load_completed_runs("TestProject", limit=50)

    def test_delivered_but_abandoned_run_included(self, workspace):
        """A run stamped 'abandoned' but with a completed reflect stage IS counted."""
        self._write_run(workspace, "run_deliv_aband", "abandoned", self._delivered_stages())
        ids = [r["id"] for r in self._load(workspace)]
        assert "run_deliv_aband" in ids, \
            f"delivered-but-abandoned run must be included for calibration: {ids}"

    def test_plain_completed_still_included(self, workspace):
        self._write_run(workspace, "run_plain", "completed", self._delivered_stages())
        ids = [r["id"] for r in self._load(workspace)]
        assert "run_plain" in ids

    def test_genuinely_abandoned_empty_shell_excluded(self, workspace):
        """A truly-abandoned run with NO delivery marker stays excluded (not a
        completed run — must not pollute calibration)."""
        self._write_run(workspace, "run_empty", "abandoned",
                        [{"stage": "evaluate", "status": "completed", "token_cost": 5000}])
        ids = [r["id"] for r in self._load(workspace)]
        assert "run_empty" not in ids, \
            f"a non-delivered abandoned run must NOT be counted as completed: {ids}"
