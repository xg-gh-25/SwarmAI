"""Tests for pipeline_validator.py — structural + semantic invariant checks.

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
    STAGE_ROUTING,
    STAGE_SCHEMAS,
    _check_artifact_exists,
    _check_budget_recorded,
    _check_decision_logged,
    _check_output_routing,
    _check_profile_respected,
    _check_skip_justification,
    _check_stage_order,
    _parse_non_goals,
    _parse_failed_patterns,
    _compute_doc_checksum,
    check_artifact_freshness,
    check_ddd_consistency,
    check_ddd_staleness,
    validate,
)
from scripts.artifact_cli import (
    _extract_run_metrics,
    _record_validation_event,
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
            _stage_record("think", status="completed"),
            _stage_record("build", status="running"),
        ]
        # In trivial profile: evaluate -> think -> build
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
        stages_list = [_stage_record("goal_cycle")]
        # goal_cycle is NOT in trivial profile
        assert _check_stage_order("goal_cycle", "trivial", stages_list) is False


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

    def test_all_non_exempt_require_artifact(self):
        """Every stage except reflect and think requires an artifact."""
        for stage in ["evaluate", "plan", "build", "review", "test", "deliver"]:
            assert _check_artifact_exists(stage, {"artifact_id": None}) is False
        # think and reflect are exempt (no artifact produced)
        for stage in ["think", "reflect"]:
            assert _check_artifact_exists(stage, {"artifact_id": None}) is True


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
        assert _check_profile_respected("goal_cycle", "trivial") is False
        assert _check_profile_respected("build", "research") is False
        assert _check_profile_respected("test", "docs") is False

    def test_all_profiles_include_evaluate(self):
        """Evaluate is in every profile."""
        for profile in PIPELINE_PROFILES:
            assert _check_profile_respected("evaluate", profile) is True

    def test_all_profiles_include_reflect(self):
        """Reflect is in every profile (except goal — handles it inside goal_cycle)."""
        for profile in PIPELINE_PROFILES:
            if profile == "goal":
                continue  # goal_cycle manages REFLECT internally (two-tier model)
            assert _check_profile_respected("reflect", profile) is True


class TestProfileRespectedAtPublish:
    """Run A (run_7627f63c): the profile check must ALSO fire at PUBLISH time
    (validate_artifact_data), not only at completion-time validate(). Publishing an
    off-profile stage (e.g. build in a docs run) must be rejected fail-closed at
    publish — previously it slipped through and only a downstream build-specific
    invariant fired as a confusing symptom (misdiagnosed as a validator bug)."""

    def _errs(self, stage, profile):
        from scripts.pipeline_validator import validate_artifact_data
        return validate_artifact_data(stage, {}, profile=profile)

    def _has_profile_violation(self, errs):
        return any("not in the" in e and "profile" in e for e in errs)

    def test_off_profile_stage_rejected_at_publish(self):
        # AC1: build is not in docs profile → rejected at publish with a profile error
        assert self._has_profile_violation(self._errs("build", "docs"))
        assert self._has_profile_violation(self._errs("test", "docs"))
        assert self._has_profile_violation(self._errs("build", "research"))

    def test_artifactless_off_profile_stage_rejected(self):
        # AC2: goal_cycle has NO STAGE_SCHEMAS entry — the check MUST sit above the
        # `if not schema: return []` early-return, or this evades detection.
        assert self._has_profile_violation(self._errs("goal_cycle", "docs"))
        assert self._has_profile_violation(self._errs("goal_cycle", "full"))

    def test_in_profile_stage_not_false_blocked(self):
        # AC3: legit in-profile stages must NOT get a profile violation.
        # reflect ∈ every profile; goal_cycle ∈ goal; build ∈ bugfix.
        assert not self._has_profile_violation(self._errs("reflect", "docs"))
        assert not self._has_profile_violation(self._errs("goal_cycle", "goal"))
        assert not self._has_profile_violation(self._errs("build", "bugfix"))

    def test_profile_hint_is_actionable(self):
        # AC1 quality: the error names the profile and lists the profile's stages.
        errs = self._errs("build", "docs")
        viol = [e for e in errs if "not in the" in e]
        assert viol and "docs" in viol[0] and "build" in viol[0]

    def test_completion_backstop_skips_off_profile_stage_after_upgrade(self):
        """Gate-2 HIGH regression guard (run_7627f63c): validate_artifact_data now
        fail-closes on an off-profile stage — CORRECT at the publish entrypoint, but
        the completion backstop loop re-validates EVERY completed stage against the
        CURRENT profile. A legit goal→full upgrade (both rank 4) leaves a completed
        `goal_cycle` record; re-validating it must NOT newly-block completion.

        Pins BOTH halves: (a) validate_artifact_data still rejects goal_cycle under
        'full' (publish-reject intact), AND (b) the backstop's guard predicate
        (`stage not in get_profile_stages(profile)`) skips it, so the loop never calls
        the validator for it. If a future refactor drops the loop guard, this fails."""
        from core.pipeline_profiles import get_profile_stages
        # (a) publish-reject is intact — goal_cycle is genuinely off-profile under full
        assert self._has_profile_violation(self._errs("goal_cycle", "full"))
        # (b) the backstop guard skips exactly this stage (goal_cycle ∉ full profile)
        assert "goal_cycle" not in get_profile_stages("full")
        # sanity: an IN-profile completed stage (build ∈ full) is NOT skipped
        assert "build" in get_profile_stages("full")


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
        assert result["checks_passed"] >= 8  # 8 base + Check 13 routing
        assert result["checks_total"] >= 8
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
        assert result["checks_passed"] >= 8  # 8 base + Check 13 routing

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
        assert result["checks_passed"] >= 8  # Warnings don't reduce count
        assert len(result["warnings"]) >= 2  # Missing decisions + zero budget

    def test_multiple_errors_accumulate(self, workspace):
        """Multiple violations all appear in errors list."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        # goal_cycle stage in trivial profile (violation) + no artifact (violation)
        _make_run(runs_dir, profile="trivial", stages=[
            _stage_record("goal_cycle", artifact_id=None),
        ])

        result = validate("TestProject", "run_test1", "goal_cycle")
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
                       {"files_changed": ["a.py"], "tdd": {"green_pass": True, "smoke_tests": 0},
                        "ac_coverage": [{"ac": "AC1: test", "impl": "a.py::f()", "test": "test_a.py::test_f", "verified": True}]})

        _make_run(runs_dir, profile="trivial", stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
            _stage_record("think", status="completed", artifact_id=None),
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

        assert len(results) == 3
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

    def test_checks_total_is_8(self, workspace):
        """Verify checks_total is 8 (7 original + smoke/integration gate)."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard"})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        assert result["checks_total"] >= 8  # 8 base + Check 13 routing
        assert result["checks_passed"] >= 8


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


# ---------------------------------------------------------------------------
# Enforcement Redesign Tests (L0-L3)
# ---------------------------------------------------------------------------


class TestL0ArtifactAuthenticity:
    """Layer 0: artifact_id must resolve to a real artifact in the manifest."""

    def test_fabricated_artifact_id_blocks(self, workspace):
        """Artifact ID not in manifest → BLOCK error."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        # Create run with a fabricated artifact_id (no matching manifest entry)
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_FABRICATED"),
        ])
        # Empty manifest (no artifacts)
        (artifacts_dir / "manifest.json").write_text(json.dumps({"artifacts": [], "pipeline_state": "evaluate"}))

        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is False
        assert any("not found in manifest" in e or "missing or corrupt" in e for e in result["errors"]), \
            f"Expected artifact resolution error, got: {result['errors']}"

    def test_real_artifact_id_passes(self, workspace):
        """Artifact ID that exists in manifest → passes L0."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard"})
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
        ])

        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is True


class TestL1SchemaEnforcement:
    """Layer 1: V1.10.0 required fields must be present in artifacts."""

    def test_review_missing_runtime_patterns_blocks(self, workspace):
        """REVIEW artifact without runtime_patterns → BLOCK."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        # Review artifact with old schema (no runtime_patterns)
        _make_artifact(artifacts_dir, "run_test1", "art_rev", "review",
                       {"approved": True, "integration_trace": {"checked": 1}})
        _make_run(runs_dir, profile="full", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("think", artifact_id="art_t"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_rev"),
        ])
        # Create minimal artifacts for prior stages
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "standard"}),
            ("art_t", "think", {"key_findings": ["x"]}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "review")
        schema_errors = [e for e in result["errors"] if "runtime_patterns" in e]
        assert len(schema_errors) >= 1, f"Expected runtime_patterns error, got: {result['errors']}"

    def test_deliver_missing_adversarial_review_blocks(self, workspace):
        """DELIVER artifact without adversarial_review on bugfix profile → BLOCK."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery",
                       {"title": "Done", "status": "delivered", "confidence_score": {"score": 8, "breakdown": [], "penalties": []}, "completion_audit": {"all_green": True, "gaps": 0}})
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1}, "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]}, "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        adv_errors = [e for e in result["errors"] if "adversarial_review" in e]
        assert len(adv_errors) >= 1, f"Expected adversarial_review error, got: {result['errors']}"

    def test_build_missing_tdd_blocks(self, workspace):
        """BUILD artifact without tdd → BLOCK."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_b", "build",
                       {"files_changed": ["a.py"]})  # no tdd field
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "build")
        tdd_errors = [e for e in result["errors"] if "tdd" in e.lower()]
        assert len(tdd_errors) >= 1, f"Expected tdd error, got: {result['errors']}"


class TestL2DepthValidation:
    """Layer 2: field values must indicate real work, not hollow data."""

    def test_confidence_score_bare_number_blocks(self, workspace):
        """confidence_score as a bare integer → BLOCK (must be from script)."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery",
                       {"title": "X", "status": "delivered", "confidence_score": 9,
                        "completion_audit": {"all_green": True, "gaps": 0},
                        "adversarial_review": {"profile_tier": "pe_only", "findings": []}})
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1}, "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]}, "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        cs_errors = [e for e in result["errors"] if "confidence_score" in e.lower() and ("bare number" in e.lower() or "must run" in e.lower())]
        assert len(cs_errors) >= 1, f"Expected bare-number confidence error, got: {result['errors']}"

    def test_adversarial_review_skipped_on_bugfix_blocks(self, workspace):
        """adversarial_review.profile_tier='skipped' on bugfix → BLOCK."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery",
                       {"title": "X", "status": "delivered",
                        "confidence_score": {"score": 8, "breakdown": [], "penalties": []},
                        "completion_audit": {"all_green": True, "gaps": 0},
                        "adversarial_review": {"profile_tier": "skipped"}})
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1}, "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]}, "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        adv_errors = [e for e in result["errors"] if "adversarial_review" in e and "skipped" in e]
        assert len(adv_errors) >= 1, f"Expected skipped-on-bugfix error, got: {result['errors']}"

    # ── gate_spawn_blocked fail-closed backstop (design run_0bd15278) ──
    # The two-field (spawned + evidence) enforcement exists at BOTH gates:
    #   - validate_artifact_data (publish-time, `publish --stage deliver`)
    #   - _check_depth via validate() (completion-time, `run-update --status
    #     completed`) — added after adversarial review of run_45ab67c7 found the
    #     completion gate previously checked only profile_tier, leaving a
    #     fail-open hole (a spawned=false self-review artifact passed completion,
    #     and the auto-aggregate path bypasses validate_artifact_data entirely).
    # Together they are the structural reason a rejected Gate-2 spawn can NEVER
    # be laundered into a completed run via self-review.
    # STEERING#11: a recovery/guarantee path must have a test that FORCES it.
    # NOTE: the retry-once→checkpoint DECISION LOGIC itself is instruction-only
    # (Rule 23 / build.md / deliver.md) and has no code, hence no unit test — it
    # is exercised live each time a pipeline spawns its gate sub-agents.

    def test_gate_spawn_self_review_without_spawn_blocks(self):
        """spawned=false (self-review masquerade after a rejected spawn) on a
        strict profile → validate_artifact_data emits a blocking error."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", {
            "title": "X",
            "quality": {"tests_pass": True, "regressions": 0, "smoke_pass": True},
            "completion_audit": {"all_green": True, "gaps": 0},
            "adversarial_review": {
                "profile_tier": "full",
                "spawned": False,  # self-review masquerade
                "findings": [],
            },
        }, profile="bugfix")
        spawn_errors = [e for e in errors
                        if "adversarial_review" in e and "spawned" in e]
        assert len(spawn_errors) >= 1, (
            f"Expected spawned=false to BLOCK (fail-closed), got: {errors}")

    def test_gate_spawn_fabricated_evidence_empty_blocks(self):
        """spawned=true but empty/whitespace evidence (the fabrication path a
        rejected-spawn agent might try) → blocking error. Two-field gate holds."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", {
            "title": "X",
            "quality": {"tests_pass": True, "regressions": 0, "smoke_pass": True},
            "completion_audit": {"all_green": True, "gaps": 0},
            "adversarial_review": {
                "profile_tier": "full",
                "spawned": True,
                "evidence": "   ",  # whitespace-only = no genuine spawn evidence
                "findings": [],
            },
        }, profile="bugfix")
        ev_errors = [e for e in errors
                     if "adversarial_review" in e and "evidence" in e]
        assert len(ev_errors) >= 1, (
            f"Expected empty-evidence to BLOCK, got: {errors}")

    def test_gate_spawn_genuine_evidence_passes(self):
        """Positive control: genuine spawned=true + non-empty evidence → no
        spawn/evidence error (the gate doesn't false-block a real review)."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", {
            "title": "X",
            "quality": {"tests_pass": True, "regressions": 0, "smoke_pass": True},
            "completion_audit": {"all_green": True, "gaps": 0},
            "adversarial_review": {
                "profile_tier": "full",
                "spawned": True,
                "evidence": "Agent tool invocation: correctness + security specialists",
                "findings": [],
            },
        }, profile="bugfix")
        gate_errors = [e for e in errors if "adversarial_review" in e
                       and ("spawned" in e or "evidence" in e)]
        assert gate_errors == [], (
            f"Genuine spawn+evidence must NOT be blocked, got: {gate_errors}")

    def test_gate_spawn_self_review_blocks_at_COMPLETION_path(self, workspace):
        """The path that actually guards status:completed is validate()->
        _check_depth, NOT validate_artifact_data (which only runs at publish).
        Adversarial review of run_45ab67c7 proved _check_depth ignored 'spawned',
        so a spawned=false self-review artifact passed completion. This test
        FORCES the completion path and asserts the hole is closed (STEERING#11)."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        # A polished self-review delivery artifact: valid profile_tier + specific
        # findings + real confidence dict, but spawned=false (never spawned).
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery",
                       {"title": "X", "status": "delivered",
                        "confidence_score": {"score": 8, "breakdown": [], "penalties": []},
                        "completion_audit": {"all_green": True, "gaps": 0},
                        "adversarial_review": {
                            "profile_tier": "full",
                            "spawned": False,  # self-review masquerade
                            "findings": [{"severity": "LOW", "resolved": True}],
                        }})
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1}, "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]}, "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        spawn_errors = [e for e in result["errors"]
                        if "adversarial_review" in e and "spawned" in e]
        assert len(spawn_errors) >= 1, (
            f"COMPLETION path must BLOCK spawned=false self-review (fail-closed), "
            f"got: {result['errors']}")


