"""Tests for pipeline_validator.py — 7 structural invariant checks.

Tests the validator against synthetic pipeline runs with known
good/bad data to verify all 7 checks fire correctly.

Key properties tested:
    - Stage order enforcement across all 5 profiles
    - Artifact existence (required for all stages except reflect)
    - Artifact schema (required vs recommended fields)
    - Decision logging (required for non-optional stages)
    - Budget recording (token_cost > 0)
    - Profile respect (stage must be in selected profile)
    - DDD cross-document consistency (non-goals vs approach, failed patterns)
    - Summary command validates all stages
    - Edge cases: missing run, missing stage record, corrupt data
"""

import json
import os
import sys
from pathlib import Path

import pytest

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline_validator import (
    DECISION_OPTIONAL_STAGES,
    NO_ARTIFACT_STAGES,
    STAGE_SCHEMAS,
    _check_artifact_exists,
    _check_budget_recorded,
    _check_decision_logged,
    _check_profile_respected,
    _check_stage_order,
    _parse_non_goals,
    _parse_failed_patterns,
    _compute_doc_checksum,
    check_ddd_consistency,
    check_ddd_staleness,
    validate,
)
from core.pipeline_profiles import get_profile_stages, PIPELINE_PROFILES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Create a temporary workspace with a project and pipeline run."""
    ws = tmp_path / "SwarmWS"
    project_dir = ws / "Projects" / "TestProject" / ".artifacts"
    runs_dir = project_dir / "runs" / "run_test1"
    runs_dir.mkdir(parents=True)
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    return ws


def _make_run(runs_dir: Path, run_id: str = "run_test1", profile: str = "full",
              stages: list | None = None, status: str = "running") -> dict:
    """Create a run.json file and return the run dict."""
    run = {
        "id": run_id,
        "project": "TestProject",
        "requirement": "Test requirement",
        "profile": profile,
        "status": status,
        "stages": stages or [],
        "taste_decisions": [],
        "budget": {"session_total": 800000, "consumed": 0, "remaining": 800000,
                   "stage_estimates": {}, "calibration_source": "defaults"},
        "created_at": "2026-03-24T00:00:00Z",
        "updated_at": "2026-03-24T00:00:00Z",
        "completed_at": None,
    }
    run_file = runs_dir / "run.json"
    run_file.parent.mkdir(parents=True, exist_ok=True)
    run_file.write_text(json.dumps(run))
    return run


def _make_artifact(artifacts_dir: Path, run_id: str, artifact_id: str,
                   artifact_type: str, data: dict) -> None:
    """Create an artifact file and register in manifest."""
    # Write artifact data file in runs/<run_id>/
    run_dir = artifacts_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{artifact_type}-20260324.json"
    (run_dir / filename).write_text(json.dumps(data))

    # Update manifest
    manifest_file = artifacts_dir / "manifest.json"
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text())
    else:
        manifest = {"artifacts": [], "pipeline_state": "evaluate"}

    manifest["artifacts"].append({
        "id": artifact_id,
        "type": artifact_type,
        "file": f"runs/{run_id}/{filename}",
        "producer": "test",
        "summary": "test artifact",
        "created_at": "2026-03-24T00:00:00Z",
        "run_id": run_id,
    })
    manifest_file.write_text(json.dumps(manifest))


_SENTINEL = object()

def _stage_record(stage: str, status: str = "completed",
                  artifact_id: str | None = "art_test",
                  token_cost: int = 5000,
                  decisions: list | object = _SENTINEL) -> dict:
    """Build a stage record dict."""
    if decisions is _SENTINEL:
        decisions = [
            {"description": "test decision", "classification": "mechanical",
             "reasoning": "test"}
        ]
    return {
        "stage": stage,
        "status": status,
        "artifact_id": artifact_id,
        "started_at": "2026-03-24T00:00:00Z",
        "completed_at": "2026-03-24T00:01:00Z",
        "token_cost": token_cost,
        "retry_count": 0,
        "notes": f"{stage} completed",
        "decisions": decisions,
    }


# ---------------------------------------------------------------------------
# Check 1: Stage Order
# ---------------------------------------------------------------------------

class TestStageOrder:
    def test_first_stage_always_valid(self):
        """First stage in any profile is always valid."""
        for profile_name, stages in PIPELINE_PROFILES.items():
            result = _check_stage_order(
                stages[0], profile_name,
                [_stage_record(stages[0])]
            )
            assert result is True, f"First stage '{stages[0]}' in '{profile_name}' should be valid"

    def test_second_stage_requires_first(self):
        """Second stage requires first to be completed."""
        stages_list = [
            _stage_record("evaluate", status="completed"),
            _stage_record("build", status="running"),
        ]
        # In trivial profile: evaluate -> build
        assert _check_stage_order("build", "trivial", stages_list) is True

    def test_skipped_stage_before_fails(self):
        """Skipping a required prior stage fails order check."""
        # In full profile: evaluate -> think -> plan -> build
        # Missing think and plan
        stages_list = [
            _stage_record("evaluate", status="completed"),
            _stage_record("build", status="running"),
        ]
        assert _check_stage_order("build", "full", stages_list) is False

    def test_skipped_status_counts_as_done(self):
        """Stages with status 'skipped' count as completed for order."""
        stages_list = [
            _stage_record("evaluate", status="completed"),
            _stage_record("think", status="skipped"),
            _stage_record("plan", status="skipped"),
            _stage_record("build", status="running"),
        ]
        assert _check_stage_order("build", "full", stages_list) is True

    def test_stage_not_in_profile(self):
        """Stage not in profile fails order check."""
        stages_list = [_stage_record("think")]
        # think is NOT in trivial profile
        assert _check_stage_order("think", "trivial", stages_list) is False


# ---------------------------------------------------------------------------
# Check 2: Artifact Exists
# ---------------------------------------------------------------------------

class TestArtifactExists:
    def test_with_artifact_id(self):
        assert _check_artifact_exists("build", {"artifact_id": "art_123"}) is True

    def test_missing_artifact_id(self):
        assert _check_artifact_exists("build", {"artifact_id": None}) is False

    def test_empty_artifact_id(self):
        assert _check_artifact_exists("build", {"artifact_id": ""}) is False

    def test_reflect_exempt(self):
        """reflect never needs an artifact."""
        assert _check_artifact_exists("reflect", {"artifact_id": None}) is True

    def test_all_non_reflect_require_artifact(self):
        """Every stage except reflect requires an artifact."""
        for stage in ["evaluate", "think", "plan", "build", "review", "test", "deliver"]:
            assert _check_artifact_exists(stage, {"artifact_id": None}) is False


# ---------------------------------------------------------------------------
# Check 3: Artifact Schema (tested via validate integration)
# ---------------------------------------------------------------------------

class TestArtifactSchema:
    def test_evaluate_required_fields(self, workspace):
        """Evaluate artifact must have recommendation and scope."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "trivial"})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_evaluate_missing_required(self, workspace):
        """Missing required field produces BLOCK error."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        # Missing 'scope'
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO"})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is False
        assert any("scope" in e for e in result["errors"])

    def test_missing_recommended_is_warning(self, workspace):
        """Missing recommended field produces warning, not error."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        # Has required fields but missing recommended 'acceptance_criteria'
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "trivial"})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is True  # Still valid
        assert any("acceptance_criteria" in w for w in result["warnings"])

    def test_all_stages_have_schemas(self):
        """Every non-reflect stage has a schema defined."""
        for stage in ["evaluate", "think", "plan", "build", "review", "test", "deliver"]:
            assert stage in STAGE_SCHEMAS, f"Missing schema for {stage}"


