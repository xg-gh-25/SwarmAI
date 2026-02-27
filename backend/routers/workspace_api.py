"""FastAPI router for the SwarmWS single-workspace model.

This module provides the REST API endpoints for managing the singleton
SwarmWS workspace, its projects, and filesystem operations (folders,
files, renaming).  It is registered with a prefix in ``main.py``.

Public endpoints:

- ``GET  /workspace``              — Retrieve singleton workspace config
- ``PUT  /workspace``              — Update workspace config (icon, context)
- ``GET  /workspace/tree``         — Return workspace filesystem tree as nested JSON
- ``GET  /projects``               — List all projects
- ``POST /projects``               — Create a new project
- ``GET  /projects/{project_id}``  — Get project by ID
- ``PUT  /projects/{project_id}``  — Update project metadata
- ``DELETE /projects/{project_id}``— Delete a project
- ``POST /workspace/folders``      — Create a folder inside the workspace
- ``DELETE /workspace/folders``     — Delete a folder or file
- ``PUT  /workspace/rename``       — Rename / move an item

Helper functions:

- ``_should_include``      — Hidden-file filter (excludes dotfiles except .project.json)
- ``_compute_max_mtime``   — Recursive max mtime for ETag computation
- ``_build_tree``          — Recursive tree builder with depth bounding and sorting

Helper models (request bodies):

- ``FolderCreateRequest``  — ``path: str``
- ``FolderDeleteRequest``  — ``path: str``
- ``FolderRenameRequest``  — ``old_path: str``, ``new_path: str``
"""

import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from core.swarm_workspace_manager import swarm_workspace_manager
from core.context_snapshot_cache import context_cache
from database import db
from schemas.workspace_config import (
    TreeNodeResponse,
    WorkspaceConfigResponse,
    WorkspaceConfigUpdate,
)

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
# Workspace tree endpoint
# ─────────────────────────────────────────────────────────────────────────────


def _should_include(name: str) -> bool:
    """Return True if a file/directory name should appear in the tree.

    Excludes hidden entries (starting with ``'.'``) except ``.project.json``
    which carries project metadata.
    """
    if name == ".project.json":
        return True
    if name.startswith("."):
        return False
    return True


def _compute_max_mtime(root: Path, depth: int) -> float:
    """Recursively compute the maximum mtime across workspace entries.

    Walks the filesystem up to *depth* levels, applying the same hidden-file
    exclusion rules as the tree builder.  Returns the maximum ``st_mtime``
    found, or ``0.0`` if the root does not exist.
    """
    max_mtime: float = 0.0

    try:
        stat = root.stat()
        max_mtime = max(max_mtime, stat.st_mtime)
    except OSError:
        return 0.0

    if not root.is_dir() or depth <= 0:
        return max_mtime

    try:
        entries = list(root.iterdir())
    except OSError:
        return max_mtime

    for entry in entries:
        if not _should_include(entry.name):
            continue
        try:
            entry_stat = entry.stat()
            max_mtime = max(max_mtime, entry_stat.st_mtime)
        except OSError:
            continue
        if entry.is_dir() and depth > 1:
            child_mtime = _compute_max_mtime(entry, depth - 1)
            max_mtime = max(max_mtime, child_mtime)

    return max_mtime


def _build_tree(
    root: Path,
    workspace_root: Path,
    depth: int,
) -> list[dict]:
    """Build a nested tree of workspace entries.

    Walks *root* up to *depth* levels, excluding hidden entries (except
    ``.project.json``).  Directories are sorted before files; both groups
    are sorted alphabetically.

    Each node is a plain dict matching ``TreeNodeResponse`` fields so it
    can be serialised directly by FastAPI.
    """
    if depth <= 0:
        return []

    try:
        entries = list(root.iterdir())
    except OSError:
        return []

    # Partition into dirs and files, filtering hidden entries
    dirs: list[Path] = []
    files: list[Path] = []
    for entry in entries:
        if not _should_include(entry.name):
            continue
        if entry.is_dir():
            dirs.append(entry)
        else:
            files.append(entry)

    # Sort: directories first (alphabetically), then files (alphabetically)
    dirs.sort(key=lambda p: p.name.lower())
    files.sort(key=lambda p: p.name.lower())

    result: list[dict] = []

    for d in dirs:
        rel_path = str(d.relative_to(workspace_root)).replace("\\", "/")
        children = _build_tree(d, workspace_root, depth - 1) if depth > 1 else None
        result.append({
            "name": d.name,
            "path": rel_path,
            "type": "directory",
            "is_system_managed": swarm_workspace_manager.is_system_managed(rel_path),
            "children": children,
        })

    for f in files:
        rel_path = str(f.relative_to(workspace_root)).replace("\\", "/")
        result.append({
            "name": f.name,
            "path": rel_path,
            "type": "file",
            "is_system_managed": swarm_workspace_manager.is_system_managed(rel_path),
            "children": None,
        })

    return result