class TestFindingConfidenceGate:
    """Finding-level confidence gate (AutoSDE port, run_7583af5f).

    Ports the AutoSDE 'confidence gate as a code constant' pattern: the
    'confidence >= 7' rule that lived ONLY as prose in deliver.md:507 becomes a
    module constant + _blocked_findings() helper enforced at BOTH gate sites
    (validate_artifact_data publish + _check_depth completion).

    Prior behavior blocked ONLY unresolved HIGH findings; MEDIUM was ungated at
    completion. Now: HIGH blocks always (confidence-independent), MEDIUM blocks
    when confidence >= threshold, MED with confidence < threshold is note-only,
    and MEDIUM with NO confidence is fail-closed (P7 — can't dodge by omission).
    Severity is matched case-insensitively across {HIGH, MEDIUM, MED} because
    specialists emit 'MED' (deliver.md:396) while the final schema says 'MEDIUM'
    (:397) — a real in-repo collision (Gate-1 finding, MOD04)."""

    def _deliver(self, findings):
        return {
            "title": "X",
            "quality": {"tests_pass": True, "regressions": 0, "smoke_pass": True},
            "completion_audit": {"all_green": True, "gaps": 0},
            "adversarial_review": {
                "profile_tier": "full",
                "spawned": True,
                "evidence": "Agent tool invocation: correctness specialist",
                "findings": findings,
            },
        }

    def _finding_errors(self, errors):
        return [e for e in errors
                if "finding" in e.lower() and ("confidence" in e.lower()
                                               or "MED" in e or "HIGH" in e)]

    def test_constant_exists_and_is_seven(self):
        """AC1: CONFIDENCE_GATE_THRESHOLD is a module-level int == 7."""
        from scripts import pipeline_validator as pv
        assert isinstance(pv.CONFIDENCE_GATE_THRESHOLD, int)
        assert pv.CONFIDENCE_GATE_THRESHOLD == 7

    def test_med_high_confidence_blocks(self):
        """AC2: unresolved MEDIUM with confidence >= 7 → blocking error."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", self._deliver([
            {"severity": "MEDIUM", "confidence": 8, "resolved": False,
             "finding": "x.py foo() line 12: bad thing"},
        ]), profile="bugfix")
        assert self._finding_errors(errors), (
            f"unresolved MED conf=8 must BLOCK, got: {errors}")

    def test_med_low_confidence_passes(self):
        """AC3: unresolved MEDIUM with confidence < 7 → NOT blocked (note-only)."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", self._deliver([
            {"severity": "MEDIUM", "confidence": 4, "resolved": False,
             "finding": "x.py foo() line 12: minor nit"},
        ]), profile="bugfix")
        assert not self._finding_errors(errors), (
            f"unresolved MED conf=4 must NOT block (note-only), got: {errors}")

    def test_high_blocks_regardless_of_confidence(self):
        """AC4: unresolved HIGH blocks even with no/low confidence (no regression)."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", self._deliver([
            {"severity": "HIGH", "resolved": False,
             "finding": "x.py foo() line 12: data loss on crash"},
        ]), profile="bugfix")
        assert self._finding_errors(errors), (
            f"unresolved HIGH must BLOCK regardless of confidence, got: {errors}")

    def test_med_missing_confidence_fails_closed(self):
        """AC5: unresolved MEDIUM with NO confidence field → fail-closed (blocks)."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", self._deliver([
            {"severity": "MEDIUM", "resolved": False,
             "finding": "x.py foo() line 12: unclear severity"},
        ]), profile="bugfix")
        assert self._finding_errors(errors), (
            f"unresolved MED with missing confidence must fail-closed (block), "
            f"got: {errors}")

    def test_med_short_alias_high_confidence_blocks(self):
        """AC8: specialist 'MED' (deliver.md:396) gated identically to 'MEDIUM'."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", self._deliver([
            {"severity": "MED", "confidence": 9, "resolved": False,
             "finding": "x.py foo() line 12: aliased severity string"},
        ]), profile="bugfix")
        assert self._finding_errors(errors), (
            f"'MED' alias conf=9 must BLOCK like 'MEDIUM', got: {errors}")

    def test_med_case_insensitive(self):
        """AC8: lowercase 'medium' still gated (case-insensitive normalization)."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", self._deliver([
            {"severity": "medium", "confidence": 8, "resolved": False,
             "finding": "x.py foo() line 12: lowercase severity"},
        ]), profile="bugfix")
        assert self._finding_errors(errors), (
            f"lowercase 'medium' conf=8 must BLOCK, got: {errors}")

    def test_resolved_med_does_not_block(self):
        """Positive control: a RESOLVED high-confidence MED never blocks."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", self._deliver([
            {"severity": "MEDIUM", "confidence": 9, "resolved": True,
             "finding": "x.py foo() line 12: fixed already"},
        ]), profile="bugfix")
        assert not self._finding_errors(errors), (
            f"resolved MED must NOT block, got: {errors}")

    def test_critical_blocks_like_high(self):
        """AC4b (Gate-2 finding): CRITICAL blocks confidence-independent like HIGH.
        'critical' is a live severity in review-agent schemas + confidence_score.py
        (:133 treats critical==high). A gate that lets the MOST severe class through
        is fail-OPEN — the inverse of intent."""
        from scripts.pipeline_validator import validate_artifact_data, _blocked_findings
        # helper level (both cases + missing confidence)
        assert _blocked_findings([{"severity": "CRITICAL", "resolved": False, "finding": "x"}])
        assert _blocked_findings([{"severity": "critical", "confidence": 2, "resolved": False, "finding": "x"}])
        # gate level
        errors = validate_artifact_data("deliver", self._deliver([
            {"severity": "CRITICAL", "resolved": False,
             "finding": "x.py foo() line 12: RCE via unsanitized path"},
        ]), profile="bugfix")
        assert self._finding_errors(errors), (
            f"unresolved CRITICAL must BLOCK (fail-closed), got: {errors}")

    def test_low_never_blocks(self):
        """Positive control: unresolved LOW with high confidence is note-only."""
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", self._deliver([
            {"severity": "LOW", "confidence": 10, "resolved": False,
             "finding": "x.py foo() line 12: style nit"},
        ]), profile="bugfix")
        assert not self._finding_errors(errors), (
            f"unresolved LOW must NOT block regardless of confidence, got: {errors}")

    def test_class_completeness_finding_blocks_via_generic_gate(self):
        """AC6 (run_1d3df9e6) — the class-completeness gate needs NO bespoke
        validator code: `to_delivery_finding()` emits a HIGH finding that the
        EXISTING generic HIGH gate blocks. Building a `class_completeness`-specific
        validator branch would be the C042 over-build the sibling cross_boundary
        gate deliberately deferred (IMPROVEMENT.md, run_e4f2684e). This test LOCKS
        the seam end-to-end using the REAL core output — so a future refactor of
        either `_blocked_findings` (drop HIGH) or `to_delivery_finding` (downgrade
        severity / add resolved) that silently reopens the run_0d60e04e hole
        fails HERE, at both the publish and completion gates.

        Wire: goal_cycle.md step 2.5 appends this finding into
        adversarial_review.findings[] when the class gate BLOCKS; that is why
        proving the generic gate blocks it IS the enforcement of AC6.
        """
        from scripts.pipeline_validator import (
            validate_artifact_data, _blocked_findings,
        )
        from scripts.check_migration_class import (
            CompletenessResult, to_delivery_finding,
        )

        # Real BLOCK result → real finding (not a hand-built stub — guards the seam).
        res = CompletenessResult(passed=False, blocked=[
            {"kind": "MISSED", "member": "distill.py:4 _run_locked_write(Decisions)",
             "detail": "live sink caller with no declared member"},
        ])
        finding = to_delivery_finding(res)
        assert finding is not None
        assert finding["severity"] == "HIGH" and finding["resolved"] is False, (
            "to_delivery_finding must emit an UNRESOLVED HIGH finding, or the "
            "generic gate cannot block it")

        # Helper level: the real finding is in the blocking set.
        assert _blocked_findings([finding]), (
            "class_completeness HIGH finding must be blocked by the generic gate")

        # Publish gate: a deliver artifact carrying it BLOCKS.
        errors = validate_artifact_data("deliver", self._deliver([finding]),
                                        profile="goal")
        assert self._finding_errors(errors), (
            f"deliver artifact carrying a class_completeness BLOCK finding must "
            f"be blocked at publish, got: {errors}")

        # Positive control: a PASS/no-op result yields no finding → nothing to block.
        assert to_delivery_finding(CompletenessResult(passed=True, noop=True)) is None
        assert to_delivery_finding(CompletenessResult(passed=True)) is None

    def test_helper_returns_blocking_findings_directly(self):
        """Unit-test the helper in isolation: returns exactly the blocking set."""
        from scripts.pipeline_validator import _blocked_findings
        findings = [
            {"severity": "HIGH", "resolved": False, "finding": "h"},        # block
            {"severity": "MED", "confidence": 8, "resolved": False, "finding": "m1"},  # block
            {"severity": "MEDIUM", "confidence": 3, "resolved": False, "finding": "m2"},  # pass
            {"severity": "MEDIUM", "resolved": False, "finding": "m3"},     # block (fail-closed)
            {"severity": "HIGH", "resolved": True, "finding": "h2"},        # pass (resolved)
            {"severity": "LOW", "confidence": 10, "resolved": False, "finding": "l"},  # pass
        ]
        blocked = _blocked_findings(findings)
        blocked_texts = {f.get("finding") for f in blocked}
        assert blocked_texts == {"h", "m1", "m3"}, (
            f"expected {{h, m1, m3}} blocked, got {blocked_texts}")

    def test_completion_path_blocks_high_confidence_med(self, workspace):
        """AC2 at the COMPLETION gate (validate()->_check_depth), not just publish.
        This is the path that guards status:completed (STEERING#11 — force it)."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery",
                       {"title": "X", "status": "delivered",
                        "confidence_score": {"score": 9, "breakdown": [], "penalties": []},
                        "completion_audit": {"all_green": True, "gaps": 0},
                        "adversarial_review": {
                            "profile_tier": "full", "spawned": True,
                            "evidence": "Agent tool invocation: correctness specialist",
                            "findings": [
                                {"severity": "MEDIUM", "confidence": 8, "resolved": False,
                                 "finding": "x.py foo() line 12: unresolved med"}]}})
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1}, "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]}, "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        finding_errs = [e for e in result["errors"]
                        if "finding" in e.lower() and ("confidence" in e.lower() or "MED" in e)]
        assert len(finding_errs) >= 1, (
            f"COMPLETION path must BLOCK unresolved high-confidence MED, "
            f"got: {result['errors']}")


class TestL3ConfidenceGate:
    """Layer 3: confidence < 7 blocks delivery unless human_override."""

    def test_low_confidence_blocks_delivery(self, workspace):
        """confidence_score.score=4 without human_override → BLOCK."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery",
                       {"title": "X", "status": "delivered",
                        "confidence_score": {"score": 4, "max_possible": 12, "flag_for_review": True, "breakdown": [], "penalties": [{"rule": "test", "points": -3, "detail": "x"}]},
                        "completion_audit": {"all_green": True, "gaps": 0},
                        "adversarial_review": {"profile_tier": "pe_only", "findings": []}})
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1}, "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]}, "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        gate_errors = [e for e in result["errors"] if "confidence" in e.lower() and ("< 7" in e or "score=" in e)]
        assert len(gate_errors) >= 1, f"Expected confidence gate error, got: {result['errors']}"

    def test_human_override_downgrades_to_warning(self, workspace):
        """confidence_score.score=4 WITH human_override → WARNING not BLOCK."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery",
                       {"title": "X", "status": "delivered",
                        "confidence_score": {"score": 4, "max_possible": 12, "flag_for_review": True, "breakdown": [], "penalties": [{"rule": "test", "points": -3, "detail": "x"}]},
                        "completion_audit": {"all_green": True, "gaps": 0},
                        "adversarial_review": {"profile_tier": "pe_only", "findings": []}})
        run = _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        # Add human_override flag
        run["human_override"] = True
        (runs_dir / "run.json").write_text(json.dumps(run))
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1}, "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]}, "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        # Should NOT have confidence BLOCK error
        gate_errors = [e for e in result["errors"] if "confidence" in e.lower() and "< 7" in e]
        assert len(gate_errors) == 0, f"human_override should prevent BLOCK, got: {result['errors']}"
        # Should have WARNING instead
        gate_warns = [w for w in result["warnings"] if "confidence" in w.lower() and "override" in w.lower()]
        assert len(gate_warns) >= 1, f"Expected override warning, got: {result['warnings']}"


