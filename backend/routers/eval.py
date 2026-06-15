"""
Eval API endpoints — serves OS Eval Dashboard.

Read-only in P2. Mutations (run trigger, CRUD) added in P3.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from core.eval_service import get_eval_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
async def get_eval_health():
    """Current OS Health Score + per-dimension breakdown."""
    svc = get_eval_service()
    return svc.get_health()


@router.get("/history")
async def get_eval_history(limit: int = Query(default=20, ge=1, le=100)):
    """List eval runs sorted by date (newest first)."""
    svc = get_eval_service()
    return svc.get_history(limit=limit)


@router.get("/golden-set")
async def get_golden_set(category: Optional[str] = Query(default=None)):
    """Return golden set cases (optionally filtered by category)."""
    svc = get_eval_service()
    return svc.get_golden_set(category=category)


@router.get("/golden-set/{case_id}")
async def get_case_detail(case_id: str):
    """Return full detail for a single golden set case."""
    svc = get_eval_service()
    detail = svc.get_case_detail(case_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Case '{case_id}' not found")
    return detail


@router.post("/reload")
async def reload_eval_data():
    """Reload golden set + history from disk (after manual eval run).

    Note: No auth in P2 (single-user desktop app). Add rate-limit in P3 if needed.
    """
    svc = get_eval_service()
    svc.reload()
    return {"status": "reloaded", "cases": svc.case_count, "runs": svc.run_count}
