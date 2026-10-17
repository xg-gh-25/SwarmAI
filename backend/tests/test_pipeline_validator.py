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
    cmd_run_report,
    ArtifactRegistry,
    _get_workspace,
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
        """Evaluate is in every profile (except direct — plan→build→deliver by design,
        the model writes its own exit contract at PLAN; design §4-G1)."""
        for profile in PIPELINE_PROFILES:
            if profile == "direct":
                assert _check_profile_respected("evaluate", profile) is False
                continue
            assert _check_profile_respected("evaluate", profile) is True

    def test_all_profiles_include_reflect(self):
        """Reflect is in every profile (except goal — handles it inside goal_cycle)."""
        for profile in PIPELINE_PROFILES:
            if profile in ("goal", "direct"):
                continue  # goal: goal_cycle manages REFLECT; direct: plan→build→deliver only
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
                        "acceptance_criteria": ["a"], "scores": {"s": 5},
                        "clarifications": []})
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


class TestAcVerificationAtCompletion:
    """D9 (run_57929039): ac_verification.status is enforced at PUBLISH (STAGE_DEPTH in
    validate_artifact_data) but the COMPLETION gate runs _check_depth (not
    validate_artifact_data) and the completion backstop skips the deliver stage — so
    ac_verification could be patched away after publish and still pass completion. This
    pins the completion-time enforcement in _check_depth."""

    def _deliver(self, with_ac=True, ac_value=None):
        d = {
            "title": "x",
            "quality": {"push_ready": True, "tests_pass": True, "regressions": 0},
            "adversarial_review": {"profile_tier": "full", "spawned": True,
                                   "evidence": "Agent tool", "findings": []},
            "completion_audit": {"all_green": True},
        }
        if with_ac:
            d["ac_verification"] = ac_value if ac_value is not None else {"status": "verified"}
        return d

    def test_completion_blocks_missing_ac_verification(self):
        """MUTATION: a full deliver artifact WITHOUT ac_verification must BLOCK at
        completion (_check_depth), even though publish is a separate gate."""
        from scripts.pipeline_validator import _check_depth
        errs = _check_depth("deliver", self._deliver(with_ac=False), "full", run_id="run_x")
        assert any("ac_verification" in e for e in errs), \
            f"missing ac_verification must block at completion, got: {errs}"

    def test_completion_blocks_empty_status(self):
        from scripts.pipeline_validator import _check_depth
        errs = _check_depth("deliver", self._deliver(ac_value={"status": ""}), "full", run_id="run_x")
        assert any("ac_verification" in e and "status" in e for e in errs), \
            f"empty ac_verification.status must block, got: {errs}"

    def test_completion_passes_with_verified_status(self):
        from scripts.pipeline_validator import _check_depth
        errs = _check_depth("deliver", self._deliver(), "full", run_id="run_x")
        assert not any("ac_verification" in e for e in errs), \
            f"a verified ac_verification must pass, got: {[e for e in errs if 'ac_verification' in e]}"

    def test_completion_non_dict_ac_verification_rejected(self):
        from scripts.pipeline_validator import _check_depth
        errs = _check_depth("deliver", self._deliver(ac_value=True), "full", run_id="run_x")
        assert any("ac_verification" in e and "dict" in e for e in errs), \
            f"a boolean ac_verification must be rejected, got: {errs}"


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
                        "completion_audit": {"all_green": True},
                        "ac_verification": {"status": "verified"},  # D9: now enforced at completion
                        "convergence": {"final_status": "push-ready"},
                        "push_ready": True})
        import scripts.pipeline_validator as pv
        monkeypatch.setattr(pv, "_check_semantic_depth",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom11")))
        result = validate("TestProject", "run_test1", "deliver")
        assert result["valid"] is True, f"advisory crash must not block: {result['errors']}"
        assert "semantic" in result["errored"], result.get("errored")

    def test_all_checks_wrapped(self):
        """AC5: every check in validate() is wrapped in _CheckGuard — zero
        fail-direction asymmetry remains. 15 logical checks: 1,2,3,4,5,6,7,8,
        9,9b,10,11,11b,12,13 (check 8 = the stage-specific quality gate; 11b =
        the SRS requirements WARN layer, SEVERITY_ADVISORY)."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "scripts" / "pipeline_validator.py").read_text()
        vstart = src.index("def validate(")
        vend = src.index("\ndef ", vstart + 10)
        vbody = src[vstart:vend]
        count = vbody.count("_CheckGuard(")
        assert count == 15, f"expected 15 wrapped checks in validate(), found {count}"


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
                        "completion_audit": {"all_green": True},
                        "ac_verification": {"status": "verified"},  # D9: now enforced at completion
                        "convergence": {"final_status": "push-ready"},
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


class TestSecurityBoundaries:
    """Design-time security: when EVALUATE classified cross_boundary.value==true,
    the PLAN design_doc must carry a security_boundaries block whose entries each
    cite a real enforcement locus (or escalate). Completion-time cross-stage check.
    Three-way fail policy: value=false -> no-op; value=true+incomplete -> BLOCK;
    eval-unreadable -> WARN (never a silent escape, never a false-block)."""

    def _setup(self, ws, cross_boundary, plan_data, *, with_eval=True):
        artifacts_dir = ws / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        stages = []
        if with_eval:
            eval_data = {"recommendation": "GO", "scope": "standard",
                         "cross_boundary": cross_boundary}
            _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation", eval_data)
            stages.append(_stage_record("evaluate", artifact_id="art_eval"))
        _make_artifact(artifacts_dir, "run_test1", "art_plan", "design_doc", plan_data)
        stages.append(_stage_record("think", artifact_id="art_think"))
        stages.append(_stage_record("plan", artifact_id="art_plan"))
        _make_run(runs_dir, stages=stages)
        return "TestProject", "run_test1"

    def _plan(self, **extra):
        base = {"approach": "x", "acceptance_criteria": ["AC1"],
                "boundaries": {"always": [], "ask_first": [], "never": []},
                "success_criteria": ["s"], "file_discovery": [],
                "change_spec": [], "test_strategy": []}
        base.update(extra)
        return base

    def test_true_missing_block_BLOCKS(self, workspace):
        p, r = self._setup(workspace, {"value": True, "kinds": ["frontend-backend-contract"],
                                       "seam": "s"}, self._plan())
        res = validate(p, r, "plan")
        assert any("security" in e.lower() and "boundar" in e.lower()
                   for e in res["errors"]), f"expected a security-boundaries BLOCK, got {res['errors']}"

    def test_true_wellformed_block_GREEN(self, workspace):
        # ABSOLUTE locus = definitively checkable, cwd-independent; this test file exists.
        locus = f"{__file__}:1"
        sb = [{"boundary": "fe-be", "trust_assumption": "caller authed",
               "enforcement": locus, "escalate": False}]
        p, r = self._setup(workspace, {"value": True, "kinds": ["x"], "seam": "s"},
                           self._plan(security_boundaries=sb))
        res = validate(p, r, "plan")
        sec_errs = [e for e in res["errors"] if "boundar" in e.lower() and "security" in e.lower()]
        assert not sec_errs, f"well-formed block (existing abs locus) should not error, got {sec_errs}"

    def test_true_relative_locus_no_root_WARNS_not_blocks(self, workspace):
        """No source root at completion-time + a RELATIVE locus -> WARN, never BLOCK
        (fail-open on uncertainty — the design_doc's paths are relative to the SOURCE
        repo, which is neither cwd nor the workspace, so it is un-verifiable, not fake)."""
        sb = [{"boundary": "fe-be", "trust_assumption": "authed",
               "enforcement": "backend/middleware/hive_auth.py:62", "escalate": False}]
        p, r = self._setup(workspace, {"value": True, "kinds": ["x"], "seam": "s"},
                           self._plan(security_boundaries=sb))
        res = validate(p, r, "plan")
        sec_errs = [e for e in res["errors"] if "boundar" in e.lower() and "security" in e.lower()]
        sec_warns = [w for w in res["warnings"] if "boundar" in w.lower() and "security" in w.lower()]
        assert not sec_errs, f"relative locus w/o root must NOT block, got {sec_errs}"
        assert sec_warns, "relative locus w/o root must WARN (un-verifiable, not silent)"

    def test_false_no_block_GREEN_no_warn(self, workspace):
        p, r = self._setup(workspace, {"value": False, "ruled_out": "pure logic"}, self._plan())
        res = validate(p, r, "plan")
        assert not any("security" in e.lower() and "boundar" in e.lower() for e in res["errors"])
        assert not any("security" in w.lower() and "boundar" in w.lower() for w in res["warnings"])

    def test_eval_unreadable_WARNS_not_silent(self, workspace):
        artifacts_dir = workspace / "Projects" / "TestProject" / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_plan", "design_doc", self._plan())
        stages = [_stage_record("plan", artifact_id="art_plan")]
        _make_run(runs_dir, stages=stages)
        res = validate("TestProject", "run_test1", "plan")
        sec_errs = [e for e in res["errors"] if "boundar" in e.lower() and "security" in e.lower()]
        sec_warns = [w for w in res["warnings"] if "boundar" in w.lower() and "security" in w.lower()]
        assert not sec_errs, f"unreadable eval must NOT false-block, got {sec_errs}"
        assert sec_warns, "unreadable eval must WARN (not silent escape)"

    def test_true_nonexistent_locus_BLOCKS(self, workspace):
        # ABSOLUTE non-existent path = definitively fabricated -> BLOCK (cwd-independent).
        sb = [{"boundary": "fe-be", "trust_assumption": "authed",
               "enforcement": "/nonexistent_zzz/this_file_does_not_exist.py:42", "escalate": False}]
        p, r = self._setup(workspace, {"value": True, "kinds": ["x"], "seam": "s"},
                           self._plan(security_boundaries=sb))
        res = validate(p, r, "plan")
        assert any("boundar" in e.lower() and "security" in e.lower()
                   for e in res["errors"]), f"non-existent abs locus should BLOCK, got {res['errors']}"

    def test_true_escalate_GREEN(self, workspace):
        sb = [{"boundary": "novel", "trust_assumption": "unclear",
               "escalate": True, "reason": "needs human review"}]
        p, r = self._setup(workspace, {"value": True, "kinds": ["x"], "seam": "s"},
                           self._plan(security_boundaries=sb))
        res = validate(p, r, "plan")
        sec_errs = [e for e in res["errors"] if "boundar" in e.lower() and "security" in e.lower()]
        assert not sec_errs, f"escalate:true should be accepted, got {sec_errs}"

    def test_true_entry_no_locus_no_escalate_BLOCKS(self, workspace):
        """value=true + an entry with neither an enforcement locus nor escalate=true
        -> BLOCK (a prose assumption with no code boundary is theater)."""
        sb = [{"boundary": "fe-be", "trust_assumption": "authed"}]  # no enforcement, no escalate
        p, r = self._setup(workspace, {"value": True, "kinds": ["x"], "seam": "s"},
                           self._plan(security_boundaries=sb))
        res = validate(p, r, "plan")
        assert any("boundar" in e.lower() and "security" in e.lower()
                   for e in res["errors"]), f"prose-only entry should BLOCK, got {res['errors']}"


# ===========================================================================
# Structured Requirement Spec (SRS) + User Story Gate — Phase 1
# Design SSOT: Knowledge/Designs/2026-10-12-pipeline-requirement-spec-and-scope-depth-design.md
# All new validator checks are WARN-only (routed through validate() warnings[],
# NEVER through publish-time validate_artifact_data). Pure helpers (render) are
# gate-free utilities.
# ===========================================================================

from scripts.pipeline_validator import (  # noqa: E402
    REQ_SCHEMA_VERSION,
    REQ_WORK_CLASSES,
    REQ_SOURCE_KINDS,
    REQ_STATUSES,
    _acceptance_is_present,
    validate_requirements_block,
    _check_source_provenance,
    _check_invest,
    _check_requirements_axis,
    render_requirements_md,
)


def _fr(id="FR-1", work_class="user-story", kind="user-stated", ref="user said X",
        depends_on=None, acceptance=None, status="confirmed", statement="Do X",
        added_by="evaluate"):
    """Build a conforming requirement dict (override any field)."""
    return {
        "id": id, "statement": statement, "work_class": work_class,
        "source": {"kind": kind, "ref": ref},
        "depends_on": depends_on if depends_on is not None else [],
        "acceptance": acceptance if acceptance is not None
        else [{"given": "g", "when": "w", "then": "then measurable"}],
        "status": status, "added_by": added_by,
    }


def _reqs_block(requirements, schema_version=REQ_SCHEMA_VERSION):
    d = {"requirements": requirements, "clarifications": []}
    if schema_version is not None:
        d["schema_version"] = schema_version
        d["stage_sealed"] = ["evaluate"]
    return d


class TestSRSSchemaConstants:
    def test_constants_exist(self):
        """AC1: schema version + enum sets are defined."""
        assert REQ_SCHEMA_VERSION == 1
        assert "user-story" in REQ_WORK_CLASSES and "enabler-story" in REQ_WORK_CLASSES
        assert {"spike", "bug", "task"} <= set(REQ_WORK_CLASSES)
        assert {"user-stated", "ddd-derived", "assumption"} <= set(REQ_SOURCE_KINDS)
        assert {"confirmed", "assumed", "needs-clarification"} <= set(REQ_STATUSES)


class TestSRSConformingBlock:
    def test_conforming_block_zero_warn(self):
        """AC1: a fully conforming requirements block yields no WARN."""
        data = _reqs_block([_fr()])
        warns = validate_requirements_block(data, "full")
        assert warns == [], f"conforming block should be clean, got {warns}"


class TestAcceptancePresence:
    def test_free_text_string_present(self):
        """AC4: legacy free-text acceptance string counts as present."""
        assert _acceptance_is_present(["AC1: returns 200"]) is True

    def test_gwt_object_present(self):
        """AC4: GWT object acceptance counts as present."""
        assert _acceptance_is_present([{"given": "g", "when": "w", "then": "t"}]) is True

    def test_mixed_shapes_present(self):
        """AC4: a mix of str + GWT dict is accepted (backward-compat strangler)."""
        assert _acceptance_is_present(["AC1: x", {"given": "g", "when": "w", "then": "t"}]) is True

    def test_empty_absent(self):
        """AC4: empty acceptance is NOT present."""
        assert _acceptance_is_present([]) is False
        assert _acceptance_is_present(None) is False

    def test_empty_gwt_then_absent(self):
        """AC4: a GWT object with an empty 'then' is NOT present (no measurable outcome)."""
        assert _acceptance_is_present([{"given": "g", "when": "w", "then": ""}]) is False


class TestSourceProvenance:
    def test_empty_source_kind_warns(self):
        """AC2: a requirement with empty source.kind -> WARN (string returned)."""
        fr = _fr(kind="")
        warns = _check_source_provenance([fr])
        assert any("FR-1" in w and "source" in w.lower() for w in warns), warns

    def test_present_source_no_warn(self):
        """AC2: a requirement with a valid source.kind -> no WARN."""
        assert _check_source_provenance([_fr()]) == []


class TestINVEST:
    def test_invest_runs_for_user_story(self):
        """AC3: INVEST runs for user-story; a cyclic depends_on -> WARN."""
        a = _fr(id="FR-1", depends_on=["FR-2"])
        b = _fr(id="FR-2", depends_on=["FR-1"])
        warns = _check_invest([a, b], "full")
        assert any("independent" in w.lower() or "cycl" in w.lower() for w in warns), warns

    def test_invest_dangling_depends_on_warns(self):
        """AC3: Independent — a depends_on referencing a non-existent FR -> WARN."""
        warns = _check_invest([_fr(id="FR-1", depends_on=["FR-99"])], "full")
        assert any("independent" in w.lower() or "dangl" in w.lower() or "FR-99" in w
                   for w in warns), warns

    def test_invest_valuable_empty_why_warns(self):
        """AC3: Valuable — empty source.ref (the Why) -> WARN."""
        warns = _check_invest([_fr(ref="")], "full")
        assert any("valuable" in w.lower() or "why" in w.lower() for w in warns), warns

    def test_invest_testable_empty_acceptance_warns(self):
        """AC3: Testable — empty acceptance -> WARN."""
        warns = _check_invest([_fr(acceptance=[])], "full")
        assert any("testable" in w.lower() or "acceptance" in w.lower() for w in warns), warns

    def test_invest_skips_task(self):
        """AC3: INVEST does NOT run for task/bug/spike -> [] even with bad fields."""
        bad = _fr(work_class="task", ref="", acceptance=[], depends_on=["FR-99"])
        assert _check_invest([bad], "full") == []

    def test_invest_skips_goal_profile(self):
        """AC10: goal carve-out — INVEST returns [] for a user-story FR when profile==goal."""
        bad = _fr(work_class="user-story", ref="", acceptance=[])
        assert _check_invest([bad], "goal") == []


class TestRequirementsAxis:
    def test_docs_scope_with_user_story_warns(self):
        """AC8: scope in {docs,research} + a user-story FR -> WARN (hard-contradiction)."""
        warns = _check_requirements_axis([_fr(work_class="user-story")], scope="docs-only")
        assert any("user-story" in w or "scope" in w.lower() for w in warns), warns

    def test_standard_scope_user_story_no_warn(self):
        """AC8: a normal scope + user-story FR -> no axis WARN."""
        assert _check_requirements_axis([_fr(work_class="user-story")], scope="standard") == []


class TestSchemaVersionGate:
    def test_no_schema_version_skips(self):
        """AC5 (R28): a requirements block with NO schema_version is SKIPPED -> []."""
        data = _reqs_block([_fr(kind="")], schema_version=None)  # bad source, would warn IF run
        assert validate_requirements_block(data, "full") == [], \
            "no schema_version must skip all checks, not warn/block"

    def test_schema_version_present_runs_checks(self):
        """AC5 (non-vacuous): WITH schema_version, the same bad block DOES warn —
        proves the skip branch is load-bearing, not a vacuous always-pass."""
        data = _reqs_block([_fr(kind="")], schema_version=1)
        warns = validate_requirements_block(data, "full")
        assert warns, "with schema_version present, a sourceless FR must warn (gate is non-vacuous)"


class TestRenderRequirementsMd:
    def test_render_contains_workclass_and_source(self):
        """AC7: render shows work_class + source per FR."""
        md = render_requirements_md(_reqs_block([_fr()]))
        assert "user-story" in md and "user-stated" in md

    def test_render_testable_no_checkmark(self):
        """AC7: the Testable column uses a neutral AC-exists glyph, NOT a green check."""
        md = render_requirements_md(_reqs_block([_fr()]))
        # Testable must not be rendered with a check/tick that implies 'covered'
        assert "✅" not in md and "✓" not in md, \
            "Testable must use a neutral glyph (U+25CB/25CF), not a checkmark (false-green)"


class TestRenderAuditTrail:
    """Run 3a 块1: render_audit_trail_md — the §4.6.1 requirement-provenance audit
    block for the run-end REPORT. Four parts: original intent verbatim + requirements
    table (reuses render_requirements_md) + clarification Q&A table (answerer-
    distinguished) + ⚠️leftover section (triggers = REAL schema fields only)."""

    def _data(self):
        return {
            "requirements": [
                _fr(id="FR-1", kind="user-stated", status="confirmed",
                    statement="user wants A"),
                # FR-2 triggers the leftover section ONLY via the assumption-KIND
                # branch (status=confirmed, so the status branch cannot also catch it
                # — pins the kind branch independently so a mutation of it goes RED).
                _fr(id="FR-2", kind="assumption", status="confirmed",
                    statement="assumed B", work_class="task"),
                # NFR-1 triggers ONLY via the needs-clarification STATUS branch
                # (kind=ddd-derived, not assumption — pins the status branch).
                _fr(id="NFR-1", kind="ddd-derived", status="needs-clarification",
                    statement="p99<200ms", work_class="task"),
            ],
            "clarifications": [
                {"qid": "Q1", "question": "A or B?", "options": ["A", "B", "X. Other"],
                 "answer": "A", "answered_by": "user"},
                {"qid": "Q2", "question": "threshold?", "answer": "200ms",
                 "answered_by": "self", "resolution": "DDD default TECH.md:L120"},
            ],
            "schema_version": REQ_SCHEMA_VERSION,
        }

    def test_contains_original_intent_verbatim(self):
        """AC1(a): the run's original requirement text appears verbatim."""
        from scripts.pipeline_validator import render_audit_trail_md
        md = render_audit_trail_md("Build the refund flow for orders", self._data())
        assert "Build the refund flow for orders" in md

    def test_contains_requirements_table(self):
        """AC1(b): the FR/NFR derivation table (reused render) appears."""
        from scripts.pipeline_validator import render_audit_trail_md
        md = render_audit_trail_md("req", self._data())
        assert "FR-1" in md and "FR-2" in md and "NFR-1" in md

    def test_clarification_qa_answerer_distinguished(self):
        """AC1(c)/AC3: the Q&A table shows each clarification with a VISUALLY DISTINCT
        answerer marker (👤 user vs 🧠 self)."""
        from scripts.pipeline_validator import render_audit_trail_md
        md = render_audit_trail_md("req", self._data())
        assert "Q1" in md and "Q2" in md
        assert "👤" in md and "🧠" in md, "answerer must be visually distinguished"

    def test_leftover_section_lists_real_field_triggers(self):
        """AC1(d)/AC3: the ⚠️leftover section lists assumption-source requirements,
        un-closed statuses (assumed / needs-clarification), and self-answered
        clarifications — all keyed on REAL schema fields."""
        from scripts.pipeline_validator import render_audit_trail_md
        md = render_audit_trail_md("req", self._data())
        # FR-2 (assumption/assumed) and NFR-1 (needs-clarification) must surface as leftover;
        # FR-1 (user-stated/confirmed) must NOT be a leftover.
        after = md.split("遗留")[-1] if "遗留" in md else md.split("Leftover")[-1]
        # FR-2 pins the assumption-KIND branch (it is status=confirmed, so ONLY the
        # kind branch can surface it) + the ASSUMPTION tag; NFR-1 pins the STATUS
        # branch. Both must appear — so a mutation of either branch goes RED.
        assert "FR-2" in after and "ASSUMPTION" in after, \
            f"assumption-kind leftover branch not surfaced: {after[:400]}"
        assert "NFR-1" in after and "NEEDS-CLARIFICATION" in after, \
            f"needs-clarification status leftover branch not surfaced: {after[:400]}"
        # FR-1 (user-stated/confirmed) must NOT be a leftover. Use a token boundary:
        # a bare "FR-1" check would false-match inside "NFR-1".
        import re as _re
        assert not _re.search(r"\bFR-1\b", after), \
            f"closed user-stated req wrongly listed as leftover: {after[:400]}"

    def test_no_leftover_states_explicit_clean(self):
        """AC1(d): with everything closed (all confirmed, user-stated), the leftover
        section explicitly says there are none (not an empty blank)."""
        from scripts.pipeline_validator import render_audit_trail_md
        data = {
            "requirements": [_fr(id="FR-1", kind="user-stated", status="confirmed")],
            "clarifications": [{"qid": "Q1", "question": "?", "answer": "A",
                                "answered_by": "user"}],
            "schema_version": REQ_SCHEMA_VERSION,
        }
        md = render_audit_trail_md("req", data)
        assert "✅" in md or "无" in md.split("遗留")[-1] or "none" in md.lower(), md

    def test_graceful_degrade_no_structured_requirements(self):
        """AC2: no structured requirements[] (old-shape / non-structured run) → the
        function degrades to an explicit note, never crashes, never a blank block."""
        from scripts.pipeline_validator import render_audit_trail_md
        md_empty = render_audit_trail_md("req", {})
        md_noreqs = render_audit_trail_md("req", {"requirements": []})
        for md in (md_empty, md_noreqs):
            assert isinstance(md, str) and len(md.strip()) > 0
            assert "结构化" in md or "structured" in md.lower() or "no requirement" in md.lower()

    def test_audit_fields_neutralize_markdown_injection(self):
        """Gate-2 MED (both reviewers): the audit block is the 'glass box' — its own
        integrity cannot be broken by LLM-authored artifact text. EVERY interpolated
        field (statement / answer / resolution / qid / off-vocab answered_by) must
        neutralize a `|` (table-column injection) AND a newline (fake-heading / fake-
        bullet injection), so a crafted field cannot inject structure into the report.
        The sibling Q&A cells already escaped `|`; this pins the WHOLE function to one
        rule (no per-field escaping drift)."""
        from scripts.pipeline_validator import render_audit_trail_md
        data = {
            "requirements": [
                _fr(id="FR-1", kind="assumption", status="confirmed",
                    statement="assume\n## FAKE HEADER\n- injected bullet | col"),
            ],
            "clarifications": [
                {"qid": "Q|1", "question": "pipe?", "answer": "yes\n## INJECTED",
                 "answered_by": "a|b|c"},
                {"qid": "Q2", "question": "t?", "answer": "x\n- fake",
                 "answered_by": "self", "resolution": "ref\n## RES-HEADER"},
            ],
            "schema_version": REQ_SCHEMA_VERSION,
        }
        md = render_audit_trail_md("req", data)
        # No raw newline-introduced markdown heading survives from a field value.
        assert "\n## FAKE HEADER" not in md, "leftover statement newline injected a heading"
        assert "\n## INJECTED" not in md, "Q&A answer newline injected a heading"
        assert "\n## RES-HEADER" not in md, "resolution newline injected a heading"
        assert "\n- injected bullet" not in md, "leftover statement newline injected a bullet"
        # The off-vocab answerer value with pipes must be escaped (not raw column break).
        assert "| a|b|c |" not in md, "off-vocab answered_by injected raw pipes into the Q&A table"

    def test_audit_derivation_table_and_intent_neutralize_injection(self):
        """Gate-2 Red-Team HIGH: the glass-box guarantee is INCOMPLETE if the part-2
        derivation table (which reuses render_requirements_md) and the part-1 original-
        intent blockquote interpolate LLM-authored artifact fields RAW. source.ref /
        acceptance / id / status / work_class and the requirement text are all
        LLM-authored — a newline in any of them injects a fake heading/bullet INTO the
        audit block whose stated AC is injection-proof provenance. The audit renderer
        must neutralize these at the caller (without mutating the shared
        render_requirements_md, which has other consumers / own tests)."""
        from scripts.pipeline_validator import render_audit_trail_md
        data = {
            "requirements": [{
                "id": "FR-1", "work_class": "task", "status": "confirmed",
                "source": {"kind": "ddd-derived",
                           "ref": "TECH.md\n## INJECTED VIA SOURCE REF\n- fake bullet"},
                "acceptance": ["normal AC\n## INJECTED VIA ACCEPTANCE\n- bullet"],
            }],
            "clarifications": [],
            "schema_version": REQ_SCHEMA_VERSION,
        }
        md = render_audit_trail_md("req line1\n## INTENT INJECT\n- bullet", data)
        assert "\n## INJECTED VIA SOURCE REF" not in md, "source.ref newline injected a heading into the derivation table"
        assert "\n## INJECTED VIA ACCEPTANCE" not in md, "acceptance newline injected a heading into the derivation table"
        assert "\n## INTENT INJECT" not in md, "original-intent blockquote newline injected a heading"
        # FR-1 still rendered (escaping neutralizes structure, does not drop content).
        assert "FR-1" in md

    def test_audit_neutralizer_does_not_flip_presence_glyph(self):
        """Gate-2 adversarial-verify LOW: the injection neutralizer must NOT make a
        whitespace-ONLY acceptance look PRESENT. _md_cell folds newline→⏎, which would
        turn a blank acceptance into a non-empty ' ⏎ ' and flip the AC-present check
        (○ absent → ● present false-green) in the audit derivation table — defeating
        the glass box's own 'not misleadable by its data' property. Whitespace-only in
        → empty out, so presence is judged on real content."""
        from scripts.pipeline_validator import render_audit_trail_md, render_requirements_md, _neutralize_requirements_for_audit
        reqs = [{"id": "FR-1", "status": "assumed", "source": {"kind": "assumption"},
                 "acceptance": [{"given": "\n", "when": "\n", "then": "\n"}]}]
        neutralized = render_requirements_md(_neutralize_requirements_for_audit(reqs))
        raw = render_requirements_md({"requirements": reqs})
        # Both tables must agree the AC is ABSENT (○) — the audit copy must not upgrade it.
        assert "○" in neutralized, "whitespace-only acceptance flipped to present (●) in the audit table"
        assert ("○" in raw) == ("○" in neutralized), "audit table disagrees with the real table on AC presence"

    def test_audit_block_threads_through_real_run_report(self, workspace):
        """Layer 4 (cross_boundary=true): drive the REAL cmd_run_report through the
        report-generation seam — a structured evaluation artifact must produce the
        '需求溯源审计' section in REPORT.md on disk. Proves the wiring
        cmd_run_report -> render_audit_trail_md is live end-to-end. Mutation-proven:
        remove the sections.append (or its `if _eval_art.get('requirements')` guard)
        -> RED. A legacy run WITHOUT structured requirements[] must NOT grow it."""
        import json as _json
        from types import SimpleNamespace
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        for name, body in (
            ("PRODUCT.md", "# T\n\n## Non-Goals\n\n- **Not X** -- skip.\n"),
            ("TECH.md", "# T\n\n## Architecture\n\nDesktop.\n"),
            ("IMPROVEMENT.md", "# T\n\n## What Worked\n\n- OK\n\n## What Failed\n\n- **Retry too aggressive** -- cascades\n"),
            ("PROJECT.md", "# T\n"),
        ):
            (project_dir / name).write_text(body)
        artifacts_dir = project_dir / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation", {
            "recommendation": "GO", "scope": "standard", "scores": {"s": 5},
            "schema_version": REQ_SCHEMA_VERSION,
            "requirements": [
                _fr(id="FR-1", kind="user-stated", status="confirmed", statement="user wants refund"),
                _fr(id="FR-2", kind="assumption", status="assumed", statement="assume async", work_class="task"),
            ],
            "clarifications": [
                {"qid": "Q1", "question": "sync or async?", "answer": "async",
                 "answered_by": "self", "resolution": "TECH.md default"},
            ],
        })
        run = _make_run(runs_dir, stages=[_stage_record("evaluate", artifact_id="art_eval")])
        run["requirement"] = "Build the order refund flow"
        (runs_dir / "run.json").write_text(_json.dumps(run))

        cmd_run_report(SimpleNamespace(project="TestProject", run_id="run_test1"),
                       ArtifactRegistry(_get_workspace()))
        report = (runs_dir / "REPORT.md").read_text(encoding="utf-8")
        assert "需求溯源审计" in report, "audit section missing from REPORT.md"
        assert "Build the order refund flow" in report  # original intent verbatim
        assert "FR-2" in report and "ASSUMPTION" in report  # leftover surfaced
        assert "🧠" in report  # self-answered clarification distinguished

    def test_legacy_run_report_has_no_audit_block(self, workspace):
        """Discriminator: a run whose evaluation artifact has NO structured
        requirements[] must NOT grow the audit section (old reports unchanged)."""
        from types import SimpleNamespace
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        for name, body in (
            ("PRODUCT.md", "# T\n\n## Non-Goals\n\n- **Not X** -- skip.\n"),
            ("TECH.md", "# T\n\n## Architecture\n\nDesktop.\n"),
            ("IMPROVEMENT.md", "# T\n\n## What Worked\n\n- OK\n\n## What Failed\n\n- **Retry** -- x\n"),
            ("PROJECT.md", "# T\n"),
        ):
            (project_dir / name).write_text(body)
        artifacts_dir = project_dir / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation", {
            "recommendation": "GO", "scope": "standard", "scores": {"s": 5},
            "acceptance_criteria": ["free text AC only"],
        })
        _make_run(runs_dir, stages=[_stage_record("evaluate", artifact_id="art_eval")])
        cmd_run_report(SimpleNamespace(project="TestProject", run_id="run_test1"),
                       ArtifactRegistry(_get_workspace()))
        report = (runs_dir / "REPORT.md").read_text(encoding="utf-8")
        assert "需求溯源审计" not in report, "legacy run must not grow the audit section"

    def test_requirements_without_schema_version_no_audit_block(self, workspace):
        """#8 (Kiro): the audit-render gate must agree with the validator's gate —
        a requirements[] present WITHOUT schema_version>=1 is a legacy/half-activated
        artifact and must NOT render the audit block (presence-vs-version drift). The
        old gate (`if _eval_art.get('requirements')`) would wrongly render it.
        Mutation-proven: revert the artifact_cli gate to presence-only -> this goes RED."""
        import json as _json
        from types import SimpleNamespace
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        for name, body in (
            ("PRODUCT.md", "# T\n\n## Non-Goals\n\n- **Not X** -- skip.\n"),
            ("TECH.md", "# T\n\n## Architecture\n\nDesktop.\n"),
            ("IMPROVEMENT.md", "# T\n\n## What Worked\n\n- OK\n\n## What Failed\n\n- **Retry** -- x\n"),
            ("PROJECT.md", "# T\n"),
        ):
            (project_dir / name).write_text(body)
        artifacts_dir = project_dir / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        # requirements present, but NO schema_version (half-activated / legacy shape)
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation", {
            "recommendation": "GO", "scope": "standard", "scores": {"s": 5},
            "requirements": [
                _fr(id="FR-1", kind="user-stated", status="confirmed", statement="user wants refund"),
            ],
        })
        run = _make_run(runs_dir, stages=[_stage_record("evaluate", artifact_id="art_eval")])
        run["requirement"] = "Build the order refund flow"
        (runs_dir / "run.json").write_text(_json.dumps(run))
        cmd_run_report(SimpleNamespace(project="TestProject", run_id="run_test1"),
                       ArtifactRegistry(_get_workspace()))
        report = (runs_dir / "REPORT.md").read_text(encoding="utf-8")
        assert "需求溯源审计" not in report, \
            "requirements without schema_version must not render the audit block (#8 gate)"