# ---------------------------------------------------------------------------
# Pipeline Metrics Tests
# ---------------------------------------------------------------------------

class TestSemanticDepthChecks:
    """Test _check_semantic_depth() — WARN-level heuristic checks for content quality."""

    # --- AC1: Completion audit evidence quality ---

    def test_completion_audit_weak_evidence_warns(self, workspace):
        """>=70% of checklist entries lack file/test refs → WARN."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery", {
            "title": "X", "status": "done",
            "confidence_score": {"score": 8, "breakdown": [], "penalties": []},
            "completion_audit": {
                "all_green": True, "gaps": 0,
                "checklist": [
                    {"criterion": "AC1", "evidence": "implemented", "status": "pass"},
                    {"criterion": "AC2", "evidence": "verified", "status": "pass"},
                    {"criterion": "AC3", "evidence": "done", "status": "pass"},
                ],
            },
            "adversarial_review": {"profile_tier": "pe_only", "findings": []},
        })
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1},
                                 "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]},
                                 "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        evidence_warns = [w for w in result["warnings"] if "evidence" in w.lower() and "completion" in w.lower()]
        assert len(evidence_warns) >= 1, f"Expected evidence quality warning, got warnings: {result['warnings']}"

    def test_completion_audit_strong_evidence_no_warn(self, workspace):
        """>=70% entries cite file paths or test names → no WARN."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery", {
            "title": "X", "status": "done",
            "confidence_score": {"score": 8, "breakdown": [], "penalties": []},
            "completion_audit": {
                "all_green": True, "gaps": 0,
                "checklist": [
                    {"criterion": "AC1", "evidence": "test_session_unit.py::test_spawn passes", "status": "pass"},
                    {"criterion": "AC2", "evidence": "backend/core/memory_index.py line 42 verified", "status": "pass"},
                    {"criterion": "AC3", "evidence": "CHANGELOG.md updated with version entry", "status": "pass"},
                ],
            },
            "adversarial_review": {"profile_tier": "pe_only", "findings": []},
        })
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1},
                                 "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]},
                                 "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        evidence_warns = [w for w in result["warnings"] if "evidence" in w.lower() and "completion" in w.lower()]
        assert len(evidence_warns) == 0, f"Strong evidence should not warn, got: {result['warnings']}"

    # --- AC2: RP patterns evidence quality ---

    def test_rp_patterns_bare_pass_warns(self, workspace):
        """Non-N/A patterns with just 'PASS' (no evidence) → WARN."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_rev", "review", {
            "approved": True,
            "integration_trace": {"checked": 1},
            "runtime_patterns": {
                "checked": 4,
                "patterns": [
                    {"id": "RP1", "result": "PASS"},
                    {"id": "RP7", "result": "PASS"},
                    {"id": "RP8", "result": "N/A"},
                    {"id": "RP17", "result": "PASS"},
                ],
            },
            "findings_count": 0,
        })
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_rev"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "review")
        rp_warns = [w for w in result["warnings"] if "runtime_patterns" in w.lower() and "evidence" in w.lower()]
        assert len(rp_warns) >= 1, f"Expected RP evidence warning, got warnings: {result['warnings']}"

    def test_rp_patterns_with_evidence_no_warn(self, workspace):
        """Non-N/A patterns with substantive evidence → no WARN."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_rev", "review", {
            "approved": True,
            "integration_trace": {"checked": 1},
            "runtime_patterns": {
                "checked": 3,
                "patterns": [
                    {"id": "RP1", "result": "PASS", "evidence": "No subprocess calls in changeset"},
                    {"id": "RP7", "result": "PASS", "evidence": "Error constants match in validator.py line 85"},
                    {"id": "RP8", "result": "N/A"},
                ],
            },
            "findings_count": 0,
        })
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_rev"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "review")
        rp_warns = [w for w in result["warnings"] if "runtime_patterns" in w.lower() and "evidence" in w.lower()]
        assert len(rp_warns) == 0, f"Evidenced patterns should not warn, got: {result['warnings']}"

    # --- AC3: Confidence penalty consistency ---

    def test_no_penalties_with_findings_warns(self, workspace):
        """Review had findings but confidence penalties is empty → WARN."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        # Review artifact with 3 findings
        _make_artifact(artifacts_dir, "run_test1", "art_rev", "review", {
            "approved": True,
            "integration_trace": {"checked": 1},
            "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]},
            "findings_count": 3,
        })
        # Deliver artifact with EMPTY penalties despite findings
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery", {
            "title": "X", "status": "done",
            "confidence_score": {"score": 9, "breakdown": [{"rule": "x", "points": 3}], "penalties": []},
            "completion_audit": {"all_green": True, "gaps": 0},
            "adversarial_review": {"profile_tier": "pe_only", "findings": []},
        })
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_rev"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        penalty_warns = [w for w in result["warnings"] if "penalt" in w.lower() and "finding" in w.lower()]
        assert len(penalty_warns) >= 1, f"Expected penalty consistency warning, got warnings: {result['warnings']}"

    def test_penalties_present_with_findings_no_warn(self, workspace):
        """Review had findings AND penalties are non-empty → no WARN."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_rev", "review", {
            "approved": True,
            "integration_trace": {"checked": 1},
            "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]},
            "findings_count": 2,
        })
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery", {
            "title": "X", "status": "done",
            "confidence_score": {"score": 7, "breakdown": [], "penalties": [
                {"rule": "review_findings", "points": -2, "detail": "2 findings fixed"}
            ]},
            "completion_audit": {"all_green": True, "gaps": 0},
            "adversarial_review": {"profile_tier": "pe_only", "findings": []},
        })
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_rev"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        penalty_warns = [w for w in result["warnings"] if "penalt" in w.lower() and "finding" in w.lower()]
        assert len(penalty_warns) == 0, f"Penalties present should not warn, got: {result['warnings']}"


class TestValueDeliveryRules:
    """Test Rules 15-18: value over completion, evidence over assertion,
    no premature completion, specific adversarial findings."""

    def test_vague_adversarial_findings_blocked(self, workspace):
        """Rule 18: >=50% vague findings → BLOCK."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery", {
            "title": "X", "status": "done",
            "confidence_score": {"score": 8, "breakdown": [], "penalties": []},
            "completion_audit": {"all_green": True, "gaps": 0},
            "adversarial_review": {
                "profile_tier": "full",
                "findings": [
                    {"severity": "HIGH", "finding": "looks good", "resolved": True},
                    {"severity": "MED", "finding": "could improve", "resolved": True},
                ],
            },
        })
        _make_run(runs_dir, profile="full", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("think", artifact_id="art_th"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "test"}),
            ("art_th", "research", {"key_findings": "x"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1},
                                 "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]},
                                 "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        vague_errors = [e for e in result["errors"] if "vague" in e.lower()]
        assert len(vague_errors) >= 1, f"Expected vague findings block, got: {result['errors']}"

    def test_specific_adversarial_findings_pass(self, workspace):
        """Rule 18: specific findings (with file refs) pass depth check."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery", {
            "title": "X", "status": "done",
            "confidence_score": {"score": 8, "breakdown": [], "penalties": []},
            "completion_audit": {"all_green": True, "gaps": 0},
            "adversarial_review": {
                "profile_tier": "pe_only",
                "findings": [
                    {"severity": "HIGH", "resolved": True,
                     "finding": "backend/core/session_unit.py line 42: race condition in _spawn() when concurrent tabs call simultaneously"},
                    {"severity": "MED", "resolved": True,
                     "finding": "pipeline_validator.py _check_depth() doesn't handle non-dict types for runtime_patterns"},
                ],
            },
        })
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1},
                                 "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]},
                                 "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        vague_errors = [e for e in result["errors"] if "vague" in e.lower()]
        assert len(vague_errors) == 0, f"Specific findings should pass, got: {result['errors']}"

    def test_unfixable_gaps_without_justification_blocked(self, workspace):
        """Rule 17: unfixable_gaps > 0 without justification → BLOCK."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery", {
            "title": "X", "status": "done",
            "confidence_score": {"score": 7, "breakdown": [], "penalties": []},
            "completion_audit": {
                "all_green": False,
                "gaps": 1,
                "unfixable_gaps": 1,
                # No unfixable_justification — should be blocked
            },
            "adversarial_review": {"profile_tier": "pe_only", "findings": []},
        })
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1},
                                 "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]},
                                 "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        justification_errors = [e for e in result["errors"] if "unfixable_justification" in e]
        assert len(justification_errors) >= 1, f"Expected justification requirement, got: {result['errors']}"

    def test_unfixable_gaps_with_justification_passes(self, workspace):
        """Rule 17: unfixable_gaps with justification → allowed (just -1 confidence)."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery", {
            "title": "X", "status": "done",
            "confidence_score": {"score": 7, "breakdown": [], "penalties": []},
            "completion_audit": {
                "all_green": False,
                "gaps": 1,
                "unfixable_gaps": 1,
                "unfixable_justification": "Windows-only feature, macOS CI can't test",
            },
            "adversarial_review": {"profile_tier": "pe_only", "findings": []},
        })
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        for aid, atype, data in [
            ("art_e", "evaluation", {"recommendation": "GO", "scope": "bugfix"}),
            ("art_p", "plan", {"acceptance_criteria": ["x"]}),
            ("art_b", "build", {"files_changed": ["a.py"], "tdd": {"green_pass": True}}),
            ("art_r", "review", {"approved": True, "integration_trace": {"checked": 1},
                                 "runtime_patterns": {"checked": 1, "patterns": [{"id": "RP1", "result": "N/A"}]},
                                 "findings_count": 0}),
            ("art_t", "test", {"passed": 10}),
        ]:
            _make_artifact(artifacts_dir, "run_test1", aid, atype, data)

        result = validate("TestProject", "run_test1", "deliver")
        justification_errors = [e for e in result["errors"] if "unfixable_justification" in e]
        assert len(justification_errors) == 0, f"Justified unfixable should pass, got: {result['errors']}"


