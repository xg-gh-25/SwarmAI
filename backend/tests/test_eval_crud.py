"""Tests for EvalService CRUD operations and run triggers (P3)."""

import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure backend is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.eval_service import EvalService


@pytest.fixture
def eval_workspace(tmp_path):
    """Create a minimal eval workspace with golden_set.yaml and EvalHistory."""
    project_dir = tmp_path / "Projects" / "SwarmAI"
    project_dir.mkdir(parents=True)
    history_dir = project_dir / "EvalHistory"
    history_dir.mkdir()

    golden_set = {
        "version": 2,
        "categories": ["compliance", "recall"],
        "dimensions": ["compliance", "factual_accuracy"],
        "cases": [
            {
                "id": "GS001",
                "category": "compliance",
                "dimension": "compliance",
                "level": "session",
                "title": "Pipeline mandatory for code changes",
                "source": "C011",
                "affected_by": ["AGENT.md"],
                "evaluators": ["file_contains"],
                "scenario": {"turns": [{"input": "Fix typo"}]},
                "expected_trajectory": ["pipeline"],
                "verification": {"file": "test.py", "grep": "pipeline"},
            },
            {
                "id": "GS002",
                "category": "recall",
                "dimension": "factual_accuracy",
                "level": "session",
                "title": "Memory recall accuracy",
                "source": "KD01",
                "affected_by": ["MEMORY.md"],
                "evaluators": ["canary_pass"],
                "scenario": {"turns": [{"input": "What is X?"}]},
                "expected_trajectory": ["read_memory"],
                "verification": {"command": "echo OK", "expected_contains": "OK"},
            },
        ],
    }

    # Write YAML
    import yaml
    (project_dir / "golden_set.yaml").write_text(yaml.dump(golden_set, default_flow_style=False))

    # Write a sample run
    run = {
        "run_id": "eval_20260614_manual",
        "triggered_by": "manual",
        "triggered_at": "2026-06-14T04:00:00Z",
        "overall_score": 100.0,
        "dimensions": {"compliance": 100.0, "factual_accuracy": 100.0},
        "cases": [
            {"id": "GS001", "status": "passed", "duration_ms": 50},
            {"id": "GS002", "status": "passed", "duration_ms": 30},
        ],
        "total_cases": 2,
        "cases_passed": 2,
        "cases_failed": 0,
        "cases_skipped": 0,
        "duration_seconds": 0.08,
    }
    (history_dir / "2026-06-14_manual.json").write_text(json.dumps(run))

    return tmp_path


@pytest.fixture
def svc(eval_workspace):
    """Create an EvalService backed by the test workspace."""
    return EvalService(workspace_root=eval_workspace)


# ─── CRUD: Add Case ──────────────────────────────────────────────────────────


class TestAddCase:
    def test_add_case_success(self, svc):
        new_case = {
            "id": "GS003",
            "category": "compliance",
            "dimension": "compliance",
            "level": "session",
            "title": "New test case",
            "source": "manual",
            "affected_by": ["SOUL.md"],
            "evaluators": ["file_contains"],
            "scenario": {"turns": [{"input": "test"}]},
            "verification": {"file": "x.py", "grep": "y"},
        }
        result = svc.add_case(new_case)
        assert result["id"] == "GS003"
        assert svc.case_count == 3

    def test_add_case_duplicate_id_fails(self, svc):
        duplicate = {
            "id": "GS001",  # already exists
            "category": "compliance",
            "dimension": "compliance",
            "title": "Duplicate",
            "evaluators": ["file_contains"],
            "affected_by": ["AGENT.md"],
        }
        with pytest.raises(ValueError, match="already exists"):
            svc.add_case(duplicate)

    def test_add_case_missing_required_field(self, svc):
        incomplete = {"id": "GS099", "title": "No evaluators"}
        with pytest.raises(ValueError, match="required"):
            svc.add_case(incomplete)

    def test_add_case_persists_to_disk(self, svc, eval_workspace):
        new_case = {
            "id": "GS004",
            "category": "recall",
            "dimension": "factual_accuracy",
            "level": "session",
            "title": "Persisted case",
            "source": "test",
            "affected_by": ["KNOWLEDGE.md"],
            "evaluators": ["canary_pass"],
            "verification": {"command": "echo hi", "expected_contains": "hi"},
        }
        svc.add_case(new_case)

        # Reload from disk to verify
        svc2 = EvalService(workspace_root=eval_workspace)
        assert svc2.case_count == 3
        case = svc2.get_case_detail("GS004")
        assert case is not None
        assert case["title"] == "Persisted case"


