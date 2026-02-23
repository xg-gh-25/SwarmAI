"""PlanItem API endpoints for the Plan section of the Daily Work Operating Loop.

This module provides CRUD endpoints for PlanItem entities, which represent
prioritized work items in the Plan section. PlanItems are workspace-scoped
and support filtering by status and focus_type.

Requirements: 22.8
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from schemas.plan_item import (
    PlanItemCreate,
    PlanItemUpdate,
    PlanItemResponse,
    PlanItemStatus,
    FocusType,
)
from core.plan_item_manager import plan_item_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{workspace_id}/plan-items", response_model=list[PlanItemResponse])
async def list_plan_items(
    workspace_id: str,
    status: Optional[PlanItemStatus] = Query(None, description="Filter by status"),
    focus_type: Optional[FocusType] = Query(None, description="Filter by focus type"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """List PlanItems for a workspace with optional filtering and pagination.

    Requirement 22.8: GET /api/workspaces/{id}/plan-items with filters.
    """
    return await plan_item_manager.list(
        workspace_id=workspace_id,
        status=status,
        focus_type=focus_type,
        limit=limit,
        offset=offset,
    )


@router.post("/{workspace_id}/plan-items", response_model=PlanItemResponse, status_code=201)
async def create_plan_item(workspace_id: str, data: PlanItemCreate):
    """Create a new PlanItem in the specified workspace.

    Requirement 22.8: POST /api/workspaces/{id}/plan-items.
    """
    # Ensure workspace_id from path is used
    data.workspace_id = workspace_id
    try:
        return await plan_item_manager.create(data)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{workspace_id}/plan-items/{item_id}", response_model=PlanItemResponse)
async def update_plan_item(workspace_id: str, item_id: str, data: PlanItemUpdate):
    """Update an existing PlanItem.

    Requirement 22.8: PUT /api/workspaces/{id}/plan-items/{item_id}.
    """
    result = await plan_item_manager.update(item_id, data)
    if not result:
        raise HTTPException(status_code=404, detail=f"PlanItem {item_id} not found")
    return result


@router.delete("/{workspace_id}/plan-items/{item_id}")
async def delete_plan_item(workspace_id: str, item_id: str):
    """Delete a PlanItem.

    Requirement 22.8: DELETE /api/workspaces/{id}/plan-items/{item_id}.
    """
    deleted = await plan_item_manager.delete(item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"PlanItem {item_id} not found")
    return {"status": "deleted", "plan_item_id": item_id}