# ---------------------------------------------------------------------------
# Check 4: Decision Logged
# ---------------------------------------------------------------------------

class TestDecisionLogged:
    def test_with_decisions(self):
        record = _stage_record("build", decisions=[
            {"description": "x", "classification": "mechanical", "reasoning": "y"}
        ])
        assert _check_decision_logged("build", record) is True

    def test_no_decisions(self):
        record = _stage_record("build", decisions=[])
        assert _check_decision_logged("build", record) is False

    def test_reflect_optional(self):
        """Reflect doesn't require decisions."""
        record = _stage_record("reflect", decisions=[])
        assert _check_decision_logged("reflect", record) is True

    def test_deliver_optional(self):
        """Deliver doesn't require decisions."""
        record = _stage_record("deliver", decisions=[])
        assert _check_decision_logged("deliver", record) is True

    def test_optional_stages_match_constant(self):
        """DECISION_OPTIONAL_STAGES contains exactly reflect and deliver."""
        assert DECISION_OPTIONAL_STAGES == {"reflect", "deliver"}


# ---------------------------------------------------------------------------
# Check 5: Budget Recorded
# ---------------------------------------------------------------------------

class TestBudgetRecorded:
    def test_positive_cost(self):
        assert _check_budget_recorded({"token_cost": 5000}) is True

    def test_zero_cost(self):
        assert _check_budget_recorded({"token_cost": 0}) is False

    def test_missing_cost(self):
        assert _check_budget_recorded({}) is False

    def test_negative_cost(self):
        """Negative cost is technically > 0 check — this is False."""
        assert _check_budget_recorded({"token_cost": -1}) is False