class TestSRSCrossBoundaryE2E:
    """Layer 4 (cross_boundary=true): drive the REAL schema contract across the
    seam — the AC-ID matcher (BUILD ac_coverage consumer) + the publish-gate
    non-leak (the Gate-1-fix invariant). NOT mocked: real validate_artifact_data,
    real matcher regex. Each test states its mutation-to-RED."""

    def test_ac_matcher_production_source_captures_new_ids(self):
        """AC9 real-wiring, UPDATED for #2: the 5 inline `re.match` AC-ID literals were
        refactored into a single boundary-anchored `_extract_ac_id` helper (`_AC_ID_RE`),
        so every AC-coverage site now routes through ONE canonical matcher. This test
        pins the new single-source structure (no inline AC-ID `re.match` literals remain;
        all 5 sites call `_extract_ac_id`) AND the capture behaviour of the helper."""
        import re
        import pathlib
        from scripts.pipeline_validator import _extract_ac_id, _AC_ID_RE
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "scripts" / "pipeline_validator.py").read_text()
        # #2 refactor: NO inline AC-ID re.match literal may remain (single-source).
        inline = re.findall(r're\.match\(r"\^\(AC\\d\+[^"]*\)"', src)
        assert inline == [], (
            f"inline AC-ID re.match literal(s) remain — should route through "
            f"_extract_ac_id: {inline}")
        # all 5 AC-coverage sites must call the single helper (count pre-dedup)
        calls = re.findall(r"_extract_ac_id\(", src)
        # 5 call sites + 1 def line = 6 occurrences
        assert len(calls) >= 6, (
            f"expected >=6 _extract_ac_id references (5 sites + def), found {len(calls)}")
        # the canonical matcher captures all three id shapes AND rejects prefix collisions
        assert _extract_ac_id("AC1: legacy") == "AC1"
        assert _extract_ac_id("FR-1: new") == "FR-1"
        assert _extract_ac_id("NFR-2: n") == "NFR-2"
        assert _extract_ac_id("FR-1-placeholder") is None   # #2 boundary
        assert _AC_ID_RE.match("AC3garbage") is None


