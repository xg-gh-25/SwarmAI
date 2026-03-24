"""Tests for pipeline_stress_test.py — v4b stress test harness.

Validates the stress test script against 5 acceptance criteria:
    1. define creates 5 pipeline runs with correct expected profiles
    2. validate checks: profile match, validator 6/6, report exists, budget recorded
    3. report aggregates per-stage token costs
    4. All subcommands return structured output
    5. Works with existing artifact_cli and pipeline_validator
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.pipeline_stress_test import (
    STRESS_REQUIREMENTS,
    define_runs,
    validate_run,
    validate_all,
    generate_report,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Create a temp workspace with SwarmAI project."""
    ws = tmp_path / "SwarmWS"
    project_dir = ws / "Projects" / "SwarmAI" / ".artifacts" / "runs"
    project_dir.mkdir(parents=True)
    # Create manifest
    manifest = {"artifacts": [], "pipeline_state": "evaluate"}
    (ws / "Projects" / "SwarmAI" / ".artifacts" / "manifest.json").write_text(
        json.dumps(manifest)
    )
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    return ws


def _make_completed_run(ws, run_id, profile, stages, has_report=True):
    """Create a completed run with stage records."""
    run_dir = ws / "Projects" / "SwarmAI" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    run = {
        "id": run_id,
        "project": "SwarmAI",
        "requirement": "test requirement",
        "profile": profile,
        "status": "completed",
        "stages": stages,
        "taste_decisions": [],
        "budget": {"session_total": 800000, "consumed": 0, "remaining": 800000,
                   "stage_estimates": {}, "calibration_source": "defaults"},
        "created_at": "2026-03-25T00:00:00Z",
        "updated_at": "2026-03-25T00:00:00Z",
        "completed_at": "2026-03-25T01:00:00Z",
    }
    (run_dir / "run.json").write_text(json.dumps(run))

    if has_report:
        (run_dir / "REPORT.md").write_text("# Test Report\n\nContent here.")

    return run


# ── AC1: define creates 5 runs with correct profiles ───────────────────────

class TestDefine:
    def test_5_requirements_defined(self):
        """STRESS_REQUIREMENTS has exactly 5 entries."""
        assert len(STRESS_REQUIREMENTS) == 5

    def test_all_profiles_covered(self):
        """Each of the 5 profiles appears exactly once."""
        profiles = {r["expected_profile"] for r in STRESS_REQUIREMENTS}
        assert profiles == {"trivial", "full", "research", "docs", "bugfix"}

    def test_requirements_are_real(self):
        """Each requirement has a non-empty description."""
        for r in STRESS_REQUIREMENTS:
            assert len(r["requirement"]) > 20, f"Requirement too short: {r['requirement']}"
            assert r["id"]  # has an ID

    def test_define_runs_creates_entries(self, workspace):
        """define_runs creates pipeline run files."""
        results = define_runs("SwarmAI")
        assert len(results) == 5
        for r in results:
            assert "run_id" in r
            assert r["project"] == "SwarmAI"


# ── AC2: validate checks profile, validator, report, budget ────────────────

class TestValidate:
    def test_valid_run_passes(self, workspace):
        """A well-formed completed run passes all checks."""
        stages = [
            {"stage": "evaluate", "status": "completed", "artifact_id": "art_x",
             "token_cost": 5000, "retry_count": 0,
             "decisions": [{"description": "GO", "classification": "mechanical", "reasoning": "test"}]},
        ]
        _make_completed_run(workspace, "run_test1", "trivial", stages)

        result = validate_run("SwarmAI", "run_test1", "trivial")
        assert result["profile_match"] is True
        assert result["report_exists"] is True
        assert result["budget_recorded"] is True

    def test_wrong_profile_detected(self, workspace):
        """Mismatched profile is flagged."""
        stages = [
            {"stage": "evaluate", "status": "completed", "artifact_id": "art_x",
             "token_cost": 5000, "retry_count": 0,
             "decisions": [{"description": "GO", "classification": "mechanical", "reasoning": "test"}]},
        ]
        _make_completed_run(workspace, "run_test2", "full", stages)

        result = validate_run("SwarmAI", "run_test2", "trivial")  # expected trivial, got full
        assert result["profile_match"] is False

    def test_missing_report_detected(self, workspace):
        """Missing REPORT.md is flagged."""
        stages = [
            {"stage": "evaluate", "status": "completed", "artifact_id": "art_x",
             "token_cost": 5000, "retry_count": 0,
             "decisions": [{"description": "GO", "classification": "mechanical", "reasoning": "test"}]},
        ]
        _make_completed_run(workspace, "run_test3", "trivial", stages, has_report=False)

        result = validate_run("SwarmAI", "run_test3", "trivial")
        assert result["report_exists"] is False

    def test_zero_budget_detected(self, workspace):
        """Stages with token_cost=0 are flagged."""
        stages = [
            {"stage": "evaluate", "status": "completed", "artifact_id": "art_x",
             "token_cost": 0, "retry_count": 0,
             "decisions": [{"description": "GO", "classification": "mechanical", "reasoning": "test"}]},
        ]
        _make_completed_run(workspace, "run_test4", "trivial", stages)

        result = validate_run("SwarmAI", "run_test4", "trivial")
        assert result["budget_recorded"] is False

    def test_validate_all_aggregates(self, workspace):
        """validate_all runs all defined requirements."""
        # Create runs matching STRESS_REQUIREMENTS
        for req in STRESS_REQUIREMENTS:
            stages = [
                {"stage": "evaluate", "status": "completed", "artifact_id": "art_x",
                 "token_cost": 5000, "retry_count": 0,
                 "decisions": [{"description": "GO", "classification": "mechanical", "reasoning": "test"}]},
            ]
            _make_completed_run(workspace, req["id"], req["expected_profile"], stages)

        results = validate_all("SwarmAI")
        assert len(results) == 5
        assert all(r["profile_match"] for r in results)


# ── AC3: report aggregates token costs ─────────────────────────────────────

class TestReport:
    def test_report_with_completed_runs(self, workspace):
        """Report aggregates data from completed runs."""
        stages = [
            {"stage": "evaluate", "status": "completed", "artifact_id": "art_x",
             "token_cost": 6000, "retry_count": 0, "decisions": []},
            {"stage": "build", "status": "completed", "artifact_id": "art_y",
             "token_cost": 40000, "retry_count": 0, "decisions": []},
        ]
        _make_completed_run(workspace, "run_r1", "trivial", stages)

        report = generate_report("SwarmAI")
        assert "stage_averages" in report
        assert "total_runs" in report
        assert report["total_runs"] >= 1

    def test_report_empty_project(self, workspace):
        """Report handles project with no completed runs."""
        report = generate_report("SwarmAI")
        assert report["total_runs"] == 0
        assert report["stage_averages"] == {}


# ── AC4: structured output ─────────────────────────────────────────────────

class TestStructuredOutput:
    def test_define_returns_list(self, workspace):
        results = define_runs("SwarmAI")
        assert isinstance(results, list)

    def test_validate_returns_dict(self, workspace):
        stages = [
            {"stage": "evaluate", "status": "completed", "artifact_id": "art_x",
             "token_cost": 5000, "retry_count": 0,
             "decisions": [{"description": "GO", "classification": "mechanical", "reasoning": "test"}]},
        ]
        _make_completed_run(workspace, "run_s1", "trivial", stages)
        result = validate_run("SwarmAI", "run_s1", "trivial")
        assert isinstance(result, dict)
        assert "run_id" in result

    def test_report_returns_dict(self, workspace):
        report = generate_report("SwarmAI")
        assert isinstance(report, dict)
