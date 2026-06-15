"""
Eval API endpoints — serves OS Eval Dashboard.

P2: Read-only endpoints (health, history, golden-set, case detail).
P3: Mutations (CRUD on golden set, run triggers).
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from core.eval_service import get_eval_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Request Models ──────────────────────────────────────────────────────────


class CreateCaseRequest(BaseModel):
    id: str = Field(..., min_length=1, description="Unique case ID (e.g. GS021)")
    category: str
    dimension: str
    title: str = ""
    level: str = "session"
    source: str = ""
    affected_by: list[str] = Field(default_factory=list)
    evaluators: list[str] = Field(default_factory=list)
    scenario: dict = Field(default_factory=dict)
    verification: dict = Field(default_factory=dict)
    expected_trajectory: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)


class UpdateCaseRequest(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    dimension: Optional[str] = None
    level: Optional[str] = None
    source: Optional[str] = None
    affected_by: Optional[list[str]] = None
    evaluators: Optional[list[str]] = None
    scenario: Optional[dict] = None
    verification: Optional[dict] = None
    expected_trajectory: Optional[list[str]] = None
    assertions: Optional[list[str]] = None
    tier: Optional[str] = None


class TriggerRunRequest(BaseModel):
    trigger: str = Field(default="manual", description="Trigger type")
    case_ids: Optional[list[str]] = Field(default=None, description="Specific case IDs to run")


# ─── GET Endpoints (P2) ─────────────────────────────────────────────────────


@router.get("/health")
async def get_eval_health():
    """Current OS Health Score + per-dimension breakdown + Intelligence Velocity."""
    svc = get_eval_service()
    health = svc.get_health()
    health["intelligence_velocity"] = svc.compute_intelligence_velocity(detail=True)
    return health


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


@router.get("/runs/{run_id}")
async def get_run_detail(run_id: str):
    """Get a specific eval run by ID."""
    svc = get_eval_service()
    run = svc.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run


# ─── POST Endpoints (P3 — Mutations) ────────────────────────────────────────


@router.post("/golden-set")
async def create_case(req: CreateCaseRequest):
    """Create a new golden set case."""
    svc = get_eval_service()
    try:
        case = svc.add_case(req.model_dump(exclude_none=True))
        return {"status": "created", "case": case}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/golden-set/{case_id}")
async def update_case(case_id: str, req: UpdateCaseRequest):
    """Update an existing golden set case."""
    svc = get_eval_service()
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        case = svc.update_case(case_id, updates)
        return {"status": "updated", "case": case}
    except ValueError as e:
        raise HTTPException(status_code=400 if "Cannot change" in str(e) else 404, detail=str(e))


@router.delete("/golden-set/{case_id}")
async def delete_case(case_id: str):
    """Archive (soft-delete) a golden set case."""
    svc = get_eval_service()
    try:
        case = svc.delete_case(case_id)
        return {"status": "archived", "case_id": case_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/run")
async def trigger_eval_run(req: TriggerRunRequest):
    """Trigger a full eval run in background. Returns run_id immediately."""
    svc = get_eval_service()
    try:
        run_id = svc.trigger_run(trigger=req.trigger, case_ids=req.case_ids)
        return {"status": "started", "run_id": run_id}
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/canary")
def run_canary():
    """Run programmatic-only eval cases synchronously (<5s).

    Note: sync def so FastAPI runs in threadpool, not blocking event loop.
    """
    svc = get_eval_service()
    try:
        result = svc.run_canary()
        return result
    except Exception as e:
        logger.error("eval canary failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run-cases")
async def run_specific_cases(req: TriggerRunRequest):
    """Run specific case IDs. Same as /run but requires case_ids."""
    if not req.case_ids:
        raise HTTPException(status_code=400, detail="case_ids required")
    svc = get_eval_service()
    try:
        run_id = svc.trigger_run(trigger=req.trigger, case_ids=req.case_ids)
        return {"status": "started", "run_id": run_id}
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/promote-stable")
async def promote_stable_cases():
    """Promote cases with 10+ consecutive passes to stable tier."""
    svc = get_eval_service()
    promoted = svc.promote_stable_cases()
    return {"status": "ok", "promoted": promoted, "count": len(promoted)}


@router.post("/reload")
async def reload_eval_data():
    """Reload golden set + history from disk."""
    svc = get_eval_service()
    svc.reload()
    return {"status": "reloaded", "cases": svc.case_count, "runs": svc.run_count}
