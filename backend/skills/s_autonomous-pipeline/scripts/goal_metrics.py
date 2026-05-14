"""Goal loop feedback metrics — track cycle efficiency and goal completion.

Provides historical velocity data for auto-tuning cycle scope in future goals.
Stores metrics inside run.json under the 'goal_metrics' field.

Usage:
    from scripts.goal_metrics import GoalMetrics

    gm = GoalMetrics(run_dir=Path("Projects/SwarmAI/.artifacts/runs/run_abc123"))
    gm.track_goal_start(dod_criteria=[...])
    gm.track_cycle(cycle_num=1, progress_delta=0.33, ...)
    gm.track_goal_complete(status="success", ...)
    velocity = gm.get_velocity()
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Valid status values for track_goal_complete
VALID_STATUSES = frozenset({"success", "checkpoint", "stop", "revert_limit", "budget"})


class GoalMetrics:
    """Track cycle efficiency and goal completion for pipeline goal loops.

    Reads and writes to run.json in the specified run directory.
    All state is stored under the 'goal_metrics' key in run.json.
    One instance per goal loop execution — created at Pre-Cycle Setup,
    reused through all cycles and at exit.
    """

    def __init__(self, run_dir: Path | None = None):
        """Initialize GoalMetrics with a run directory.

        Args:
            run_dir: Path to the pipeline run directory containing run.json.
                     If None, operates in read-only mode with empty data.
        """
        self._run_dir = Path(run_dir) if run_dir else None

    def _load_run(self) -> dict:
        """Load run.json from the run directory.

        Returns empty dict if file is missing or contains invalid JSON.
        """
        if not self._run_dir:
            return {}
        run_file = self._run_dir / "run.json"
        if not run_file.exists():
            return {}
        try:
            return json.loads(run_file.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return {}

    def _save_run(self, data: dict) -> None:
        """Save updated run.json atomically (write to temp + rename).

        Atomic write prevents corruption from crashes mid-write.
        """
        if not self._run_dir:
            return
        run_file = self._run_dir / "run.json"
        # Atomic write: temp file in same dir + rename
        fd, tmp_path = tempfile.mkstemp(dir=self._run_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, run_file)
        except OSError:
            # Best-effort cleanup on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _ensure_goal_metrics(self, data: dict) -> dict:
        """Ensure goal_metrics field exists with all required sub-fields."""
        defaults = {
            "started_at": None,
            "dod_criteria_count": 0,
            "cycles": [],
            "completed_at": None,
            "status": None,
        }
        if "goal_metrics" not in data:
            data["goal_metrics"] = defaults
        else:
            # Merge defaults for any missing keys (defensive against partial data)
            for k, v in defaults.items():
                data["goal_metrics"].setdefault(k, v)
        return data["goal_metrics"]

    def track_goal_start(self, dod_criteria: list[dict]) -> None:
        """Record goal initiation — criteria count and start time.

        Idempotent: if already started, does not overwrite existing data.

        Args:
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

    def track_cycle(self, cycle_num: int, *,
                    progress_delta: float,
                    files_changed: int,
                    tests_added: int,
                    regression: bool = False) -> None:
        """Record per-cycle metrics for velocity tracking.

        Args:
            cycle_num: 1-indexed cycle number.
            progress_delta: Fraction of DoD criteria newly met (0.0-1.0).
            files_changed: Number of source files modified this cycle.
            tests_added: Number of new tests written this cycle.
            regression: Whether a test regression occurred this cycle.

        Raises:
            ValueError: If progress_delta not in [0.0, 1.0] or counts negative.
        """
        if not (0.0 <= progress_delta <= 1.0):
            raise ValueError(
                f"progress_delta must be 0.0-1.0, got {progress_delta}"
            )
        if files_changed < 0 or tests_added < 0:
            raise ValueError("files_changed and tests_added must be non-negative")

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

    def track_goal_complete(self, *,
                           status: str,
                           total_cycles: int,
                           dod_met: int,
                           dod_total: int) -> None:
        """Record goal outcome and update aggregate stats.

        Args:
            status: One of 'success', 'checkpoint', 'stop', 'revert_limit', 'budget'.
            total_cycles: Total cycles executed.
            dod_met: Number of DoD criteria met.
            dod_total: Total number of DoD criteria.

        Raises:
            ValueError: If status is not a recognized value.
        """
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}"
            )

        data = self._load_run()
        gm = self._ensure_goal_metrics(data)

        gm["completed_at"] = datetime.now(timezone.utc).isoformat()
        gm["status"] = status
        gm["total_cycles"] = total_cycles
        gm["dod_met"] = dod_met
        gm["dod_total"] = dod_total
        self._save_run(data)

    def get_velocity(self) -> dict:
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

        # Defensive: .get() for each cycle field to handle partial data
        total_delta = sum(c.get("progress_delta", 0.0) for c in cycles)
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

    def get_recommended_cycle_scope(self) -> str:
        """Auto-tune: recommend cycle scope based on historical velocity.

        High velocity (>15% delta/cycle) → larger scope per cycle.
        Low velocity (<5% delta/cycle) → smaller, more focused scope.

        Returns:
            Human-readable scope recommendation string.
        """
        velocity = self.get_velocity()
        avg_delta = velocity["avg_delta_per_cycle"]

        if avg_delta == 0.0:
            return "one function or one test file per cycle (no history, using default)"
        elif avg_delta >= 0.15:
            return "multiple files or one module per cycle (high velocity — expand scope)"
        elif avg_delta >= 0.05:
            return "one function or one test file per cycle (moderate velocity — maintain scope)"
        else:
            return "single function or single test case per cycle (low velocity — narrow focus)"

    def summary(self) -> dict:
        """Return metrics summary for the current run.

        Returns:
            Dict with stable schema: dod_criteria_count, cycles_completed,
            current_velocity, status, total_cycles, dod_met.
        """
        data = self._load_run()
        gm = data.get("goal_metrics", {})

        cycles = gm.get("cycles", [])
        total_delta = sum(c.get("progress_delta", 0.0) for c in cycles)
        avg_delta = total_delta / len(cycles) if cycles else 0.0

        return {
            "dod_criteria_count": gm.get("dod_criteria_count", 0),
            "cycles_completed": len(cycles),
            "current_velocity": round(avg_delta, 4),
            "status": gm.get("status"),
            "total_cycles": gm.get("total_cycles"),
            "dod_met": gm.get("dod_met"),
        }
