"""Memory health and compliance API endpoints.

Exposes DailyActivity extraction metrics and memory pipeline health. (The one-click
"Save to Memory" endpoint is gone with its button, 2026-11-01: CDF reads every
session at its end anyway.)

Key public symbols:

- ``router``  — FastAPI APIRouter mounted at ``/api``.
- ``GET /api/memory-compliance``       — Returns extraction metrics.
- ``GET /api/memory-health``           — Returns full memory pipeline health.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["memory"])

# Will be set during app startup via set_compliance_tracker()
_compliance_tracker = None


def set_compliance_tracker(tracker) -> None:
    """Inject the ComplianceTracker instance at startup."""
    global _compliance_tracker
    _compliance_tracker = tracker


@router.get("/memory-compliance")
async def get_memory_compliance():
    """Return DailyActivity extraction compliance metrics."""
    if _compliance_tracker is None:
        return {"metrics": [], "retention_days": 30}
    metrics = _compliance_tracker.get_metrics()
    return {
        "metrics": [m.to_dict() for m in metrics],
        "retention_days": 30,
    }


@router.get("/memory-health")
async def get_memory_health():
    """Return comprehensive memory pipeline health status.

    Checks:
    - MEMORY.md: exists, last modified, size in bytes
    - DailyActivity: file count, undistilled count, last write date
    - Distillation: flag status, last distillation date
    - Compliance: recent extraction success/failure counts
    """
    from core.initialization_manager import initialization_manager

    try:
        ws_path = Path(initialization_manager.get_cached_workspace_path())
    except Exception:
        return {"status": "error", "message": "Workspace not initialized"}

    health: dict = {"status": "ok", "checks": {}}

    # Sections 1+2 are all blocking FS I/O (stat + glob + per-file read_text over
    # DailyActivity). Run the whole scan in ONE worker thread (run_b2d3ece0) — NOT
    # per-file to_thread inside the loop (that would be N dispatches). Section 3 below
    # is in-memory, stays on the loop.
    def _scan_fs_health() -> dict:
        checks: dict = {}
        status_warn = False
        # 1. MEMORY.md health
        memory_path = ws_path / ".context" / "MEMORY.md"
        if memory_path.exists():
            stat = memory_path.stat()
            checks["memory_md"] = {
                "exists": True,
                "size_bytes": stat.st_size,
                "last_modified": date.fromtimestamp(stat.st_mtime).isoformat(),
                "warning": "large" if stat.st_size > 5120 else None,
            }
        else:
            checks["memory_md"] = {"exists": False}
            status_warn = True

        # 2. DailyActivity health
        da_dir = ws_path / "Knowledge" / "DailyActivity"
        if da_dir.is_dir():
            da_files = sorted(
                [f for f in da_dir.glob("*.md") if f.stem[:4].isdigit()],
                key=lambda f: f.stem,
                reverse=True,
            )
            checks["daily_activity"] = {
                "total_files": len(da_files),
                "last_write_date": da_files[0].stem if da_files else None,
            }
        else:
            checks["daily_activity"] = {"exists": False}
            status_warn = True
        return {"checks": checks, "status_warn": status_warn}

    _fs = await asyncio.to_thread(_scan_fs_health)
    health["checks"].update(_fs["checks"])
    if _fs["status_warn"]:
        health["status"] = "warning"

    # 3. Compliance tracker (in-memory)
    if _compliance_tracker is not None:
        metrics = _compliance_tracker.get_metrics(days=7)
        total_sessions = sum(m.sessions_processed for m in metrics)
        total_failures = sum(m.failures for m in metrics)
        health["checks"]["compliance_7d"] = {
            "sessions_processed": total_sessions,
            "failures": total_failures,
            "success_rate": round(
                (total_sessions - total_failures) / max(total_sessions, 1) * 100, 1
            ),
        }
    else:
        health["checks"]["compliance_7d"] = {"available": False}

    # Set overall status
    da_check = health["checks"].get("daily_activity", {})
    if da_check.get("undistilled_files", 0) > 10:
        health["status"] = "warning"
    if not health["checks"].get("memory_md", {}).get("exists", False):
        health["status"] = "error"

    return health


@router.get("/recall/coverage")
async def get_recall_coverage():
    """Report memory_vec embedding coverage — reads the last COMMITTED WAL snapshot.

    Honest WAL semantics (run_e9b15722, corrected per Gate-2): SQLite isolation is
    per-CONNECTION, not per-process — a fresh reader (in-process OR external) sees
    all COMMITTED rows equally. So being "inside the daemon" buys nothing by itself.
    What this endpoint gives is a read that runs RIGHT AFTER the daemon's own
    recovery drain commits, against a live connection — so it reflects committed
    state without racing a separate tool's stale snapshot. The earlier "external
    read returned entries=0" symptom was UNCOMMITTED singleton writes sitting in
    the -wal that no reader (here or in a script) would see until commit. This
    endpoint reads committed coverage; trust it only insofar as writers commit.
    Verification surface for the backfill convergence (M1).
    """
    # Memory vector recall was RETIRED (pure-filesystem READ-line finalize,
    # run_2f621986, design 2026-06-28 §3). memory_vec is no longer written or
    # read — recall is keyword/FTS5 only. This endpoint previously reported
    # memory_vec coverage; it now reports the retirement so any UI/monitor that
    # polls it sees an explicit "not applicable" instead of a 500 on a table that
    # is intentionally empty/absent.
    return {
        "available": False,
        "reason": "memory vector recall retired (pure-filesystem, keyword/FTS5 only)",
        "retired": True,
    }
