"""Tests for eval_runner.py."""
import json
import sys
from pathlib import Path

import pytest

# Add parent to path for import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.scripts.eval_runner import (
    load_golden_set,
    compute_scores,
    eval_keyword_match,
    eval_trajectory,
    evaluate_case,
    filter_cases_by_tags,
    _find_workspace_root,
    _find_swarmai_repo,
)


class TestLoadGoldenSet:
    """Test golden_set.yaml loading and validation."""

    def test_loads_successfully(self):
        root = _find_workspace_root()
        gs_path = root / "Projects" / "SwarmAI" / "golden_set.yaml"
        data = load_golden_set(gs_path)
        assert data["version"] == 2
        assert len(data["cases"]) >= 20  # Grows via flywheel (auto-seed from corrections)

    def test_all_cases_have_required_fields(self):
        root = _find_workspace_root()
        gs_path = root / "Projects" / "SwarmAI" / "golden_set.yaml"
        data = load_golden_set(gs_path)
        for case in data["cases"]:
            assert "id" in case, f"Missing id: {case.get('title')}"
            assert "evaluators" in case, f"{case['id']} missing evaluators"
            assert "affected_by" in case, f"{case['id']} missing affected_by"
            assert "category" in case, f"{case['id']} missing category"
            assert "dimension" in case, f"{case['id']} missing dimension"
            assert "title" in case, f"{case['id']} missing title"

    def test_unique_ids(self):
        root = _find_workspace_root()
        gs_path = root / "Projects" / "SwarmAI" / "golden_set.yaml"
        data = load_golden_set(gs_path)
        ids = [c["id"] for c in data["cases"]]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[i for i in ids if ids.count(i) > 1]}"

    def test_valid_categories(self):
        root = _find_workspace_root()
        gs_path = root / "Projects" / "SwarmAI" / "golden_set.yaml"
        data = load_golden_set(gs_path)
        valid_categories = set(data["categories"])
        for case in data["cases"]:
            assert case["category"] in valid_categories, f"{case['id']} has invalid category '{case['category']}'"


class TestComputeScores:
    """Test score computation logic."""

    def test_all_passed(self):
        cases = [{"dimension": "capability"}, {"dimension": "capability"}]
        results = [{"status": "passed"}, {"status": "passed"}]
        scores = compute_scores(cases, results)
        assert scores["overall"] == 100.0
        assert scores["dimensions"]["capability"] == 100.0

    def test_mixed_results(self):
        cases = [{"dimension": "capability"}, {"dimension": "factual_accuracy"}]
        results = [{"status": "passed"}, {"status": "failed"}]
        scores = compute_scores(cases, results)
        assert scores["overall"] == 50.0
        assert scores["dimensions"]["capability"] == 100.0
        assert scores["dimensions"]["factual_accuracy"] == 0.0

    def test_skipped_excluded(self):
        cases = [{"dimension": "capability"}, {"dimension": "compliance"}]
        results = [{"status": "passed"}, {"status": "skipped"}]
        scores = compute_scores(cases, results)
        assert scores["overall"] == 100.0  # Only scored cases count
        assert scores["scored_count"] == 1
        assert scores["skipped_count"] == 1

    def test_all_skipped(self):
        cases = [{"dimension": "capability"}]
        results = [{"status": "skipped"}]
        scores = compute_scores(cases, results)
        assert scores["overall"] == 0.0
        assert scores["scored_count"] == 0


class TestPathResolution:
    """Test workspace/repo discovery."""

    def test_workspace_found(self):
        root = _find_workspace_root()
        assert (root / "Projects" / "SwarmAI").is_dir()

    def test_swarmai_repo_found(self):
        repo = _find_swarmai_repo()
        assert (repo / "backend" / "core").is_dir()


