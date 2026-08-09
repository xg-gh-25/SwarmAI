"""Memory health, compliance, and one-click save API endpoints.

Exposes DailyActivity extraction metrics, memory pipeline health,
and LLM-powered session memory extraction for the frontend 🧠 button.

Key public symbols:

- ``router``  — FastAPI APIRouter mounted at ``/api``.
- ``GET /api/memory-compliance``       — Returns extraction metrics.
- ``GET /api/memory-health``           — Returns full memory pipeline health.
- ``POST /api/memory/save-session``    — One-click: extract session → MEMORY.md.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

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
            undistilled = 0
            last_distilled_date = None
            for f in da_files:
                try:
                    content = f.read_text(encoding="utf-8")
                    if not content.startswith("---"):
                        undistilled += 1
                        continue
                    end = content.find("---", 3)
                    if end == -1:
                        undistilled += 1
                        continue
                    fm = content[3:end].strip()
                    if "distilled: true" not in fm:
                        undistilled += 1
                    elif last_distilled_date is None:
                        for line in fm.splitlines():
                            if line.startswith("distilled_date:"):
                                last_distilled_date = line.split(":", 1)[1].strip().strip('"\'')
                                break
                except (OSError, UnicodeDecodeError):
                    continue

            checks["daily_activity"] = {
                "total_files": len(da_files),
                "undistilled_files": undistilled,
                "last_write_date": da_files[0].stem if da_files else None,
                "last_distilled_date": last_distilled_date,
            }
            flag_path = da_dir / ".needs_distillation"
            checks["distillation_flag"] = {
                "flag_exists": flag_path.exists(),
                "flag_content": flag_path.read_text().strip() if flag_path.exists() else None,
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


# ---------------------------------------------------------------------------
# One-click "Save to Memory" endpoint
# ---------------------------------------------------------------------------


class SaveSessionRequest(BaseModel):
    """Request body for POST /api/memory/save-session."""

    session_id: str
    since_message_idx: int = 0


class SaveSessionResponse(BaseModel):
    """Response body for POST /api/memory/save-session."""

    status: str  # "saved" | "empty" | "error"
    entries: dict[str, int] = {}
    total_saved: int = 0
    next_message_idx: int = 0
    message: Optional[str] = None


@router.post("/memory/save-session", response_model=SaveSessionResponse)
async def save_session_to_memory(req: SaveSessionRequest):
    """Extract key decisions/lessons from a chat session and save to MEMORY.md.

    This powers the frontend 🧠 "Save to Memory" button. Uses LLM extraction
    (Sonnet) to identify important entries, then writes via ``locked_write.py``
    with ``dedup=True`` — a MECHANICAL filter (120-char prefix + bold-title
    match, shared with distillation via ``filter_duplicate_entries``) drops
    entries already present in MEMORY.md, inside the write lock. (The LLM prompt
    also sees existing content as a soft hint, but the mechanical filter is the
    real guarantee — R3-C.)

    The ``since_message_idx`` field enables incremental saves — on a second
    click within the same session, only new messages are processed.
    """
    from core.memory_extractor import extract_and_save

    try:
        result = await extract_and_save(
            session_id=req.session_id,
            since_message_idx=req.since_message_idx,
        )
    except Exception as exc:
        logger.error(
            "Unhandled error in extract_and_save for session %s: %s",
            req.session_id,
            exc,
            exc_info=True,
        )
        return SaveSessionResponse(
            status="error",
            entries={"key_decisions": 0, "lessons_learned": 0, "open_threads": 0, "recent_context": 0},
            total_saved=0,
            next_message_idx=req.since_message_idx,
            message=f"Extraction failed: {type(exc).__name__}. Please try again.",
        )

    if result.error:
        if result.total_saved > 0:
            # Partial success
            status = "saved"
        elif "Not enough" in (result.error or "") or "Nothing new" in (result.error or ""):
            status = "empty"
        else:
            status = "error"
    else:
        status = "saved" if result.total_saved > 0 else "empty"

    return SaveSessionResponse(
        status=status,
        entries={
            "key_decisions": result.key_decisions,
            "lessons_learned": result.lessons_learned,
            "open_threads": result.open_threads,
            "recent_context": result.recent_context,
        },
        total_saved=result.total_saved,
        next_message_idx=result.next_message_idx,
        message=result.error,
    )


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
