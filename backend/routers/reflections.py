"""Reflection API endpoints for the Reflection section of the Daily Work Operating Loop.

This module provides CRUD endpoints for Reflection entities, which represent
structured review items capturing progress, insights, and lessons learned.
Reflections use hybrid storage: content stored as markdown files in the
filesystem under Artifacts/Reports/, metadata tracked in the database.

Requirements: 28.9
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from schemas.reflection import (
    ReflectionCreate,
    ReflectionUpdate,
    ReflectionResponse,
    ReflectionType,
)
from core.reflection_manager import reflection_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{workspace_id}/reflections", response_model=list[ReflectionResponse])
async def list_reflections(
    workspace_id: str,
    reflection_type: Optional[ReflectionType] = Query(None, description="Filter by reflection type"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """List Reflections for a workspace with optional filtering and pagination.

    Requirement 28.9: GET /api/workspaces/{id}/reflections with filters.
    """
    return await reflection_manager.list(
        workspace_id=workspace_id,
        reflection_type=reflection_type,
        limit=limit,
        offset=offset,
    )


@router.post("/{workspace_id}/reflections", response_model=ReflectionResponse, status_code=201)
async def create_reflection(workspace_id: str, data: ReflectionCreate):
    """Create a new Reflection in the specified workspace.

    Requirement 28.9: POST /api/workspaces/{id}/reflections.
    Creates the reflection metadata in the database and writes content
    to the filesystem under Artifacts/Reports/ folder.
    """
    data.workspace_id = workspace_id
    try:
        return await reflection_manager.create(data)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{workspace_id}/reflections/{reflection_id}", response_model=ReflectionResponse)
async def update_reflection(
    workspace_id: str,
    reflection_id: str,
    data: ReflectionUpdate,
):
    """Update an existing Reflection.

    Requirement 28.9: PUT /api/workspaces/{id}/reflections/{reflection_id}.
    """
    result = await reflection_manager.update(reflection_id, data)
    if not result:
        raise HTTPException(status_code=404, detail=f"Reflection {reflection_id} not found")
    return result


@router.delete("/{workspace_id}/reflections/{reflection_id}")
async def delete_reflection(workspace_id: str, reflection_id: str):
    """Delete a Reflection.

    Requirement 28.9: DELETE /api/workspaces/{id}/reflections/{reflection_id}.
    """
    deleted = await reflection_manager.delete(reflection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Reflection {reflection_id} not found")
    return {"status": "deleted", "reflection_id": reflection_id}