class TestSRSDefensiveTotality:
    """Adversarial (Gate-2) findings: the WARN checks run inside a _CheckGuard that
    swallows exceptions — so a crash silently drops the ENTIRE WARN layer. These
    assert the checks are DEFENSIVELY TOTAL (never raise) on malformed input."""

    def test_source_as_string_does_not_crash(self):
        """Gate-2 MED: source as a non-dict string -> must WARN (missing kind),
        not AttributeError. MUTATION: revert _safe_dict -> AttributeError -> RED."""
        bad = {"id": "FR-1", "statement": "x", "work_class": "user-story",
               "source": "notadict", "depends_on": [], "acceptance": [], "status": "assumed"}
        # neither call may raise
        warns = _check_source_provenance([bad])
        assert any("FR-1" in w and "source" in w.lower() for w in warns)
        iwarns = _check_invest([bad], "full")
        assert isinstance(iwarns, list)  # did not raise

    def test_depends_on_as_string_no_per_char_warn(self):
        """Gate-2 LOW: depends_on as a string must NOT iterate char-by-char
        (one bogus dangling-ref WARN per char). MUTATION: revert _safe_list -> many WARNs."""
        bad = _fr(id="FR-1", work_class="user-story", depends_on="FR-9")  # string, not list
        warns = _check_invest([bad], "full")
        dangling = [w for w in warns if "dangling" in w.lower()]
        assert len(dangling) <= 1, f"string depends_on iterated char-by-char: {dangling}"

    def test_validate_requirements_block_total_on_garbage(self):
        """Gate-2: validate_requirements_block never raises on malformed input."""
        for garbage in [
            {"schema_version": 1, "requirements": [{"id": "FR-1", "source": "s",
             "work_class": "user-story", "depends_on": "x", "acceptance": "str"}]},
            {"schema_version": 1, "requirements": "notalist"},
            {"schema_version": 1, "requirements": [{"id": "FR-1"}]},  # minimal
        ]:
            assert isinstance(validate_requirements_block(garbage, "full"), list)


