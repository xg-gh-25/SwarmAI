"""Dedicated REST API router for project CRUD and history endpoints.

Extracted from ``workspace_api.py`` to provide a focused, single-responsibility
router for project lifecycle management.  All endpoints use the project UUID
as the path parameter; human-readable name lookup is available via query param.

Public endpoints:

- ``POST /projects``                  — Create a new project (201)
- ``GET  /projects``                  — List all projects; ``?name=`` for lookup
- ``GET  /projects/{project_id}``     — Get project by UUID (404 if missing)
- ``PUT  /projects/{project_id}``     — Update project metadata
- ``DELETE /projects/{project_id}``   — Delete project (204)
- ``GET  /projects/{project_id}/history`` — Project update history array

Error mapping:

- ``ValueError``       → 404 (not found) or 409 (duplicate name)
- Pydantic validation  → 422 (automatic via FastAPI)
- ``OSError``          → 500 (filesystem failure)
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from core.swarm_workspace_manager import swarm_workspace_manager
from database import db
from schemas.project import (
    ProjectCreate,
    ProjectHistoryResponse,
    ProjectResponse,
    ProjectUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

async def _get_workspace_path() -> str:
    """Resolve the active workspace path from the database config.

    Returns:
        Expanded absolute path to the workspace root.

    Raises:
        HTTPException: 500 if workspace is not configured.
    """
    config = await db.workspace_config.get_config()
    if config is None or "file_path" not in config:
        raise HTTPException(status_code=500, detail="Workspace not configured")
    return swarm_workspace_manager.expand_path(config["file_path"])


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(request: ProjectCreate):
    """Create a new project in the workspace.

    Returns 201 with the full project metadata on success.
    Returns 409 if a project with the same name already exists.
    """
    expanded_path = await _get_workspace_path()
    try:
        metadata = await swarm_workspace_manager.create_project(
            request.name, expanded_path
        )
    except ValueError as exc:
        detail = str(exc)
        if "already exists" in detail.lower():
            raise HTTPException(status_code=409, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    except OSError as exc:
        logger.error("Filesystem error creating project '%s': %s", request.name, exc)
        raise HTTPException(status_code=500, detail="Failed to create project")

    logger.info("Created project '%s'", request.name)
    return ProjectResponse(**metadata)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(name: Optional[str] = Query(None, description="Filter by project name")):
    """List all projects, or look up a single project by name.

    When ``?name=`` is provided, returns a list with at most one matching
    project (empty list if no match).  Without the query param, returns
    all projects sorted by ``created_at`` descending.
    """
    expanded_path = await _get_workspace_path()

    if name is not None:
        try:
            project = await swarm_workspace_manager.get_project_by_name(
                name, expanded_path
            )
            return [ProjectResponse(**project)]
        except ValueError:
            return []
        except OSError as exc:
            logger.error("Filesystem error looking up project '%s': %s", name, exc)
            raise HTTPException(status_code=500, detail="Failed to look up project")

    try:
        projects = await swarm_workspace_manager.list_projects(expanded_path)
    except OSError as exc:
        logger.error("Filesystem error listing projects: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to list projects")

    return [ProjectResponse(**p) for p in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str):
    """Get a project by its UUID.

    Returns 404 if no project matches the given ID.
    """
    expanded_path = await _get_workspace_path()
    try:
        metadata = await swarm_workspace_manager.get_project(
            project_id, expanded_path
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Project not found")
    except OSError as exc:
        logger.error("Filesystem error reading project '%s': %s", project_id, exc)
        raise HTTPException(status_code=500, detail="Failed to read project")

    return ProjectResponse(**metadata)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, request: ProjectUpdate):
    """Update an existing project's metadata.

    Uses ``exclude_unset=True`` so only fields the client explicitly sent
    are included — this correctly handles nullable fields like ``priority``
    (sending ``null`` clears it; omitting it leaves it unchanged).

    Returns 404 if the project is not found.
    Returns 409 if a name change conflicts with an existing project.
    """
    expanded_path = await _get_workspace_path()

    # Only include fields explicitly sent by the client.
    # This correctly handles nullable fields like priority — sending
    # {"priority": null} will clear it, while omitting priority leaves it unchanged.
    updates = request.model_dump(exclude_unset=True)

    if not updates:
        # Nothing to update — return current state
        try:
            metadata = await swarm_workspace_manager.get_project(
                project_id, expanded_path
            )
        except ValueError:
            raise HTTPException(status_code=404, detail="Project not found")
        return ProjectResponse(**metadata)

    try:
        metadata = await swarm_workspace_manager.update_project(
            project_id, updates, source="user", workspace_path=expanded_path
        )
    except ValueError as exc:
        detail = str(exc)
        if "already exists" in detail.lower():
            raise HTTPException(status_code=409, detail=detail)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    except OSError as exc:
        logger.error("Filesystem error updating project '%s': %s", project_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update project")

    logger.info("Updated project id: %s", project_id)
    return ProjectResponse(**metadata)


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str):
    """Delete a project by its UUID.

    Returns 204 on success, 404 if the project is not found.
    """
    expanded_path = await _get_workspace_path()
    try:
        await swarm_workspace_manager.delete_project(project_id, expanded_path)
    except ValueError:
        raise HTTPException(status_code=404, detail="Project not found")
    except OSError as exc:
        logger.error("Filesystem error deleting project '%s': %s", project_id, exc)
        raise HTTPException(status_code=500, detail="Failed to delete project")

    logger.info("Deleted project id: %s", project_id)
    return Response(status_code=204)


@router.get("/{project_id}/history", response_model=ProjectHistoryResponse)
async def get_project_history(project_id: str):
    """Get the update history for a project.

    Returns the ``update_history`` array from ``.project.json``.
    Returns 404 if the project is not found.
    """
    expanded_path = await _get_workspace_path()
    try:
        history = await swarm_workspace_manager.get_project_history(
            project_id, expanded_path
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Project not found")
    except OSError as exc:
        logger.error("Filesystem error reading history for '%s': %s", project_id, exc)
        raise HTTPException(status_code=500, detail="Failed to read project history")

    return ProjectHistoryResponse(project_id=project_id, history=history)