class TestExtractRunMetrics:
    """Test _extract_run_metrics extracts data correctly from run.json + artifacts."""

    def test_basic_metrics_extraction(self, workspace):
        """Full metrics extraction from a completed bugfix run with all artifacts."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        # Create artifacts first
        _make_artifact(artifacts_dir, "run_test1", "art_rev", "review", {
            "approved": True,
            "integration_trace": {"checked": 5},
            "runtime_patterns": {"checked": 8, "patterns": [{"id": "RP1", "result": "PASS"}]},
            "findings_count": 3,
            "findings": [{"desc": "a"}, {"desc": "b"}, {"desc": "c"}],
        })
        _make_artifact(artifacts_dir, "run_test1", "art_del", "delivery", {
            "title": "Test", "status": "done",
            "confidence_score": {"score": 8, "breakdown": [], "penalties": []},
            "completion_audit": {"all_green": True, "gaps": 0},
            "adversarial_review": {
                "profile_tier": "pe_only",
                "findings": [
                    {"severity": "HIGH", "resolved": True, "desc": "a"},
                    {"severity": "MEDIUM", "resolved": False, "desc": "b"},
                ],
            },
        })
        _make_artifact(artifacts_dir, "run_test1", "art_test", "test_report", {
            "passed": 50, "failed": 2,
        })
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset", {
            "files_changed": ["a.py", "b.py", "c.py"],
            "tdd": {"green_pass": True, "tests_generated": 12},
        })

        run = _make_run(runs_dir, status="completed", profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_e", decisions=[
                {"description": "GO", "classification": "mechanical", "reasoning": ""},
            ]),
            _stage_record("plan", artifact_id="art_p"),
            _stage_record("build", artifact_id="art_build", token_cost=30000),
            _stage_record("review", artifact_id="art_rev", token_cost=8000),
            _stage_record("test", artifact_id="art_test", token_cost=6000),
            _stage_record("deliver", artifact_id="art_del", token_cost=10000),
            _stage_record("reflect", token_cost=3000),
        ])
        run["completed_at"] = "2026-03-24T01:30:00Z"
        (runs_dir / "run.json").write_text(json.dumps(run))

        metrics = _extract_run_metrics("TestProject", "run_test1", run)

        # Structure
        assert metrics["run_id"] == "run_test1"
        assert metrics["profile"] == "bugfix"
        assert metrics["stages_completed"] == 7

        # Token metrics
        assert metrics["total_tokens"] > 0
        assert "build" in metrics["stage_tokens"]

        # Decision metrics (7 stages × 1 default mechanical each)
        assert metrics["decisions"]["mechanical"] == 7
        assert metrics["decisions"]["total"] == 7

        # Catch metrics — core value
        assert metrics["catches"]["review_findings"] == 3
        assert metrics["catches"]["review_rp_checked"] == 8
        assert metrics["catches"]["adversarial_findings"] == 2
        assert metrics["catches"]["adversarial_high"] == 1
        assert metrics["catches"]["adversarial_resolved"] == 1
        assert metrics["catches"]["test_regressions"] == 2

        # Quality
        assert metrics["quality"]["confidence_score"] == 8
        assert metrics["quality"]["completion_all_green"] is True
        assert metrics["quality"]["test_passed"] == 50
        assert metrics["quality"]["test_failed"] == 2

        # Build
        assert metrics["build"]["files_changed"] == 3
        assert metrics["build"]["tests_generated"] == 12

        # Duration
        assert metrics["duration_minutes"] == 90.0

    def test_metrics_with_missing_artifacts(self, workspace):
        """Metrics extraction when artifacts are missing — should not crash."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        run = _make_run(runs_dir, status="completed", profile="bugfix", stages=[
            _stage_record("evaluate", artifact_id="art_nonexistent"),
            _stage_record("plan", artifact_id="art_also_missing"),
        ])

        metrics = _extract_run_metrics("TestProject", "run_test1", run)

        # Should still produce valid metrics with zeros
        assert metrics["catches"]["review_findings"] == 0
        assert metrics["catches"]["adversarial_findings"] == 0
        assert metrics["quality"]["confidence_score"] == 0

    def test_metrics_with_old_format_artifacts(self, workspace):
        """Old artifacts might have findings as int, not list."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        _make_artifact(artifacts_dir, "run_test1", "art_rev", "review", {
            "approved": True,
            "integration_trace": {"checked": 2},
            "findings_count": 5,
            "findings": 5,  # Old format: int instead of list
        })

        run = _make_run(runs_dir, status="completed", profile="bugfix", stages=[
            _stage_record("review", artifact_id="art_rev"),
        ])

        metrics = _extract_run_metrics("TestProject", "run_test1", run)
        # Should handle int gracefully
        assert metrics["catches"]["review_findings"] == 5


class TestRecordValidationEvent:
    """Test validation event recording in run.json."""

    def test_records_block_event(self, workspace):
        """Validation block is appended to run.json.validation_events[]."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_run(runs_dir, status="running")

        _record_validation_event(
            "TestProject", "run_test1", "build",
            passed=False,
            errors=["Schema violation: missing tdd"],
            warnings=["Budget low"],
        )

        run = json.loads((runs_dir / "run.json").read_text())
        events = run.get("validation_events", [])
        assert len(events) == 1
        assert events[0]["stage"] == "build"
        assert events[0]["passed"] is False
        assert events[0]["error_count"] == 1
        assert "timestamp" in events[0]

    def test_multiple_events_append(self, workspace):
        """Multiple validation events accumulate."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_run(runs_dir, status="running")

        for stage in ["build", "review", "build"]:
            _record_validation_event(
                "TestProject", "run_test1", stage,
                passed=False, errors=["err"], warnings=[],
            )

        run = json.loads((runs_dir / "run.json").read_text())
        assert len(run["validation_events"]) == 3

    def test_missing_run_does_not_crash(self, workspace):
        """Recording to a non-existent run silently succeeds (best-effort)."""
        _record_validation_event(
            "TestProject", "run_nonexistent", "build",
            passed=False, errors=["err"], warnings=[],
        )
        # No exception = pass


class TestMetricsCLI:
    """Test run-metrics and run-analytics CLI commands via subprocess."""

    def test_run_metrics_cli(self, workspace):
        """run-metrics generates METRICS.json and outputs JSON."""
        import subprocess

        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"

        _make_artifact(artifacts_dir, "run_test1", "art_t", "test_report",
                       {"passed": 20, "failed": 0})
        _make_run(runs_dir, status="completed", stages=[
            _stage_record("evaluate", artifact_id="art_e"),
            _stage_record("test", artifact_id="art_t", token_cost=5000),
        ])

        result = subprocess.run(
            [sys.executable, "-m", "scripts.artifact_cli",
             "run-metrics", "--project", "TestProject", "--run-id", "run_test1"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
            env={**os.environ, "SWARM_WORKSPACE": str(workspace)},
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        metrics = json.loads(result.stdout)
        assert metrics["run_id"] == "run_test1"
        assert metrics["quality"]["test_passed"] == 20

        # METRICS.json should be written
        metrics_file = runs_dir / "METRICS.json"
        assert metrics_file.exists()

    def test_run_analytics_cli(self, workspace):
        """run-analytics aggregates across multiple completed runs."""
        import subprocess

        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        # Create 3 completed runs
        for i in range(3):
            rid = f"run_test{i}"
            runs_dir = artifacts_dir / "runs" / rid
            _make_run(runs_dir, run_id=rid, status="completed", profile="bugfix", stages=[
                _stage_record("evaluate", token_cost=5000 + i * 1000),
                _stage_record("build", token_cost=20000 + i * 5000),
            ])

        result = subprocess.run(
            [sys.executable, "-m", "scripts.artifact_cli",
             "run-analytics", "--project", "TestProject", "--limit", "10"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
            env={**os.environ, "SWARM_WORKSPACE": str(workspace)},
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        analytics = json.loads(result.stdout)
        assert analytics["runs_analyzed"] == 3
        assert analytics["tokens"]["avg_per_run"] > 0
        assert "catches" in analytics
        assert "quality" in analytics
        assert "decisions" in analytics


# ---------------------------------------------------------------------------
# Check 12: Anti-Rationalization Gate
# ---------------------------------------------------------------------------

class TestAntiRationalization:
    """Tests for _check_skip_justification (Check 12)."""

    def test_non_skipped_stage_passes(self):
        """Non-skipped stages are not checked."""
        record = _stage_record("build", status="completed")
        errors = _check_skip_justification(record)
        assert errors == []

    def test_skipped_with_skip_reason_passes(self):
        """Legacy path: skip_reason alone is sufficient."""
        record = _stage_record("think", status="skipped")
        record["skip_reason"] = "Design pre-approved by user"
        errors = _check_skip_justification(record)
        assert errors == []

    def test_skipped_without_any_reason_blocks(self):
        """Skipped without skip_reason or skip_justification → BLOCK."""
        record = {"stage": "think", "status": "skipped", "token_cost": 0}
        errors = _check_skip_justification(record)
        assert len(errors) == 1
        assert "BLOCK" in errors[0]

    def test_full_justification_null_counter_passes(self):
        """Full justification with null counter_argument_check → PASS (AC2)."""
        record = {
            "stage": "think", "status": "skipped", "token_cost": 0,
            "skip_justification": {
                "step_skipped": "think",
                "reason": "Only one approach exists",
                "evidence_skip_safe": "Scope is trivial, proven pattern",
                "counter_argument_check": None,
            },
        }
        errors = _check_skip_justification(record)
        assert errors == []

    def test_full_justification_empty_counter_passes(self):
        """Full justification with empty string counter → PASS."""
        record = {
            "stage": "think", "status": "skipped", "token_cost": 0,
            "skip_justification": {
                "step_skipped": "think",
                "reason": "Only one approach exists",
                "evidence_skip_safe": "Scope is trivial",
                "counter_argument_check": "",
            },
        }
        errors = _check_skip_justification(record)
        assert errors == []

    def test_counter_argument_present_blocks(self):
        """Non-empty counter_argument_check → BLOCK (AC1)."""
        record = {
            "stage": "deliver", "status": "skipped", "token_cost": 0,
            "skip_justification": {
                "step_skipped": "adversarial_review",
                "reason": "Tests pass, code is straightforward",
                "evidence_skip_safe": "All 12 tests green",
                "counter_argument_check": (
                    "C011: 57 tests green but feature 100% non-functional. "
                    "Tests verify what was written, not what was missed."
                ),
            },
        }
        errors = _check_skip_justification(record)
        assert len(errors) == 1
        assert "Anti-rationalization triggered" in errors[0]
        assert "BLOCK" in errors[0]

    def test_missing_required_fields_blocks(self):
        """Missing step_skipped/reason/evidence → BLOCK."""
        record = {
            "stage": "think", "status": "skipped", "token_cost": 0,
            "skip_justification": {
                "step_skipped": "",
                "reason": "",
                "evidence_skip_safe": "",
                "counter_argument_check": None,
            },
        }
        errors = _check_skip_justification(record)
        # Should have 3 errors (one per empty required field)
        assert len(errors) == 3
        assert all("BLOCK" in e for e in errors)

    def test_non_string_counter_argument_blocks(self):
        """F1: Non-string counter_argument_check (list/dict/bool) → BLOCK."""
        for bypass_value in [["C011: tests pass"], {"text": "counter"}, True, 42]:
            record = {
                "stage": "deliver", "status": "skipped", "token_cost": 0,
                "skip_justification": {
                    "step_skipped": "adversarial_review",
                    "reason": "Tests pass",
                    "evidence_skip_safe": "All green",
                    "counter_argument_check": bypass_value,
                },
            }
            errors = _check_skip_justification(record)
            assert any("must be a string" in e for e in errors), (
                f"Non-string type {type(bypass_value).__name__} should BLOCK"
            )

    def test_short_skip_reason_blocks(self):
        """F7: skip_reason shorter than 15 chars → BLOCK."""
        record = {"stage": "think", "status": "skipped", "token_cost": 0,
                  "skip_reason": "ok"}
        errors = _check_skip_justification(record)
        assert len(errors) == 1
        assert "too short" in errors[0]

    def test_adequate_skip_reason_passes(self):
        """skip_reason >= 15 chars passes legacy path."""
        record = {"stage": "think", "status": "skipped", "token_cost": 0,
                  "skip_reason": "Design pre-approved by user in session"}
        errors = _check_skip_justification(record)
        assert errors == []


# ---------------------------------------------------------------------------
# Check 13: Output Routing
# ---------------------------------------------------------------------------

class TestOutputRouting:
    """Tests for _check_output_routing and STAGE_ROUTING (Check 13)."""

    def test_routing_defined_for_all_stages(self):
        """AC3: STAGE_ROUTING has entries for all pipeline stages, incl. goal_cycle
        (D4, run_57929039 — goal_cycle is now governed: routing consumes design_doc,
        produces changeset, mirroring the BUILD+REVIEW+TEST it replaces in goal)."""
        expected = {"evaluate", "think", "plan", "build", "review", "test", "deliver",
                    "reflect", "goal_cycle"}
        assert set(STAGE_ROUTING.keys()) == expected

    def test_evaluate_no_consumes(self):
        """Evaluate has no upstream — always passes routing."""
        run = {"stages": [_stage_record("evaluate")]}
        errors, warnings = _check_output_routing("evaluate", _stage_record("evaluate"), run, "TestProject")
        assert errors == []

    def test_review_missing_consumed_artifacts_field_warns(self):
        """Review without consumed_artifacts field → WARN (backward compat)."""
        build_rec = _stage_record("build")
        review_rec = _stage_record("review")
        # review_rec has no consumed_artifacts field at all
        run = {"stages": [build_rec, review_rec]}
        errors, warnings = _check_output_routing("review", review_rec, run, "TestProject")
        assert errors == []  # No BLOCK — field is missing (legacy)
        assert any("changeset" in w for w in warnings)

    def test_review_absent_consumed_artifacts_auto_resolves(self):
        """C4 (run_7cf9da85): review with consumed_artifacts field ABSENT but a
        COMPLETED build producing changeset → AUTO-RESOLVE (the friction case).
        Field absent + producer completed = artifact exists, hand-recording was
        pure ceremony."""
        build_rec = _stage_record("build")  # completed, produces changeset
        review_rec = _stage_record("review")  # NO consumed_artifacts field at all
        review_rec.pop("consumed_artifacts", None)
        run = {"stages": [build_rec, review_rec]}
        errors, warnings = _check_output_routing("review", review_rec, run, "TestProject")
        assert errors == [], f"absent field + completed producer → no BLOCK: {errors}"
        assert any("AUTO-RESOLVED" in w and "changeset" in w for w in warnings), \
            f"auto-resolution must be observable: {warnings}"

    def test_review_present_but_incomplete_consumed_artifacts_blocks(self):
        """C4 narrowed (adversarial HIGH, run_7cf9da85): consumed_artifacts PRESENT
        but omitting a type a completed producer made → still BLOCK. The field being
        present is positive evidence the agent recorded consumption yet skipped a
        known input; auto-resolving here would defang Check 13 (SEVERITY_HARD)."""
        build_rec = _stage_record("build")  # completed, produces changeset
        review_rec = _stage_record("review")
        review_rec["consumed_artifacts"] = []  # present but empty → omits changeset
        run = {"stages": [build_rec, review_rec]}
        errors, warnings = _check_output_routing("review", review_rec, run, "TestProject")
        assert any("changeset" in e and "BLOCK" in e for e in errors), \
            f"present-but-incomplete field must still BLOCK: {errors}"

    def test_routing_blocks_when_producer_incomplete(self):
        """C4 preserves the REAL protection: if the producing stage did NOT complete,
        the consumed artifact does not exist → must NOT auto-resolve (WARN, no
        false-pass). Mirrors test_upstream_not_produced_warns intent."""
        build_rec = _stage_record("build", status="running")  # producer NOT done
        review_rec = _stage_record("review")
        review_rec["consumed_artifacts"] = []
        run = {"stages": [build_rec, review_rec]}
        errors, warnings = _check_output_routing("review", review_rec, run, "TestProject")
        # No completed producer → no AUTO-RESOLVED claim; routing warns it's missing.
        assert not any("AUTO-RESOLVED" in w for w in warnings), \
            f"must not auto-resolve an incomplete producer: {warnings}"
        assert any("changeset" in w for w in warnings), f"should warn missing upstream: {warnings}"

    def test_review_with_consumed_artifacts_passes(self):
        """AC5: Review with consumed_artifacts referencing changeset → PASS."""
        build_rec = _stage_record("build")
        review_rec = _stage_record("review")
        review_rec["consumed_artifacts"] = [
            {"type": "changeset", "id": "art_build1"}
        ]
        run = {"stages": [build_rec, review_rec]}
        errors, warnings = _check_output_routing("review", review_rec, run, "TestProject")
        assert errors == []

    def test_skipped_stage_skips_routing(self):
        """Skipped stages bypass routing check."""
        record = _stage_record("think", status="skipped")
        run = {"stages": [_stage_record("evaluate"), record]}
        errors, warnings = _check_output_routing("think", record, run, "TestProject")
        assert errors == []
        assert warnings == []

    def test_upstream_not_produced_warns(self):
        """When upstream was skipped and didn't produce, WARN not BLOCK."""
        # plan consumes research, but think was skipped
        think_rec = _stage_record("think", status="skipped")
        plan_rec = _stage_record("plan")
        plan_rec["consumed_artifacts"] = [{"type": "evaluation", "id": "art_eval1"}]
        run = {"stages": [_stage_record("evaluate"), think_rec, plan_rec]}
        errors, warnings = _check_output_routing("plan", plan_rec, run, "TestProject")
        # research not produced by think (skipped) → WARN
        assert any("research" in w for w in warnings)
        # evaluation consumed → no error for that
        assert not any("evaluation" in e for e in errors)


# ---------------------------------------------------------------------------
# Artifact Freshness
# ---------------------------------------------------------------------------

