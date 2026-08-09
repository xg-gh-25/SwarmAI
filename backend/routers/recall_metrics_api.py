"""Recall Metrics API — the READ/visibility side of the unified-recall metrics layer.

Unified-recall Run 3 (run_35f42b75; design Knowledge/Designs/2026-08-09-unified-recall-
architecture.md §3.5). Run 2 landed the collecting substrate (core/recall_metrics.py →
recall_metrics SQLite table); this exposes it.

    GET /api/recall/metrics?window=<hours>&context=<ctx>
        → {generated_at, contexts:[{context, domain, count, p50_ms, p95_ms}]}

Contract: READ-ONLY. The handler issues a single SELECT via db.get_recall_metrics_summary
(a readonly pooled borrow) and NEVER touches core.recall_metrics' in-memory rings — so it
can't double-drain the flush loop's samples. Percentiles are computed read-side from the
raw rows (SQLite has no percentile fn; the design forbids pre-aggregation). It never
blocks the recall hot path — it reads a table the flush loop writes ~every 5 min.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/recall", tags=["recall-metrics"])


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
