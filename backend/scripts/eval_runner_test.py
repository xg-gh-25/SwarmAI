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