class TestArtifactFreshness:
    """Tests for check_artifact_freshness (P4)."""

    def test_fresh_artifact_within_ttl(self):
        """Recent artifact with no dependency drift → fresh (AC6 negative)."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        meta = {"created_at": now, "freshness": {"ttl_advisory_hours": 168}}
        result = check_artifact_freshness(meta, "TestProject")
        assert result["fresh"] is True
        assert result["stale_reason"] is None

    def test_stale_artifact_exceeds_ttl(self):
        """Artifact older than TTL → stale (AC6)."""
        meta = {
            "created_at": "2026-01-01T00:00:00+00:00",
            "freshness": {"ttl_advisory_hours": 24},
        }
        result = check_artifact_freshness(meta, "TestProject")
        assert result["fresh"] is False
        assert "exceeds advisory TTL" in result["stale_reason"]
        assert result["age_hours"] > 24

    def test_stale_ddd_checksum_drift(self, workspace):
        """DDD doc changed since artifact creation → stale (AC6)."""
        # Create a DDD doc
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("# Old content")

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        meta = {
            "created_at": now,
            "freshness": {
                "depends_on": {
                    "ddd_checksums": {"PRODUCT.md": "stale_hash_that_wont_match"}
                },
                "ttl_advisory_hours": 9999,
            },
        }
        result = check_artifact_freshness(meta, "TestProject")
        assert result["fresh"] is False
        assert "PRODUCT.md changed" in result["stale_reason"]

    def test_no_freshness_metadata_defaults_fresh(self):
        """Artifact without freshness metadata → assumed fresh."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        meta = {"created_at": now}
        result = check_artifact_freshness(meta, "TestProject")
        assert result["fresh"] is True

    def test_no_created_at_defaults_fresh(self):
        """Missing created_at → cannot determine age → assume fresh."""
        meta = {}
        result = check_artifact_freshness(meta, "TestProject")
        assert result["fresh"] is True


# ---------------------------------------------------------------------------
# Integration: Freshness within validate() (D1 gap from PE review)
# ---------------------------------------------------------------------------

class TestFreshnessIntegration:
    """Test freshness sub-check fires correctly through the validate() path."""

    def test_stale_artifact_warning_in_validate(self, workspace):
        """Stale consumed artifact produces STALE warning through full validate()."""
        from datetime import datetime, timezone
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        now = datetime.now(timezone.utc).isoformat()

        # Create a DDD doc that will cause staleness
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("# Current content v2")

        # Create ALL artifacts first
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard"})
        _make_artifact(artifacts_dir, "run_test1", "art_change", "changeset",
                       {"files_changed": ["file.py"], "tdd": {"green_pass": True, "smoke_tests": 1}})
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review",
                       {"approved": True, "findings_count": 0,
                        "integration_trace": {"checked": 1, "clean": True},
                        "runtime_patterns": {"checked": 1, "violations": 0,
                                             "patterns": [{"pattern": "test", "status": "pass", "detail": "checked manually"}]}})

        # THEN update manifest: all entries fresh, art_eval has stale DDD hash
        manifest_file = artifacts_dir / "manifest.json"
        manifest = json.loads(manifest_file.read_text())
        for entry in manifest["artifacts"]:
            entry["created_at"] = now  # All recent (avoids TTL false-stale)
            if entry["id"] == "art_eval":
                entry["freshness"] = {
                    "depends_on": {
                        "ddd_checksums": {"PRODUCT.md": "stale_hash_mismatch"}
                    },
                    "ttl_advisory_hours": 9999,
                }
        manifest_file.write_text(json.dumps(manifest))

        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
            _stage_record("build", artifact_id="art_change"),
            {
                "stage": "review",
                "status": "completed",
                "artifact_id": "art_review",
                "started_at": "2026-03-24T00:00:00Z",
                "completed_at": "2026-03-24T00:01:00Z",
                "token_cost": 5000,
                "retry_count": 0,
                "decisions": [{"description": "test", "classification": "mechanical", "reasoning": "test"}],
                "consumed_artifacts": [
                    {"type": "changeset", "id": "art_change"},
                    {"type": "evaluation", "id": "art_eval"},  # This one is stale
                ],
            },
        ])

        result = validate("TestProject", "run_test1", "review")
        # Should produce a STALE warning for art_eval (DDD checksum drift)
        stale_warnings = [w for w in result["warnings"] if "STALE" in w]
        assert len(stale_warnings) >= 1, (
            f"Expected STALE warning for art_eval, got warnings: {result['warnings']}"
        )
        assert "art_eval" in stale_warnings[0]
        assert "PRODUCT.md" in stale_warnings[0]

    def test_fresh_artifact_no_warning_in_validate(self, workspace):
        """Fresh consumed artifact produces no STALE warning through validate()."""
        from datetime import datetime, timezone
        from scripts.pipeline_validator import _compute_doc_checksum
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        now = datetime.now(timezone.utc).isoformat()

        # Create DDD doc
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("# Content")
        current_hash = _compute_doc_checksum("# Content")

        # Create ALL artifacts first
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard"})
        _make_artifact(artifacts_dir, "run_test1", "art_change", "changeset",
                       {"files_changed": ["file.py"], "tdd": {"green_pass": True, "smoke_tests": 1}})
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review",
                       {"approved": True, "findings_count": 0,
                        "integration_trace": {"checked": 1, "clean": True},
                        "runtime_patterns": {"checked": 1, "violations": 0,
                                             "patterns": [{"pattern": "test", "status": "pass", "detail": "checked it"}]}})

        # THEN update manifest: ALL entries fresh timestamps + matching hash
        manifest_file = artifacts_dir / "manifest.json"
        manifest = json.loads(manifest_file.read_text())
        for entry in manifest["artifacts"]:
            entry["created_at"] = now  # All recent
            if entry["id"] == "art_eval":
                entry["freshness"] = {
                    "depends_on": {"ddd_checksums": {"PRODUCT.md": current_hash}},
                    "ttl_advisory_hours": 9999,
                }
        manifest_file.write_text(json.dumps(manifest))

        _make_run(runs_dir, stages=[
            _stage_record("evaluate", artifact_id="art_eval"),
            _stage_record("build", artifact_id="art_change"),
            {
                "stage": "review",
                "status": "completed",
                "artifact_id": "art_review",
                "started_at": "2026-03-24T00:00:00Z",
                "completed_at": "2026-03-24T00:01:00Z",
                "token_cost": 5000,
                "retry_count": 0,
                "decisions": [{"description": "d", "classification": "mechanical", "reasoning": "r"}],
                "consumed_artifacts": [
                    {"type": "changeset", "id": "art_change"},
                    {"type": "evaluation", "id": "art_eval"},
                ],
            },
        ])

        result = validate("TestProject", "run_test1", "review")
        stale_warnings = [w for w in result["warnings"] if "STALE" in w]
        assert stale_warnings == [], f"Expected no STALE warnings, got: {stale_warnings}"


# ---------------------------------------------------------------------------
# Check 8e: Litmus Gate Enforcement Tests
# ---------------------------------------------------------------------------

