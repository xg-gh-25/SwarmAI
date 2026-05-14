"""Tests for GoalMetrics — pipeline goal loop feedback tracking.

Covers: track_goal_start, track_cycle, track_goal_complete,
get_velocity, get_recommended_cycle_scope, summary, aggregate_velocity.
Plus: error handling, validation, corrupt data resilience, idempotency.
"""
import json
from pathlib import Path

import pytest

import sys

# P6: Add skill scripts to path for test imports.
# This matches how the agent executes: CWD = skill dir, `from scripts.X`.
sys.path.insert(0, str(Path(__file__).parent.parent / "skills" / "s_autonomous-pipeline"))

from scripts.goal_metrics import GoalMetrics, VALID_STATUSES, _compute_cycle_stats


@pytest.fixture
def tmp_run_dir(tmp_path):
    """Create a temp directory with a realistic run.json."""
    run_json = tmp_path / "run.json"
    run_json.write_text(json.dumps({
        "id": "run_test123",
        "project": "TestProject",
        "requirement": "test goal",
        "profile": "goal",
        "status": "running",
        "stages": [],
        "taste_decisions": [],
    }))
    return tmp_path


@pytest.fixture
def metrics(tmp_run_dir):
    """GoalMetrics instance backed by temp run dir."""
    return GoalMetrics(run_dir=tmp_run_dir)


class TestTrackGoalStart:
    def test_records_start(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[
            {"type": "command", "check": "echo ok", "desc": "test1"},
            {"type": "rubric", "check": "is good", "desc": "test2"},
        ])
        data = json.loads((tmp_run_dir / "run.json").read_text())
        gm = data["goal_metrics"]
        assert gm["dod_criteria_count"] == 2
        assert gm["started_at"] is not None
        assert gm["cycles"] == []
        assert gm["status"] is None
        # P1: verify other run.json fields preserved
        assert data["id"] == "run_test123"
        assert data["project"] == "TestProject"
        assert data["stages"] == []

    def test_idempotent_if_already_started(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[{"type": "command", "check": "x", "desc": "y"}])
        metrics.track_goal_start(dod_criteria=[{"type": "command", "check": "x", "desc": "y"}])
        data = json.loads((tmp_run_dir / "run.json").read_text())
        assert data["goal_metrics"]["dod_criteria_count"] == 1


class TestTrackCycle:
    def test_appends_cycle(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[])
        metrics.track_cycle(cycle_num=1, progress_delta=0.33,
                           files_changed=2, tests_added=3, regression=False)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        cycles = data["goal_metrics"]["cycles"]
        assert len(cycles) == 1
        assert cycles[0]["num"] == 1
        assert cycles[0]["progress_delta"] == 0.33
        assert cycles[0]["files_changed"] == 2
        assert cycles[0]["tests_added"] == 3
        assert cycles[0]["regression"] is False

    def test_multiple_cycles(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[])
        metrics.track_cycle(cycle_num=1, progress_delta=0.2,
                           files_changed=1, tests_added=1)
        metrics.track_cycle(cycle_num=2, progress_delta=0.3,
                           files_changed=2, tests_added=2)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        assert len(data["goal_metrics"]["cycles"]) == 2

    def test_regression_flag(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[])
        metrics.track_cycle(cycle_num=1, progress_delta=0.0,
                           files_changed=1, tests_added=0, regression=True)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        assert data["goal_metrics"]["cycles"][0]["regression"] is True

    def test_has_docstring(self, metrics):
        assert metrics.track_cycle.__doc__ is not None

    def test_validates_progress_delta_range(self, metrics):
        metrics.track_goal_start(dod_criteria=[])
        with pytest.raises(ValueError, match="progress_delta must be 0.0-1.0"):
            metrics.track_cycle(cycle_num=1, progress_delta=1.5,
                               files_changed=1, tests_added=0)
        with pytest.raises(ValueError, match="progress_delta must be 0.0-1.0"):
            metrics.track_cycle(cycle_num=1, progress_delta=-0.1,
                               files_changed=1, tests_added=0)

    def test_validates_non_negative_counts(self, metrics):
        metrics.track_goal_start(dod_criteria=[])
        with pytest.raises(ValueError, match="non-negative"):
            metrics.track_cycle(cycle_num=1, progress_delta=0.5,
                               files_changed=-1, tests_added=0)

    def test_without_prior_start(self, metrics, tmp_run_dir):
        """track_cycle without track_goal_start should still work (defensive)."""
        metrics.track_cycle(cycle_num=1, progress_delta=0.5,
                           files_changed=1, tests_added=1)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        assert len(data["goal_metrics"]["cycles"]) == 1

    def test_duplicate_cycle_num_idempotent(self, metrics, tmp_run_dir):
        """P7: duplicate cycle_num should be silently skipped."""
        metrics.track_goal_start(dod_criteria=[])
        metrics.track_cycle(cycle_num=1, progress_delta=0.5,
                           files_changed=2, tests_added=1)
        metrics.track_cycle(cycle_num=1, progress_delta=0.8,
                           files_changed=5, tests_added=3)  # duplicate
        data = json.loads((tmp_run_dir / "run.json").read_text())
        cycles = data["goal_metrics"]["cycles"]
        assert len(cycles) == 1
        assert cycles[0]["progress_delta"] == 0.5  # first write wins