# ---------------------------------------------------------------------------
# Check 6: Profile Respected
# ---------------------------------------------------------------------------

class TestProfileRespected:
    def test_stage_in_profile(self):
        assert _check_profile_respected("evaluate", "full") is True
        assert _check_profile_respected("build", "trivial") is True

    def test_stage_not_in_profile(self):
        assert _check_profile_respected("think", "trivial") is False
        assert _check_profile_respected("build", "research") is False
        assert _check_profile_respected("test", "docs") is False

    def test_all_profiles_include_evaluate(self):
        """Evaluate is in every profile."""
        for profile in PIPELINE_PROFILES:
            assert _check_profile_respected("evaluate", profile) is True

    def test_all_profiles_include_reflect(self):
        """Reflect is in every profile."""
        for profile in PIPELINE_PROFILES:
            assert _check_profile_respected("reflect", profile) is True


# ---------------------------------------------------------------------------
# Integration: validate() full pipeline
# ---------------------------------------------------------------------------

class TestValidateIntegration:
    def test_missing_run(self, workspace):
        """Non-existent run returns error."""
        result = validate("TestProject", "run_nonexistent", "evaluate")
        assert result["valid"] is False
        assert "not found" in result["errors"][0]

    def test_missing_stage_record(self, workspace):
        """Stage not in run's stages list returns error."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_run(runs_dir, stages=[_stage_record("evaluate")])

        result = validate("TestProject", "run_test1", "build")
        assert result["valid"] is False
        assert "No stage record" in result["errors"][0]

    def test_perfect_stage(self, workspace):
        """A well-formed stage passes all 7 checks."""
        # Create DDD docs so check 7 doesn't warn about missing docs
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("# Test\n\n## Non-Goals\n\n- **Not X** -- skip.\n")
        (project_dir / "TECH.md").write_text("# Test\n\n## Architecture\n\nDesktop app.\n")
        (project_dir / "IMPROVEMENT.md").write_text(
            "# Test\n\n## What Worked\n\n- OK\n\n## What Failed\n\n- **Retry logic was too aggressive** -- caused cascading failures\n"
        )
        (project_dir / "PROJECT.md").write_text("# Test\n")

        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard",
                        "acceptance_criteria": ["a"], "scores": {"s": 5}})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is True
        assert result["checks_passed"] == 8
        assert result["checks_total"] == 8
        assert len(result["errors"]) == 0
        assert len(result["warnings"]) == 0

    def test_reflect_passes_with_nothing(self, workspace):
        """Reflect stage passes with no artifact, no decisions, any token_cost."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        full_stages = [_stage_record(s) for s in get_profile_stages("full")[:-1]]
        full_stages.append(_stage_record("reflect", artifact_id=None, decisions=[]))
        _make_run(runs_dir, stages=full_stages)

        result = validate("TestProject", "run_test1", "reflect")
        assert result["valid"] is True
        assert result["checks_passed"] == 8

    def test_warnings_dont_block(self, workspace):
        """Warnings don't make valid=false, and checks_passed stays at 7."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard"})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval", token_cost=0, decisions=[]),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is True  # No BLOCK errors
        assert result["checks_passed"] == 8  # Warnings don't reduce count
        assert len(result["warnings"]) >= 2  # Missing decisions + zero budget

    def test_multiple_errors_accumulate(self, workspace):
        """Multiple violations all appear in errors list."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        # think stage in trivial profile (violation) + no artifact (violation)
        _make_run(runs_dir, profile="trivial", stages=[
            _stage_record("think", artifact_id=None),
        ])

        result = validate("TestProject", "run_test1", "think")
        assert result["valid"] is False
        assert len(result["errors"]) >= 2  # Profile + artifact