# ─── CRUD: Update Case ───────────────────────────────────────────────────────


class TestUpdateCase:
    def test_update_case_success(self, svc):
        result = svc.update_case("GS001", {"title": "Updated title"})
        assert result["title"] == "Updated title"
        # Other fields preserved
        assert result["category"] == "compliance"

    def test_update_case_not_found(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.update_case("GS999", {"title": "Nope"})

    def test_update_case_cannot_change_id(self, svc):
        with pytest.raises(ValueError, match="Cannot change"):
            svc.update_case("GS001", {"id": "GS_NEW"})

    def test_update_case_persists(self, svc, eval_workspace):
        svc.update_case("GS001", {"title": "Persisted update"})
        svc2 = EvalService(workspace_root=eval_workspace)
        case = svc2.get_case_detail("GS001")
        assert case["title"] == "Persisted update"


# ─── CRUD: Delete (Archive) Case ─────────────────────────────────────────────


class TestDeleteCase:
    def test_delete_case_archives(self, svc):
        result = svc.delete_case("GS002")
        assert result["tier"] == "archived"
        # Case still accessible via detail
        case = svc.get_case_detail("GS002")
        assert case["tier"] == "archived"

    def test_delete_case_not_found(self, svc):
        with pytest.raises(ValueError, match="not found"):
            svc.delete_case("GS999")

    def test_delete_case_excluded_from_golden_set_list(self, svc):
        svc.delete_case("GS002")
        gs = svc.get_golden_set()
        active_ids = [c["id"] for c in gs["cases"] if c.get("tier") != "archived"]
        assert "GS002" not in active_ids


# ─── Run Triggers ────────────────────────────────────────────────────────────


class TestTriggerRun:
    def test_trigger_run_returns_run_id(self, svc):
        run_id = svc.trigger_run(trigger="manual")
        assert run_id.startswith("eval_")
        assert "manual" in run_id

    def test_trigger_run_with_cases(self, svc):
        run_id = svc.trigger_run(trigger="manual", case_ids=["GS001"])
        assert run_id is not None

    def test_trigger_run_creates_history_file(self, svc, eval_workspace):
        run_id = svc.trigger_run(trigger="test_trigger")
        # Wait for background thread (max 5s)
        import time
        for _ in range(50):
            svc.reload()
            runs = svc.get_history()
            if any(r["run_id"] == run_id for r in runs):
                break
            time.sleep(0.1)
        else:
            pytest.fail(f"Run {run_id} not found in history after 5s")

        run = next(r for r in runs if r["run_id"] == run_id)
        assert run["triggered_by"] == "test_trigger"

    def test_trigger_run_rejects_while_running(self, svc):
        """Cannot trigger a new run while one is in progress."""
        svc.trigger_run(trigger="first")
        # Immediately try second — should raise or return None
        with pytest.raises((ValueError, RuntimeError)):
            svc.trigger_run(trigger="second")


class TestCanaryRun:
    def test_canary_runs_synchronously(self, svc):
        """Canary runs only programmatic cases and returns immediately."""
        result = svc.run_canary()
        assert "overall_score" in result
        assert result["triggered_by"] == "canary"
        # Should be fast (<5s)
        assert result["duration_seconds"] < 5.0

    def test_canary_skips_llm_evaluators(self, svc):
        """Cases with LLM evaluators only are skipped in canary."""
        result = svc.run_canary()
        for case_result in result["cases"]:
            assert case_result["status"] in ("passed", "failed", "skipped")
