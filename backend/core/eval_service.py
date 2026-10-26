"""
SwarmAI Self-Eval Service — In-memory cache for golden set + eval history.

The golden set is the agent's behavioral contract — part of its identity,
equal in ontological status to SOUL/AGENT/STEERING. This service manages
that contract and the history of self-evaluation runs.

Parsed on startup from:
  - the golden set: the product set shipped with the backend
    (templates/eval/golden_set.yaml, read-only) + the workspace's
    Eval/golden_set.private.yaml (core.golden_set_store)
  - Eval/EvalHistory/*.json (self-eval run results)

Serves the Eval Dashboard API with zero-latency reads.
Cache invalidated on: eval run completion, manual reload.
"""

import json
import logging
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from utils.file_lock import flock_exclusive, flock_unlock
from core.project_registry import DEFAULT_PROJECT_NAME

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
        self.shadowed_private_ids: list[str] = []

        if workspace_root is self._UNSET:
            self._workspace_root = self._find_workspace()
        elif workspace_root is None:
            # Empty fallback — no workspace, no loading
            self._workspace_root = Path.home()
            self._project_dir = self._workspace_root / "nonexistent"
            self._golden_set_path = self._project_dir / "golden_set.yaml"
            self._private_golden_set_path = self._project_dir / "golden_set.private.yaml"
            self._history_dir = self._project_dir / "EvalHistory"
            self.empty = True
            return
        else:
            self._workspace_root = workspace_root

        # Eval is a SYSTEM-LEVEL subsystem (top-level Eval/, sibling of Projects/),
        # decoupled from DDD — it does NOT live under Projects/SwarmAI/ anymore.
        from core.golden_set_store import private_golden_set_path, product_golden_set_path

        self.empty = False
        self._project_dir = self._workspace_root / "Eval"
        # The product set ships with the backend and is READ-ONLY here: no method
        # of this service writes it (see _merge_and_write). The user's own cases
        # live in the private file, the only golden-set file this service writes.
        self._golden_set_path = product_golden_set_path()
        self._private_golden_set_path = private_golden_set_path(self._workspace_root)
        self._history_dir = self._project_dir / "EvalHistory"
        self._load()

    @staticmethod
    def _find_workspace() -> Path:
        """The user's workspace. A fresh install has no Eval/ folder yet and is a
        valid workspace: the product set ships with the backend and EvalHistory/
        is created on the first run."""
        from config import get_swarmws

        ws = get_swarmws()
        if not ws.is_dir():
            raise FileNotFoundError(f"workspace not found: {ws}")
        return ws

    def _load(self) -> None:
        """Load golden set + history into memory."""
        self._load_golden_set()
        self._load_history()

    def _load_golden_set(self) -> None:
        """Load the product set + the private set through the one shared reader
        (core.golden_set_store.load_merged). Each case carries an in-memory
        ``_origin`` tag ("product" | "private") that keeps product cases read-only
        and is stripped on write. A load error leaves the set empty and is logged
        as an ERROR (never silently)."""
        if yaml is None:
            logger.warning("eval_service: PyYAML not available, golden set not loaded")
            self._golden_set = {"version": 2, "cases": []}
            self._cases = []
            return

        from core.golden_set_store import load_merged

        try:
            data = load_merged(self._workspace_root, product_path=self._golden_set_path)
        except Exception as e:
            logger.error("eval_service: golden set not loaded: %s", e)
            self._golden_set = {"version": 2, "cases": []}
            self._cases = []
            return

        merged = data["cases"]
        self.shadowed_private_ids = list(data.get("shadowed_private_ids") or [])
        if self.shadowed_private_ids:
            logger.warning("eval_service: private cases skipped (same id as a product case): %s",
                           ", ".join(self.shadowed_private_ids))
        self._golden_set = {k: v for k, v in data.items() if k != "shadowed_private_ids"}
        self._cases = merged
        logger.info(
            "eval_service: loaded %d cases (%d product, %d private)",
            len(merged),
            sum(1 for c in merged if c.get("_origin") == "product"),
            sum(1 for c in merged if c.get("_origin") == "private"),
        )
        self._validate_case_taxonomy(merged)

    def _validate_case_taxonomy(self, cases: list[dict]) -> None:
        """Fail-LOUD (warn, never raise) guard on the case taxonomy.

        Every case's ``dimension`` must be one of the canonical dimensions and its
        ``category`` one of the canonical categories declared in the PUBLIC
        golden_set.yaml metadata. An off-canonical value is NOT dropped — the case
        still loads (PIT118: fail-loud != fail-hard) — but a WARNING names the
        offending value + case id so the drift is visible immediately.

        Why: compute_scores (eval_runner) aggregates raw ``dimension`` tags with no
        validation, so an off-canonical dimension silently leaks into
        /api/eval/health while /api/eval/golden-set shows only the yaml-declared
        canonical set — the exact divergence this guard surfaces at load time.
        Skipped when the metadata list is absent (nothing to validate against →
        no false-alarm storm). (run_8c44b7bf)
        """
        canonical_dims = set(self._golden_set.get("dimensions") or [])
        canonical_cats = set(self._golden_set.get("categories") or [])
        bad_dims: list[str] = []
        bad_cats: list[str] = []
        for case in cases:
            cid = case.get("id", "?")
            dim = case.get("dimension")
            if canonical_dims and dim is not None and dim not in canonical_dims:
                bad_dims.append(f"{cid}={dim!r}")
            cat = case.get("category")
            if canonical_cats and cat is not None and cat not in canonical_cats:
                bad_cats.append(f"{cid}={cat!r}")
        # One summary WARNING per axis (not N lines) — names every offender so the
        # drift is actionable without a log storm.
        if bad_dims:
            logger.warning(
                "eval_service: %d case(s) with off-canonical dimension (not in %s) "
                "— these leak into /health per-dimension scores: %s",
                len(bad_dims), sorted(canonical_dims), ", ".join(bad_dims),
            )
        if bad_cats:
            logger.warning(
                "eval_service: %d case(s) with off-canonical category (not in %s): %s",
                len(bad_cats), sorted(canonical_cats), ", ".join(bad_cats),
            )

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

    def behavior_case_count(self) -> int:
        """Number of non-archived behavior-tier cases — i.e. how many REAL agent
        spawns an include_behavior=True sweep will trigger. Surfaced in the
        /api/eval/run response so a caller opting into behavior sees the cost
        magnitude (each spawn is ~17-120s + Bedrock cost)."""
        return sum(
            1 for c in self._cases
            if c.get("eval_method") == "behavior" and c.get("tier") != "archived"
        )

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
                "cases_error": latest.get("cases_error", 0),
                "cases_timeout": latest.get("cases_timeout", 0),
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
                "cases_error": r.get("cases_error", 0),
                "cases_timeout": r.get("cases_timeout", 0),
                "duration_seconds": r.get("duration_seconds"),
                "dimensions": r.get("dimensions", {}),
            }
            for r in self._runs[:limit]
        ]

    @staticmethod
    def _project_low_detail(d: dict) -> dict:
        """Allowlist-project one low-score session detail (Gate-2 F4). Session-
        derived data → same privacy posture as drafts. Numeric scores + dimension
        enum pass through; the judge's free-text `reason` (which can quote the real
        prompt) is bounded to 240 chars; session_id is an opaque uuid kept for human
        lookup. Any field NOT listed here is dropped (fail-closed allowlist, not a
        denylist — an unanticipated future field never leaks by default)."""
        if not isinstance(d, dict):
            return {}
        reason = d.get("reason")
        if isinstance(reason, str) and len(reason) > 240:
            reason = reason[:237] + "…"
        return {
            "session_id": d.get("session_id"),
            "goal_score": d.get("goal_score"),
            "tool_score": d.get("tool_score"),
            "dimension": d.get("dimension"),
            "reason": reason,
        }

    def get_lines(self, now: datetime | None = None) -> dict:
        """The three lines the dashboard explains (design eval-as-product §3.3, §3.6):
        when each last ran, what it found, and when the next full run is due, plus the
        golden set's size and limits. Computed from the run records, the daily canary
        and online state files, and the jobs' own schedules (jobs.scheduler.load_jobs,
        the same source as /api/jobs), so no time or count is written anywhere by hand.

        The full run's cron fires every week and a 14-day gate decides whether it
        really runs, so the next full run is the first fire the gate lets through
        (eval_scheduled._should_run_biweekly), not simply the next fire."""
        from core.eval_followups import QUICK_TRIGGERS
        from core.golden_set_store import PRODUCT_CAP, TOTAL_CAP, is_product_case
        from scripts.eval_runner import _run_is_comparable

        now = now or datetime.now(timezone.utc)
        with self._data_lock:
            runs, cases = list(self._runs), list(self._cases)
        canary = self._context_state(".eval-canary-daily.json") or {}
        online = self._context_state(".session-quality-state.json") or {}
        quick_run = next((r for r in runs if r.get("triggered_by") in QUICK_TRIGGERS), None)
        full_run = next((r for r in runs if _run_is_comparable(r)), None)
        quick = {"last_run": None, "passed": None, "total": None}
        if canary.get("updated_at"):
            quick = {"last_run": canary["updated_at"], "passed": canary.get("passed"),
                     "total": canary.get("total")}
        if quick_run and (quick["last_run"] or "") < (quick_run.get("triggered_at") or ""):
            bvt = quick_run.get("bvt") or {}
            quick = {"last_run": quick_run.get("triggered_at"), "passed": bvt.get("passed"),
                     "total": bvt.get("total")}
        live = [c for c in cases if c.get("tier") != "archived"]
        product = sum(1 for c in live if is_product_case(c))
        return {
            "quick": quick,
            "full": {"last_run": (full_run or {}).get("triggered_at"),
                     "score": (full_run or {}).get("overall_score"),
                     "next_run": self._next_full_run(now)},
            "online": {"last_run": online.get("last_run"),
                       "sessions_checked": online.get("sessions_checked"),
                       "next_run": self._next_job_fire("session-quality", now)},
            "golden_set": {"product": product, "private": len(live) - product, "total": len(live),
                           "product_cap": PRODUCT_CAP, "total_cap": TOTAL_CAP},
        }

    @staticmethod
    def _job(job_id: str):
        try:
            from jobs.scheduler import load_jobs
            return next((j for j in load_jobs() if j.id == job_id and j.enabled), None)
        except Exception:  # noqa: BLE001 — no scheduler config: no next run to show
            return None

    def _next_job_fire(self, job_id: str, now: datetime) -> str | None:
        from jobs.cron_utils import next_fire

        job = self._job(job_id)
        fire = next_fire(job.schedule, now) if job else None
        return fire.isoformat() if fire else None

    def _next_full_run(self, now: datetime) -> str | None:
        from jobs.cron_utils import next_fire
        from jobs.handlers.eval_scheduled import _read_biweekly_last_run, _should_run_biweekly

        job = self._job("eval-scheduled")
        if job is None:
            return None
        last, cursor = _read_biweekly_last_run(), now
        for _ in range(8):   # weekly fires: the gate lets one through within 3
            fire = next_fire(job.schedule, cursor)
            if fire is None:
                return None
            if _should_run_biweekly(last, fire):
                return fire.isoformat()
            cursor = fire
        return None

    def get_follow_ups(self) -> list[dict]:
        """What needs the user's attention (core.eval_followups.build_follow_ups):
        the same list the HTML report's first section shows."""
        from core.eval_followups import build_follow_ups

        with self._data_lock:
            runs, cases = list(self._runs), list(self._cases)
        return build_follow_ups(runs, cases, self._context_state(".session-quality-state.json"),
                                shadowed=self.shadowed_private_ids,
                                daily_canary=self._context_state(".eval-canary-daily.json"))

    def _context_state(self, name: str) -> dict | None:
        """A JSON state file under .context/ as written, or None when absent / unreadable."""
        import json as _json

        if not self._workspace_root:
            return None
        path = self._workspace_root / ".context" / name
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def get_session_quality(self) -> dict:
        """The Online tab: the last online-scoring run (jobs/handlers/session_quality),
        read from its state file and projected field by field (session-derived text
        is the owner's, shown on the owner's machine, but only these fields and each
        bounded). No file yet → ``last_run: None``; a sampler fault → ``sampler_error``
        (shown as "did not run", never as zero)."""
        data = self._context_state(".session-quality-state.json") or {}

        def _example(e) -> dict:
            if not isinstance(e, dict):
                return {}
            return {"session_id": e.get("session_id"),
                    "title": str(e.get("title") or "")[:80],
                    "snippet": str(e.get("snippet") or "")[:160],
                    "reason": str(e.get("reason") or "")[:240]}

        checks = {}
        for cid, c in (data.get("checks") or {}).items():
            if not isinstance(c, dict):
                continue
            checks[str(cid)[:40]] = {"checked": c.get("checked"), "failed": c.get("failed"),
                                     "rate": c.get("rate"),
                                     "examples": [_example(e) for e in (c.get("examples") or [])[:3]]}
        sampler_error = data.get("sampler_error")
        return {
            "last_run": data.get("last_run"),
            "window_days": data.get("window_days"),
            "sessions_checked": data.get("sessions_checked", 0),
            "checks": checks,
            "low_details": [{**self._project_low_detail(d),
                             "title": str((d or {}).get("title") or "")[:80] if isinstance(d, dict) else ""}
                            for d in (data.get("low_details") or [])],
            "sampler_error": str(sampler_error)[:300] if sampler_error else None,
            "judge_error": str(data["judge_error"])[:300] if data.get("judge_error") else None,
            "trend": [t for t in (data.get("trend") or []) if isinstance(t, dict)][-12:],
        }

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
                    # what the case protects, in plain words (shown in the list)
                    "purpose": c.get("purpose") if isinstance(c.get("purpose"), dict) else None,
                    # `source` (e.g. "STEERING R1 + C011") is instance-identifying
                    # governance state — expose only for product cases (Gate-2 leak).
                    "source": c.get("source") if c.get("_origin") != "private" else None,
                    "tier": c.get("tier", "active"),
                    "eval_method": c.get("eval_method"),
                    # _origin is the product|private TAG only (never private content) —
                    # lets the UI tell shipped product cases from the user's own
                    # cases. Allowlist projection, so no content leaks.
                    "_origin": c.get("_origin"),
                    "affected_by": c.get("affected_by", []),
                    "evaluators": c.get("evaluators", []),
                    "last_result": self._get_case_last_result(c.get("id")),
                }
                for c in cases
            ],
        }

    # ALLOWLIST (not denylist) of fields kept in PRIVATE case detail. A denylist
    # leaks any NEW content field by default (Gate-2 run_1f588e53: expected_response_contains
    # + source slipped a denylist); an allowlist fails closed — an unanticipated
    # field is dropped, never exposed. Private cases can reference instance state
    # (MEMORY/STEERING/local governance), so only safe rendering metadata survives.
    # NOTE: `source` (e.g. "STEERING R1 + C011") is instance-identifying → NOT kept.
    _PRIVATE_DETAIL_ALLOWED = (
        "id", "category", "dimension", "level", "title", "tier",
        "eval_method", "affected_by", "evaluators", "_origin",
    )

    def get_case_detail(self, case_id: str) -> Optional[dict]:
        """Return case detail + history. For PRIVATE cases, return ONLY an
        allowlist of rendering metadata (fail-closed) so instance content never
        leaves via the API; PRODUCT cases return in full."""
        case = next((c for c in self._cases if c.get("id") == case_id), None)
        if not case:
            return None

        if case.get("_origin") == "private":
            detail = {k: case[k] for k in self._PRIVATE_DETAIL_ALLOWED if k in case}
            detail["history"] = self._get_case_history(case_id)
            detail["_content_redacted"] = True
            return detail
        return {**case, "history": self._get_case_history(case_id)}

    # ─── CRUD Operations (P3) ───────────────────────────────────────────────

    _REQUIRED_CASE_FIELDS = {"id", "category", "dimension", "evaluators", "affected_by"}

    def add_case(self, case_data: dict) -> dict:
        """Add a new case to golden set. Raises ValueError on invalid input.

        Gate enforcement (code, not convention):
        - gate_refs (C044/run_b1efcb5b): EVERY case's dotted refs (MEMORY./STEERING./
          AGENT./SOUL./EVOLUTION.) must resolve to non-empty .context content, else
          the case silently feeds the LLM judge empty/wrong context. Fail-open when
          the workspace has no .context/. Bare filenames (AGENT.md) are out of scope.
        - gate_teeth + auto-stamp (run_5edf2cc0 G2/G3/G6/G8): a GATE-ELIGIBLE case
          (fast deterministic evaluator, would enter the BVT) MUST declare a
          negative_command and is auto-stamped with its content-bound
          validated_by_4gate hash. Without this it lands unstamped → compute_bvt
          drops it → the gate silently shrinks. Non-gate-eligible cases (auto-seed
          behavior cases) skip teeth/stamp but still pass gate_refs."""
        missing = self._REQUIRED_CASE_FIELDS - set(case_data.keys())
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        from core.golden_set_store import TOTAL_CAP
        from scripts.golden_case_validator import (
            _is_gate_eligible, gate_teeth, gate_refs, gate_purpose, compute_case_stamp,
        )
        # Anti-drift (C044 BLOCKER 2): every dotted ref (MEMORY./STEERING./AGENT./
        # SOUL./EVOLUTION.) MUST resolve to non-empty content, else the case silently
        # feeds the LLM judge empty/wrong context. Fail-open if this workspace has no
        # .context/ (gate_refs handles that). Applies to ALL cases, not just
        # gate-eligible — an LLM-judge case is exactly where a drifted ref hides.
        ok, errs = gate_refs(case_data, root=self._workspace_root)
        if not ok:
            raise ValueError(f"Case rejected (refs drift): {'; '.join(errs)}")
        ok, errs = gate_purpose(case_data)
        if not ok:
            raise ValueError(f"Case rejected: {'; '.join(errs)}")
        if _is_gate_eligible(case_data):
            ok, errs = gate_teeth(case_data, grandfathered=False)
            if not ok:
                raise ValueError(f"Gate-eligible case rejected: {'; '.join(errs)}")
            # Auto-stamp so it enters the BVT (and re-stamps on any later edit via
            # update_case, which recomputes nothing — drift is caught at compute_bvt).
            case_data = {**case_data, "validated_by_4gate": compute_case_stamp(case_data)}

        with self._data_lock:
            case_id = case_data["id"]
            if any(c.get("id") == case_id for c in self._cases):
                raise ValueError(f"Case '{case_id}' already exists")
            live = sum(1 for c in self._cases if c.get("tier") != "archived")
            if live >= TOTAL_CAP:
                raise ValueError(
                    f"The golden set already has {live} cases (limit {TOTAL_CAP}). "
                    f"Retire a case before adding one."
                )

            self._cases.append(case_data)
            self._persist_golden_set()
        return case_data

    def update_case(self, case_id: str, updates: dict) -> dict:
        """Update an existing case. Raises ValueError if not found or changing id."""
        if "id" in updates and updates["id"] != case_id:
            raise ValueError("Cannot change case ID via update")

        from scripts.golden_case_validator import gate_refs
        with self._data_lock:
            case = next((c for c in self._cases if c.get("id") == case_id), None)
            if case is None:
                raise ValueError(f"Case '{case_id}' not found")
            self._refuse_product(case)

            # Validate the MERGED result (C044): an update that re-anchors a ref must
            # land on a resolvable target, not just any string. This is the sanctioned
            # path to fix drifted refs — the gate confirms the fix actually resolves.
            merged = {**case, **updates}
            ok, errs = gate_refs(merged, root=self._workspace_root)
            if not ok:
                raise ValueError(f"Update rejected (refs drift): {'; '.join(errs)}")

            case.update(updates)
            self._persist_golden_set()
        return case

    def delete_case(self, case_id: str) -> dict:
        """Archive (soft-delete) a case. Sets tier='archived'."""
        with self._data_lock:
            case = next((c for c in self._cases if c.get("id") == case_id), None)
            if case is None:
                raise ValueError(f"Case '{case_id}' not found")
            self._refuse_product(case)

            case["tier"] = "archived"
            self._persist_golden_set()
        return case

    @staticmethod
    def _refuse_product(case: dict) -> None:
        """Product cases ship with the backend and are read-only on every install;
        a change to one is a code change to templates/eval/golden_set.yaml."""
        from core.golden_set_store import is_product_case

        if is_product_case(case):
            raise ValueError(
                f"Case '{case.get('id')}' is a product case and is read-only"
            )

    def hard_delete_cases(self, ids) -> dict:
        """PHYSICALLY remove cases from the golden_set file(s) — not soft-archive.

        Unlike delete_case (tier='archived', row stays on disk forever), this drops
        the rows entirely. It is the FIRST path that shrinks self._cases, so it must
        defeat the merge-preserve disk-only re-append (which would otherwise resurrect
        a removed-from-memory case): it passes the removed ids as `removed_ids` to the
        locked persist, where _merge_partition_cases skips re-appending them.

        Disk-truth (Gate-2 F): we _load() fresh from disk under the lock FIRST, so the
        delete operates on the CURRENT on-disk corpus — not a possibly-stale in-memory
        snapshot loaded at process start. This makes deleted/not_found accurate and
        ensures an on-disk-but-not-yet-in-memory case is actually removed, not silently
        reported not_found and left behind.

        Atomicity (Gate-2 E): self._cases is snapshotted before the destructive filter
        and restored if persist raises, so a mid-write failure never leaves memory
        diverged from disk.

        Cross-process caveat: the flock serializes the on-disk RMW, but a DIFFERENT
        long-lived process holding these ids in ITS own self._cases would re-append
        them on its next flush (its removed_ids is empty). The safe caller is therefore
        the daemon's own singleton via POST /api/eval/golden-set/hard-delete — the
        load() here guarantees THIS process sees disk truth; other processes must
        reload() to converge. Not a general multi-writer-safe primitive.

        Returns {"deleted": [...], "not_found": [...], "refused": [...]} —
        destructive-op semantics: a typo'd id is reported, never a silent no-op;
        a product case id is refused (product cases are read-only).
        """
        from core.golden_set_store import is_product_case

        want = list(dict.fromkeys(ids))  # dedupe, preserve order
        with self._data_lock:
            self._load()  # disk truth before judging present/absent (Gate-2 F)
            product = {c.get("id") for c in self._cases if is_product_case(c)}
            present = {c.get("id") for c in self._cases} - product
            refused = [i for i in want if i in product]
            deleted = [i for i in want if i in present]
            not_found = [i for i in want if i not in present and i not in product]
            if deleted:
                drop = set(deleted)
                snapshot = list(self._cases)  # rollback point (Gate-2 E)
                self._cases = [c for c in self._cases if c.get("id") not in drop]
                try:
                    self._persist_golden_set(removed_ids=frozenset(drop))
                except Exception:
                    self._cases = snapshot  # restore — never leave memory diverged
                    raise
        return {"deleted": deleted, "not_found": not_found, "refused": refused}

    # ─── Run Triggers (P3) ────────────────────────────────────────────────

    def trigger_run(self, trigger: str = "manual", case_ids: list[str] | None = None,
                    include_behavior: bool = False) -> str:
        """Trigger an eval run in background thread. Returns run_id.

        Raises RuntimeError if a run is already in progress.

        ``include_behavior`` (default False) is the opt-in for the behavior tier
        (real agent spawns + Bedrock cost). Default-False keeps any caller that
        omits it safe — only an explicit opt-in (the HTTP API's
        TriggerRunRequest.include_behavior) runs behavior.
        """
        with self._run_lock:
            if self._running:
                raise RuntimeError("An eval run is already in progress")
            self._running = True

        now = datetime.now(timezone.utc)
        short_id = uuid.uuid4().hex[:6]
        run_id = f"eval_{now.strftime('%Y%m%d_%H%M%S')}_{short_id}_{trigger}"

        # Durability: write a status='running' marker SYNCHRONOUSLY (before the
        # thread starts) so a daemon SIGKILL mid-run leaves a detectable record
        # instead of a 404-forever ghost. The marker lives in the isolated
        # .inflight/ namespace (invisible to all EvalHistory readers) and is
        # cleared once the run writes its terminal record. NOT written inside the
        # thread — that would reopen the very SIGKILL window this closes.
        with self._data_lock:
            self._write_inflight_marker(run_id, trigger, now.isoformat())

        thread = threading.Thread(
            target=self._execute_run,
            args=(run_id, trigger, case_ids, include_behavior),
            daemon=True,
            name=f"eval-run-{run_id}",
        )
        thread.start()
        return run_id

    def run_canary(self) -> dict:
        """Run programmatic-only cases synchronously. Returns result dict."""
        from scripts.eval_runner import run_eval

        cases_data = {"cases": [c for c in self._cases if c.get("tier") != "archived"]}

        result = run_eval(cases_data, "canary", None, self._workspace_root, programmatic_only=True)
        short_id = uuid.uuid4().hex[:6]
        result["run_id"] = f"eval_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{short_id}_canary"

        # Persist
        self._write_run_result(result)
        self._load_history()
        return result

    def get_run(self, run_id: str) -> Optional[dict]:
        """Get a specific run by ID.

        Completed runs (in self._runs, loaded from EvalHistory/*.json) take
        precedence. If not found there, fall back to the .inflight/ marker so an
        in-progress OR mid-flight-killed run returns status=running instead of
        404. A truly-unknown id still returns None.
        """
        completed = next((r for r in self._runs if r.get("run_id") == run_id), None)
        if completed is not None:
            return completed
        marker_path = self._inflight_dir / f"{run_id}.json"
        try:
            return json.loads(marker_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    @property
    def is_running(self) -> bool:
        """Whether an eval run is currently in progress."""
        return self._running

    # ─── P4: Auto-Growth Methods ─────────────────────────────────────────

    def get_affected_cases(self, changed_files: list[str]) -> list[dict]:
        """Return cases whose affected_by intersects with changed files.

        Excludes behavior cases (eval_method=behavior): they spawn a REAL agent
        (~17-120s + Bedrock cost) and must NEVER be auto-triggered by a file
        edit via the change-trigger hook. They run only on explicit opt-in
        (behavior_trajectory tag or named case_filter). Without this filter, a
        behavior case declaring affected_by:[AGENT.md] would silently spawn
        agents from a PostToolUse hook on every governance edit (adversarial
        Gate-2 MED, run_75b656c1).

        Also excludes 'live'-tier cases: they depend on runtime state (a built
        index / live workspace) and WILL flap in a rebuild window. Auto-firing
        one from the change-trigger hook on an ordinary edit would surface a
        transient runtime-state miss as a spurious canary failure — the exact
        flap the live tier exists to prevent. This is a tier-set site (P8):
        'live' is non-auto-triggerable here, same as it is non-gate-eligible in
        compute_bvt and non-promotable in promote_stable_cases.
        """
        # Normalize: strip paths, keep filename only
        filenames = {f.split("/")[-1] for f in changed_files}

        return [
            c for c in self._cases
            if c.get("tier") not in ("archived", "stable", "live")
            and c.get("eval_method") != "behavior"
            and any(af in filenames for af in c.get("affected_by", []))
        ]

    def promote_stable_cases(self, min_consecutive_passes: int = 10) -> list[str]:
        """Promote cases with N+ consecutive passes to stable tier.

        Returns list of promoted case IDs.
        """
        promoted = []

        # SSOT: the non-promotable tier set lives in eval_runner (imported here so
        # the live-tier invariant — a passing 'live' case must never auto-promote
        # into gate-eligible 'stable' — is defined in exactly one place).
        from scripts.eval_runner import _NON_PROMOTABLE_TIERS

        from core.golden_set_store import is_product_case

        with self._data_lock:
            for case in self._cases:
                # A product case's tier is part of its stamp and ships with the
                # backend; promoting it would drop it out of the BVT (stale stamp).
                if case.get("tier") in _NON_PROMOTABLE_TIERS or is_product_case(case):
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

        # pass_rate = QUALITY axis (honest, on scored cases only). Kept raw so
        # IV stays readable as quality even when the judge infra partially fails.
        # coverage = MEASUREMENT axis: fraction of intended-scorable cases that
        # actually scored. A run where the judge broke (cases_error > 0) measured
        # a non-representative subset, so its IV is untrustworthy — we discount
        # the FINAL IV by coverage (applied once, named below), NOT pass_rate.
        # This keeps the two failure modes separate: low pass_rate = agent bad;
        # low coverage = infra broke. (errors only, NOT skips — skips can be
        # legit: canary programmatic_only, pending behavior cases.)
        pass_rate = 0.0
        n_error = 0
        coverage = 1.0
        if self._runs:
            latest = self._runs[0]
            pass_rate = latest.get("overall_score", 0) or 0
            scored = latest.get("scored_count")
            n_error = latest.get("cases_error", 0) or 0
            if scored is None:
                # Legacy run without scored_count — derive from pass+fail.
                scored = (latest.get("cases_passed", 0) or 0) + (latest.get("cases_failed", 0) or 0)
            intended = scored + n_error
            coverage = (scored / intended) if intended > 0 else 1.0

        # Stability ratio
        stability_ratio = len(stable_cases) / total

        # Golden set size score (log scale: 10→50, 50→80, 100→100)
        import math
        gs_score = min(100, 30 * math.log10(max(total, 1) + 1))

        # Growth: count draft/recent cases (proxy)
        draft_count = len([c for c in self._cases if c.get("tier") == "draft"])
        growth_score = min(100, draft_count * 20)  # Each draft case = 20 points, max 100

        base_score = (
            pass_rate * 0.4 + stability_ratio * 100 * 0.3
            + gs_score * 0.2 + growth_score * 0.1
        )
        # Apply measurement-coverage ONCE to the final IV: a run that couldn't
        # evaluate part of its set yields an untrustworthy IV, so we discount it
        # by coverage. pass_rate itself stays honest (quality), so the detail
        # breakdown still reads "agent passed X% of what ran" independently.
        score = round(base_score * coverage, 1)

        if detail:
            return {
                "score": score,
                "components": {
                    "pass_rate": round(pass_rate, 1),
                    "coverage": round(coverage, 3),
                    "cases_error": n_error,
                    "base_score_pre_coverage": round(base_score, 1),
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

    def _persist_golden_set(self, removed_ids: frozenset = frozenset()) -> None:
        """Atomic write golden_set.yaml with merge-preserve pattern.

        Before writing, re-reads the current disk state and merges, in two parts:
        - Cases in BOTH disk and memory: preserves user-owned disk fields
          (tags/notes/promoted_from) that this process didn't modify, overlaying
          all other in-memory fields.
        - Cases on disk but NOT in memory: appended verbatim. These were added by
          another session (or manual edit) after this process loaded golden_set.yaml.
          Without this, they would be silently dropped (the 2026-06-25 data-loss bug,
          Radar b40b9545). Safe because delete_case is a soft delete — see the
          INVARIANT comment at the disk-only append below.
        - New in-memory cases (add_case) are written as-is.

        Concurrency: in-process writers serialize on ``self._data_lock``; CROSS-process
        writers serialize on an OS-level exclusive lock (a sidecar ``.lock`` file via
        ``utils.file_lock.flock_exclusive``) held for the entire read-modify-write span
        in ``_merge_and_write``. Together these close the TOCTOU window — a second
        SwarmAI process cannot atomic-replace the file between our re-read and our
        rename. flock auto-releases on holder death, so a crashed writer leaves no
        stale lock. (run_0fac5a91 closed what run_fb4b42d2 only narrowed.)
        """
        if yaml is None:
            raise RuntimeError("PyYAML not available, cannot persist")
        if getattr(self, "empty", False):
            # The no-workspace stand-in has nowhere to write; never invent a path.
            raise RuntimeError("eval_service: no workspace, golden set not writable")

        # Cross-process serialization: self._data_lock is a threading.Lock
        # (in-process only). Two SwarmAI processes (e.g. daemon + a CLI eval run)
        # can otherwise interleave the re-read→merge→rename below and lose each
        # other's writes. Hold an OS-level exclusive lock on a sidecar .lock file
        # for the WHOLE read-modify-write span (acquire BEFORE the disk re-read,
        # release AFTER the rename) so the merge always sees a quiescent file.
        # flock auto-releases if the holder process dies, so no stale lock.
        # (run_0fac5a91, todo 7e233ecb — closes the TOCTOU narrowed by run_fb4b42d2.)
        # Only the private file is ever written (the product set is read-only),
        # so only its sidecar lock is taken. flock auto-releases on holder death.
        self._private_golden_set_path.parent.mkdir(parents=True, exist_ok=True)
        private_lock = open(
            self._private_golden_set_path.with_suffix(
                self._private_golden_set_path.suffix + ".lock"
            ),
            "w",
        )
        flock_exclusive(private_lock)
        try:
            self._merge_and_write(yaml, removed_ids=removed_ids)
        finally:
            flock_unlock(private_lock)
            private_lock.close()

    def _merge_and_write(self, yaml, removed_ids: frozenset = frozenset()) -> None:
        """Read-modify-write body of _persist_golden_set. MUST be called only
        while the caller holds the private file's sidecar lock.

        Writes ONLY the private file: product cases are skipped, so nothing on a
        user's machine can write the shipped product set (this is the structural
        half of "product cases are read-only"; the methods refuse first). A case
        without ``_origin`` is a newly added one and is private. ``_origin`` is
        stripped before serialize — it is an in-memory tag, never persisted.

        removed_ids (run_110678fb): hard-deleted ids, so the merge-preserve
        re-append does not resurrect them.
        """
        from core.golden_set_store import is_product_case

        private_mem = [c for c in self._cases if not is_product_case(c)]
        self._write_partition(yaml, self._private_golden_set_path, private_mem, {"version": 2}, removed_ids)

    @staticmethod
    def _merge_partition_cases(disk_cases: dict, mem_cases: list,
                               removed_ids: frozenset = frozenset()) -> list:
        """Merge one file's in-memory cases with its own disk state: preserve
        user-owned disk fields on in-both cases, append disk-only (externally
        added) cases. Same data-loss guard as before (Radar b40b9545), but
        scoped to a SINGLE file's disk contents — never the union.

        removed_ids (run_110678fb): ids being HARD-deleted. A hard-deleted id is
        absent from mem_cases (the caller dropped it from self._cases) AND must NOT
        be resurrected by the disk-only re-append below. Without this set, "disk-only"
        means "externally-added → preserve"; WITH it, a disk-only id that is in
        removed_ids means "just hard-deleted → drop". Default empty → every existing
        persist caller is byte-identical (only hard_delete_cases passes a non-empty
        set). removed_ids is origin-AGNOSTIC: pass the full set to both partitions;
        the partition that doesn't hold the id simply skips nothing."""
        _USER_OWNED_FIELDS = frozenset({"tags", "notes", "promoted_from"})
        merged_cases: list[dict] = []
        merged_ids: set[str] = set()
        for mem_case in mem_cases:
            case_id = mem_case.get("id")
            if case_id and case_id in disk_cases:
                merged = dict(disk_cases[case_id])
                for key, value in mem_case.items():
                    if key not in _USER_OWNED_FIELDS:
                        merged[key] = value
                merged_cases.append(merged)
            else:
                merged_cases.append(mem_case)
            if case_id:
                merged_ids.add(case_id)
        # Disk-only cases for THIS file — append verbatim UNLESS hard-deleted.
        for case_id, disk_case in disk_cases.items():
            if case_id not in merged_ids and case_id not in removed_ids:
                merged_cases.append(disk_case)
                merged_ids.add(case_id)
        return merged_cases

    def _write_partition(self, yaml, path: Path, mem_cases: list, meta: dict,
                         removed_ids: frozenset = frozenset()) -> None:
        """Re-read ONE file, merge its own cases, strip _origin, atomic-write.
        removed_ids (run_110678fb): hard-deleted ids the disk-only re-append must skip."""
        disk_cases = {}
        if path.exists():
            try:
                disk_data = yaml.safe_load(path.read_text()) or {}
                for case in disk_data.get("cases", []):
                    if case.get("id"):
                        disk_cases[case["id"]] = case
            except Exception:
                pass

        merged_cases = self._merge_partition_cases(disk_cases, mem_cases, removed_ids)
        # Strip the internal routing tag — never persist _origin to disk.
        clean_cases = [{k: v for k, v in c.items() if k != "_origin"} for c in merged_cases]
        out = dict(meta)
        out["cases"] = clean_cases
        content = yaml.dump(out, default_flow_style=False, allow_unicode=True, sort_keys=False)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd = tempfile.NamedTemporaryFile(
            mode="w", dir=str(path.parent), suffix=".yaml.tmp", delete=False
        )
        try:
            tmp_fd.write(content)
            tmp_fd.flush()
            tmp_fd.close()
            Path(tmp_fd.name).replace(path)
        except Exception:
            Path(tmp_fd.name).unlink(missing_ok=True)
            raise

    @property
    def _inflight_dir(self) -> Path:
        """Isolated namespace for status='running' markers of in-flight runs.

        A subdir of EvalHistory/, deliberately NOT matched by the non-recursive
        ``glob("*.json")`` that every EvalHistory reader uses (eval_service
        ._load_history, eval_runner._load_history, ci_eval_gate._reports_by_mtime,
        routers/eval.py). This namespace
        isolation is the whole point: a running marker here is INVISIBLE to those
        readers, so none of them can misread it as a scoreless completed run — no
        per-reader status filter is needed.

        INVARIANT — do NOT change any EvalHistory reader from a non-recursive
        ``glob("*.json")`` to ``rglob`` / ``os.walk`` / ``iterdir``: that would
        break the isolation and re-expose the running markers to every reader.

        FOLLOW-UP (out of the minimal fix scope): a startup sweep to relabel stale
        markers (status running → interrupted) or age them out after N days. The
        minimal fix only needs the marker to PERSIST (durable+detectable) and
        get_run to surface status=running; the sweep is a later enhancement.
        """
        return self._history_dir / ".inflight"

    def _write_inflight_marker(self, run_id: str, trigger: str, triggered_at: str) -> None:
        """Atomically write a status='running' marker for an in-flight run.

        Keyed by run_id (stable → one run = one marker, overwrite-safe). Written
        via same-dir tempfile + atomic replace so get_run never reads a partial
        marker without a 'status' key. Same idiom as _persist_golden_set's
        _write_partition (same-dir tempfile is required — a cross-filesystem
        os.replace raises EXDEV and is NOT atomic).
        """
        marker = {
            "run_id": run_id,
            "triggered_by": trigger,
            "triggered_at": triggered_at,
            "status": "running",
            "overall_score": None,
            "dimensions": {},
            "cases": [],
            "total_cases": 0,
            # Shape parity with completed/failure records so a get_run consumer
            # (e.g. a UI rendering the running state) never KeyErrors on these.
            "cases_passed": 0,
            "cases_failed": 0,
            "cases_skipped": 0,
            "duration_seconds": 0,
        }
        self._inflight_dir.mkdir(parents=True, exist_ok=True)
        tmp_fd = tempfile.NamedTemporaryFile(
            mode="w", dir=str(self._inflight_dir), suffix=".json.tmp", delete=False
        )
        try:
            json.dump(marker, tmp_fd, indent=2)
            tmp_fd.flush()
            tmp_fd.close()
            Path(tmp_fd.name).replace(self._inflight_dir / f"{run_id}.json")
        except Exception:
            Path(tmp_fd.name).unlink(missing_ok=True)
            raise

    def _clear_inflight_marker(self, run_id: str) -> None:
        """Delete an in-flight marker once a terminal record is durable. Best-effort."""
        try:
            (self._inflight_dir / f"{run_id}.json").unlink(missing_ok=True)
        except OSError as e:
            logger.debug("eval_service: could not clear inflight marker %s: %s", run_id, e)

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

    def _execute_run(self, run_id: str, trigger: str, case_ids: list[str] | None,
                     include_behavior: bool = False) -> None:
        """Background execution of eval run.

        ``include_behavior`` forwards the caller's opt-in into run_eval. It
        defaults False so the auto-seed hook path (which never passes it) and any
        legacy caller stay safe; only an explicit HTTP-API opt-in runs behavior.
        """
        # Track whether a terminal record (completed OR failed) actually reached
        # disk. The .inflight marker is cleared in `finally` ONLY if it did — so a
        # run whose BOTH write paths fail keeps its marker (durable + detectable,
        # the whole point) rather than reverting to a 404 ghost.
        terminal_written = False
        try:
            from scripts.eval_runner import run_eval, generate_html_report

            cases_data = {"cases": [c for c in self._cases if c.get("tier") != "archived"]}
            # Manual/GUI full run → verify canary teeth (the per-session canary
            # path at run_canary() stays verify_teeth=False — it is deadline-bound).
            result = run_eval(cases_data, trigger, case_ids, self._workspace_root,
                              verify_teeth=True, include_behavior=include_behavior)
            result["run_id"] = run_id

            self._write_run_result(result)
            terminal_written = True
            with self._data_lock:
                self._load_history()

            # Generate HTML report alongside JSON (best-effort), over the same
            # merged set the run used (product + private).
            try:
                generate_html_report(result, self._golden_set, self._workspace_root)
            except Exception as html_err:
                logger.warning("eval_service: HTML report generation failed: %s", html_err)

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
                # Gate blocks in canonical shape so ci_eval_gate reads a
                # well-formed report (run_21490939 Gate-1 F2). A crashed run
                # cannot ASSERT a clean gate: bvt.green False, and redline
                # not-violated (a crash never OBSERVED a red-line failure —
                # freshness/bvt already block the push; don't fabricate a veto
                # the run never saw).
                "bvt": {"total": 0, "passed": 0, "failed": 0, "error": 0,
                        "skipped": 0, "green": False},
                "redline": {"violated": False, "total": 0, "violations": [], "skipped": []},
            }
            try:
                self._write_run_result(failure_result)
                terminal_written = True
                with self._data_lock:
                    self._load_history()
            except Exception:
                pass  # Best effort — don't mask original error
        finally:
            # Clear the in-flight marker ONLY when a terminal record is durable
            # (get_run then returns the completed/failed record from _runs, which
            # _load_history already repopulated). If neither write succeeded, the
            # marker stays so the run remains detectable, not a 404 ghost.
            if terminal_written:
                self._clear_inflight_marker(run_id)
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

    # --- v3 Phase 3: governance proposal review (accept / reject / defer) ---

    def _evolution_md(self) -> Path:
        return self._workspace_root / ".context" / "EVOLUTION.md"

    def _read_governance_proposals(self) -> list[dict]:
        """The pending governance proposals, derived from the GC table every time.

        EVOLUTION.md's Governance Candidates table is the only store
        (core/evolution/governance_miner.py): there is no second copy to race,
        drift or go stale. Rows an older version left in .evolution_proposals.json
        are not read (design 2026-10-26-evolution-loop-admission §3.2).
        """
        from core.evolution.governance_miner import generate_governance_proposals

        return [p.to_proposal_dict() for p in generate_governance_proposals(self._evolution_md())]

    def get_pending_governance(self) -> dict:
        """List governance proposals awaiting human decision."""
        proposals = self._read_governance_proposals()
        return {"proposals": proposals, "total": len(proposals)}

    def decide_governance(self, proposal_id: str, decision: str) -> dict:
        """Accept / reject / defer a governance proposal.

        accept / reject: write ✔ ACCEPTED / ✖ REJECTED into the GC row's Count cell,
                so it is never proposed again. Writing the rule into a governance
                file is the s_self-evolution path, on the user's approval.
        defer:  leave the proposal in place (no-op on the file).

        NEVER writes SOUL/AGENT/STEERING (Gate-1 Check-7).
        """
        if decision not in ("accept", "reject", "defer"):
            return {"status": "error", "error": f"invalid decision: {decision}"}

        proposals = self._read_governance_proposals()
        target = next((p for p in proposals if p.get("id") == proposal_id), None)
        if target is None:
            return {"status": "not_found", "proposal_id": proposal_id}

        if decision == "defer":
            return {"status": "deferred", "proposal_id": proposal_id}

        from core.evolution.governance_miner import mark_gc_decision

        if not mark_gc_decision(self._evolution_md(), target["gc_id"], accepted=(decision == "accept")):
            # The row changed between reading the list and writing the verdict
            # (decided elsewhere, edited or deleted): report it, write nothing.
            return {"status": "not_found", "proposal_id": proposal_id}
        result = {"status": "rejected" if decision == "reject" else "accepted",
                  "proposal_id": proposal_id, "action_taken": "marked"}
        if decision == "accept":
            result["next_step"] = ("Write the rule into AGENT/STEERING/SOUL with "
                                   "s_self-evolution — accepting records the verdict only.")
        return result


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
                except FileNotFoundError as e:
                    # The workspace folder does not exist yet (startup ordering):
                    # not cached, so the next call retries once it exists.
                    logger.warning("eval_service: not ready (%s); empty service for this call", e)
                    return EvalService(workspace_root=None)
                except Exception as e:
                    logger.error("eval_service: init failed: %s", e)
                    _eval_service = EvalService(workspace_root=None)
    return _eval_service