class TestTrackGoalComplete:
    def test_records_completion(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[
            {"type": "command", "check": "x", "desc": "y"},
            {"type": "command", "check": "z", "desc": "w"},
        ])
        metrics.track_cycle(cycle_num=1, progress_delta=0.5,
                           files_changed=2, tests_added=2)
        metrics.track_goal_complete(status="success",
                                   total_cycles=1, dod_met=2, dod_total=2)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        gm = data["goal_metrics"]
        assert gm["status"] == "success"
        assert gm["completed_at"] is not None
        assert gm["total_cycles"] == 1
        assert gm["dod_met"] == 2
        assert gm["dod_total"] == 2

    def test_checkpoint_status(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[])
        metrics.track_goal_complete(status="checkpoint",
                                   total_cycles=8, dod_met=1, dod_total=3)
        data = json.loads((tmp_run_dir / "run.json").read_text())
        assert data["goal_metrics"]["status"] == "checkpoint"

    def test_validates_status_enum(self, metrics):
        metrics.track_goal_start(dod_criteria=[])
        with pytest.raises(ValueError, match="Invalid status"):
            metrics.track_goal_complete(status="invalid_status",
                                       total_cycles=1, dod_met=1, dod_total=1)


class TestGetVelocity:
    def test_empty_history(self, metrics):
        v = metrics.get_velocity()
        assert v["avg_cycles_per_goal"] == 0
        assert v["avg_delta_per_cycle"] == 0.0
        assert v["completion_rate"] == 0.0
        assert v["regression_rate"] == 0.0

    def test_with_data(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[
            {"type": "command", "check": "x", "desc": "a"},
        ])
        metrics.track_cycle(cycle_num=1, progress_delta=0.5,
                           files_changed=1, tests_added=1)
        metrics.track_cycle(cycle_num=2, progress_delta=0.5,
                           files_changed=1, tests_added=1, regression=True)
        metrics.track_goal_complete(status="success",
                                   total_cycles=2, dod_met=1, dod_total=1)
        v = metrics.get_velocity()
        assert v["avg_cycles_per_goal"] == 2
        assert v["avg_delta_per_cycle"] == 0.5
        assert v["completion_rate"] == 1.0
        assert v["regression_rate"] == 0.5


class TestGetRecommendedCycleScope:
    def test_no_history_returns_default(self, metrics):
        scope = metrics.get_recommended_cycle_scope()
        assert "one" in scope.lower() or "default" in scope.lower()

    def test_high_velocity_larger_scope(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[])
        for i in range(5):
            metrics.track_cycle(cycle_num=i + 1,
                               progress_delta=0.2, files_changed=3, tests_added=2)
        metrics.track_goal_complete(status="success",
                                   total_cycles=5, dod_met=1, dod_total=1)
        scope = metrics.get_recommended_cycle_scope()
        assert "module" in scope.lower() or "multiple" in scope.lower()

    def test_low_velocity_smaller_scope(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[])
        for i in range(5):
            metrics.track_cycle(cycle_num=i + 1,
                               progress_delta=0.03, files_changed=1, tests_added=1)
        metrics.track_goal_complete(status="checkpoint",
                                   total_cycles=5, dod_met=0, dod_total=1)
        scope = metrics.get_recommended_cycle_scope()
        assert "single" in scope.lower() or "one" in scope.lower()


