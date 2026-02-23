"""Artifact API endpoints for the Artifacts section of the Daily Work Operating Loop.

This module provides CRUD endpoints for Artifact entities, which represent
durable knowledge outputs produced from task execution. Artifacts use hybrid
storage: content stored as files in filesystem, metadata tracked in database.

Requirements: 27.8
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from schemas.artifact import (
    ArtifactCreate,
    ArtifactUpdate,
    ArtifactResponse,
    ArtifactType,
)
from core.artifact_manager import artifact_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{workspace_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(
    workspace_id: str,
    artifact_type: Optional[ArtifactType] = Query(None, description="Filter by artifact type"),
    limit: int = Query(50, ge=1, le=200, description="Maximum number of results"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
):
    """List Artifacts for a workspace with optional filtering and pagination.

    Requirement 27.8: GET /api/workspaces/{id}/artifacts with filters.
    """
    return await artifact_manager.list(
        workspace_id=workspace_id,
        artifact_type=artifact_type,
        limit=limit,
        offset=offset,
    )


@router.post("/{workspace_id}/artifacts", response_model=ArtifactResponse, status_code=201)
async def create_artifact(workspace_id: str, data: ArtifactCreate):
    """Create a new Artifact in the specified workspace.

    Requirement 27.8: POST /api/workspaces/{id}/artifacts.
    Creates the artifact metadata in the database and writes content
    to the filesystem under Artifacts/{type}/ folder.
    """
    # Ensure workspace_id from path is used
    data.workspace_id = workspace_id
    try:
        return await artifact_manager.create(data)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/{workspace_id}/artifacts/{artifact_id}", response_model=ArtifactResponse)
async def update_artifact(
    workspace_id: str,
    artifact_id: str,
    data: ArtifactUpdate,
):
    """Update an existing Artifact, optionally creating a new version.

    Requirement 27.8: PUT /api/workspaces/{id}/artifacts/{artifact_id}.
    When content changes are provided via the update, a new versioned file
    is created while preserving the previous version.
    """
    result = await artifact_manager.update(artifact_id, data)
    if not result:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
    return result


@router.delete("/{workspace_id}/artifacts/{artifact_id}")
async def delete_artifact(workspace_id: str, artifact_id: str):
    """Delete an Artifact.

    Requirement 27.8: DELETE /api/workspaces/{id}/artifacts/{artifact_id}.
    """
    deleted = await artifact_manager.delete(artifact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Artifact {artifact_id} not found")
    return {"status": "deleted", "artifact_id": artifact_id}