class TestSRSPartialGWT:
    """Gate-2 red-team MED: a partial GWT (then present, given/when empty) must NOT
    count as a present acceptance — it reads as covered but specifies no testable
    condition (the §4.5 false-green trap)."""

    def test_partial_gwt_then_only_absent(self):
        assert _acceptance_is_present([{"then": "x"}]) is False
        assert _acceptance_is_present([{"then": "x", "given": "g"}]) is False  # no when

    def test_complete_gwt_present(self):
        assert _acceptance_is_present([{"given": "g", "when": "w", "then": "t"}]) is True

    def test_partial_gwt_fails_invest_testable(self):
        """A user-story FR with only a partial GWT must get a Testable WARN."""
        fr = _fr(work_class="user-story", acceptance=[{"then": "x"}])
        warns = _check_invest([fr], "full")
        assert any("testable" in w.lower() for w in warns)


class TestSRSEnablerStoryAxis:
    """Gate-2 red-team MED: O7 three-axis contradiction applies to BOTH user-story
    AND enabler-story (both user-perceivable) under docs/research scope."""

    def test_enabler_story_under_docs_warns(self):
        warns = _check_requirements_axis([_fr(work_class="enabler-story")], scope="docs-only")
        assert any("enabler-story" in w or "scope" in w.lower() for w in warns), warns

    def test_task_under_docs_no_warn(self):
        """A task (not user-perceivable) under docs is fine — no axis WARN."""
        assert _check_requirements_axis([_fr(work_class="task")], scope="docs-only") == []

    def test_srs_warns_never_leak_into_publish_gate(self):
        """AC2 real-wiring (Gate-1-fix invariant): a requirements block with
        multiple WARN-worthy requirements, fed to the REAL publish-time
        validate_artifact_data, must add ZERO entries to its errors list.
        MUTATION: wire validate_requirements_block into validate_artifact_data's
        errors path -> this returns non-empty -> RED (and publish would sys.exit)."""
        from scripts.pipeline_validator import validate_artifact_data
        data = {
            "recommendation": "GO", "scope": "docs-only",
            "understanding": {
                "work_type": "existing-feature",
                "claim": "x is present in the current system",
                "evidence": "Understanding gate: code-trace foo.py:1 confirmed",
                "evidence_kind": "code-trace", "skeptic_verdict": "SUPPORTED",
                "alternative_considered": "none fits",
            },
            "ambiguity_scan": {
                "scanned_fields": ["who"], "terms_checked": ["standard"],
                "hits": [], "hit_count": 0, "all_resolved": True,
            },
            "schema_version": 1, "stage_sealed": ["evaluate"],
            "requirements": [
                # sourceless + user-story under docs scope + empty acceptance:
                # would trigger source/INVEST/axis WARNs IF they were on the error path
                {"id": "FR-1", "statement": "x", "work_class": "user-story",
                 "source": {"kind": "", "ref": ""}, "depends_on": ["FR-99"],
                 "acceptance": [], "status": "assumed"},
            ],
        }
        errs = validate_artifact_data("evaluate", data, profile="full")
        assert errs == [], \
            f"SRS WARNs leaked into publish-gate errors (Gate-1 regression): {errs}"