class TestKeywordMatch:
    """Test keyword_match evaluator."""

    def test_all_keywords_present(self):
        case = {
            "id": "TEST001",
            "expected_response_contains": ["pipeline", "trivial"],
            "evaluators": ["keyword_match"],
        }
        # Simulate a response that contains both keywords
        result = eval_keyword_match(case, simulated_response="Use pipeline with trivial profile")
        assert result["status"] == "passed"

    def test_keyword_missing(self):
        case = {
            "id": "TEST002",
            "expected_response_contains": ["pipeline", "DEFER"],
            "evaluators": ["keyword_match"],
        }
        result = eval_keyword_match(case, simulated_response="Use pipeline for this change")
        assert result["status"] == "failed"
        assert "DEFER" in result["notes"]

    def test_case_insensitive(self):
        case = {
            "id": "TEST003",
            "expected_response_contains": ["Pipeline"],
            "evaluators": ["keyword_match"],
        }
        result = eval_keyword_match(case, simulated_response="run pipeline now")
        assert result["status"] == "passed"

    def test_no_keywords_defined(self):
        case = {
            "id": "TEST004",
            "evaluators": ["keyword_match"],
        }
        result = eval_keyword_match(case, simulated_response="anything")
        assert result["status"] == "skipped"


class TestTrajectoryEvaluator:
    """Test trajectory_* evaluators."""

    def test_in_order_pass(self):
        case = {
            "id": "TEST010",
            "expected_trajectory": ["Read target file", "Invoke pipeline"],
            "trajectory_match": "in_order",
            "evaluators": ["trajectory_in_order"],
        }
        actual_trajectory = [
            "Read target file",
            "Discuss approach",
            "Invoke pipeline",
        ]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "passed"

    def test_in_order_fail_wrong_order(self):
        case = {
            "id": "TEST011",
            "expected_trajectory": ["Read target file", "Invoke pipeline"],
            "trajectory_match": "in_order",
            "evaluators": ["trajectory_in_order"],
        }
        actual_trajectory = [
            "Invoke pipeline",
            "Read target file",
        ]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "failed"

    def test_in_order_fail_missing_step(self):
        case = {
            "id": "TEST012",
            "expected_trajectory": ["Read target file", "Invoke pipeline"],
            "trajectory_match": "in_order",
            "evaluators": ["trajectory_in_order"],
        }
        actual_trajectory = ["Read target file"]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "failed"

    def test_any_order_pass(self):
        case = {
            "id": "TEST013",
            "expected_trajectory": ["Invoke pipeline", "Read target file"],
            "trajectory_match": "any_order",
            "evaluators": ["trajectory_any_order"],
        }
        actual_trajectory = [
            "Read target file",
            "Invoke pipeline",
        ]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "passed"

    def test_exact_pass(self):
        case = {
            "id": "TEST014",
            "expected_trajectory": ["Read file", "Edit file"],
            "trajectory_match": "exact",
            "evaluators": ["trajectory_exact"],
        }
        actual_trajectory = ["Read file", "Edit file"]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "passed"

    def test_exact_fail_extra_step(self):
        case = {
            "id": "TEST015",
            "expected_trajectory": ["Read file", "Edit file"],
            "trajectory_match": "exact",
            "evaluators": ["trajectory_exact"],
        }
        actual_trajectory = ["Read file", "Think", "Edit file"]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "failed"

    def test_no_trajectory_skips(self):
        case = {
            "id": "TEST016",
            "evaluators": ["trajectory_in_order"],
        }
        result = eval_trajectory(case, actual_trajectory=[])
        assert result["status"] == "skipped"

    def test_substring_matching(self):
        """Trajectory steps should match as substrings (case-insensitive)."""
        case = {
            "id": "TEST017",
            "expected_trajectory": ["Read initialization_manager", "Verify get_session_context exists"],
            "trajectory_match": "in_order",
            "evaluators": ["trajectory_in_order"],
        }
        actual_trajectory = [
            "Read file: backend/core/initialization_manager.py",
            "Grep: verify get_session_context exists in the module",
        ]
        result = eval_trajectory(case, actual_trajectory=actual_trajectory)
        assert result["status"] == "passed"


