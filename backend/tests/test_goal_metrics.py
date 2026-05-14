"""Tests for GoalMetrics — pipeline goal loop feedback tracking.

Covers: track_goal_start, track_cycle, track_goal_complete,
get_velocity, get_recommended_cycle_scope, summary.
"""
import json
import tempfile
from pathlib import Path

import pytest

# Import path: from backend/ CWD, scripts/ is a package under the skill dir.
# But the DoD check expects `from scripts.goal_metrics import GoalMetrics`
# running from `backend/skills/s_autonomous-pipeline/` context.
# For pytest, we import via the full path.
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_autonomous-pipeline"))

from scripts.goal_metrics import GoalMetrics


@pytest.fixture
def tmp_run_dir(tmp_path):
    """Create a temp directory with an empty run.json."""
    run_json = tmp_path / "run.json"
    run_json.write_text(json.dumps({
        "id": "run_test123",
        "project": "TestProject",
        "requirement": "test goal",
        "profile": "goal",
        "status": "running",
        "stages": [],
    }))
    return tmp_path


@pytest.fixture
def metrics(tmp_run_dir):
    """GoalMetrics instance backed by temp run dir."""
    return GoalMetrics(run_dir=tmp_run_dir)


class TestTrackGoalStart:
    def test_records_start(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[
            {"type": "command", "check": "echo ok", "desc": "test1"},
            {"type": "rubric", "check": "is good", "desc": "test2"},
        ])
        data = json.loads((tmp_run_dir / "run.json").read_text())
        gm = data["goal_metrics"]
        assert gm["dod_criteria_count"] == 2
        assert gm["started_at"] is not None
        assert gm["cycles"] == []
        assert gm["status"] is None

    def test_idempotent_if_already_started(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[{"type": "command", "check": "x", "desc": "y"}])
        metrics.track_goal_start("run_test123", dod_criteria=[{"type": "command", "check": "x", "desc": "y"}])
        data = json.loads((tmp_run_dir / "run.json").read_text())
        assert data["goal_metrics"]["dod_criteria_count"] == 1


class TestTrackCycle:
    def test_appends_cycle(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[])
        metrics.track_cycle("run_test123", cycle_num=1,
                           progress_delta=0.33, files_changed=2,
                           tests_added=3, regression=False)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        cycles = data["goal_metrics"]["cycles"]
        assert len(cycles) == 1
        assert cycles[0]["num"] == 1
        assert cycles[0]["progress_delta"] == 0.33
        assert cycles[0]["files_changed"] == 2
        assert cycles[0]["tests_added"] == 3
        assert cycles[0]["regression"] is False

    def test_multiple_cycles(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[])
        metrics.track_cycle("run_test123", cycle_num=1, progress_delta=0.2,
                           files_changed=1, tests_added=1)
        metrics.track_cycle("run_test123", cycle_num=2, progress_delta=0.3,
                           files_changed=2, tests_added=2)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        assert len(data["goal_metrics"]["cycles"]) == 2

    def test_regression_flag(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[])
        metrics.track_cycle("run_test123", cycle_num=1, progress_delta=0.0,
                           files_changed=1, tests_added=0, regression=True)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        assert data["goal_metrics"]["cycles"][0]["regression"] is True

    def test_has_docstring(self, metrics):
        assert metrics.track_cycle.__doc__ is not None


class TestTrackGoalComplete:
    def test_records_completion(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[
            {"type": "command", "check": "x", "desc": "y"},
            {"type": "command", "check": "z", "desc": "w"},
        ])
        metrics.track_cycle("run_test123", cycle_num=1, progress_delta=0.5,
                           files_changed=2, tests_added=2)
        metrics.track_goal_complete("run_test123", status="success",
                                   total_cycles=1, dod_met=2, dod_total=2)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        gm = data["goal_metrics"]
        assert gm["status"] == "success"
        assert gm["completed_at"] is not None
        assert gm["total_cycles"] == 1
        assert gm["dod_met"] == 2
        assert gm["dod_total"] == 2

    def test_checkpoint_status(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[])
        metrics.track_goal_complete("run_test123", status="checkpoint",
                                   total_cycles=8, dod_met=1, dod_total=3)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        assert data["goal_metrics"]["status"] == "checkpoint"


class TestGetVelocity:
    def test_empty_history(self, metrics):
        v = metrics.get_velocity()
        assert v["avg_cycles_per_goal"] == 0
        assert v["avg_delta_per_cycle"] == 0.0
        assert v["completion_rate"] == 0.0
        assert v["regression_rate"] == 0.0

    def test_with_data(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[
            {"type": "command", "check": "x", "desc": "a"},
        ])
        metrics.track_cycle("run_test123", cycle_num=1, progress_delta=0.5,
                           files_changed=1, tests_added=1)
        metrics.track_cycle("run_test123", cycle_num=2, progress_delta=0.5,
                           files_changed=1, tests_added=1, regression=True)
        metrics.track_goal_complete("run_test123", status="success",
                                   total_cycles=2, dod_met=1, dod_total=1)
        v = metrics.get_velocity()
        assert v["avg_cycles_per_goal"] == 2
        assert v["avg_delta_per_cycle"] == 0.5
        assert v["completion_rate"] == 1.0
        assert v["regression_rate"] == 0.5  # 1 regression out of 2 cycles


class TestGetRecommendedCycleScope:
    def test_no_history_returns_default(self, metrics):
        scope = metrics.get_recommended_cycle_scope()
        assert "one" in scope.lower()  # default mentions "one" something

    def test_high_velocity_larger_scope(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[])
        # Simulate high velocity: 20% per cycle
        for i in range(5):
            metrics.track_cycle("run_test123", cycle_num=i + 1,
                               progress_delta=0.2, files_changed=3, tests_added=2)
        metrics.track_goal_complete("run_test123", status="success",
                                   total_cycles=5, dod_met=1, dod_total=1)
        scope = metrics.get_recommended_cycle_scope()
        # High velocity → larger scope
        assert "module" in scope.lower() or "multiple" in scope.lower()

    def test_low_velocity_smaller_scope(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[])
        # Simulate low velocity: 3% per cycle
        for i in range(5):
            metrics.track_cycle("run_test123", cycle_num=i + 1,
                               progress_delta=0.03, files_changed=1, tests_added=1)
        metrics.track_goal_complete("run_test123", status="checkpoint",
                                   total_cycles=5, dod_met=0, dod_total=1)
        scope = metrics.get_recommended_cycle_scope()
        # Low velocity → smaller scope
        assert "single" in scope.lower() or "one" in scope.lower()


class TestSummary:
    def test_summary_active_goal(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[
            {"type": "command", "check": "x", "desc": "a"},
        ])
        metrics.track_cycle("run_test123", cycle_num=1, progress_delta=0.5,
                           files_changed=2, tests_added=2)
        s = metrics.summary("run_test123")
        assert s["dod_criteria_count"] == 1
        assert s["cycles_completed"] == 1
        assert s["current_velocity"] == 0.5
        assert s["status"] is None  # still in progress

    def test_summary_completed_goal(self, metrics, tmp_run_dir):
        metrics.track_goal_start("run_test123", dod_criteria=[])
        metrics.track_goal_complete("run_test123", status="success",
                                   total_cycles=3, dod_met=2, dod_total=2)
        s = metrics.summary("run_test123")
        assert s["status"] == "success"
        assert s["total_cycles"] == 3