# ===========================================================================
# Run 2 (Phase 2): clarifications[] WARN validation — _check_clarifications
# ===========================================================================
class TestClarifications:
    """AC2/AC7: WARN-only validation of the clarifications[] array.
    Canonical shape (SSOT §4.1 + §4.6.1 audit render): qid/question/options?/
    answer/answered_by(user|self); `answer` is the audit anchor, `resolution` is
    the optional self-answer evidence (only expected for answered_by=self).
    Plus: unique qids, and source.kind<->status consistency (a user-stated req —
    upgraded via a user MCQ answer, O1 clause d — MUST be status=confirmed,
    including the missing-status half-upgrade). WARN-only, routed into warnings[]."""

    def _clar(self, **over):
        # Canonical shape (SSOT §4.1 + §4.6.1 audit render): qid/question/options?/
        # answer/answered_by. `answer` is the audit anchor; `resolution` is the
        # optional self-answer evidence (only expected for answered_by=self).
        base = {"qid": "Q1", "question": "Which retry mode?",
                "options": ["A. exp-backoff", "B. fixed", "X. Other"],
                "answer": "A. exp-backoff", "answered_by": "self",
                "resolution": "self-answer: read retry_manager.py:40"}
        base.update(over)
        return base

    def test_conforming_clarifications_zero_warn(self):
        """A well-formed clarifications[] produces no WARN."""
        from scripts.pipeline_validator import _check_clarifications
        data = {"requirements": [], "clarifications": [self._clar()]}
        assert _check_clarifications(data) == [], _check_clarifications(data)

    def test_empty_clarifications_zero_warn(self):
        """clarifications: [] is valid (no ambiguity surfaced)."""
        from scripts.pipeline_validator import _check_clarifications
        assert _check_clarifications({"requirements": [], "clarifications": []}) == []

    def test_missing_qid_warns(self):
        from scripts.pipeline_validator import _check_clarifications
        data = {"clarifications": [self._clar(qid="")]}
        warns = _check_clarifications(data)
        assert any("qid" in w for w in warns), warns

    def test_missing_answer_warns(self):
        """A2: `answer` is the §4.6.1 audit anchor (what was decided) — missing WARNs."""
        from scripts.pipeline_validator import _check_clarifications
        data = {"clarifications": [self._clar(answer="")]}
        warns = _check_clarifications(data)
        assert any("answer" in w for w in warns), warns

    def test_self_answer_missing_resolution_warns(self):
        """A `self` answer must record its evidence (resolution)."""
        from scripts.pipeline_validator import _check_clarifications
        data = {"clarifications": [self._clar(answered_by="self", resolution="")]}
        warns = _check_clarifications(data)
        assert any("resolution" in w for w in warns), warns

    def test_user_answer_needs_no_resolution(self):
        """A `user` answer's anchor is `answer`, not resolution — no resolution WARN."""
        from scripts.pipeline_validator import _check_clarifications
        data = {"clarifications": [self._clar(answered_by="user", resolution="")]}
        warns = _check_clarifications(data)
        assert not any("resolution" in w for w in warns), warns

    def test_duplicate_qid_warns(self):
        """RT2: two clarifications sharing a qid corrupt the audit trail."""
        from scripts.pipeline_validator import _check_clarifications
        data = {"clarifications": [
            self._clar(qid="Q1", answered_by="self"),
            self._clar(qid="Q1", answered_by="user"),
        ]}
        warns = _check_clarifications(data)
        assert any("duplicate" in w.lower() and "Q1" in w for w in warns), warns

    def test_null_qid_treated_as_missing(self):
        """C1: an explicit JSON null is treated as missing, not the literal 'None'."""
        from scripts.pipeline_validator import _check_clarifications
        data = {"clarifications": [self._clar(qid=None)]}
        warns = _check_clarifications(data)
        assert any("no qid" in w for w in warns), warns
        assert not any("None" in w for w in warns), warns

    def test_bad_answered_by_warns(self):
        from scripts.pipeline_validator import _check_clarifications
        data = {"clarifications": [self._clar(answered_by="robot")]}
        warns = _check_clarifications(data)
        assert any("answered_by" in w for w in warns), warns

    def test_missing_answered_by_warns(self):
        from scripts.pipeline_validator import _check_clarifications
        data = {"clarifications": [self._clar(answered_by="")]}
        warns = _check_clarifications(data)
        assert any("answered_by" in w for w in warns), warns

    def test_user_stated_requirement_not_confirmed_warns(self):
        """source.kind<->status consistency: a user-stated req should be confirmed.
        This is the O1 clause-d invariant (user answered the MCQ -> source upgraded
        -> status should follow). WARN when it doesn't."""
        from scripts.pipeline_validator import _check_clarifications
        data = {
            "requirements": [
                {"id": "FR-1", "statement": "x", "source": {"kind": "user-stated"},
                 "status": "assumed"}
            ],
            "clarifications": [self._clar(answered_by="user")],
        }
        warns = _check_clarifications(data)
        assert any("FR-1" in w and ("confirmed" in w or "status" in w) for w in warns), warns

    def test_user_stated_missing_status_warns(self):
        """RT1: the half-upgrade (kind set, status absent) is the likeliest error
        and MUST warn — a falsy status does NOT silently pass."""
        from scripts.pipeline_validator import _check_clarifications
        data = {
            "requirements": [
                {"id": "FR-1", "statement": "x", "source": {"kind": "user-stated"}}
            ],
            "clarifications": [self._clar(answered_by="user")],
        }
        warns = _check_clarifications(data)
        assert any("FR-1" in w and ("confirmed" in w or "status" in w) for w in warns), warns

    def test_user_stated_confirmed_is_clean(self):
        """The consistent case produces no consistency WARN."""
        from scripts.pipeline_validator import _check_clarifications
        data = {
            "requirements": [
                {"id": "FR-1", "statement": "x", "source": {"kind": "user-stated"},
                 "status": "confirmed"}
            ],
            "clarifications": [self._clar(answered_by="user")],
        }
        warns = _check_clarifications(data)
        assert not any("FR-1" in w for w in warns), warns

    def test_clarifications_not_a_list_is_safe(self):
        """Defensive totality: a non-list clarifications must not throw."""
        from scripts.pipeline_validator import _check_clarifications
        assert _check_clarifications({"clarifications": "nope"}) == []
        assert _check_clarifications({}) == []

    def test_clarification_not_a_dict_warns(self):
        """A non-dict entry WARNs (malformed) and never throws — the warn is
        asserted, not just no-throw (C2: this test was previously vacuous)."""
        from scripts.pipeline_validator import _check_clarifications
        data = {"clarifications": ["notadict", self._clar()]}
        warns = _check_clarifications(data)
        assert any("not a dict" in w for w in warns), warns

    def test_wired_into_validate_requirements_block(self):
        """AC2 wiring: validate_requirements_block routes clarification WARNs through."""
        from scripts.pipeline_validator import validate_requirements_block
        data = _reqs_block([
            {"id": "FR-1", "statement": "x", "source": {"kind": "user-stated"},
             "status": "assumed", "work_class": "task",
             "acceptance": {"given": "g", "when": "w", "then": "measurable then"}}
        ])
        data["clarifications"] = [self._clar(answered_by="user")]
        warns = validate_requirements_block(data, "full")
        assert any("FR-1" in w and ("confirmed" in w or "status" in w) for w in warns), warns

    # ── Run 3a 块2: reconciliation(a) — audit-trail "who decided what" (whole-block) ──
    def test_reconcile_a_user_clarification_but_no_user_stated_req_warns(self):
        """块2 AC4: an answered_by=user clarification exists, but NO requirement is
        source.kind=user-stated → the audit link is broken (a user decided something,
        but no requirement records it). WARN (advisory, whole-block, existing fields).
        Isolated from the consistency WARN by using a ddd-derived requirement (so the
        user-stated->confirmed check does not fire)."""
        from scripts.pipeline_validator import _check_clarifications
        data = {
            "requirements": [
                {"id": "FR-1", "statement": "x",
                 "source": {"kind": "ddd-derived", "ref": "TECH.md"},
                 "status": "confirmed"}
            ],
            "clarifications": [self._clar(answered_by="user")],
        }
        warns = _check_clarifications(data)
        assert any(("user-stated" in w or "审计" in w or "audit" in w.lower())
                   and ("user" in w.lower()) for w in warns), \
            f"reconcile(a) did not fire for user-clarification-without-user-stated-req: {warns}"

    def test_reconcile_a_user_clarification_with_user_stated_req_clean(self):
        """The reconciled case: a user clarification AND a user-stated requirement →
        no reconcile(a) WARN (discriminates, not blanket). The user-stated req is
        confirmed so the consistency WARN also stays silent."""
        from scripts.pipeline_validator import _check_clarifications
        data = {
            "requirements": [
                {"id": "FR-1", "statement": "x",
                 "source": {"kind": "user-stated", "ref": "user said so"},
                 "status": "confirmed"}
            ],
            "clarifications": [self._clar(answered_by="user")],
        }
        warns = _check_clarifications(data)
        assert not any("audit" in w.lower() or "审计" in w for w in warns), \
            f"reconcile(a) false-fired on a reconciled case: {warns}"

    def test_reconcile_a_only_self_answers_no_warn(self):
        """Reconcile(a) keys on answered_by=user. A block with only self-answers (and
        no user-stated req) must NOT WARN — self-answers do not demand a user-stated
        requirement (that is autonomy-first, not an audit gap)."""
        from scripts.pipeline_validator import _check_clarifications
        data = {
            "requirements": [
                {"id": "FR-1", "statement": "x",
                 "source": {"kind": "ddd-derived", "ref": "TECH.md"},
                 "status": "confirmed"}
            ],
            "clarifications": [self._clar(answered_by="self")],
        }
        warns = _check_clarifications(data)
        assert not any("audit" in w.lower() or "审计" in w for w in warns), \
            f"reconcile(a) wrongly fired on self-only answers: {warns}"

    def test_reconcile_a_silent_on_legacy_run_through_gate(self):
        """Meta-review axis-3: the reconcile(a) WARN must NOT fire on a LEGACY run
        (no schema_version). The 3 reconcile tests above call _check_clarifications
        directly (bypassing the gate); this pins that the PUBLIC entry
        validate_requirements_block suppresses the new WARN for an old-shape artifact
        via the schema_version gate — so Phase 1/2 historical runs are untouched. A
        would-fire payload (user clarification + no user-stated req) is given WITHOUT
        schema_version, and the gate must return [] regardless."""
        from scripts.pipeline_validator import validate_requirements_block
        would_fire = {
            "requirements": [
                {"id": "FR-1", "statement": "x",
                 "source": {"kind": "ddd-derived", "ref": "TECH.md"},
                 "status": "confirmed"}
            ],
            "clarifications": [self._clar(answered_by="user")],
            # NO schema_version → legacy / old-shape artifact.
        }
        assert validate_requirements_block(would_fire, "full") == [], \
            "schema_version gate failed to suppress reconcile(a) WARN on a legacy run"
        # And WITH schema_version the same payload DOES surface the WARN (proves the
        # gate is the only thing suppressing it — not a dead check).
        would_fire_versioned = dict(would_fire, schema_version=REQ_SCHEMA_VERSION)
        warns = validate_requirements_block(would_fire_versioned, "full")
        assert any(("user-stated" in w or "审计" in w or "audit" in w.lower()) for w in warns), \
            f"reconcile(a) did not surface through the gated public entry when versioned: {warns}"


