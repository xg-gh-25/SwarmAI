"""Recall Metrics API — the READ/visibility side of the unified-recall metrics layer.

Unified-recall Run 3 (run_35f42b75; design Knowledge/Designs/2026-08-09-unified-recall-
architecture.md §3.5). Run 2 landed the collecting substrate (core/recall_metrics.py →
recall_metrics SQLite table); this exposes it.

    GET /api/recall/metrics?window=<hours>&context=<ctx>
        → {generated_at, contexts:[{context, domain, count, p50_ms, p95_ms}]}

    GET /api/recall/knowledge
        → {generated_at, window_days, contexts:{ctx:{runs, hits, prev_runs, prev_hits}},
           stores:{projects, general, sessions, archives}}
      The C&M overlay's 知识 tab ("when knowledge is needed, is it found?"): how many
      recalls ran and brought something back in the last 7 days and the 7 before, per
      context (session_prompt = the session's first message, session_followup = the
      topic changed, session_ddd = the active project's Brain), and how much each store
      recall reads holds: the project Brains and their entries (counted as Brain Hub
      counts them, routers/ddd_brain), the General Brain's Library files (its Brain Hub
      count), the past sessions with messages, the MEMORY / EVOLUTION archive shards and
      their entries (core/archive_browse, the same as /eval/archive-list). A store that
      cannot be read is null, never 0.

Contract: READ-ONLY. The handlers issue SELECTs via readonly pooled borrows and NEVER
touch core.recall_metrics' in-memory rings — so they can't double-drain the flush loop's
samples. Percentiles are computed read-side from the raw rows (SQLite has no percentile
fn; the design forbids pre-aggregation). File counts run on the nav pool, off the loop.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query

from core import executors

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/recall", tags=["recall-metrics"])

KNOWLEDGE_WINDOW_DAYS = 7


@router.get("/metrics")
async def get_recall_metrics(
    window: Optional[int] = Query(default=None, description="lookback window in hours; omit = all-time"),
    context: Optional[str] = Query(default=None, description="restrict to one recall context"),
) -> dict:
    """Aggregated recall latency: count/p50/p95 by (context, domain).

    Read-only, non-blocking. Degrades to an empty contexts list on any error (an
    observability read must never 500 a dashboard poll)."""
    try:
        from database import db
        rows = await db.get_recall_metrics_summary(window_hours=window, context=context)
    except Exception:  # noqa: BLE001 — visibility read must never 500
        logger.warning("recall metrics summary failed", exc_info=True)
        rows = []
    return {"generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "contexts": rows}


def _project_brains() -> Optional[dict]:
    """{brains, entries}: the DDD projects and their knowledge entries, as Brain Hub
    counts them (routers/ddd_brain: _list_project_dirs + _entry_count)."""
    try:
        from routers import ddd_brain
        dirs = ddd_brain._list_project_dirs()
        return {"brains": len(dirs), "entries": sum(ddd_brain._entry_count(d) for d in dirs)}
    except Exception:  # noqa: BLE001 — one store never fails the others
        logger.warning("recall knowledge: project brains unreadable", exc_info=True)
        return None


def _general_library() -> Optional[dict]:
    """{files}: the General Brain's Library files, Brain Hub's count of it."""
    try:
        from routers import ddd_brain
        return {"files": int(ddd_brain._general_summary()["library"]["files"])}
    except Exception:  # noqa: BLE001
        logger.warning("recall knowledge: General Brain unreadable", exc_info=True)
        return None


def _archives(ws: Optional[str]) -> Optional[dict]:
    """{files, entries}: MEMORY + EVOLUTION archive shards and their entries."""
    if not ws:
        return None
    try:
        from core.archive_browse import list_archive_files
        files = entries = 0
        for source in ("memory", "evolution"):
            listed = list_archive_files(Path(ws), source)
            files += listed["total_files"]
            entries += sum(int(f.get("entry_count") or 0) for f in listed["files"])
        return {"files": files, "entries": entries}
    except Exception:  # noqa: BLE001
        logger.warning("recall knowledge: archives unreadable", exc_info=True)
        return None


def _file_stores(ws: Optional[str]) -> dict:
    return {"projects": _project_brains(), "general": _general_library(), "archives": _archives(ws)}


@router.get("/knowledge")
async def get_recall_knowledge() -> dict:
    """Recall hits per context over 7-day windows + what each recalled store holds
    (module docstring). Read-only; every part degrades on its own (empty contexts, a
    null store), never a 500."""
    from core.initialization_manager import initialization_manager

    ws = initialization_manager.get_cached_workspace_path()

    async def _hits() -> dict:
        from database import db
        return await db.get_recall_hit_windows(KNOWLEDGE_WINDOW_DAYS)

    async def _sessions() -> Optional[int]:
        from database import db
        return await db.count_message_sessions()

    hits, sessions, files = await asyncio.gather(
        _hits(), _sessions(), executors.run_nav(_file_stores, ws), return_exceptions=True)
    if isinstance(hits, BaseException):
        logger.warning("recall knowledge: hit windows failed: %s", hits)
        hits = {}
    if isinstance(sessions, BaseException):
        logger.warning("recall knowledge: session count failed: %s", sessions)
        sessions = None
    if isinstance(files, BaseException):
        logger.warning("recall knowledge: store counts failed: %s", files)
        files = {"projects": None, "general": None, "archives": None}
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "window_days": KNOWLEDGE_WINDOW_DAYS,
        "contexts": hits,
        "stores": {**files, "sessions": sessions},
    }