# ---------------------------------------------------------------------------
# Summary command integration
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary_validates_all_stages(self, workspace):
        """Summary validates all completed/running stages."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard"})
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       {"files_changed": ["a.py"]})

        _make_run(runs_dir, profile="trivial", stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
            _stage_record("build", artifact_id="art_build"),
        ])

        # Import and call the summary logic directly
        from scripts.pipeline_validator import _load_run
        run = _load_run("TestProject", "run_test1")
        assert run is not None

        results = []
        for stage_rec in run["stages"]:
            if stage_rec["status"] in ("completed", "running"):
                r = validate("TestProject", "run_test1", stage_rec["stage"])
                results.append(r)

        assert len(results) == 2
        assert all(r["valid"] for r in results)


# ---------------------------------------------------------------------------
# Check 7: DDD Cross-Document Consistency
# ---------------------------------------------------------------------------

class TestParseNonGoals:
    def test_extracts_bold_keywords(self):
        text = """## Non-Goals

- **Not a cloud SaaS** -- Desktop-first, local-first.
- **Not a general chatbot** -- Opinionated.
"""
        goals = _parse_non_goals(text)
        assert "not a cloud saas" in goals
        assert "not a general chatbot" in goals

    def test_empty_section(self):
        text = """## Non-Goals

## Next Section
"""
        assert _parse_non_goals(text) == []

    def test_no_section(self):
        text = "# Product\n\nSome content without non-goals."
        assert _parse_non_goals(text) == []

    def test_non_bold_bullets(self):
        text = """## Non-Goals

- We don't do cloud hosting
- No mobile app planned
"""
        goals = _parse_non_goals(text)
        assert len(goals) == 2
        assert "we don't do cloud hosting" in goals


class TestParseFailedPatterns:
    def test_extracts_bold_patterns(self):
        text = """## What Failed

- **Big-bang refactor of 5,000+ line module** -- caused 15+ bugs
- **Memory pipeline trusting its own output** -- stale snapshots
"""
        patterns = _parse_failed_patterns(text)
        assert len(patterns) == 2
        assert "big-bang refactor of 5,000+ line module" in patterns

    def test_empty_section(self):
        text = """## What Failed

## Known Issues
"""
        assert _parse_failed_patterns(text) == []

    def test_no_section(self):
        text = "# Improvement\n\nNo failures here."
        assert _parse_failed_patterns(text) == []


class TestDocChecksum:
    def test_deterministic(self):
        text = "Hello World"
        assert _compute_doc_checksum(text) == _compute_doc_checksum(text)

    def test_whitespace_insensitive(self):
        assert _compute_doc_checksum("Hello  World") == _compute_doc_checksum("Hello World")
        assert _compute_doc_checksum("Hello\n\nWorld") == _compute_doc_checksum("Hello World")

    def test_different_content(self):
        assert _compute_doc_checksum("Hello") != _compute_doc_checksum("World")


class TestDDDConsistency:
    def test_no_project_dir(self, workspace):
        """Missing project returns warning, not error."""
        result = check_ddd_consistency("NonExistentProject")
        assert len(result["warnings"]) >= 1
        assert "No DDD documents" in result["warnings"][0]

    def test_complete_project_no_conflicts(self, workspace):
        """Well-formed DDD docs with no conflicts produce no warnings."""
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("""# Test -- Product Context