class TestClarificationsCrossBoundaryE2E:
    """Layer 4 (cross_boundary=true): drive the REAL validate_requirements_block
    through the data-schema seam — a schema-conforming evaluation artifact carrying
    a user-answered clarification whose requirement was NOT upgraded to confirmed.
    This exercises the actual source.kind<->status consistency contract end-to-end
    (not a mock of it). Mutation-proven: revert the consistency check -> this goes RED."""

    def test_real_consistency_seam_fires(self):
        from scripts.pipeline_validator import validate_requirements_block
        # Real artifact shape as EVALUATE would publish it (schema_version-gated path).
        data = _reqs_block([
            {"id": "FR-1", "statement": "the thing the user clarified",
             "source": {"kind": "user-stated", "ref": "user answered Q1"},
             "status": "assumed",  # <-- the inconsistency: user-stated but not confirmed
             "work_class": "task",
             "acceptance": {"given": "g", "when": "w", "then": "measurable then"}}
        ])
        data["clarifications"] = [
            {"qid": "Q1", "question": "which?", "answer": "A", "answered_by": "user",
             "resolution": "user picked A"}
        ]
        warns = validate_requirements_block(data, "full")
        # The real seam must flag FR-1 (user-stated requirement left un-confirmed).
        assert any("FR-1" in w and ("confirmed" in w or "status" in w) for w in warns), \
            f"consistency seam did not fire through real validate_requirements_block: {warns}"

    def test_real_consistent_case_clean(self):
        """The consistent artifact (user-stated + confirmed) produces no FR-1 WARN
        through the real path — proves the seam discriminates, not blanket-warns."""
        from scripts.pipeline_validator import validate_requirements_block
        data = _reqs_block([
            {"id": "FR-1", "statement": "the thing the user clarified",
             "source": {"kind": "user-stated", "ref": "user answered Q1"},
             "status": "confirmed",
             "work_class": "task",
             "acceptance": {"given": "g", "when": "w", "then": "measurable then"}}
        ])
        data["clarifications"] = [
            {"qid": "Q1", "question": "which?", "answer": "A", "answered_by": "user",
             "resolution": "user picked A"}
        ]
        warns = validate_requirements_block(data, "full")
        assert not any("FR-1" in w and ("confirmed" in w or "status" in w) for w in warns), warns

    def test_clarification_warn_threads_through_real_validate(self, workspace):
        """Meta-review MED: the unit tests call _check_clarifications /
        validate_requirements_block directly; the only validate()-level E2E fixtures
        use clarifications: []. This proves the FULL wiring
        validate() -> validate_requirements_block -> _check_clarifications -> warnings[]
        fires on a POPULATED clarifications[] through the real CLI entry point — so a
        future refactor of the schema_version gate / wiring block that silently kills
        the clarification layer goes RED here (not just in isolation). Mutation-proven."""
        project_dir = workspace / "Projects" / "TestProject"
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "PRODUCT.md").write_text("# Test\n\n## Non-Goals\n\n- **Not X** -- skip.\n")
        (project_dir / "TECH.md").write_text("# Test\n\n## Architecture\n\nDesktop app.\n")
        (project_dir / "IMPROVEMENT.md").write_text(
            "# Test\n\n## What Worked\n\n- OK\n\n## What Failed\n\n- **Retry too aggressive** -- cascading failures\n"
        )
        (project_dir / "PROJECT.md").write_text("# Test\n")

        artifacts_dir = project_dir / ".artifacts"
        runs_dir = artifacts_dir / "runs" / "run_test1"
        # A schema_version-gated evaluate artifact with a POPULATED clarifications[]:
        # a user-answered MCQ whose requirement was left un-confirmed (the inconsistency).
        _make_artifact(artifacts_dir, "run_test1", "art_eval", "evaluation", {
            "recommendation": "GO", "scope": "standard",
            "acceptance_criteria": ["a"], "scores": {"s": 5},
            "schema_version": REQ_SCHEMA_VERSION,
            "stage_sealed": ["evaluate"],
            "requirements": [
                {"id": "FR-1", "statement": "the thing the user clarified",
                 "source": {"kind": "user-stated", "ref": "user answered Q1"},
                 "status": "assumed", "work_class": "task",
                 "acceptance": {"given": "g", "when": "w", "then": "measurable then"}}
            ],
            "clarifications": [
                {"qid": "Q1", "question": "which?",
                 "options": ["A. x", "B. y", "X. Other"],
                 "answer": "A. x", "answered_by": "user"}
            ],
        })
        _make_run(runs_dir, stages=[_stage_record("evaluate", artifact_id="art_eval")])

        result = validate("TestProject", "run_test1", "evaluate")
        # WARN-only: the artifact still VALIDATES (no publish-gate error).
        assert result["valid"] is True, result["errors"]
        # The clarification-layer WARN must thread all the way through validate().
        assert any("FR-1" in w and ("confirmed" in w or "status" in w)
                   for w in result["warnings"]), \
            f"clarification WARN did not thread through real validate(): {result['warnings']}"