@router.get("/workspace/tree")
async def get_workspace_tree(
    depth: int = Query(default=4, ge=1, le=5),
    if_none_match: Optional[str] = Header(default=None),
) -> list[dict]:
    """Return the SwarmWS filesystem tree as nested JSON.

    Supports conditional requests via ETag / If-None-Match.
    Returns 304 Not Modified when the workspace tree has not changed.

    Walks the workspace root directory up to ``depth`` levels.
    Each node includes:

    - name: str (display name)
    - path: str (relative to workspace root)
    - type: ``"file"`` | ``"directory"``
    - is_system_managed: bool
    - children: list[node] (for directories, if expanded)

    System-managed items are annotated so the frontend can show
    lock badges and suppress delete/rename actions.

    Requirements: 10.1, 11.5, 15.1
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)

    if not workspace_root.is_dir():
        raise HTTPException(
            status_code=500,
            detail="Workspace root directory does not exist",
        )

    # Compute ETag from recursive max mtime
    max_mtime = _compute_max_mtime(workspace_root, depth)
    etag = hashlib.md5(f"{max_mtime}:{depth}".encode()).hexdigest()
    etag_value = f'"{etag}"'

    # Check conditional request
    if if_none_match and if_none_match.strip() == etag_value:
        return Response(status_code=304, headers={"ETag": etag_value})

    tree = _build_tree(workspace_root, workspace_root, depth)

    return Response(
        content=json.dumps(tree),
        media_type="application/json",
        headers={"ETag": etag_value},
    )



# ─────────────────────────────────────────────────────────────────────────────
# Project endpoints — REMOVED
# Legacy project CRUD endpoints have been extracted to the dedicated
# ``routers/projects.py`` router (registered separately in main.py).
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Folder / file operations
# ─────────────────────────────────────────────────────────────────────────────


async def create_folder(request: FolderCreateRequest):
    """Create a folder inside the workspace.

    Increments project_files_version for context cache invalidation
    when the path is under a project directory (Requirement 34.2).
    """
    expanded_path = await _get_workspace_path()
    target = _validate_relative_path(request.path, expanded_path)

    # Validate depth
    is_valid, error_msg = swarm_workspace_manager.validate_depth(request.path)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    target.mkdir(parents=True, exist_ok=True)

    # Increment project_files_version for context cache invalidation (Req 34.2)
    context_cache.increment_project_files_version()

    logger.info("Created folder: %s", request.path)
    return {"path": request.path}




async def delete_folder(request: FolderDeleteRequest):
    """Delete a folder or file inside the workspace.

    Increments project_files_version for context cache invalidation
    when project files are removed (Requirement 34.2).  Also increments
    memory_version when the deleted path is under Knowledge/Memory/.
    """
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

    # Increment version counters for context cache invalidation (Req 34.2)
    context_cache.increment_project_files_version()
    normalized = request.path.replace("\\", "/")
    if "Knowledge/Memory" in normalized or "Knowledge/Memory" in normalized.replace("\\", "/"):
        context_cache.increment_memory_version()

    logger.info("Deleted: %s", request.path)
    return Response(status_code=204)




async def rename_item(request: FolderRenameRequest):
    """Rename or move an item inside the workspace.

    Increments project_files_version for context cache invalidation
    when project files are renamed or moved (Requirement 34.2).
    """
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

    # Increment project_files_version for context cache invalidation (Req 34.2)
    context_cache.increment_project_files_version()

    logger.info("Renamed '%s' → '%s'", request.old_path, request.new_path)
    return {"old_path": request.old_path, "new_path": request.new_path}