## Non-Goals

- **Not a mobile app** -- Desktop only.
""")
        (project_dir / "TECH.md").write_text("""# Test -- Technical Context

## Architecture

Desktop app with Tauri shell and Python backend.
""")
        (project_dir / "IMPROVEMENT.md").write_text("""# Test -- Lessons

## What Worked

- Good stuff

## What Failed

- **Retry logic was too aggressive** -- caused cascading failures
""")
        (project_dir / "PROJECT.md").write_text("# Test -- Project Context\n")

        result = check_ddd_consistency("TestProject")
        assert len(result["warnings"]) == 0
        assert len(result["checksums"]) == 4
        assert len(result["non_goals"]) == 1
        assert len(result["failed_patterns"]) == 1

    def test_non_goal_conflict_detected(self, workspace):
        """Non-goal keyword appearing in TECH.md architecture triggers warning."""
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("""# Test

## Non-Goals

- **Not a cloud SaaS** -- Desktop only.
""")
        (project_dir / "TECH.md").write_text("""# Test

## Architecture

Cloud-native SaaS with microservices deployed on AWS.
""")

        result = check_ddd_consistency("TestProject")
        conflict_warnings = [w for w in result["warnings"] if "DDD conflict" in w]
        assert len(conflict_warnings) >= 1
        assert any("cloud" in w.lower() or "saas" in w.lower() for w in conflict_warnings)

    def test_non_goal_vs_context_text(self, workspace):
        """Non-goal keyword in pipeline context triggers warning."""
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("""# Test

## Non-Goals

- **Not a cloud SaaS** -- Desktop only.
""")
        (project_dir / "TECH.md").write_text("# Test\n\n## Architecture\n\nDesktop app.\n")

        result = check_ddd_consistency("TestProject", context_text="Deploy to cloud infrastructure")
        conflict_warnings = [w for w in result["warnings"] if "pipeline context" in w]
        assert len(conflict_warnings) >= 1

    def test_missing_docs_warned(self, workspace):
        """Missing DDD docs produce informational warning."""
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("# Test\n\n## Non-Goals\n\n- **Nothing** -- nope\n")

        result = check_ddd_consistency("TestProject")
        incomplete = [w for w in result["warnings"] if "DDD incomplete" in w]
        assert len(incomplete) == 1
        assert "TECH.md" in incomplete[0]

    def test_empty_improvement_warned(self, workspace):
        """Empty What Failed section produces note."""
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("# Test\n")
        (project_dir / "TECH.md").write_text("# Test\n")
        (project_dir / "IMPROVEMENT.md").write_text("""# Test

## What Worked

- Good stuff

## What Failed

## Known Issues
""")
        (project_dir / "PROJECT.md").write_text("# Test\n")

        result = check_ddd_consistency("TestProject")
        assert any("no 'What Failed' entries" in w for w in result["warnings"])

    def test_checksums_computed(self, workspace):
        """All present docs get checksums."""
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("# A\n")
        (project_dir / "TECH.md").write_text("# B\n")

        result = check_ddd_consistency("TestProject")
        assert "PRODUCT.md" in result["checksums"]
        assert "TECH.md" in result["checksums"]
        assert len(result["checksums"]["PRODUCT.md"]) == 12  # md5[:12]


class TestDDDInValidate:
    """Check 7 runs within validate() at evaluate stage."""

    def test_ddd_check_runs_at_evaluate(self, workspace):
        """DDD check runs at evaluate and adds warnings (not errors)."""
        # Setup project with a conflict
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("""# Test

## Non-Goals

- **Not a cloud SaaS** -- Desktop only.
""")
        (project_dir / "TECH.md").write_text("""# Test

## Architecture