class TestSummary:
    def test_summary_active_goal(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[
            {"type": "command", "check": "x", "desc": "a"},
        ])
        metrics.track_cycle(cycle_num=1, progress_delta=0.5,
                           files_changed=2, tests_added=2)
        s = metrics.summary()
        assert s["dod_criteria_count"] == 1
        assert s["cycles_completed"] == 1
        assert s["current_velocity"] == 0.5
        assert s["status"] is None
        assert s["total_cycles"] is None
        assert s["dod_met"] is None

    def test_summary_completed_goal(self, metrics, tmp_run_dir):
        metrics.track_goal_start(dod_criteria=[])
        metrics.track_goal_complete(status="success",
                                   total_cycles=3, dod_met=2, dod_total=2)
        s = metrics.summary()
        assert s["status"] == "success"
        assert s["total_cycles"] == 3
        assert s["dod_met"] == 2


class TestAggregateVelocity:
    """P2: Cross-run aggregation for meaningful auto-tuning."""

    def _make_run(self, runs_dir: Path, run_id: str, cycles: list, status: str):
        """Helper: create a completed goal run.json."""
        run_dir = runs_dir / run_id
        run_dir.mkdir(parents=True)
        data = {
            "id": run_id,
            "goal_metrics": {
                "started_at": "2026-01-01T00:00:00+00:00",
                "dod_criteria_count": 3,
                "cycles": cycles,
                "completed_at": "2026-01-02T00:00:00+00:00",
                "status": status,
                "total_cycles": len(cycles),
                "dod_met": 3 if status == "success" else 1,
                "dod_total": 3,
            },
        }
        (run_dir / "run.json").write_text(json.dumps(data))

    def test_no_runs(self, tmp_path):
        agg = GoalMetrics.aggregate_velocity(tmp_path)
        assert agg["runs_analyzed"] == 0
        assert agg["avg_delta_per_cycle"] == 0.0

    def test_single_run(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        self._make_run(runs_dir, "run_aaa", [
            {"num": 1, "progress_delta": 0.33, "regression": False},
            {"num": 2, "progress_delta": 0.33, "regression": False},
            {"num": 3, "progress_delta": 0.34, "regression": True},
        ], "success")

        agg = GoalMetrics.aggregate_velocity(runs_dir)
        assert agg["runs_analyzed"] == 1
        assert agg["completion_rate"] == 1.0
        assert 0.33 <= agg["avg_delta_per_cycle"] <= 0.34
        assert agg["regression_rate"] > 0

    def test_multiple_runs(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        # Run 1: success, 3 cycles, high velocity
        self._make_run(runs_dir, "run_aaa", [
            {"num": 1, "progress_delta": 0.5, "regression": False},
            {"num": 2, "progress_delta": 0.5, "regression": False},
        ], "success")
        # Run 2: checkpoint, 5 cycles, low velocity
        self._make_run(runs_dir, "run_bbb", [
            {"num": 1, "progress_delta": 0.05, "regression": False},
            {"num": 2, "progress_delta": 0.05, "regression": True},
            {"num": 3, "progress_delta": 0.05, "regression": False},
            {"num": 4, "progress_delta": 0.05, "regression": False},
            {"num": 5, "progress_delta": 0.05, "regression": False},
        ], "checkpoint")

        agg = GoalMetrics.aggregate_velocity(runs_dir)
        assert agg["runs_analyzed"] == 2
        assert agg["completion_rate"] == 0.5  # 1/2 succeeded
        # 7 total cycles: (0.5+0.5+0.05*5) / 7 = 1.25/7 ≈ 0.1786
        assert 0.15 < agg["avg_delta_per_cycle"] < 0.20
        # 1 regression out of 7 cycles
        assert 0.1 < agg["regression_rate"] < 0.2

    def test_skips_non_goal_runs(self, tmp_path):
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_normal"
        run_dir.mkdir(parents=True)
        # Normal pipeline run (no goal_metrics.started_at)
        (run_dir / "run.json").write_text(json.dumps({
            "id": "run_normal", "profile": "full", "stages": [],
        }))

        agg = GoalMetrics.aggregate_velocity(runs_dir)
        assert agg["runs_analyzed"] == 0

    def test_skips_corrupt_json(self, tmp_path):
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "run_bad"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text("{corrupt")

        agg = GoalMetrics.aggregate_velocity(runs_dir)
        assert agg["runs_analyzed"] == 0


class TestResilience:
    """Tests for corrupt data, missing files, partial schemas."""

    def test_corrupt_json_no_data_loss(self, tmp_path):
        """P1: Corrupt run.json should not crash AND should not overwrite."""
        run_json = tmp_path / "run.json"
        original = '{"id":"run_x","stages":[1,2,3],"important":"data"}'
        run_json.write_text(original)
        # Corrupt it
        run_json.write_text("{incomplete json")

        gm = GoalMetrics(run_dir=tmp_path)
        # Attempt to write should be refused (load was corrupt)
        gm.track_goal_start(dod_criteria=[{"type": "command", "check": "x", "desc": "y"}])
        # File should NOT have been overwritten
        content = run_json.read_text()
        assert content == "{incomplete json"  # unchanged — write was blocked

    def test_corrupt_json_velocity_returns_empty(self, tmp_path):
        """Corrupt file → velocity returns zeros (not crash)."""
        (tmp_path / "run.json").write_text("{bad")
        gm = GoalMetrics(run_dir=tmp_path)
        v = gm.get_velocity()
        assert v["avg_cycles_per_goal"] == 0

    def test_empty_json(self, tmp_path):
        """Empty run.json (valid JSON {}) should work."""
        run_json = tmp_path / "run.json"
        run_json.write_text("{}")
        gm = GoalMetrics(run_dir=tmp_path)
        gm.track_goal_start(dod_criteria=[{"type": "command", "check": "x", "desc": "y"}])
        data = json.loads(run_json.read_text())
        assert data["goal_metrics"]["dod_criteria_count"] == 1

    def test_missing_cycles_key(self, tmp_path):
        """goal_metrics exists but missing 'cycles' key — should auto-fill."""
        run_json = tmp_path / "run.json"
        run_json.write_text(json.dumps({
            "goal_metrics": {"started_at": "2026-01-01T00:00:00+00:00", "dod_criteria_count": 1}
        }))
        gm = GoalMetrics(run_dir=tmp_path)
        gm.track_cycle(cycle_num=1, progress_delta=0.5,
                       files_changed=1, tests_added=1)
        data = json.loads(run_json.read_text())
        assert len(data["goal_metrics"]["cycles"]) == 1

    def test_no_run_dir(self):
        """GoalMetrics with None run_dir should not crash."""
        gm = GoalMetrics(run_dir=None)
        v = gm.get_velocity()
        assert v["avg_delta_per_cycle"] == 0.0
        scope = gm.get_recommended_cycle_scope()
        assert "default" in scope

    def test_missing_run_json_creates_file(self, tmp_path):
        """Non-existent run.json → track_goal_start creates it."""
        gm = GoalMetrics(run_dir=tmp_path)
        gm.track_goal_start(dod_criteria=[])
        assert (tmp_path / "run.json").exists()

    def test_stale_tmp_cleanup(self, tmp_path):
        """P3: stale .tmp files from prior crashes are cleaned on write."""
        run_json = tmp_path / "run.json"
        run_json.write_text(json.dumps({"id": "x"}))
        # Simulate stale temp
        (tmp_path / "stale1.tmp").write_text("junk")
        (tmp_path / "stale2.tmp").write_text("junk")

        gm = GoalMetrics(run_dir=tmp_path)
        gm.track_goal_start(dod_criteria=[])

        # Stale temps should be cleaned
        assert not (tmp_path / "stale1.tmp").exists()
        assert not (tmp_path / "stale2.tmp").exists()


class TestComputeCycleStats:
    """Unit tests for the shared helper function (P8)."""

    def test_empty(self):
        s = _compute_cycle_stats([])
        assert s["avg_delta"] == 0.0
        assert s["regression_rate"] == 0.0

    def test_normal(self):
        cycles = [
            {"progress_delta": 0.3, "regression": False},
            {"progress_delta": 0.5, "regression": True},
        ]
        s = _compute_cycle_stats(cycles)
        assert s["avg_delta"] == 0.4
        assert s["regression_count"] == 1
        assert s["regression_rate"] == 0.5

    def test_missing_fields_defensive(self):
        """Handles entries with missing keys."""
        cycles = [
            {},  # no progress_delta, no regression
            {"progress_delta": 0.6},
        ]
        s = _compute_cycle_stats(cycles)
        assert s["avg_delta"] == 0.3  # (0.0 + 0.6) / 2
