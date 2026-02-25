"""FastAPI router for the SwarmWS single-workspace model.

This module provides the REST API endpoints for managing the singleton
SwarmWS workspace, its projects, and filesystem operations (folders,
files, renaming).  It is registered with a prefix in ``main.py``.

Public endpoints:

- ``GET  /workspace``              — Retrieve singleton workspace config
- ``PUT  /workspace``              — Update workspace config (icon, context)
- ``GET  /projects``               — List all projects
- ``POST /projects``               — Create a new project
- ``GET  /projects/{project_id}``  — Get project by ID
- ``PUT  /projects/{project_id}``  — Update project metadata
- ``DELETE /projects/{project_id}``— Delete a project
- ``POST /workspace/folders``      — Create a folder inside the workspace
- ``DELETE /workspace/folders``     — Delete a folder or file
- ``PUT  /workspace/rename``       — Rename / move an item

Helper models (request bodies):

- ``FolderCreateRequest``  — ``path: str``
- ``FolderDeleteRequest``  — ``path: str``
- ``FolderRenameRequest``  — ``old_path: str``, ``new_path: str``
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from core.swarm_workspace_manager import swarm_workspace_manager
from database import db
from schemas.workspace_config import WorkspaceConfigResponse, WorkspaceConfigUpdate

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workspace-api"])


# ─────────────────────────────────────────────────────────────────────────────
# Request body models for folder operations
# ─────────────────────────────────────────────────────────────────────────────

class FolderCreateRequest(BaseModel):
    """Request body for creating a folder."""
    path: str


class FolderDeleteRequest(BaseModel):
    """Request body for deleting a folder or file."""
    path: str


class FolderRenameRequest(BaseModel):
    """Request body for renaming / moving an item."""
    old_path: str
    new_path: str


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

async def _get_workspace_path() -> str:
    """Return the expanded absolute workspace root path.

    Reads the singleton workspace config from the database and expands
    any path placeholders.

    Returns:
        Expanded absolute path to the workspace root.

    Raises:
        HTTPException: 404 if no workspace config exists.
    """
    config = await db.workspace_config.get_config()
    if config is None:
        raise HTTPException(status_code=404, detail="Workspace not configured")
    return swarm_workspace_manager.expand_path(config["file_path"])

def _validate_relative_path(relative_path: str, workspace_root: str) -> Path:
    """Validate that a relative path resolves within the workspace root.

    Prevents path traversal attacks by resolving the full path and
    verifying it stays under the workspace root.

    Args:
        relative_path: User-supplied relative path.
        workspace_root: Expanded absolute workspace root.

    Returns:
        The resolved absolute Path.

    Raises:
        HTTPException: 400 if path is empty, contains traversal, or escapes root.
    """
    stripped = relative_path.strip("/").replace("\\", "/")
    if not stripped:
        raise HTTPException(status_code=400, detail="Path cannot be empty")

    resolved = (Path(workspace_root) / stripped).resolve()
    root_resolved = Path(workspace_root).resolve()

    if not resolved.is_relative_to(root_resolved):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    return resolved



# ─────────────────────────────────────────────────────────────────────────────
# Workspace config endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/workspace", response_model=WorkspaceConfigResponse)
async def get_workspace():
    """Get the singleton workspace configuration."""
    config = await db.workspace_config.get_config()
    if config is None:
        raise HTTPException(status_code=404, detail="Workspace not configured")

    config["file_path"] = swarm_workspace_manager.expand_path(config["file_path"])
    return WorkspaceConfigResponse(**config)



@router.put("/workspace", response_model=WorkspaceConfigResponse)
async def update_workspace(request: WorkspaceConfigUpdate):
    """Update the singleton workspace configuration (icon, context)."""
    updates: dict = {}
    if request.icon is not None:
        updates["icon"] = request.icon
    if request.context is not None:
        updates["context"] = request.context

    if not updates:
        # Nothing to update — return current config
        config = await db.workspace_config.get_config()
        if config is None:
            raise HTTPException(status_code=404, detail="Workspace not configured")
        config["file_path"] = swarm_workspace_manager.expand_path(config["file_path"])
        return WorkspaceConfigResponse(**config)

    result = await db.workspace_config.update_config(updates)
    if result is None:
        raise HTTPException(status_code=404, detail="Workspace not configured")

    result["file_path"] = swarm_workspace_manager.expand_path(result["file_path"])
    return WorkspaceConfigResponse(**result)



# ─────────────────────────────────────────────────────────────────────────────
# Project endpoints — REMOVED
# Legacy project CRUD endpoints have been extracted to the dedicated
# ``routers/projects.py`` router (registered separately in main.py).
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Folder / file operations
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/workspace/folders", status_code=201)
async def create_folder(request: FolderCreateRequest):
    """Create a folder inside the workspace."""
    expanded_path = await _get_workspace_path()
    target = _validate_relative_path(request.path, expanded_path)

    # Validate depth
    is_valid, error_msg = swarm_workspace_manager.validate_depth(request.path)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    target.mkdir(parents=True, exist_ok=True)

    logger.info("Created folder: %s", request.path)
    return {"path": request.path}




@router.delete("/workspace/folders", status_code=204)
async def delete_folder(request: FolderDeleteRequest):
    """Delete a folder or file inside the workspace."""
    expanded_path = await _get_workspace_path()
    target = _validate_relative_path(request.path, expanded_path)

    if swarm_workspace_manager.is_system_managed(request.path):
        raise HTTPException(
            status_code=403, detail="Cannot delete system-managed item"
        )

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")

    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()

    logger.info("Deleted: %s", request.path)
    return Response(status_code=204)




@router.put("/workspace/rename")
async def rename_item(request: FolderRenameRequest):
    """Rename or move an item inside the workspace."""
    expanded_path = await _get_workspace_path()
    old_target = _validate_relative_path(request.old_path, expanded_path)
    new_target = _validate_relative_path(request.new_path, expanded_path)

    if swarm_workspace_manager.is_system_managed(request.old_path):
        raise HTTPException(
            status_code=403, detail="Cannot rename system-managed item"
        )

    if swarm_workspace_manager.is_system_managed(request.new_path):
        raise HTTPException(
            status_code=403, detail="Cannot overwrite system-managed item"
        )

    if not old_target.exists():
        raise HTTPException(status_code=404, detail="Source path not found")

    # If the destination is a directory path, validate depth
    if new_target.suffix == "" or old_target.is_dir():
        is_valid, error_msg = swarm_workspace_manager.validate_depth(request.new_path)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)

    new_target.parent.mkdir(parents=True, exist_ok=True)
    old_target.rename(new_target)

    logger.info("Renamed '%s' → '%s'", request.old_path, request.new_path)
    return {"old_path": request.old_path, "new_path": request.new_path}

