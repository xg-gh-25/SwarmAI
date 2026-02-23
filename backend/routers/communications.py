"""Communication API endpoints for the Communicate section of the Daily Work Operating Loop.

This module provides CRUD endpoints for Communication entities, which represent
stakeholder alignment work items in the Communicate section. Communications are
workspace-scoped and support filtering by status and channel_type.

Requirements: 23.8
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from schemas.communication import (
    CommunicationCreate,
    CommunicationUpdate,
    CommunicationResponse,
    CommunicationStatus,
    ChannelType,
)
from core.communication_manager import communication_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{workspace_id}/communications", response_model=list[CommunicationResponse])
async def list_communications(
    workspace_id: str,
    status: Optional[CommunicationStatus] = Query(None, description="Filter by status"),
    channel_type: Optional[ChannelType] = Query(None, description="Filter by channel type"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """List Communications for a workspace with optional filtering and pagination.

    Requirement 23.8: GET /api/workspaces/{id}/communications with filters.
    """
    results = await communication_manager.list(
        workspace_id=workspace_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    # Apply channel_type filter (manager doesn't support it natively)
    if channel_type is not None:
        results = [r for r in results if r.channel_type == channel_type]
    return results


@router.post("/{workspace_id}/communications", response_model=CommunicationResponse, status_code=201)
async def create_communication(workspace_id: str, data: CommunicationCreate):
    """Create a new Communication in the specified workspace.

    Requirement 23.8: POST /api/workspaces/{id}/communications.
    """
    # Ensure workspace_id from path is used
    data.workspace_id = workspace_id
    try:
        return await communication_manager.create(data)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{workspace_id}/communications/{comm_id}", response_model=CommunicationResponse)
async def update_communication(workspace_id: str, comm_id: str, data: CommunicationUpdate):
    """Update an existing Communication.

    Requirement 23.8: PUT /api/workspaces/{id}/communications/{comm_id}.
    """
    result = await communication_manager.update(comm_id, data)
    if not result:
        raise HTTPException(status_code=404, detail=f"Communication {comm_id} not found")
    return result


@router.delete("/{workspace_id}/communications/{comm_id}")
async def delete_communication(workspace_id: str, comm_id: str):
    """Delete a Communication.

    Requirement 23.8: DELETE /api/workspaces/{id}/communications/{comm_id}.
    """
    deleted = await communication_manager.delete(comm_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Communication {comm_id} not found")
    return {"status": "deleted", "communication_id": comm_id}