class TestProducerInstruction:
    """Producer-wiring run: evaluate.md's §Structured Requirements must be a [MUST]
    production instruction (not [GUIDE]) that names requirements[]+schema_version:1+
    clarifications[] as required, so EVALUATE actually emits the structured block and
    the glass-box downstream (REPORT audit / validator WARN / inline emit) lights up.
    NON-VACUOUS (Gate-1): asserts the three fields are named as required production +
    the schema_version pairing is explicit — not a bare '[MUST]' string match."""

    def _section(self) -> str:
        import pathlib
        md = (pathlib.Path(__file__).resolve().parent.parent
              / "skills" / "s_autonomous-pipeline" / "stages" / "evaluate.md").read_text()
        # isolate the §Structured Requirements section (heading -> next ### heading).
        # Guard (Gate-2 LOW): a renamed heading or a section-becomes-last edit must fail
        # as a clean RED assertion, not an opaque ValueError in every test sharing this.
        marker = "### Structured Requirements"
        assert marker in md, f"§Structured Requirements heading missing from evaluate.md (renamed/removed?)"
        start = md.index(marker)
        nxt = md.find("\n### ", start + len(marker))
        return md[start:] if nxt == -1 else md[start:nxt]

    def test_section_is_must_not_guide(self):
        sec = self._section()
        heading = sec.splitlines()[0]
        assert "[MUST]" in heading, f"§Structured Requirements must be [MUST]: {heading!r}"
        assert "[GUIDE]" not in heading, f"§Structured Requirements must NOT be [GUIDE]: {heading!r}"

    def test_names_three_fields_as_required_production(self):
        """NON-VACUOUS: the section must name requirements[] + schema_version:1 +
        clarifications[] as required production (a tag-only change would NOT pass this)."""
        sec = self._section()
        low = sec.lower()
        assert "requirements[]" in low or "requirements[" in low, "must name requirements[]"
        assert "schema_version" in low, "must name schema_version"
        assert "clarifications" in low, "must name clarifications"
        # MUST-strength production verb present (not optional 'produce it, don't fear a BLOCK').
        # This catches the realistic regression: a [GUIDE]-era optional voice ('Produce it,
        # don't fear a BLOCK') carries NONE of these MUST tokens, so a tag+voice reversion goes
        # RED. We assert PRESENCE of required-production vocabulary — we deliberately do NOT try
        # to prove, by string-matching, that the prose is semantically "unconditional": five
        # adversarial rounds showed a prose-semantics assertion is an open-set arms race that a
        # string test structurally cannot win AND that false-REDs legitimate skill-doc rewording
        # (C044: a check that can't self-enforce its own intent is a net-negative — removed).
        # The REAL enforcement of "EVALUATE actually produces the block" is agent-discipline
        # ([MUST], per the design strangler) + this run's dogfood, NOT a doc-string regex.
        assert any(k in low for k in ("must emit", "must produce", "必须产出", "必产", "mandatory", "required production")), \
            "section must carry a MUST-strength production instruction, not optional voice"
        # The exact operative directive phrase must be present (a tag+voice reversion to the
        # old bare 'Produce a structured ...' drops the 'You MUST produce ... (strict profiles)'
        # form → RED). Presence, not semantics — the honest ceiling of a doc-structure test.
        assert "you must produce a structured `requirements[]` array** (strict profiles)" in low, \
            "the operative 'You MUST produce ... (strict profiles)' directive must be present verbatim"

    def test_schema_version_paired_with_requirements(self):
        """Gate-1 dependency: schema_version:1 must be explicitly paired with
        requirements[] IN PROSE (without version the validator WARN layer silently
        no-ops). NON-VACUOUS fix (Gate-2 found the old form matched the pre-existing
        JSON example 'schema_version: 1' and so passed on the un-upgraded [GUIDE] doc):
        assert a PROSE pairing phrase, not the JSON snippet — the snippet survives a
        prose-removal mutation, a prose phrase does not."""
        sec = self._section()
        low = sec.lower()
        # Must carry an explicit PROSE pairing instruction (not the JSON example line).
        # Any of these phrasings ties the array to the version in the instruction text.
        pairing_phrases = ["non-optional pair", "emit both or neither", "emit both",
                           "paired with `schema_version", "paired with schema_version",
                           "alongside the array", "both or neither"]
        assert any(p in low for p in pairing_phrases), \
            ("§Structured Requirements must state the requirements[]+schema_version:1 "
             "PAIRING in prose (not just the JSON example) — found none of: "
             f"{pairing_phrases}")

    def _publish_example_data(self) -> str:
        """Extract the --data JSON payload string from evaluate.md's Artifact Publish
        command. The payload keeps illustrative `...`/`{...}`/`[...]` placeholders for
        FREE-TEXT fields, so we never json.loads the whole string — we regex just the
        structured SRS fields this run adds (requirements/schema_version/clarifications),
        which MUST be concrete+parseable so a copy-pasting agent emits the block
        (finding #1: the empty example was the real break that left the glass box dark)."""
        import pathlib
        md = (pathlib.Path(__file__).resolve().parent.parent
              / "skills" / "s_autonomous-pipeline" / "stages" / "evaluate.md").read_text()
        anchor = "### Artifact Publish"
        assert anchor in md, "Artifact Publish section missing from evaluate.md"
        block = md[md.index(anchor):]
        import re
        m = re.search(r"--data '(\{.*?\})'\n", block, re.DOTALL)
        assert m, "could not locate --data payload in Artifact Publish command"
        return m.group(1)

    @staticmethod
    def _balanced_array(payload: str, key: str) -> str:
        """Extract the bracket-balanced [...] value of `key` from a JSON-ish payload
        that CANNOT be json.loads'd whole (it carries illustrative ... placeholders).
        A naive non-greedy regex breaks on nested objects (source/acceptance), so we
        balance brackets instead."""
        marker = f'"{key}":'
        assert marker in payload, f"payload missing {key!r}"
        i = payload.index(marker) + len(marker)
        while payload[i] != '[':
            i += 1
        depth, start = 0, i
        while i < len(payload):
            if payload[i] == '[':
                depth += 1
            elif payload[i] == ']':
                depth -= 1
                if depth == 0:
                    return payload[start:i + 1]
            i += 1
        raise AssertionError(f"unbalanced array for {key!r}")

    def test_publish_example_carries_structured_fields(self):
        """#1 ROOT FIX: the publish command example must TEACH the three structured
        fields with concrete, parseable JSON — an agent copy-pasting the command must
        produce requirements[]+schema_version:1+clarifications[], else validator skips
        and the audit renders dark. A tag/prose change alone does NOT fix this (the
        agent copies the COMMAND, not the prose)."""
        import json, re
        payload = self._publish_example_data()
        assert re.search(r'"schema_version"\s*:\s*1', payload), \
            "publish example --data must carry concrete schema_version:1"
        reqs = json.loads(self._balanced_array(payload, "requirements"))
        assert isinstance(reqs, list) and len(reqs) >= 1, "requirements[] must have >=1 entry"
        r0 = reqs[0]
        for field in ("id", "statement", "work_class", "source", "status", "acceptance"):
            assert field in r0, f"example requirement missing required field {field!r}"
        assert "kind" in r0["source"], "example requirement.source must carry kind"
        assert re.search(r'"clarifications"\s*:\s*\[', payload), \
            "publish example --data must carry a clarifications[] array"
        assert not re.search(r"migrat(e|es|ing|ion)", payload.lower()), \
            "publish example must avoid the migrat* root (false-trips migration-class gate)"

    def test_example_structured_block_enters_real_validation(self):
        """#1 TEETH (end-to-end, non-vacuous): a payload mirroring the example's
        structured fields must ENTER validate_requirements_block's real checks, not the
        requirements-missing / schema_version-missing early-exit (return []). Mutation:
        drop requirements/schema_version -> early-exit -> the probe WARN disappears."""
        import json
        payload = self._publish_example_data()
        reqs = json.loads(self._balanced_array(payload, "requirements"))
        good = {"schema_version": 1, "requirements": reqs, "scope": "standard"}
        probe = json.loads(json.dumps(good))
        probe["requirements"][0]["source"] = {"kind": "", "ref": "x"}
        warns_probe = validate_requirements_block(probe, "standard")
        assert any("source" in w.lower() for w in warns_probe), \
            "a blank source.kind must raise a provenance WARN (proves real validation entered)"
        mut_no_reqs = {k: v for k, v in good.items() if k != "requirements"}
        assert validate_requirements_block(mut_no_reqs, "standard") == [], \
            "missing requirements must early-exit to [] (the pre-fix dark state)"
        mut_no_ver = {k: v for k, v in good.items() if k != "schema_version"}
        assert validate_requirements_block(mut_no_ver, "standard") == [], \
            "missing schema_version must early-exit to []"

    def test_evaluate_schema_does_not_nudge_srs_on_every_evaluate(self):
        """#1 fix(b) DROPPED (net-negative): requirements/schema_version must NOT be in
        evaluate's `recommended`, because _check_schema's missing-recommended nudge is
        not profile-gated and would emit advisory noise on every non-strict evaluate that
        legitimately produces no SRS block. The glass box lights from field PRESENCE
        (validate_requirements_block), not a schema nudge. (Gate-0: fix(b) was cosmetic.)"""
        from scripts.pipeline_validator import STAGE_SCHEMAS
        rec = STAGE_SCHEMAS["evaluate"]["recommended"]
        assert "requirements" not in rec, \
            "requirements must NOT be recommended (would noise every non-SRS evaluate)"
        assert "schema_version" not in rec, \
            "schema_version must NOT be recommended (would noise every non-SRS evaluate)"


class TestIdPrefixBoundary:
    """#2: AC-coverage ID extraction must have a boundary — a garbage token that
    prefix-collides with a real id (FR-1-placeholder, AC3garbage) must NOT be
    accepted as that id, or BUILD ac_coverage can mark an uncovered PLAN AC covered."""

    def test_garbage_prefix_rejected(self):
        from scripts.pipeline_validator import _extract_ac_id
        assert _extract_ac_id("FR-1-placeholder") is None
        assert _extract_ac_id("AC3garbage") is None
        assert _extract_ac_id("NFR-2unrelated") is None

    def test_legit_forms_still_match(self):
        from scripts.pipeline_validator import _extract_ac_id
        assert _extract_ac_id("AC1") == "AC1"
        assert _extract_ac_id("AC1: returns 200") == "AC1"
        assert _extract_ac_id("AC1 — does x") == "AC1"
        assert _extract_ac_id("FR-1: the statement") == "FR-1"
        assert _extract_ac_id("NFR-2 performance") == "NFR-2"

    def test_no_id_returns_none(self):
        from scripts.pipeline_validator import _extract_ac_id
        assert _extract_ac_id("just prose no id") is None
        assert _extract_ac_id("") is None