Cloud SaaS deployment with Kubernetes.
""")

        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard"})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        # DDD warnings should be present but not block
        assert result["valid"] is True
        ddd_warnings = [w for w in result["warnings"] if "DDD" in w]
        assert len(ddd_warnings) >= 1

    def test_ddd_check_skipped_on_other_stages(self, workspace):
        """DDD check auto-passes on non-evaluate stages (no extra warnings)."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       {"files_changed": ["a.py"]})
        _make_run(runs_dir, profile="trivial", stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        ddd_warnings = [w for w in result["warnings"] if "DDD" in w]
        assert len(ddd_warnings) == 0

    def test_checks_total_is_7(self, workspace):
        """Verify checks_total is now 7."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard"})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        assert result["checks_total"] == 8
        assert result["checks_passed"] == 8


# ---------------------------------------------------------------------------
# DDD Staleness Detection
# ---------------------------------------------------------------------------

def _create_ddd_docs(project_dir: Path, product: str = "# Test\n",
                     tech: str = "# Test\n", improvement: str = "# Test\n",
                     project_ctx: str = "# Test\n") -> None:
    """Helper to create DDD docs in a project directory."""
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "PRODUCT.md").write_text(product)
    (project_dir / "TECH.md").write_text(tech)
    (project_dir / "IMPROVEMENT.md").write_text(improvement)
    (project_dir / "PROJECT.md").write_text(project_ctx)


def _make_completed_run(workspace: Path, run_id: str, ddd_checksums: dict | None = None,
                        status: str = "completed") -> None:
    """Create a completed run.json with optional ddd_checksums."""
    runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
    runs_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "id": run_id,
        "project": "TestProject",
        "requirement": "Test",
        "profile": "full",
        "status": status,
        "stages": [_stage_record("evaluate")],
        "taste_decisions": [],
        "budget": {"session_total": 800000, "consumed": 0, "remaining": 800000,
                   "stage_estimates": {}, "calibration_source": "defaults"},
        "created_at": "2026-03-24T00:00:00Z",
        "updated_at": "2026-03-24T00:00:00Z",
        "completed_at": "2026-03-24T01:00:00Z",
    }
    if ddd_checksums is not None:
        run["ddd_checksums"] = ddd_checksums
    (runs_dir / "run.json").write_text(json.dumps(run))


class TestDDDStaleness:
    def test_no_runs(self, workspace):
        """Empty project has no stale runs."""
        _create_ddd_docs(workspace / "Projects" / "TestProject")
        result = check_ddd_staleness("TestProject")
        assert result["stale_runs"] == []
        assert result["fresh_runs"] == []
        assert len(result["current_checksums"]) == 4

    def test_fresh_run(self, workspace):
        """Run with matching checksums is fresh."""
        project_dir = workspace / "Projects" / "TestProject"
        _create_ddd_docs(project_dir)

        # Get current checksums and store them in a run
        current = check_ddd_consistency("TestProject")
        _make_completed_run(workspace, "run_fresh", ddd_checksums=current["checksums"])

        result = check_ddd_staleness("TestProject")
        assert len(result["fresh_runs"]) == 1
        assert "run_fresh" in result["fresh_runs"]
        assert result["stale_runs"] == []

    def test_stale_run_detected(self, workspace):
        """Run with old checksums is detected as stale after doc change."""
        project_dir = workspace / "Projects" / "TestProject"
        _create_ddd_docs(project_dir)

        # Store old checksums
        old_checksums = check_ddd_consistency("TestProject")["checksums"]
        _make_completed_run(workspace, "run_old", ddd_checksums=old_checksums)

        # Now change PRODUCT.md
        (project_dir / "PRODUCT.md").write_text("# Test v2 -- updated priorities\n")

        result = check_ddd_staleness("TestProject")
        assert len(result["stale_runs"]) == 1
        assert result["stale_runs"][0]["run_id"] == "run_old"
        assert "PRODUCT.md" in result["stale_runs"][0]["stale_docs"]

    def test_multiple_stale_docs(self, workspace):
        """Multiple changed docs all reported."""
        project_dir = workspace / "Projects" / "TestProject"
        _create_ddd_docs(project_dir)
        old_checksums = check_ddd_consistency("TestProject")["checksums"]
        _make_completed_run(workspace, "run_multi", ddd_checksums=old_checksums)

        # Change two docs
        (project_dir / "PRODUCT.md").write_text("# Changed product\n")
        (project_dir / "TECH.md").write_text("# Changed tech\n")

        result = check_ddd_staleness("TestProject")
        stale = result["stale_runs"][0]
        assert "PRODUCT.md" in stale["stale_docs"]
        assert "TECH.md" in stale["stale_docs"]

    def test_untracked_run(self, workspace):
        """Run without ddd_checksums is reported as untracked."""
        _create_ddd_docs(workspace / "Projects" / "TestProject")
        _make_completed_run(workspace, "run_no_checksums", ddd_checksums=None)

        result = check_ddd_staleness("TestProject")
        assert "run_no_checksums" in result["untracked_runs"]
        assert result["stale_runs"] == []
        assert result["fresh_runs"] == []

    def test_running_runs_ignored(self, workspace):
        """Active (non-completed) runs are not checked for staleness."""
        _create_ddd_docs(workspace / "Projects" / "TestProject")
        old_checksums = check_ddd_consistency("TestProject")["checksums"]
        _make_completed_run(workspace, "run_active", ddd_checksums=old_checksums, status="running")

        # Change docs
        (workspace / "Projects" / "TestProject" / "PRODUCT.md").write_text("# Changed\n")

        result = check_ddd_staleness("TestProject")
        # Running run should NOT appear in stale_runs
        assert result["stale_runs"] == []
        assert result["fresh_runs"] == []

    def test_new_doc_added_makes_run_stale(self, workspace):
        """If a DDD doc is added after a run, that run is stale."""
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        # Start with only 2 docs
        (project_dir / "PRODUCT.md").write_text("# Test\n")
        (project_dir / "TECH.md").write_text("# Test\n")

        old_checksums = check_ddd_consistency("TestProject")["checksums"]
        _make_completed_run(workspace, "run_partial", ddd_checksums=old_checksums)

        # Now add IMPROVEMENT.md
        (project_dir / "IMPROVEMENT.md").write_text("# New lessons\n")

        result = check_ddd_staleness("TestProject")
        assert len(result["stale_runs"]) == 1
        assert "IMPROVEMENT.md" in result["stale_runs"][0]["stale_docs"]


class TestStalenessInValidate:
    """Staleness warnings appear in validate() at evaluate stage."""

    def test_staleness_warning_in_validate(self, workspace):
        """When prior run is stale, validate() adds a staleness warning."""
        project_dir = workspace / "Projects" / "TestProject"
        _create_ddd_docs(project_dir,
                         improvement="# T\n\n## What Worked\n\n- OK\n\n## What Failed\n\n- **Old pattern was bad** -- fix\n")

        # Create an old completed run with old checksums
        old_checksums = check_ddd_consistency("TestProject")["checksums"]
        _make_completed_run(workspace, "run_old", ddd_checksums=old_checksums)

        # Change PRODUCT.md
        (project_dir / "PRODUCT.md").write_text("# Test v2 -- new priorities\n")

        # Now create a new run being validated
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard"})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is True  # Staleness is WARN, not BLOCK
        staleness_warnings = [w for w in result["warnings"] if "staleness" in w.lower()]
        assert len(staleness_warnings) >= 1
        assert "PRODUCT.md" in staleness_warnings[0]

    def test_no_staleness_when_fresh(self, workspace):
        """No staleness warning when prior run has matching checksums."""
        project_dir = workspace / "Projects" / "TestProject"
        _create_ddd_docs(project_dir,
                         improvement="# T\n\n## What Worked\n\n- OK\n\n## What Failed\n\n- **Old pattern was bad** -- fix\n")

        # Create a prior run with current checksums (no changes)
        current_checksums = check_ddd_consistency("TestProject")["checksums"]
        _make_completed_run(workspace, "run_current", ddd_checksums=current_checksums)

        # Create new run
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard"})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        staleness_warnings = [w for w in result["warnings"] if "staleness" in w.lower()]
        assert len(staleness_warnings) == 0
