"""Goal loop feedback metrics — track cycle efficiency and goal completion.

Provides historical velocity data for auto-tuning cycle scope in future goals.
Stores metrics inside run.json under the 'goal_metrics' field.

Usage:
    from scripts.goal_metrics import GoalMetrics

    gm = GoalMetrics(run_dir=Path("Projects/SwarmAI/.artifacts/runs/run_abc123"))
    gm.track_goal_start("run_abc123", dod_criteria=[...])
    gm.track_cycle("run_abc123", cycle_num=1, progress_delta=0.33, ...)
    gm.track_goal_complete("run_abc123", status="success", ...)
    velocity = gm.get_velocity()
"""
import json
from datetime import datetime, timezone
from pathlib import Path


class GoalMetrics:
    """Track cycle efficiency and goal completion for pipeline goal loops.

    Reads and writes to run.json in the specified run directory.
    All state is stored under the 'goal_metrics' key in run.json.
    """

    def __init__(self, run_dir: Path | None = None):
        """Initialize GoalMetrics with a run directory.

        Args:
            run_dir: Path to the pipeline run directory containing run.json.
                     If None, operates in read-only mode with empty data.
        """
        self._run_dir = Path(run_dir) if run_dir else None

    def _load_run(self) -> dict:
        """Load run.json from the run directory."""
        if not self._run_dir:
            return {}
        run_file = self._run_dir / "run.json"
        if not run_file.exists():
            return {}
        return json.loads(run_file.read_text())

    def _save_run(self, data: dict) -> None:
        """Save updated run.json back to disk."""
        if not self._run_dir:
            return
        run_file = self._run_dir / "run.json"
        run_file.write_text(json.dumps(data, indent=2))

    def _ensure_goal_metrics(self, data: dict) -> dict:
        """Ensure goal_metrics field exists in run data."""
        if "goal_metrics" not in data:
            data["goal_metrics"] = {
                "started_at": None,
                "dod_criteria_count": 0,
                "cycles": [],
                "completed_at": None,
                "status": None,
            }
        return data["goal_metrics"]

    def track_goal_start(self, run_id: str, dod_criteria: list[dict]) -> None:
        """Record goal initiation — criteria count and start time.

        Idempotent: if already started, does not overwrite existing data.

        Args:
            run_id: Pipeline run identifier.
            dod_criteria: List of DoD criterion dicts with type/check/desc.
        """
        data = self._load_run()
        gm = self._ensure_goal_metrics(data)

        # Idempotent — don't overwrite if already started
        if gm["started_at"] is not None:
            return

        gm["started_at"] = datetime.now(timezone.utc).isoformat()
        gm["dod_criteria_count"] = len(dod_criteria)
        gm["cycles"] = []
        gm["status"] = None
        self._save_run(data)

    def track_cycle(self, run_id: str, cycle_num: int, *,
                    progress_delta: float,
                    files_changed: int,
                    tests_added: int,
                    regression: bool = False) -> None:
        """Record per-cycle metrics for velocity tracking.

        Args:
            run_id: Pipeline run identifier.
            cycle_num: 1-indexed cycle number.
            progress_delta: Fraction of DoD criteria newly met (0.0-1.0).
            files_changed: Number of source files modified this cycle.
            tests_added: Number of new tests written this cycle.
            regression: Whether a test regression occurred this cycle.
        """
        data = self._load_run()
        gm = self._ensure_goal_metrics(data)

        gm["cycles"].append({
            "num": cycle_num,
            "progress_delta": progress_delta,
            "files_changed": files_changed,
            "tests_added": tests_added,
            "regression": regression,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self._save_run(data)

    def track_goal_complete(self, run_id: str, *,
                           status: str,
                           total_cycles: int,
                           dod_met: int,
                           dod_total: int) -> None:
        """Record goal outcome and update aggregate stats.

        Args:
            run_id: Pipeline run identifier.
            status: One of 'success', 'checkpoint', 'stop', 'revert_limit', 'budget'.
            total_cycles: Total cycles executed.
            dod_met: Number of DoD criteria met.
            dod_total: Total number of DoD criteria.
        """
        data = self._load_run()
        gm = self._ensure_goal_metrics(data)

        gm["completed_at"] = datetime.now(timezone.utc).isoformat()
        gm["status"] = status
        gm["total_cycles"] = total_cycles
        gm["dod_met"] = dod_met
        gm["dod_total"] = dod_total
        self._save_run(data)

    def get_velocity(self, project: str | None = None) -> dict:
        """Return historical velocity stats from the current run.

        Returns:
            Dict with keys: avg_cycles_per_goal, avg_delta_per_cycle,
            completion_rate, regression_rate.
        """
        data = self._load_run()
        gm = data.get("goal_metrics", {})

        cycles = gm.get("cycles", [])

        if not cycles:
            return {
                "avg_cycles_per_goal": 0,
                "avg_delta_per_cycle": 0.0,
                "completion_rate": 0.0,
                "regression_rate": 0.0,
            }

        # Compute from current run's data
        total_delta = sum(c["progress_delta"] for c in cycles)
        avg_delta = total_delta / len(cycles)
        regression_count = sum(1 for c in cycles if c.get("regression"))
        regression_rate = regression_count / len(cycles)

        # Completion rate: 1.0 if completed successfully, 0.0 otherwise
        status = gm.get("status")
        completion_rate = 1.0 if status == "success" else 0.0

        # Cycles per goal: total_cycles if completed, len(cycles) if in-progress
        total_cycles = gm.get("total_cycles", len(cycles))

        return {
            "avg_cycles_per_goal": total_cycles,
            "avg_delta_per_cycle": round(avg_delta, 4),
            "completion_rate": completion_rate,
            "regression_rate": round(regression_rate, 4),
        }

    def get_recommended_cycle_scope(self, project: str | None = None) -> str:
        """Auto-tune: recommend cycle scope based on historical velocity.

        High velocity (>15% delta/cycle) → larger scope per cycle.
        Low velocity (<5% delta/cycle) → smaller, more focused scope.

        Returns:
            Human-readable scope recommendation string.
        """
        velocity = self.get_velocity(project)
        avg_delta = velocity["avg_delta_per_cycle"]

        if avg_delta == 0.0:
            return "one function or one test file per cycle (no history, using default)"
        elif avg_delta >= 0.15:
            return "multiple files or one module per cycle (high velocity — expand scope)"
        elif avg_delta >= 0.05:
            return "one function or one test file per cycle (moderate velocity — maintain scope)"
        else:
            return "single function or single test case per cycle (low velocity — narrow focus)"

    def summary(self, run_id: str) -> dict:
        """Return metrics summary for a specific run.

        Args:
            run_id: Pipeline run identifier.

        Returns:
            Dict with dod_criteria_count, cycles_completed, current_velocity,
            status, total_cycles (if completed).
        """
        data = self._load_run()
        gm = data.get("goal_metrics", {})

        cycles = gm.get("cycles", [])
        avg_delta = (
            sum(c["progress_delta"] for c in cycles) / len(cycles)
            if cycles else 0.0
        )

        result = {
            "dod_criteria_count": gm.get("dod_criteria_count", 0),
            "cycles_completed": len(cycles),
            "current_velocity": round(avg_delta, 4),
            "status": gm.get("status"),
        }

        if gm.get("total_cycles") is not None:
            result["total_cycles"] = gm["total_cycles"]
        if gm.get("dod_met") is not None:
            result["dod_met"] = gm["dod_met"]

        return result
