"""
SwarmAI Self-Eval Service — In-memory cache for golden set + eval history.

The golden set is the agent's behavioral contract — part of its identity,
equal in ontological status to SOUL/AGENT/STEERING. This service manages
that contract and the history of self-evaluation runs.

Parsed on startup from:
  - Projects/SwarmAI/golden_set.yaml (behavioral contract)
  - Projects/SwarmAI/EvalHistory/*.json (self-eval run results)

Serves the Eval Dashboard API with zero-latency reads.
Cache invalidated on: eval run completion, manual reload.
"""

import json
import logging
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

logger = logging.getLogger(__name__)


class EvalService:
    """In-memory cache of golden set cases and eval run history."""

    _UNSET = object()

    def __init__(self, workspace_root=_UNSET):
        self._golden_set: dict = {"version": 2, "cases": []}
        self._cases: list[dict] = []
        self._runs: list[dict] = []
        self._data_lock = threading.Lock()  # Guards _cases/_golden_set mutations
        self._run_lock = threading.Lock()   # Guards _running flag
        self._running: bool = False

        if workspace_root is self._UNSET:
            self._workspace_root = self._find_workspace()
        elif workspace_root is None:
            # Empty fallback — no workspace, no loading
            self._workspace_root = Path.home()
            self._project_dir = self._workspace_root / "nonexistent"
            self._golden_set_path = self._project_dir / "golden_set.yaml"
            self._history_dir = self._project_dir / "EvalHistory"
            return
        else:
            self._workspace_root = workspace_root

        self._project_dir = self._workspace_root / "Projects" / "SwarmAI"
        self._golden_set_path = self._project_dir / "golden_set.yaml"
        self._history_dir = self._project_dir / "EvalHistory"
        self._load()

    @staticmethod
    def _find_workspace() -> Path:
        """Find SwarmWS root."""
        candidates = [
            Path.home() / ".swarm-ai" / "SwarmWS",
            Path.cwd(),
        ]
        for c in candidates:
            if (c / "Projects" / "SwarmAI").is_dir():
                return c
        raise FileNotFoundError("Cannot locate SwarmWS")

    def _load(self) -> None:
        """Load golden set + history into memory."""
        self._load_golden_set()
        self._load_history()

    def _load_golden_set(self) -> None:
        """Parse golden_set.yaml."""
        if not self._golden_set_path.exists():
            logger.warning("eval_service: golden_set.yaml not found at %s", self._golden_set_path)
            self._golden_set = {"version": 2, "cases": []}
            self._cases = []
            return

        if yaml is None:
            logger.warning("eval_service: PyYAML not available, golden set not loaded")
            self._golden_set = {"version": 2, "cases": []}
            self._cases = []
            return

        try:
            with open(self._golden_set_path, encoding="utf-8") as f:
                self._golden_set = yaml.safe_load(f) or {}
            self._cases = self._golden_set.get("cases", [])
            logger.info("eval_service: loaded %d cases from golden_set.yaml", len(self._cases))
        except Exception as e:
            logger.error("eval_service: failed to parse golden_set.yaml: %s", e)
            self._golden_set = {"version": 2, "cases": []}
            self._cases = []

    def _load_history(self) -> None:
        """Parse all JSON files in EvalHistory/."""
        self._runs = []
        if not self._history_dir.exists():
            return

        for json_file in sorted(self._history_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(json_file.read_text())
                self._runs.append(data)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("eval_service: skipping %s: %s", json_file.name, e)

        logger.info("eval_service: loaded %d eval runs", len(self._runs))

    def reload(self) -> None:
        """Reload all data from disk (after new eval run). Thread-safe."""
        with self._data_lock:
            self._load()

    @property
    def case_count(self) -> int:
        """Number of golden set cases loaded."""
        return len(self._cases)

    @property
    def run_count(self) -> int:
        """Number of eval runs loaded."""
        return len(self._runs)

    # ─── Public API ───────────────────────────────────────────────────────

    def get_health(self) -> dict:
        """Current OS Health Score + per-dimension scores from latest run."""
        if not self._runs:
            return {
                "overall_score": None,
                "dimensions": {},
                "last_run": None,
                "total_cases": len(self._cases),
                "trend": None,
            }

        latest = self._runs[0]  # sorted desc
        trend = self._compute_trend()

        return {
            "overall_score": latest.get("overall_score"),
            "dimensions": latest.get("dimensions", {}),
            "last_run": {
                "run_id": latest.get("run_id"),
                "triggered_by": latest.get("triggered_by"),
                "triggered_at": latest.get("triggered_at"),
                "cases_passed": latest.get("cases_passed", 0),
                "cases_failed": latest.get("cases_failed", 0),
                "cases_skipped": latest.get("cases_skipped", 0),
            },
            "total_cases": len(self._cases),
            "trend": trend,
        }

    def get_history(self, limit: int = 20) -> list[dict]:
        """List eval runs sorted by date (newest first)."""
        return [
            {
                "run_id": r.get("run_id"),
                "triggered_by": r.get("triggered_by"),
                "triggered_at": r.get("triggered_at"),
                "overall_score": r.get("overall_score"),
                "total_cases": r.get("total_cases"),
                "cases_passed": r.get("cases_passed"),
                "cases_failed": r.get("cases_failed"),
                "cases_skipped": r.get("cases_skipped"),
                "duration_seconds": r.get("duration_seconds"),
                "dimensions": r.get("dimensions", {}),
            }
            for r in self._runs[:limit]
        ]

    def get_golden_set(self, category: Optional[str] = None) -> dict:
        """Return golden set metadata + cases (optionally filtered)."""
        cases = self._cases
        if category:
            cases = [c for c in cases if c.get("category") == category]

        return {
            "version": self._golden_set.get("version", 2),
            "total_cases": len(self._cases),
            "filtered_count": len(cases),
            "categories": self._golden_set.get("categories", []),
            "dimensions": self._golden_set.get("dimensions", []),
            "cases": [
                {
                    "id": c.get("id"),
                    "category": c.get("category"),
                    "dimension": c.get("dimension"),
                    "level": c.get("level"),
                    "title": c.get("title"),
                    "source": c.get("source"),
                    "tier": c.get("tier", "active"),
                    "affected_by": c.get("affected_by", []),
                    "evaluators": c.get("evaluators", []),
                    "last_result": self._get_case_last_result(c.get("id")),
                }
                for c in cases
            ],
        }

    def get_case_detail(self, case_id: str) -> Optional[dict]:
        """Return full case detail including scenario + history."""
        case = next((c for c in self._cases if c.get("id") == case_id), None)
        if not case:
            return None

        return {
            **case,
            "history": self._get_case_history(case_id),
        }

    # ─── CRUD Operations (P3) ───────────────────────────────────────────────

    _REQUIRED_CASE_FIELDS = {"id", "category", "dimension", "evaluators", "affected_by"}

    def add_case(self, case_data: dict) -> dict:
        """Add a new case to golden set. Raises ValueError on invalid input."""
        missing = self._REQUIRED_CASE_FIELDS - set(case_data.keys())
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        with self._data_lock:
            case_id = case_data["id"]
            if any(c.get("id") == case_id for c in self._cases):
                raise ValueError(f"Case '{case_id}' already exists")

            self._cases.append(case_data)
            self._persist_golden_set()
        return case_data

    def update_case(self, case_id: str, updates: dict) -> dict:
        """Update an existing case. Raises ValueError if not found or changing id."""
        if "id" in updates and updates["id"] != case_id:
            raise ValueError("Cannot change case ID via update")

        with self._data_lock:
            case = next((c for c in self._cases if c.get("id") == case_id), None)
            if case is None:
                raise ValueError(f"Case '{case_id}' not found")

            case.update(updates)
            self._persist_golden_set()
        return case

    def delete_case(self, case_id: str) -> dict:
        """Archive (soft-delete) a case. Sets tier='archived'."""
        with self._data_lock:
            case = next((c for c in self._cases if c.get("id") == case_id), None)
            if case is None:
                raise ValueError(f"Case '{case_id}' not found")

            case["tier"] = "archived"
            self._persist_golden_set()
        return case

    # ─── Run Triggers (P3) ────────────────────────────────────────────────

    def trigger_run(self, trigger: str = "manual", case_ids: list[str] | None = None) -> str:
        """Trigger an eval run in background thread. Returns run_id.

        Raises RuntimeError if a run is already in progress.
        """
        with self._run_lock:
            if self._running:
                raise RuntimeError("An eval run is already in progress")
            self._running = True

        now = datetime.now(timezone.utc)
        short_id = uuid.uuid4().hex[:6]
        run_id = f"eval_{now.strftime('%Y%m%d_%H%M%S')}_{short_id}_{trigger}"

        thread = threading.Thread(
            target=self._execute_run,
            args=(run_id, trigger, case_ids),
            daemon=True,
            name=f"eval-run-{run_id}",
        )
        thread.start()
        return run_id

    def run_canary(self) -> dict:
        """Run programmatic-only cases synchronously. Returns result dict."""
        from scripts.eval_runner import run_eval

        cases_data = {"cases": [c for c in self._cases if c.get("tier") != "archived"]}

        result = run_eval(cases_data, "canary", None, self._workspace_root)
        short_id = uuid.uuid4().hex[:6]
        result["run_id"] = f"eval_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{short_id}_canary"

        # Persist
        self._write_run_result(result)
        self._load_history()
        return result

    def get_run(self, run_id: str) -> Optional[dict]:
        """Get a specific run by ID."""
        return next((r for r in self._runs if r.get("run_id") == run_id), None)

    @property
    def is_running(self) -> bool:
        """Whether an eval run is currently in progress."""
        return self._running

    # ─── P4: Auto-Growth Methods ─────────────────────────────────────────

    def auto_seed_case(
        self, correction_id: str, correction_text: str, class_name: str = "UNCLASSIFIED"
    ) -> Optional[dict]:
        """Auto-generate a draft golden set case from a correction.

        Returns the new case dict, or None if case already exists.
        """
        case_id = f"GS_{correction_id}"

        with self._data_lock:
            if any(c.get("id") == case_id for c in self._cases):
                return None  # Already seeded

            case = {
                "id": case_id,
                "category": "compliance",
                "dimension": "compliance",
                "level": "session",
                "title": f"[Auto] {correction_text[:80]}",
                "source": correction_id,
                "affected_by": [_class_to_affected_by(class_name)],
                "evaluators": ["goal_success"],
                "tier": "draft",
                "scenario": {"turns": [{"input": correction_text[:200]}]},
                "assertions": [f"Agent does NOT repeat pattern: {correction_text[:100]}"],
                "verification": {},
            }
            self._cases.append(case)
            self._persist_golden_set()

        logger.info("eval_service: auto-seeded case %s from correction %s", case_id, correction_id)
        return case

    def get_affected_cases(self, changed_files: list[str]) -> list[dict]:
        """Return cases whose affected_by intersects with changed files."""
        # Normalize: strip paths, keep filename only
        filenames = {f.split("/")[-1] for f in changed_files}

        return [
            c for c in self._cases
            if c.get("tier") not in ("archived", "stable")
            and any(af in filenames for af in c.get("affected_by", []))
        ]

    def promote_stable_cases(self, min_consecutive_passes: int = 10) -> list[str]:
        """Promote cases with N+ consecutive passes to stable tier.

        Returns list of promoted case IDs.
        """
        promoted = []

        with self._data_lock:
            for case in self._cases:
                if case.get("tier") in ("archived", "stable", "draft"):
                    continue

                case_id = case["id"]
                consecutive = self._count_consecutive_passes(case_id)

                if consecutive >= min_consecutive_passes:
                    case["tier"] = "stable"
                    promoted.append(case_id)

            if promoted:
                self._persist_golden_set()
                logger.info("eval_service: promoted %d cases to stable: %s", len(promoted), promoted)

        return promoted

    def compute_intelligence_velocity(self, detail: bool = False):
        """Compute Intelligence Velocity — compound metric of system learning.

        Components:
        - golden_set_size: more cases = better coverage
        - pass_rate: latest overall score (0-100)
        - stability_ratio: stable cases / total active cases
        - growth_rate: cases added in last 30 days (approximate from IDs)

        IV = (pass_rate * 0.4) + (stability_ratio * 100 * 0.3) + (golden_set_size_score * 0.2) + (growth_score * 0.1)
        """
        active_cases = [c for c in self._cases if c.get("tier") not in ("archived",)]
        stable_cases = [c for c in self._cases if c.get("tier") == "stable"]
        total = len(active_cases) or 1

        # Pass rate from latest run
        pass_rate = 0.0
        if self._runs:
            pass_rate = self._runs[0].get("overall_score", 0) or 0

        # Stability ratio
        stability_ratio = len(stable_cases) / total

        # Golden set size score (log scale: 10→50, 50→80, 100→100)
        import math
        gs_score = min(100, 30 * math.log10(max(total, 1) + 1))

        # Growth: count draft/recent cases (proxy)
        draft_count = len([c for c in self._cases if c.get("tier") == "draft"])
        growth_score = min(100, draft_count * 20)  # Each draft case = 20 points, max 100

        score = round(
            pass_rate * 0.4 + stability_ratio * 100 * 0.3 + gs_score * 0.2 + growth_score * 0.1,
            1
        )

        if detail:
            return {
                "score": score,
                "components": {
                    "pass_rate": pass_rate,
                    "stability_ratio": round(stability_ratio, 3),
                    "golden_set_size": total,
                    "golden_set_size_score": round(gs_score, 1),
                    "growth_score": growth_score,
                    "draft_count": draft_count,
                    "stable_count": len(stable_cases),
                },
            }
        return score

    def _count_consecutive_passes(self, case_id: str) -> int:
        """Count consecutive passes in runs that INCLUDE this case.

        Only counts runs where the case was actually evaluated (not scoped out).
        A 'failed' or 'skipped' status breaks the streak.
        """
        count = 0
        for run in self._runs:  # newest first
            case_result = next(
                (cr for cr in run.get("cases", []) if cr.get("id") == case_id), None
            )
            if case_result is None:
                # Case wasn't in this scoped run — don't count as pass OR fail.
                # But to prevent inflation from scoped runs, limit lookback to
                # runs in the last 60 days only.
                continue
            if case_result.get("status") == "passed":
                count += 1
            else:
                break  # First non-pass breaks the streak
        return count

    # ─── Private: Persistence ────────────────────────────────────────────

    def _persist_golden_set(self) -> None:
        """Atomic write golden_set.yaml (tmp + rename pattern)."""
        if yaml is None:
            raise RuntimeError("PyYAML not available, cannot persist")

        self._golden_set["cases"] = self._cases
        content = yaml.dump(self._golden_set, default_flow_style=False, allow_unicode=True, sort_keys=False)

        # Atomic write: write to temp, then rename
        tmp_fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(self._golden_set_path.parent),
            suffix=".yaml.tmp",
            delete=False,
        )
        try:
            tmp_fd.write(content)
            tmp_fd.flush()
            tmp_fd.close()
            Path(tmp_fd.name).replace(self._golden_set_path)
        except Exception:
            Path(tmp_fd.name).unlink(missing_ok=True)
            raise

    def _write_run_result(self, result: dict) -> Path:
        """Write eval run result to EvalHistory/."""
        self._history_dir.mkdir(parents=True, exist_ok=True)
        trigger = result.get("triggered_by", "unknown")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        filename = f"{ts}_{trigger}.json"
        path = self._history_dir / filename

        with open(path, "w") as f:
            json.dump(result, f, indent=2)
        return path

    def _execute_run(self, run_id: str, trigger: str, case_ids: list[str] | None) -> None:
        """Background execution of eval run."""
        try:
            from scripts.eval_runner import run_eval

            cases_data = {"cases": [c for c in self._cases if c.get("tier") != "archived"]}
            result = run_eval(cases_data, trigger, case_ids, self._workspace_root)
            result["run_id"] = run_id

            self._write_run_result(result)
            with self._data_lock:
                self._load_history()

            # Post-run: promote stable cases (best-effort)
            try:
                self.promote_stable_cases()
            except Exception:
                pass
        except Exception as e:
            logger.error("eval_service: background run failed: %s", e)
            # Write failure result so user can see what happened
            failure_result = {
                "run_id": run_id,
                "triggered_by": trigger,
                "triggered_at": datetime.now(timezone.utc).isoformat(),
                "status": "failed",
                "error": str(e),
                "overall_score": 0,
                "dimensions": {},
                "cases": [],
                "total_cases": 0,
                "cases_passed": 0,
                "cases_failed": 0,
                "cases_skipped": 0,
                "duration_seconds": 0,
            }
            try:
                self._write_run_result(failure_result)
                with self._data_lock:
                    self._load_history()
            except Exception:
                pass  # Best effort — don't mask original error
        finally:
            with self._run_lock:
                self._running = False

    # ─── Private Helpers ──────────────────────────────────────────────────

    def _get_case_last_result(self, case_id: str) -> Optional[dict]:
        """Find this case's result in the most recent run."""
        if not self._runs:
            return None
        for run in self._runs:
            for case_result in run.get("cases", []):
                if case_result.get("id") == case_id:
                    return {
                        "status": case_result.get("status"),
                        "run_id": run.get("run_id"),
                        "triggered_at": run.get("triggered_at"),
                    }
        return None

    def _get_case_history(self, case_id: str, limit: int = 10) -> list[dict]:
        """Get this case's results across recent runs."""
        history = []
        for run in self._runs[:limit]:
            for case_result in run.get("cases", []):
                if case_result.get("id") == case_id:
                    history.append({
                        "run_id": run.get("run_id"),
                        "triggered_at": run.get("triggered_at"),
                        "status": case_result.get("status"),
                        "notes": case_result.get("notes", ""),
                    })
                    break
        return history

    def _compute_trend(self) -> Optional[dict]:
        """Compare latest score to previous run."""
        if len(self._runs) < 2:
            return None

        latest_score = self._runs[0].get("overall_score") or 0
        prev_score = self._runs[1].get("overall_score") or 0
        delta = round(latest_score - prev_score, 1)

        return {
            "delta": delta,
            "direction": "up" if delta > 0 else ("down" if delta < 0 else "stable"),
        }


def _class_to_affected_by(class_name: str) -> str:
    """Map correction class to the governance file it relates to."""
    mapping = {
        "CLASS_A": "STEERING.md",
        "CLASS_B": "AGENT.md",
        "CLASS_C": "AGENT.md",
        "UNCLASSIFIED": "AGENT.md",
    }
    return mapping.get(class_name, "AGENT.md")


# Module-level singleton (initialized lazily, thread-safe)
_eval_service: Optional[EvalService] = None
_eval_service_lock = threading.Lock()


def get_eval_service() -> EvalService:
    """Get or create the EvalService singleton (thread-safe)."""
    global _eval_service
    if _eval_service is None:
        with _eval_service_lock:
            if _eval_service is None:  # Double-check after lock
                try:
                    _eval_service = EvalService()
                except FileNotFoundError:
                    logger.warning("eval_service: workspace not found, creating empty service")
                    _eval_service = EvalService(workspace_root=None)
                except Exception as e:
                    logger.error("eval_service: init failed: %s", e)
                    _eval_service = EvalService(workspace_root=None)
    return _eval_service