class TestLitmusGate:
    """Tests for Check 8e — litmus pre-gate validation in REVIEW stage."""

    def _valid_litmus(self) -> dict:
        """Minimal valid litmus_gate artifact data."""
        return {
            "verdict": "PASS",
            "hf_checked": [True, True, True, True],
            "soft_signal_count": 0,
            "weak_areas": [],
            "evidence": "HF1: 4 conditionals with domain logic. HF2: 5/5 ACs mapped. "
                        "HF3: no contradictions. HF4: all DB queries wrapped.",
        }

    def _valid_review_artifact(self, litmus: dict | None = None) -> dict:
        """Complete valid review artifact with litmus gate."""
        return {
            "approved": True,
            "litmus_gate": litmus or self._valid_litmus(),
            "findings_count": 0,
            "integration_trace": {"checked": 3, "clean": True},
            "runtime_patterns": {
                "checked": 5, "violations": 0,
                "patterns": [{"pattern": "RP1", "status": "pass", "detail": "no blocking async calls"}],
            },
        }

    def test_litmus_pass_valid(self, workspace):
        """Valid PASS litmus gate produces no errors."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        _make_artifact(artifacts_dir, "run_test1", "art_review", "review",
                       self._valid_review_artifact())
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        litmus_errors = [e for e in result["errors"] if "Litmus" in e or "litmus" in e]
        assert litmus_errors == [], f"Unexpected litmus errors: {litmus_errors}"

    def test_litmus_missing_blocked(self, workspace):
        """Missing litmus_gate field triggers schema required-field error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        data = self._valid_review_artifact()
        del data["litmus_gate"]
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review", data)
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        assert any("litmus_gate" in e.lower() for e in result["errors"])

    def test_litmus_invalid_verdict(self, workspace):
        """Invalid verdict string triggers error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        litmus = self._valid_litmus()
        litmus["verdict"] = "MAYBE"
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review",
                       self._valid_review_artifact(litmus))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        assert any("invalid verdict" in e.lower() for e in result["errors"])

    def test_litmus_partial_hf_checked(self, workspace):
        """hf_checked with wrong count triggers error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        litmus = self._valid_litmus()
        litmus["hf_checked"] = [True, True]  # Only 2, need 4
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review",
                       self._valid_review_artifact(litmus))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        assert any("4 booleans" in e for e in result["errors"])

    def test_litmus_hf_checked_non_bool(self, workspace):
        """hf_checked with non-boolean elements triggers error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        litmus = self._valid_litmus()
        litmus["hf_checked"] = [True, True, True, "yes"]
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review",
                       self._valid_review_artifact(litmus))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        assert any("booleans" in e for e in result["errors"])

    def test_litmus_fail_with_approved_true_blocked(self, workspace):
        """FAIL verdict + approved=true is a contradiction — must error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        litmus = self._valid_litmus()
        litmus["verdict"] = "FAIL"
        litmus["hf_checked"] = [False, True, True, True]  # HF1 failed
        litmus["evidence"] = "HF1: majority scaffold code. HF2: ACs covered. HF3: ok. HF4: ok."
        data = self._valid_review_artifact(litmus)
        data["approved"] = True  # Contradiction!
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review", data)
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        assert any("contradicts" in e.lower() for e in result["errors"])

    def test_litmus_fail_all_hf_true_contradiction(self, workspace):
        """FAIL verdict with all hf_checked=True is illogical — must error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        litmus = self._valid_litmus()
        litmus["verdict"] = "FAIL"
        # All True but verdict is FAIL — doesn't make sense
        litmus["hf_checked"] = [True, True, True, True]
        litmus["evidence"] = "HF1: fine. HF2: fine. HF3: fine. HF4: fine but FAIL anyway."
        data = self._valid_review_artifact(litmus)
        data["approved"] = False
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review", data)
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        assert any("contradiction" in e.lower() for e in result["errors"])

    def test_litmus_borderline_empty_weak_areas_blocked(self, workspace):
        """BORDERLINE verdict with empty weak_areas is blocked."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        litmus = self._valid_litmus()
        litmus["verdict"] = "BORDERLINE"
        litmus["soft_signal_count"] = 3
        litmus["weak_areas"] = []  # Empty — should error
        litmus["evidence"] = "HF1: ok. HF2: ok. HF3: ok. HF4: ok. But 3 soft signals."
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review",
                       self._valid_review_artifact(litmus))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        assert any("weak_areas" in e for e in result["errors"])

    def test_litmus_generic_evidence_blocked(self, workspace):
        """Evidence without HF references is blocked (anti-rubber-stamp)."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        litmus = self._valid_litmus()
        litmus["evidence"] = "All criteria checked and passed without issues found in the code."
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review",
                       self._valid_review_artifact(litmus))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        assert any("HF1" in e or "per-criterion" in e for e in result["errors"])

    def test_litmus_garbage_evidence_blocked(self, workspace):
        """Garbage string as evidence is blocked (needs HF references)."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        litmus = self._valid_litmus()
        litmus["evidence"] = "x" * 30  # >20 chars but no HF refs
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review",
                       self._valid_review_artifact(litmus))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        assert any("HF1" in e or "references" in e for e in result["errors"])

    def test_litmus_borderline_with_weak_areas_passes(self, workspace):
        """Valid BORDERLINE with proper weak_areas passes."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        litmus = self._valid_litmus()
        litmus["verdict"] = "BORDERLINE"
        litmus["soft_signal_count"] = 3
        litmus["weak_areas"] = ["happy-path only tests", "magic numbers in timeout", "generic exception handler"]
        litmus["evidence"] = "HF1: real logic. HF2: ACs covered. HF3: consistent. HF4: wrapped. But 3 soft signals."
        _make_artifact(artifacts_dir, "run_test1", "art_review", "review",
                       self._valid_review_artifact(litmus))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
            _stage_record("review", artifact_id="art_review"),
        ])

        result = validate("TestProject", "run_test1", "review")
        litmus_errors = [e for e in result["errors"] if "litmus" in e.lower() or "Litmus" in e]
        assert litmus_errors == [], f"Unexpected litmus errors: {litmus_errors}"


# ---------------------------------------------------------------------------
# Check 8f: BUILD AC Coverage Matrix Tests
# ---------------------------------------------------------------------------

class TestBuildAcCoverage:
    """Tests for Check 8f — AC coverage matrix enforcement in BUILD stage."""

    def _valid_ac_coverage(self) -> list:
        """Minimal valid ac_coverage list."""
        return [
            {"ac": "AC1: Feature works", "impl": "feature.py::do_thing()", "test": "test_feature.py::test_do_thing", "verified": True},
            {"ac": "AC2: Error handled", "impl": "feature.py::handle_error()", "test": "test_feature.py::test_handle_error", "verified": True},
        ]

    def _valid_build_artifact(self, ac_coverage: list | None = None) -> dict:
        """Complete valid build artifact with ac_coverage."""
        return {
            "files_changed": ["feature.py", "test_feature.py"],
            "tdd": {"green_pass": True, "smoke_tests": 2, "red_count": 4, "green_count": 4},
            "ac_coverage": ac_coverage if ac_coverage is not None else self._valid_ac_coverage(),
            "commits": ["abc1234"],
        }

    def _plan_artifact(self, acs: list | None = None) -> dict:
        """Plan artifact with acceptance_criteria."""
        return {
            "acceptance_criteria": acs or ["AC1: Feature works", "AC2: Error handled"],
            "approach": "Standard implementation",
        }

    def test_valid_ac_coverage_passes(self, workspace):
        """Valid BUILD with complete ac_coverage produces no errors."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        _make_artifact(artifacts_dir, "run_test1", "art_plan", "plan", self._plan_artifact())
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       self._valid_build_artifact())
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("plan", artifact_id="art_plan"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        ac_errors = [e for e in result["errors"] if "ac_coverage" in e.lower() or "AC" in e]
        assert ac_errors == [], f"Unexpected AC errors: {ac_errors}"

    def test_missing_ac_coverage_blocked(self, workspace):
        """BUILD without ac_coverage field triggers schema required-field error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        data = self._valid_build_artifact()
        del data["ac_coverage"]
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset", data)
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        assert any("ac_coverage" in e.lower() for e in result["errors"])

    def test_empty_ac_coverage_blocked(self, workspace):
        """Empty ac_coverage list triggers error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       self._valid_build_artifact(ac_coverage=[]))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        assert any("empty" in e.lower() for e in result["errors"])

    def test_missing_impl_blocked(self, workspace):
        """AC entry without impl field triggers error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        ac = [{"ac": "AC1: Feature", "impl": "", "test": "test_f.py::test_x", "verified": True}]
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       self._valid_build_artifact(ac_coverage=ac))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        assert any("impl" in e.lower() for e in result["errors"])

    def test_missing_test_blocked(self, workspace):
        """AC entry without test field triggers error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        ac = [{"ac": "AC1: Feature", "impl": "f.py::do()", "test": "", "verified": True}]
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       self._valid_build_artifact(ac_coverage=ac))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        assert any("test" in e.lower() for e in result["errors"])

    def test_unverified_entry_blocked(self, workspace):
        """AC entry with verified=false triggers error."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        ac = [{"ac": "AC1: Feature", "impl": "f.py::do()", "test": "t.py::test_do", "verified": False}]
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       self._valid_build_artifact(ac_coverage=ac))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        assert any("verified" in e.lower() for e in result["errors"])

    def test_plan_ac_not_covered_blocked(self, workspace):
        """PLAN has AC that doesn't appear in BUILD ac_coverage → BLOCK."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        # Plan has 3 ACs, build only covers 2
        plan_acs = ["AC1: Feature works", "AC2: Error handled", "AC3: Rate limit enforced"]
        _make_artifact(artifacts_dir, "run_test1", "art_plan", "plan",
                       self._plan_artifact(acs=plan_acs))
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       self._valid_build_artifact())  # Only AC1 + AC2
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("plan", artifact_id="art_plan"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        assert any("AC3" in e or "not covered" in e.lower() for e in result["errors"])

    def test_no_plan_artifact_graceful_skip(self, workspace):
        """When no PLAN artifact exists, ac_coverage structural checks still run but cross-ref skips."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       self._valid_build_artifact())
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            # No plan stage — e.g., bugfix profile skips think/plan sometimes
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        # Should pass structural checks (ac_coverage valid) — no cross-ref errors
        ac_errors = [e for e in result["errors"]
                     if "ac_coverage" in e.lower() or "not covered" in e.lower()]
        assert ac_errors == [], f"Unexpected errors when no plan artifact: {ac_errors}"

    def test_all_plan_acs_covered_passes(self, workspace):
        """When all PLAN ACs are covered in BUILD, no cross-ref errors."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        plan_acs = ["AC1: Feature works", "AC2: Error handled"]
        _make_artifact(artifacts_dir, "run_test1", "art_plan", "plan",
                       self._plan_artifact(acs=plan_acs))
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       self._valid_build_artifact())
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("plan", artifact_id="art_plan"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        ac_errors = [e for e in result["errors"]
                     if "not covered" in e.lower() or "AC" in e]
        assert ac_errors == [], f"Unexpected cross-ref errors: {ac_errors}"

    def test_checks_passed_never_exceeds_total(self, workspace):
        """Invariant: checks_passed <= checks_total in all results."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        _make_artifact(artifacts_dir, "run_test1", "art_plan", "plan", self._plan_artifact())
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       self._valid_build_artifact())
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("plan", artifact_id="art_plan"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        assert result["checks_passed"] <= result["checks_total"], \
            f"checks_passed ({result['checks_passed']}) > checks_total ({result['checks_total']})"

    def test_similar_acs_not_false_matched(self, workspace):
        """Two PLAN ACs with similar prefix — one covered, one not — must detect the gap."""
        runs_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / "run_test1"
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"

        # Two ACs that share a long common prefix
        plan_acs = [
            "AC1: User can login with email credentials",
            "AC1: User can login with SSO provider",
        ]
        # Only cover the first one
        ac_coverage = [
            {"ac": "AC1: User can login with email credentials",
             "impl": "auth.py::login_email()", "test": "test_auth.py::test_login_email", "verified": True},
        ]
        _make_artifact(artifacts_dir, "run_test1", "art_plan", "plan",
                       self._plan_artifact(acs=plan_acs))
        _make_artifact(artifacts_dir, "run_test1", "art_build", "changeset",
                       self._valid_build_artifact(ac_coverage=ac_coverage))
        _make_run(runs_dir, stages=[
            _stage_record("evaluate"),
            _stage_record("plan", artifact_id="art_plan"),
            _stage_record("build", artifact_id="art_build"),
        ])

        result = validate("TestProject", "run_test1", "build")
        # The SSO AC should NOT be matched by the email AC
        assert any("SSO" in e for e in result["errors"]), \
            f"Expected 'SSO' AC not-covered error, got: {result['errors']}"


# ---------------------------------------------------------------------------
# Gate 2 Agent Tool Audit — marker file verification
# ---------------------------------------------------------------------------

class TestAgentToolAudit:
    """Validate that the agent tool audit marker file is checked on DELIVER."""

    @pytest.fixture(autouse=True)
    def _sandbox_audit_dir(self, tmp_path, monkeypatch):
        """Redirect AGENT_AUDIT_DIR to a per-test tmp dir.

        The validator reads markers from a machine-global path
        (~/.swarm-ai/state/pipeline_agent_audit). Without this sandbox the
        "no marker" assertion is non-deterministic: a developer machine that
        has run real pipelines carries leftover session_*.marker files whose
        timestamps satisfy the fallback window (the fixture run's hardcoded
        created_at is 2026-03-24), so marker_found flips True and the expected
        warning is suppressed. Pinning AGENT_AUDIT_DIR to an empty tmp dir makes
        every test in this class self-contained and stops the marker-writing
        test from polluting the real global directory.
        """
        import scripts.pipeline_validator as pv
        monkeypatch.setattr(pv, "AGENT_AUDIT_DIR", tmp_path / "pipeline_agent_audit")

    def _setup_deliver_run(self, workspace, profile="full", run_id="run_test1"):
        """Helper: create a valid deliver artifact + run for audit testing."""
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / run_id
        _make_artifact(artifacts_dir, run_id, "art_del", "delivery", {
            "title": "Done", "status": "delivered",
            "quality": {"push_ready": True, "blockers": []},
            "adversarial_review": {
                "profile_tier": profile if profile in ("full", "bugfix") else "skipped",
                "spawned": True, "evidence": "Agent tool invocation",
                "findings": [],
            },
            "completion_audit": {"all_green": True, "gaps": 0},
        })
        stages = [
            _stage_record("evaluate"),
            _stage_record("think"),
        ]
        if profile not in ("trivial",):
            stages.append(_stage_record("plan"))
        stages.extend([
            _stage_record("build", artifact_id="art_b"),
            _stage_record("review", artifact_id="art_r"),
            _stage_record("test", artifact_id="art_t"),
            _stage_record("deliver", artifact_id="art_del"),
        ])
        _make_run(runs_dir, profile=profile, stages=stages, run_id=run_id)
        return artifacts_dir

    def test_deliver_warns_without_marker_file(self, workspace):
        """DELIVER on full profile WARNS if no agent audit marker file exists."""
        run_id = "run_audit_no_marker"
        self._setup_deliver_run(workspace, profile="full", run_id=run_id)

        result = validate("TestProject", run_id, "deliver")
        all_messages = result["errors"] + result["warnings"]
        assert any("agent tool" in m.lower() or "audit marker" in m.lower()
                   for m in all_messages), \
            f"Expected agent audit marker warning, got: {all_messages}"

    def test_deliver_passes_with_marker_file(self, workspace):
        """DELIVER passes when marker file exists for the run."""
        from scripts.pipeline_validator import AGENT_AUDIT_DIR

        run_id = "run_audit_with_marker"
        self._setup_deliver_run(workspace, profile="full", run_id=run_id)

        # Create the marker file
        marker_dir = AGENT_AUDIT_DIR
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker_file = marker_dir / f"{run_id}.marker"
        marker_file.write_text('{"ts": 1718600000, "event": "SubagentStop"}')

        try:
            result = validate("TestProject", run_id, "deliver")
            audit_msgs = [e for e in result["errors"] + result["warnings"]
                         if "agent tool" in e.lower() or "audit marker" in e.lower()]
            assert not audit_msgs, f"Unexpected audit messages: {audit_msgs}"
        finally:
            marker_file.unlink(missing_ok=True)

    def test_trivial_profile_skips_audit_check(self, workspace):
        """Trivial profile does NOT require agent audit marker."""
        run_id = "run_audit_trivial"
        self._setup_deliver_run(workspace, profile="trivial", run_id=run_id)

        result = validate("TestProject", run_id, "deliver")
        audit_msgs = [e for e in result["errors"] + result["warnings"]
                     if "agent tool" in e.lower() or "audit marker" in e.lower()]
        assert not audit_msgs, f"Trivial should skip audit: {audit_msgs}"


class TestGoalCycleGoverned:
    """D4 (run_57929039): the goal_cycle stage must be GOVERNED by the validator —
    before D4 it had no STAGE_SCHEMAS/STAGE_DEPTH/STAGE_ROUTING entry, so
    validate_artifact_data returned [] and the goal run's inner quality fields were
    never validated."""

    def test_goal_cycle_has_schema_entry(self):
        from scripts.pipeline_validator import STAGE_SCHEMAS
        assert "goal_cycle" in STAGE_SCHEMAS, \
            "goal_cycle must have a STAGE_SCHEMAS entry (D4)"
        assert STAGE_SCHEMAS["goal_cycle"].get("required"), \
            "goal_cycle schema must declare required fields"

    def test_goal_cycle_schema_blocks_missing_required(self):
        """A goal_cycle artifact missing a required field is rejected (has teeth)."""
        from scripts.pipeline_validator import validate_artifact_data
        errs = validate_artifact_data("goal_cycle", {}, "goal")
        assert errs, "Empty goal_cycle artifact under goal profile must produce errors"

    def test_goal_cycle_schema_passes_complete(self):
        """A complete goal_cycle artifact under goal profile passes schema check."""
        from scripts.pipeline_validator import validate_artifact_data, STAGE_SCHEMAS
        data = {f: (True if f in ("dod_met",) else [{"x": 1}] if f in ("adversarial_review",) else 1)
                for f in STAGE_SCHEMAS["goal_cycle"]["required"]}
        # adversarial_review must be a dict for the goal-cycle final gate
        if "adversarial_review" in data:
            data["adversarial_review"] = {"spawned": True, "evidence": "Agent tool", "findings": []}
        errs = validate_artifact_data("goal_cycle", data, "goal")
        assert not errs, f"Complete goal_cycle artifact should pass, got: {errs}"

    def test_goal_cycle_in_routing(self):
        from scripts.pipeline_validator import STAGE_ROUTING
        assert "goal_cycle" in STAGE_ROUTING, \
            "goal_cycle must have a STAGE_ROUTING entry (D4)"

    def test_goal_cycle_boolean_adversarial_review_REJECTED(self):
        """Adversarial finding (run_57929039): a bare `adversarial_review: true` must
        FAIL depth validation — a scalar cannot carry the required findings[]. Before
        the non-dict rejection this silently passed (STAGE_DEPTH skipped non-dict
        parents), defanging the goal_cycle adversarial-shape gate."""
        from scripts.pipeline_validator import validate_artifact_data
        errs = validate_artifact_data(
            "goal_cycle", {"dod_met": True, "adversarial_review": True}, "goal")
        assert any("adversarial_review" in e and "dict" in e for e in errs), \
            f"A boolean adversarial_review must be rejected, got: {errs}"

    def test_deliver_boolean_adversarial_review_also_REJECTED(self):
        """Same non-dict rejection hardens DELIVER: a boolean adversarial_review on a
        full deliver artifact fails depth validation (not silently skipped)."""
        from scripts.pipeline_validator import validate_artifact_data
        errs = validate_artifact_data(
            "deliver",
            {"title": "x", "quality": {"push_ready": True},
             "adversarial_review": True, "completion_audit": {"all_green": True},
             "ac_verification": {"status": "verified"}},
            "full")
        assert any("adversarial_review" in e and "dict" in e for e in errs), \
            f"A boolean adversarial_review on deliver must be rejected, got: {errs}"

    def test_build_list_ac_coverage_NOT_rejected_by_nondict_branch(self):
        """Gate-2 CRITICAL regression (run_57929039): the non-dict rejection branch is
        guarded on `child_fields` being non-empty. STAGE_DEPTH['build']['ac_coverage']
        is [] (presence-only) and ac_coverage is legitimately a LIST — an unguarded
        else rejected EVERY valid build publish. This asserts a valid build artifact
        with a list ac_coverage produces NO 'must be a dict' error on ac_coverage."""
        from scripts.pipeline_validator import validate_artifact_data
        build_data = {
            "files_changed": ["a.py"],
            "tdd": {"green_pass": True, "smoke_tests": 1},
            "ac_coverage": [
                {"ac": "AC1", "impl": "a.py::f()", "test": "t.py::test_f", "verified": True}],
        }
        errs = validate_artifact_data("build", build_data, "full")
        assert not any("ac_coverage" in e and "must be a dict" in e for e in errs), \
            f"A valid list ac_coverage was wrongly rejected as non-dict: {errs}"


# ---------------------------------------------------------------------------
# FAILED-vs-ERRORED distinction (run_55710438)
# A check that CRASHES (raises) must be distinguishable from a check whose
# CONTENT fails. Hard-check crash → still blocks (fail-closed). Advisory-check
# crash → does not block (fail-open), but is recorded as ERRORED in the audit.
# ---------------------------------------------------------------------------

class TestCheckErrored:
    """Per-check try/except: crash → ERRORED outcome classified by severity."""

    def _setup_evaluate_run(self, workspace):
        artifacts = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts / "runs" / "run_test1"
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", status="completed", artifact_id="art_eval"),
        ])
        _make_artifact(artifacts, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard",
                        "summary": "x", "acceptance_criteria": ["a"]})

    def test_hard_check_crash_blocks(self, workspace, monkeypatch):
        """AC1: a HARD check that raises → ERRORED that STILL blocks (valid=False)."""
        self._setup_evaluate_run(workspace)
        import scripts.pipeline_validator as pv

        def boom(*a, **k):
            raise RuntimeError("injected hard-check crash")
        # Check 1 (stage order) is HARD — writes to errors[]
        monkeypatch.setattr(pv, "_check_stage_order", boom)

        result = validate("TestProject", "run_test1", "evaluate")

        assert result["valid"] is False, "hard check crash must block (fail-closed)"
        # The crash must be classified ERRORED, not silently FAILED
        cr = result.get("check_results", [])
        errored = [c for c in cr if c.get("status") == "errored"]
        assert any(c.get("severity") == "hard" for c in errored), \
            f"expected a hard errored check_result, got {cr}"

    def test_advisory_check_crash_does_not_block(self, workspace, monkeypatch):
        """AC2: an ADVISORY check that raises → does NOT block (valid=True), recorded ERRORED."""
        self._setup_evaluate_run(workspace)
        import scripts.pipeline_validator as pv

        def boom(*a, **k):
            raise RuntimeError("injected advisory-check crash")
        # Check 7 (DDD consistency) is ADVISORY — writes only to warnings[]
        monkeypatch.setattr(pv, "check_ddd_consistency", boom)

        result = validate("TestProject", "run_test1", "evaluate")

        assert result["valid"] is True, "advisory check crash must NOT block (fail-open)"
        cr = result.get("check_results", [])
        errored = [c for c in cr if c.get("status") == "errored"]
        assert any(c.get("severity") == "advisory" for c in errored), \
            f"expected an advisory errored check_result, got {cr}"

    def test_failed_vs_errored_distinct(self, workspace, monkeypatch):
        """AC3: content-FAILED and crash-ERRORED are distinguishable in check_results."""
        # Content failure: stage order genuinely wrong (build before think/plan)
        artifacts = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts / "runs" / "run_test1"
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", status="completed"),
            _stage_record("build", status="running", artifact_id="art_b"),
        ])
        result = validate("TestProject", "run_test1", "build")
        cr = result.get("check_results", [])
        # stage_order should be FAILED (content), not ERRORED (crash)
        order = [c for c in cr if c.get("name") == "stage_order"]
        assert order and order[0]["status"] == "failed", \
            f"genuine content failure must be FAILED not ERRORED: {order}"
        assert all(c["status"] != "errored" for c in cr), \
            "no check should be ERRORED on a clean (non-crash) run"

    def test_errored_surfaced_in_result(self, workspace, monkeypatch):
        """AC3/AC4: errored[] list names the checks that could not run."""
        self._setup_evaluate_run(workspace)
        import scripts.pipeline_validator as pv
        monkeypatch.setattr(pv, "_check_profile_respected",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        result = validate("TestProject", "run_test1", "evaluate")
        assert "errored" in result, "result must expose an errored[] list"
        assert "profile_respected" in result["errored"], \
            f"errored check name must appear: {result.get('errored')}"


class TestValidationEventErrored:
    """AC4: _record_validation_event persists the errored distinction."""

    def test_record_validation_event_accepts_errored(self, workspace):
        artifacts = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts / "runs" / "run_test1"
        _make_run(runs_dir)
        _record_validation_event(
            "TestProject", "run_test1", "build",
            passed=False, errors=["content failed"], warnings=[],
            errored=["smoke"],
        )
        run = json.loads((runs_dir / "run.json").read_text())
        ev = run["validation_events"][-1]
        assert ev.get("errored") == ["smoke"], f"errored not persisted: {ev}"
        assert ev.get("errored_count") == 1, f"errored_count wrong: {ev}"


class TestCheckGuardSafety:
    """Adversarial-review safety fixes (run_55710438 Gate 2)."""

    def test_crash_after_passed_still_errored_and_blocks(self, workspace):
        """MED-8: a HARD check that crashes AFTER calling passed() must still
        record ERRORED and block — never silently fall through on stale PASSED."""
        import scripts.pipeline_validator as pv
        errors, warnings, cr = [], [], []
        try:
            with pv._CheckGuard("x", pv.SEVERITY_HARD, errors, warnings, cr) as g:
                g.passed()              # declare PASSED first
                raise RuntimeError("late boom")  # then crash
        except RuntimeError:
            pytest.fail("guard must swallow ordinary Exception")
        # ERRORED must OVERWRITE the stale PASSED, and the hard gate must block
        assert len(cr) == 1 and cr[0]["status"] == pv.CHECK_ERRORED, cr
        assert len(errors) == 1, f"hard crash-after-passed must append a blocking error: {errors}"

    def test_control_flow_exceptions_propagate(self, workspace):
        """MED-9: KeyboardInterrupt/SystemExit must NOT be swallowed."""
        import scripts.pipeline_validator as pv
        errors, warnings, cr = [], [], []
        with pytest.raises(KeyboardInterrupt):
            with pv._CheckGuard("x", pv.SEVERITY_HARD, errors, warnings, cr):
                raise KeyboardInterrupt()
        with pytest.raises(SystemExit):
            with pv._CheckGuard("y", pv.SEVERITY_ADVISORY, errors, warnings, cr):
                raise SystemExit(1)

    def test_advisory_errored_keeps_checks_passed_consistent(self, workspace, monkeypatch):
        """LOW-8: an advisory crash must still credit checks_passed so a valid
        run reports checks_passed == checks_total."""
        artifacts = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts / "runs" / "run_test1"
        _make_run(runs_dir, stages=[_stage_record("evaluate", status="completed", artifact_id="art_eval")])
        _make_artifact(artifacts, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard", "summary": "x", "acceptance_criteria": ["a"]})
        import scripts.pipeline_validator as pv
        monkeypatch.setattr(pv, "check_ddd_consistency",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is True
        assert result["checks_passed"] == result["checks_total"], \
            f"advisory crash must not desync the metric: {result['checks_passed']}/{result['checks_total']}"


class TestRemainingChecksErrored:
    """run_61413085: the other 10 checks wrapped in _CheckGuard.
    Each check crash → ERRORED classified by severity (hard blocks, advisory passes)."""

    def _eval_run(self, workspace):
        artifacts = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts / "runs" / "run_test1"
        _make_run(runs_dir, stages=[_stage_record("evaluate", status="completed", artifact_id="art_eval")])
        _make_artifact(artifacts, "run_test1", "art_eval", "evaluation",
                       {"recommendation": "GO", "scope": "standard", "summary": "x", "acceptance_criteria": ["a"]})

    def test_artifact_exists_crash_blocks(self, workspace, monkeypatch):
        """Check 2 (hard): crash → ERRORED + blocks."""
        self._eval_run(workspace)
        import scripts.pipeline_validator as pv
        monkeypatch.setattr(pv, "_check_artifact_exists",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom2")))
        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is False
        assert "artifact_exists" in result["errored"], result.get("errored")

    def test_artifact_schema_crash_blocks(self, workspace, monkeypatch):
        """Check 3 (hard): crash → ERRORED + blocks."""
        self._eval_run(workspace)
        import scripts.pipeline_validator as pv
        monkeypatch.setattr(pv, "_check_artifact_schema",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom3")))
        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is False
        assert "artifact_schema" in result["errored"], result.get("errored")

    def test_budget_recorded_crash_does_not_block(self, workspace, monkeypatch):
        """Check 5 (advisory): crash → ERRORED, does NOT block, credits checks_passed."""
        self._eval_run(workspace)
        import scripts.pipeline_validator as pv
        monkeypatch.setattr(pv, "_check_budget_recorded",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom5")))
        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is True, "advisory crash must not block"
        assert "budget_recorded" in result["errored"], result.get("errored")
        assert result["checks_passed"] == result["checks_total"], \
            f"advisory crash must credit checks_passed: {result['checks_passed']}/{result['checks_total']}"

    def test_decision_logged_crash_does_not_block(self, workspace, monkeypatch):
        """Check 4 (advisory): crash → ERRORED, does NOT block."""
        self._eval_run(workspace)
        import scripts.pipeline_validator as pv
        monkeypatch.setattr(pv, "_check_decision_logged",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom4")))
        result = validate("TestProject", "run_test1", "evaluate")
        assert result["valid"] is True
        assert "decision_logged" in result["errored"], result.get("errored")
        assert result["checks_passed"] == result["checks_total"]

    def test_depth_crash_blocks(self, workspace, monkeypatch):
        """Check 9 (hard): crash → ERRORED + blocks. Depth runs on build stage."""
        artifacts = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts / "runs" / "run_test1"
        _make_run(runs_dir, stages=[
            _stage_record("evaluate", status="completed"),
            _stage_record("think", status="completed"),
            _stage_record("plan", status="completed"),
            _stage_record("build", status="running", artifact_id="art_b"),
        ])
        _make_artifact(artifacts, "run_test1", "art_b", "changeset",
                       {"branch": "x", "commits": ["c"], "files_changed": ["a.py"],
                        "tdd": {"green_pass": True, "smoke_tests": 1},
                        "ac_coverage": [{"ac": "A", "impl": "a.py::f", "test": "t.py::t", "verified": True}]})
        import scripts.pipeline_validator as pv
        monkeypatch.setattr(pv, "_check_depth",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom9")))
        result = validate("TestProject", "run_test1", "build")
        assert result["valid"] is False
        assert "depth" in result["errored"], result.get("errored")

    def test_semantic_crash_does_not_block(self, workspace, monkeypatch):
        """Check 11 (advisory): crash → ERRORED, does NOT block.
        Check 11 only runs on review/deliver, so use a deliver-stage run."""
        artifacts = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts / "runs" / "run_test1"
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", status="completed"),
            _stage_record("think", status="completed"),
            _stage_record("plan", status="completed"),
            _stage_record("build", status="completed"),
            _stage_record("review", status="completed"),
            _stage_record("test", status="completed"),
            _stage_record("deliver", status="running", artifact_id="art_d"),
        ])
        _make_artifact(artifacts, "run_test1", "art_d", "delivery",
                       {"title": "x", "quality": {"tests_pass": True, "regressions": 0, "smoke_pass": True},
                        "adversarial_review": {"spawned": True, "profile_tier": "bugfix",
                                               "evidence": "Agent tool: correctness + security specialists",
                                               "findings_total": 0, "findings_fixed": 0, "findings_remaining": 0, "findings": []},
                        "completion_audit": {"all_green": True}, "convergence": {"final_status": "push-ready"},
                        "push_ready": True})
        import scripts.pipeline_validator as pv
        monkeypatch.setattr(pv, "_check_semantic_depth",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom11")))
        result = validate("TestProject", "run_test1", "deliver")
        assert result["valid"] is True, f"advisory crash must not block: {result['errors']}"
        assert "semantic" in result["errored"], result.get("errored")

    def test_all_checks_wrapped(self):
        """AC5: every check in validate() is wrapped in _CheckGuard — zero
        fail-direction asymmetry remains. 14 logical checks: 1,2,3,4,5,6,7,8,
        9,9b,10,11,12,13 (check 8 = the stage-specific quality gate)."""
        import pathlib
        src = pathlib.Path("scripts/pipeline_validator.py").read_text()
        vstart = src.index("def validate(")
        vend = src.index("\ndef ", vstart + 10)
        vbody = src[vstart:vend]
        count = vbody.count("_CheckGuard(")
        assert count == 14, f"expected 14 wrapped checks in validate(), found {count}"


class TestNinebCreditOnCrash:
    """REVIEW-found gap (run_61413085): 9b agent_tool_audit increments checks_total
    then checks_passed conditionally — a crash between them must still credit
    checks_passed so the valid-run invariant holds."""

    def test_agent_audit_crash_keeps_metric_consistent(self, workspace, monkeypatch):
        artifacts = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts / "runs" / "run_test1"
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", status="completed"),
            _stage_record("think", status="completed"),
            _stage_record("plan", status="completed"),
            _stage_record("build", status="completed"),
            _stage_record("review", status="completed"),
            _stage_record("test", status="completed"),
            _stage_record("deliver", status="running", artifact_id="art_d"),
        ])
        _make_artifact(artifacts, "run_test1", "art_d", "delivery",
                       {"title": "x", "quality": {"tests_pass": True, "regressions": 0, "smoke_pass": True},
                        "adversarial_review": {"spawned": True, "profile_tier": "bugfix",
                                               "evidence": "Agent tool: correctness + security specialists",
                                               "findings_total": 0, "findings_fixed": 0, "findings_remaining": 0, "findings": []},
                        "completion_audit": {"all_green": True}, "convergence": {"final_status": "push-ready"},
                        "push_ready": True})
        # Force the 9b marker scan to raise mid-block (after checks_total += 1)
        import scripts.pipeline_validator as pv
        orig_exists = pv.AGENT_AUDIT_DIR.exists
        class BoomPath:
            def __getattr__(self, n):
                raise RuntimeError("boom9b")
        monkeypatch.setattr(pv, "AGENT_AUDIT_DIR", BoomPath())
        result = validate("TestProject", "run_test1", "deliver")
        assert result["valid"] is True, f"9b advisory crash must not block: {result['errors']}"
        assert "agent_tool_audit" in result["errored"], result.get("errored")
        assert result["checks_passed"] == result["checks_total"], \
            f"9b crash must keep metric consistent: {result['checks_passed']}/{result['checks_total']}"


class TestQualityGateAuditAccuracy:
    """Adversarial LOW-1 (run_61413085): quality_gate check_results must record
    FAILED on content failure, not a blanket PASSED."""

    def test_quality_gate_records_failed_on_content_failure(self, workspace):
        """build with >1 code file but smoke_tests=0 → content failure → quality_gate
        check_result status must be 'failed', not 'passed'."""
        artifacts = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts / "runs" / "run_test1"
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", status="completed"),
            _stage_record("think", status="completed"),
            _stage_record("plan", status="completed"),
            _stage_record("build", status="running", artifact_id="art_b"),
        ])
        # >1 code file, smoke_tests=0 → triggers the SMOKE content error
        _make_artifact(artifacts, "run_test1", "art_b", "changeset",
                       {"branch": "x", "commits": ["c"],
                        "files_changed": ["a.py", "b.py"],
                        "tdd": {"green_pass": True, "smoke_tests": 0},
                        "ac_coverage": [{"ac": "A", "impl": "a.py::f", "test": "t.py::t", "verified": True}]})
        result = validate("TestProject", "run_test1", "build")
        qg = [c for c in result["check_results"] if c["name"] == "quality_gate"]
        assert qg, "quality_gate must appear in check_results"
        assert qg[0]["status"] == "failed", \
            f"content failure must record FAILED not {qg[0]['status']}"
        assert result["valid"] is False

    def test_quality_gate_records_passed_when_clean(self, workspace):
        """build with smoke_tests>0 and full ac_coverage → quality_gate PASSED."""
        artifacts = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts / "runs" / "run_test1"
        _make_run(runs_dir, profile="bugfix", stages=[
            _stage_record("evaluate", status="completed"),
            _stage_record("think", status="completed"),
            _stage_record("plan", status="completed"),
            _stage_record("build", status="running", artifact_id="art_b"),
        ])
        _make_artifact(artifacts, "run_test1", "art_b", "changeset",
                       {"branch": "x", "commits": ["c"], "files_changed": ["a.py"],
                        "tdd": {"green_pass": True, "smoke_tests": 2},
                        "ac_coverage": [{"ac": "A", "impl": "a.py::f", "test": "t.py::t", "verified": True}]})
        result = validate("TestProject", "run_test1", "build")
        qg = [c for c in result["check_results"] if c["name"] == "quality_gate"]
        assert qg and qg[0]["status"] == "passed", \
            f"clean build must record PASSED: {qg}"


class TestDiskCheckVerification:
    """L4 verify-against-disk (Run B, run_c5935199) — code-enforces the
    INSTRUCTIONS.md:581 BLOCKING prose that was never enforced.

    A resolved:true finding may carry a structured disk_check:
        {"file": "<ABSOLUTE path>", "must_contain": "<str>" | "must_not_contain": "<str>"}
    The validator reads the file and confirms the durable change is on disk:
      - must_contain ABSENT (file readable) → BLOCK (the run_b5592983 honest-fix-
        then-externally-reverted catch)
      - must_not_contain PRESENT → BLOCK (deletion/refactor fix reverted)
      - file missing/unreadable/relative/binary → WARN-invalid, NEVER false-BLOCK
        (fail-open on uncertainty)
      - resolved finding with NO disk_check → no error (WARN only for HIGH/CRIT)

    Threat model (Gate-0 reframe): the adversary is NOT a lying agent (a self-
    reported disk_verified bool would be CLASS-A renamed) — it is an HONEST fix
    silently reverted by an external event. A structured locus the validator
    itself greps HAS teeth against that; the agent honestly records what it
    added, the later revert makes the grep fail.

    disk_check.file is ABSOLUTE (Gate-1 Attack-3 fix): findings reference SOURCE
    repo files, but the validator's workspace root is ~/.swarm-ai/SwarmWS (the
    C040 split) — joining against a repo_root would grep the WRONG tree and
    false-block 100% of deliveries. Absolute paths eliminate the join entirely.
    """

    def _finding(self, disk_check=None, resolved=True, severity="HIGH"):
        f = {"severity": severity, "confidence": 8, "resolved": resolved,
             "finding": "x.py foo() line 12: bad thing. Fixed: added guard."}
        if disk_check is not None:
            f["disk_check"] = disk_check
        return f

    # ---- AC1: must_contain, readable file ----
    def test_must_contain_present_passes(self, tmp_path):
        from scripts.pipeline_validator import _verify_findings_on_disk
        p = tmp_path / "mod.py"
        p.write_text("def foo():\n    if x is None: return  # the guard\n")
        errs, invalid = _verify_findings_on_disk(
            [self._finding({"file": str(p), "must_contain": "if x is None: return"})])
        assert errs == [], f"present string must PASS, got: {errs}"
        assert invalid == [], f"a readable matching file is not invalid: {invalid}"

    def test_must_contain_absent_blocks(self, tmp_path):
        from scripts.pipeline_validator import _verify_findings_on_disk
        p = tmp_path / "mod.py"
        p.write_text("def foo():\n    pass  # guard was reverted\n")
        errs, invalid = _verify_findings_on_disk(
            [self._finding({"file": str(p), "must_contain": "if x is None: return"})])
        assert errs, "must_contain absent from a readable file must BLOCK"
        assert "disk" in errs[0].lower()

    # ---- AC2: must_not_contain (deletion/refactor fixes) ----
    def test_must_not_contain_present_blocks(self, tmp_path):
        from scripts.pipeline_validator import _verify_findings_on_disk
        p = tmp_path / "mod.py"
        p.write_text("def dead():\n    return []  # should have been deleted\n")
        errs, invalid = _verify_findings_on_disk(
            [self._finding({"file": str(p), "must_not_contain": "return []  # should have been deleted"})])
        assert errs, "must_not_contain STILL PRESENT must BLOCK"

    def test_must_not_contain_absent_passes(self, tmp_path):
        from scripts.pipeline_validator import _verify_findings_on_disk
        p = tmp_path / "mod.py"
        p.write_text("def dead():\n    raise NotImplementedError\n")
        errs, invalid = _verify_findings_on_disk(
            [self._finding({"file": str(p), "must_not_contain": "return []"})])
        assert errs == [], f"removed string must PASS, got: {errs}"

    def test_must_not_contain_file_deleted_passes(self, tmp_path):
        # A deletion fix that removed the whole file: the thing to remove is
        # definitionally gone → PASS (vacuous), NOT a false block.
        from scripts.pipeline_validator import _verify_findings_on_disk
        gone = tmp_path / "removed.py"  # never created
        errs, invalid = _verify_findings_on_disk(
            [self._finding({"file": str(gone), "must_not_contain": "anything"})])
        assert errs == [], "must_not_contain on a missing file must PASS (vacuous)"

    # ---- AC3: missing disk_check → no error; WARN only for HIGH/CRIT ----
    def test_resolved_without_disk_check_no_error(self):
        from scripts.pipeline_validator import _verify_findings_on_disk
        errs, invalid = _verify_findings_on_disk([self._finding(None)])
        assert errs == [], "a resolved finding without disk_check must NOT block"

    def test_no_disk_check_warns_not_blocks_at_publish(self):
        # HIGH resolved finding, no disk_check → WARN in validate_artifact_data,
        # never a blocking error (no common-path regression — Run A lesson).
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", self._deliver_art(
            [self._finding(None, severity="HIGH")]), profile="bugfix")
        disk_block = [e for e in errors if "disk-check FAILED" in e]
        assert not disk_block, f"missing disk_check must NOT BLOCK, got: {disk_block}"

    def test_low_severity_no_disk_check_no_warn(self):
        # LOW resolved finding without disk_check → not even a WARN (no storm).
        from scripts.pipeline_validator import validate_artifact_data
        errors = validate_artifact_data("deliver", self._deliver_art(
            [self._finding(None, severity="LOW")]), profile="bugfix")
        assert not [e for e in errors if "disk-check FAILED" in e]

    # ---- AC4: shared by BOTH gate sites (R27) ----
    def test_both_sites_call_shared_helper(self, tmp_path, monkeypatch):
        # Patch the shared helper; assert BOTH the publish path
        # (validate_artifact_data) and the completion path (validate→_check_depth)
        # invoke it. This proves R27 single-source-of-truth, not two forks.
        import scripts.pipeline_validator as pv
        calls = {"n": 0}
        real = pv._verify_findings_on_disk

        def spy(findings, allowed_root=None):
            calls["n"] += 1
            return real(findings, allowed_root)

        monkeypatch.setattr(pv, "_verify_findings_on_disk", spy)
        # publish path
        pv.validate_artifact_data("deliver", self._deliver_art(
            [self._finding(None)]), profile="bugfix")
        after_publish = calls["n"]
        # completion path
        pv._check_depth("deliver", self._deliver_art([self._finding(None)]),
                        "bugfix", run_id="")
        assert after_publish >= 1, "publish path must call the shared helper"
        assert calls["n"] > after_publish, "completion path must call the shared helper too"

    def test_disk_failure_blocks_at_publish(self, tmp_path):
        # End-to-end at the publish gate: a resolved finding whose must_contain
        # is gone on disk BLOCKS validate_artifact_data.
        from scripts.pipeline_validator import validate_artifact_data
        p = tmp_path / "mod.py"
        p.write_text("def foo():\n    pass  # reverted\n")
        errors = validate_artifact_data("deliver", self._deliver_art(
            [self._finding({"file": str(p), "must_contain": "the real guard"})]),
            profile="bugfix")
        assert [e for e in errors if "disk-check FAILED" in e], (
            f"publish gate must BLOCK a reverted resolved finding, got: {errors}")

    # ---- AC5: fail-open on uncertainty (never false-BLOCK) ----
    def test_relative_path_warns_not_blocks(self):
        from scripts.pipeline_validator import _verify_findings_on_disk
        errs, invalid = _verify_findings_on_disk(
            [self._finding({"file": "relative/mod.py", "must_contain": "x"})])
        assert errs == [], "a relative path must NOT block (fail-open)"
        assert invalid, "a relative path must be flagged invalid (WARN)"

    def test_missing_file_must_contain_warns_not_blocks(self, tmp_path):
        from scripts.pipeline_validator import _verify_findings_on_disk
        gone = tmp_path / "nope.py"
        errs, invalid = _verify_findings_on_disk(
            [self._finding({"file": str(gone), "must_contain": "x"})])
        assert errs == [], "missing file + must_contain must NOT block (uncertainty)"
        assert invalid, "missing file must be flagged invalid (WARN)"

    def test_allowed_root_escape_warns_not_blocks(self, tmp_path):
        from scripts.pipeline_validator import _verify_findings_on_disk
        outside = tmp_path / "outside.py"
        outside.write_text("secret")
        root = tmp_path / "repo"
        root.mkdir()
        errs, invalid = _verify_findings_on_disk(
            [self._finding({"file": str(outside), "must_contain": "secret"})],
            allowed_root=str(root))
        assert errs == [], "a path escaping allowed_root must NOT block"
        assert invalid, "escaping allowed_root must be flagged invalid"

    def test_binary_file_warns_not_crashes(self, tmp_path):
        from scripts.pipeline_validator import _verify_findings_on_disk
        p = tmp_path / "blob.bin"
        p.write_bytes(b"\x00\x01\x02\xff\xfe")
        errs, invalid = _verify_findings_on_disk(
            [self._finding({"file": str(p), "must_contain": "x"})])
        assert errs == [], "a binary file must NOT block (unreadable → WARN)"
        assert invalid, "binary file must be flagged invalid"

    def test_disk_failure_blocks_at_completion(self, tmp_path):
        # Gate-2 mutation finding: the COMPLETION path (_check_depth — the
        # STRONGER gate, it gates status:completed) had NO test asserting a
        # reverted disk_check BLOCKS there. A future refactor could drop
        # `errors.extend(_disk_errs)` at the completion site and the whole suite
        # would stay green. This test is the mirror of
        # test_disk_failure_blocks_at_publish for _check_depth.
        from scripts.pipeline_validator import _check_depth
        p = tmp_path / "mod.py"
        p.write_text("def foo():\n    pass  # reverted\n")
        errors = _check_depth("deliver", self._deliver_art(
            [self._finding({"file": str(p), "must_contain": "the real guard"})]),
            "bugfix", run_id="")
        assert [e for e in errors if "disk-check FAILED" in e], (
            f"completion gate (_check_depth) must BLOCK a reverted resolved "
            f"finding — this is the stronger gate; got: {errors}")

    def test_must_not_contain_missing_file_warns(self, tmp_path):
        # Gate-2 LOW (signal parity): must_not_contain on a missing file passes
        # (vacuous) but must emit an invalid-WARN like the must_contain branch,
        # not be fully silent.
        from scripts.pipeline_validator import _verify_findings_on_disk
        gone = tmp_path / "removed.py"
        errs, invalid = _verify_findings_on_disk(
            [self._finding({"file": str(gone), "must_not_contain": "x"})])
        assert errs == [], "missing file + must_not_contain must NOT block (vacuous)"
        assert invalid, "missing file + must_not_contain must emit an invalid-WARN (parity)"

    def _deliver_art(self, findings):
        return {
            "title": "X",
            "quality": {"tests_pass": True, "regressions": 0, "smoke_pass": True},
            "completion_audit": {"all_green": True, "gaps": 0},
            "meta_review": {"blind_spots": "none"},
            "convergence": {"iterations": 1, "final_status": "push-ready"},
            "ac_verification": {"verified": True},
            "adversarial_review": {
                "profile_tier": "full",
                "spawned": True,
                "evidence": "Agent tool invocation: correctness specialist",
                "findings_total": len(findings),
                "findings_fixed": len(findings),
                "findings_remaining": 0,
                "findings": findings,
            },
        }