class TestFilterCasesByTags:
    """Test tag-based filtering of cases."""

    def test_filter_smoke(self):
        cases = [
            {"id": "A", "tags": ["smoke", "regression"]},
            {"id": "B", "tags": ["full"]},
            {"id": "C", "tags": ["smoke"]},
            {"id": "D"},  # no tags
        ]
        filtered = filter_cases_by_tags(cases, ["smoke"])
        assert len(filtered) == 2
        assert {c["id"] for c in filtered} == {"A", "C"}

    def test_filter_multiple_tags(self):
        cases = [
            {"id": "A", "tags": ["smoke"]},
            {"id": "B", "tags": ["regression"]},
            {"id": "C", "tags": ["full"]},
        ]
        filtered = filter_cases_by_tags(cases, ["smoke", "regression"])
        assert len(filtered) == 2

    def test_no_filter_returns_all(self):
        cases = [{"id": "A", "tags": ["smoke"]}, {"id": "B"}]
        filtered = filter_cases_by_tags(cases, None)
        assert len(filtered) == 2

    def test_empty_tags_returns_all(self):
        cases = [{"id": "A", "tags": ["smoke"]}, {"id": "B"}]
        filtered = filter_cases_by_tags(cases, [])
        assert len(filtered) == 2


class TestProgrammaticFirstCascade:
    """Test that programmatic evaluators are tried before LLM judge."""

    def test_keyword_match_resolves_without_llm(self):
        """Case with expected_response_contains + keyword_match evaluator should not need LLM."""
        case = {
            "id": "GS_TEST",
            "category": "compliance",
            "dimension": "compliance",
            "evaluators": ["keyword_match", "goal_success"],
            "expected_response_contains": ["pipeline"],
            "affected_by": ["STEERING.R1"],
        }
        root = _find_workspace_root()
        # This should use keyword_match (programmatic), not goal_success (LLM)
        result = evaluate_case(case, root, simulated_response="Run the pipeline here")
        assert result["evaluator"] == "keyword_match"
        assert result["status"] == "passed"

    def test_fallthrough_to_llm_when_no_programmatic(self):
        """Case with only LLM evaluators falls through."""
        case = {
            "id": "GS_LLM_ONLY",
            "category": "decision",
            "dimension": "judgment_quality",
            "evaluators": ["goal_success"],
            "assertions": ["Agent decides correctly"],
            "affected_by": ["MEMORY.KD34"],
        }
        root = _find_workspace_root()
        result = evaluate_case(case, root)
        assert result["evaluator"] == "goal_success"
        assert result["status"] == "skipped"  # LLM not implemented yet


class TestGoldenSetNewFields:
    """Test that golden_set.yaml supports new fields."""

    def test_tags_field_accepted(self):
        """Cases with tags field should load without error."""
        root = _find_workspace_root()
        gs_path = root / "Projects" / "SwarmAI" / "golden_set.yaml"
        data = load_golden_set(gs_path)
        # At least some cases should have tags after our update
        tagged_cases = [c for c in data["cases"] if c.get("tags")]
        assert len(tagged_cases) > 0, "Expected at least some cases to have tags"

    def test_promoted_from_field_accepted(self):
        """Cases with promoted_from field should load without error."""
        root = _find_workspace_root()
        gs_path = root / "Projects" / "SwarmAI" / "golden_set.yaml"
        data = load_golden_set(gs_path)
        # Check that cases with source field exist (promoted_from is optional)
        sourced = [c for c in data["cases"] if c.get("source") or c.get("promoted_from")]
        assert len(sourced) > 0


class TestEvalHistoryOutput:
    """Test that eval run produces valid output."""

    def test_output_file_exists(self):
        root = _find_workspace_root()
        hist_dir = root / "Projects" / "SwarmAI" / "EvalHistory"
        json_files = list(hist_dir.glob("*.json"))
        assert len(json_files) > 0, "No eval history files found"

    def test_output_schema_valid(self):
        root = _find_workspace_root()
        hist_dir = root / "Projects" / "SwarmAI" / "EvalHistory"
        latest = sorted(hist_dir.glob("*.json"))[-1]
        data = json.loads(latest.read_text())

        assert "run_id" in data
        assert "triggered_by" in data
        assert "overall_score" in data
        assert "dimensions" in data
        assert "cases" in data
        assert "total_cases" in data
        assert isinstance(data["overall_score"], (int, float))
        assert isinstance(data["cases"], list)