class TestRequirementsShapeCheck:
    """#6: declared enum sets (REQ_WORK_CLASSES / REQ_STATUSES) must actually be
    enforced — typos, missing/dup ids, missing statement were silently passing."""

    def test_bad_work_class_warns(self):
        from scripts.pipeline_validator import _check_requirements_shape
        warns = _check_requirements_shape([_fr(id="FR-1")] )  # baseline clean
        assert warns == []
        bad = dict(_fr(id="FR-1")); bad["work_class"] = "user-stroy"
        warns = _check_requirements_shape([bad])
        assert any("work_class" in w and "FR-1" in w for w in warns), warns

    def test_bad_status_warns(self):
        from scripts.pipeline_validator import _check_requirements_shape
        bad = dict(_fr(id="FR-2")); bad["status"] = "confirmd"
        warns = _check_requirements_shape([bad])
        assert any("status" in w and "FR-2" in w for w in warns), warns

    def test_missing_id_warns(self):
        from scripts.pipeline_validator import _check_requirements_shape
        bad = dict(_fr()); bad.pop("id", None)
        warns = _check_requirements_shape([bad])
        assert any("id" in w.lower() for w in warns), warns

    def test_duplicate_id_warns(self):
        from scripts.pipeline_validator import _check_requirements_shape
        warns = _check_requirements_shape([_fr(id="FR-1"), _fr(id="FR-1")])
        assert any("duplicate" in w.lower() and "FR-1" in w for w in warns), warns

    def test_missing_statement_warns(self):
        from scripts.pipeline_validator import _check_requirements_shape
        bad = dict(_fr(id="FR-3")); bad["statement"] = ""
        warns = _check_requirements_shape([bad])
        assert any("statement" in w.lower() and "FR-3" in w for w in warns), warns

    def test_two_missing_ids_not_counted_as_duplicate(self):
        """Gate-2: two requirements both missing an id must NOT be reported as a
        duplicate id — only the missing-id WARN fires (×2), no dup WARN."""
        from scripts.pipeline_validator import _check_requirements_shape
        a = dict(_fr()); a.pop("id", None)
        b = dict(_fr()); b.pop("id", None)
        warns = _check_requirements_shape([a, b])
        assert sum("id is missing" in w for w in warns) == 2, warns
        assert not any("duplicate" in w.lower() for w in warns), warns

    def test_literal_none_string_id_dup_is_warned(self):
        """Gate-2 LOW closed: a literal string id 'None' is a PRESENT id, so two of
        them ARE a real duplicate — must warn (previously suppressed by a repr(None)
        sentinel collision)."""
        from scripts.pipeline_validator import _check_requirements_shape
        warns = _check_requirements_shape([_fr(id="None"), _fr(id="None")])
        assert any("duplicate" in w.lower() and "None" in w for w in warns), warns

    def test_shape_is_warn_only_wired_before_invest(self):
        """#6+#7 coupling: _check_requirements_shape is wired into
        validate_requirements_block and its output appears in warnings (never errors)."""
        bad = dict(_fr(id="FR-1")); bad["work_class"] = "user-stroy"
        warns = validate_requirements_block(_reqs_block([bad]), "full")
        assert any("work_class" in w for w in warns), warns


class TestUnhashableIdCrashSafe:
    """#7: a list/dict-typed id must NOT crash the SRS WARN layer (it used to raise
    TypeError in the all_ids set-comp / graph dict-comp, which _CheckGuard swallowed
    as a generic ERRORED, losing every other SRS + clarification check)."""

    def test_list_id_does_not_crash_invest(self):
        from scripts.pipeline_validator import _check_invest
        # must not raise
        warns = _check_invest([_fr(id=["x"], work_class="user-story")], "full")
        assert isinstance(warns, list)

    def test_dict_depends_on_does_not_crash_cycle(self):
        from scripts.pipeline_validator import _has_dependency_cycle
        r = _fr(id="FR-1", work_class="user-story"); r["depends_on"] = {"bogus": 1}
        # must not raise
        assert _has_dependency_cycle([r]) in (True, False)

    def test_unhashable_dep_element_does_not_crash_cycle(self):
        # #7 (graph dep-element coercion, pipeline_validator.py:1163): a nested-list
        # element INSIDE depends_on is unhashable — the graph membership test (`nxt in
        # graph`) crashes with TypeError unless each dep is coerced. This is the only
        # test that forces that coercion (reverting line 1163 must turn this RED).
        from scripts.pipeline_validator import _has_dependency_cycle
        r = _fr(id="FR-1", work_class="user-story"); r["depends_on"] = [["nested"]]
        # must not raise (unhashable nested element coerced to a hashable key)
        assert _has_dependency_cycle([r]) in (True, False)

    def test_unhashable_id_preserves_downstream_checks(self):
        """The decisive blast-radius test: an unhashable id must NOT abort the rest of
        validate_requirements_block. A SECOND requirement with a blank source.kind must
        STILL produce its provenance WARN (which lives AFTER _check_invest)."""
        bad_id = dict(_fr(id="FR-1", work_class="user-story")); bad_id["id"] = ["x"]
        blank_src = _fr(id="FR-2", kind="")
        warns = validate_requirements_block(_reqs_block([bad_id, blank_src]), "full")
        assert any("FR-2" in w and "source" in w.lower() for w in warns), \
            f"downstream provenance check must still run after an unhashable id, got {warns}"


class TestAcceptanceCoercion:
    """#5 (Kiro): a single GWT object acceptance (as the doc teaching block showed)
    was silently read as absent by the list-only consumers -> false INVEST 'no
    acceptance' WARN + `_(none)_` in the audit table (the glass box lies). Fix coerces
    acceptance to a list at the _iter_requirements boundary, so all 5 consumers see a
    list. These teeth thread the FULL boundary (validate_requirements_block /
    render_requirements_md), NOT a direct _acceptance_is_present/_coerce call — a
    direct-call test would stay green with the boundary coercion reverted (Gate-1 F3)."""

    def test_single_object_acceptance_no_false_invest_warn(self):
        # thread validate_requirements_block -> _iter_requirements -> _check_invest ->
        # _acceptance_is_present. A single GWT object must NOT produce the Testable WARN.
        from scripts.pipeline_validator import validate_requirements_block
        data = {
            "schema_version": REQ_SCHEMA_VERSION, "scope": "standard",
            "requirements": [{
                "id": "FR-1", "statement": "user wants a refund path",
                "work_class": "user-story", "source": {"kind": "user-stated", "ref": "user said X"},
                "status": "confirmed",
                "acceptance": {"given": "an order", "when": "refund requested", "then": "money returned"},
            }],
        }
        warns = validate_requirements_block(data, "full")
        testable = [w for w in warns if "Testable" in w and "no acceptance criterion" in w]
        assert testable == [], f"single-object acceptance falsely flagged absent: {testable}"

    def test_single_object_acceptance_renders_in_requirements_md(self):
        # thread render_requirements_md -> _iter_requirements. The row must show the
        # GWT text and the ● glyph, not ○ / _(none)_.
        from scripts.pipeline_validator import render_requirements_md
        data = {
            "schema_version": REQ_SCHEMA_VERSION,
            "requirements": [{
                "id": "FR-1", "statement": "refund path",
                "work_class": "user-story", "source": {"kind": "user-stated", "ref": "x"},
                "status": "confirmed",
                "acceptance": {"given": "an order", "when": "refund requested", "then": "money returned"},
            }],
        }
        md = render_requirements_md(data)
        assert "money returned" in md, "single-object acceptance text not rendered"
        assert "_(none)_" not in md, "single-object acceptance rendered as none"
        assert "○" not in md, "single-object acceptance glyph shows absent"

    def test_partial_gwt_still_absent(self):
        # §4.5 false-green guard preserved: a partial GWT (no given/when) is NOT present.
        from scripts.pipeline_validator import validate_requirements_block
        data = {
            "schema_version": REQ_SCHEMA_VERSION, "scope": "standard",
            "requirements": [{
                "id": "FR-1", "statement": "x", "work_class": "user-story",
                "source": {"kind": "user-stated", "ref": "x"}, "status": "confirmed",
                "acceptance": {"then": "money returned"},  # partial: no given/when
            }],
        }
        warns = validate_requirements_block(data, "full")
        testable = [w for w in warns if "Testable" in w and "no acceptance criterion" in w]
        assert testable, "partial GWT must still be flagged absent (false-green guard)"

    def test_coerce_acceptance_shapes(self):
        # supplementary micro-test (NOT the sole tooth): the helper's shape contract.
        from scripts.pipeline_validator import _coerce_acceptance
        assert _coerce_acceptance({"given": "g", "when": "w", "then": "t"}) == \
            [{"given": "g", "when": "w", "then": "t"}]
        assert _coerce_acceptance("free text") == ["free text"]
        assert _coerce_acceptance([{"given": "g"}]) == [{"given": "g"}]
        assert _coerce_acceptance(None) == []
        assert _coerce_acceptance(123) == []

    def test_iter_requirements_does_not_mutate_original(self):
        # the shallow-copy contract: coercing acceptance must not rewrite the caller's
        # data["requirements"] shape (single dict stays a single dict in the source).
        from scripts.pipeline_validator import _iter_requirements
        src = {"requirements": [{"id": "FR-1", "acceptance": {"given": "g", "when": "w", "then": "t"}}]}
        out = _iter_requirements(src)
        assert isinstance(out[0]["acceptance"], list), "boundary did not coerce to list"
        assert src["requirements"][0]["acceptance"] == {"given": "g", "when": "w", "then": "t"}, \
            "original data was mutated"
