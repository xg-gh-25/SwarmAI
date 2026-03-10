"""FastAPI router for the SwarmWS single-workspace model.

This module provides the REST API endpoints for managing the singleton
SwarmWS workspace, its projects, and filesystem operations (folders,
files, renaming).  It is registered with a prefix in ``main.py``.

Public endpoints:

- ``GET  /workspace``              — Retrieve singleton workspace config
- ``PUT  /workspace``              — Update workspace config (icon, context)
- ``GET  /workspace/tree``         — Return workspace filesystem tree as nested JSON
- ``GET  /workspace/file/committed`` — Return last committed version of a file (git show HEAD:<path>)
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
- ``_get_git_status``      — Run ``git status --porcelain`` and return {path: status} dict
- ``_build_tree``          — Recursive tree builder with depth bounding, sorting, and git status
- ``_is_readonly_context_file`` — Check if a path is a readonly system-default context file

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
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

from core.context_directory_loader import CONTEXT_FILES
from core.swarm_workspace_manager import SYSTEM_MANAGED_FOLDERS, swarm_workspace_manager
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


# Internal directories that exist on disk but should not appear in the
# workspace explorer tree.  These are runtime/system data, not user content.
# .git is excluded because its internals are not useful to browse.
_HIDDEN_DIRS = frozenset({"chats", ".git"})


def _should_include(name: str) -> bool:
    """Return True if a file/directory name should appear in the tree.

    Shows all files and directories including dot-files (like Kiro IDE).
    Only excludes internal runtime directories listed in ``_HIDDEN_DIRS``.
    """
    if name in _HIDDEN_DIRS:
        return False
    return True


def _get_git_status(workspace_root: Path) -> dict[str, str]:
    """Run ``git status --porcelain -z`` and return a dict of {relative_path: status}.

    Uses ``-z`` for NUL-separated output to avoid quoting of paths with spaces
    or special characters.

    Status values match the GitStatus type on the frontend:
    - 'added', 'modified', 'deleted', 'renamed', 'untracked', 'conflicting'

    Returns an empty dict if the workspace is not a git repo or git fails.
    """
    git_dir = workspace_root / ".git"
    if not git_dir.is_dir():
        return {}

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-z", "-uall"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return {}
    except (OSError, subprocess.TimeoutExpired):
        return {}

    status_map: dict[str, str] = {}
    # -z output: entries separated by NUL, renames have two NUL-separated paths
    entries = result.stdout.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        if len(entry) < 4:
            i += 1
            continue

        xy = entry[:2]
        filepath = entry[3:]

        # Renames: the next NUL-separated entry is the destination path
        if xy[0] == "R" or xy[1] == "R":
            i += 1
            if i < len(entries):
                filepath = entries[i]  # use the destination (new) path

        # Normalize path separators
        filepath = filepath.replace("\\", "/")

        # Map git status codes to our GitStatus enum
        if "U" in xy or (xy[0] == "A" and xy[1] == "A") or (xy[0] == "D" and xy[1] == "D"):
            status_map[filepath] = "conflicting"
        elif xy[0] == "R" or xy[1] == "R":
            status_map[filepath] = "renamed"
        elif xy == "??":
            status_map[filepath] = "untracked"
        elif xy == "!!":
            status_map[filepath] = "ignored"
        elif "D" in xy:
            status_map[filepath] = "deleted"
        elif "A" in xy:
            status_map[filepath] = "added"
        elif "M" in xy or "T" in xy:
            status_map[filepath] = "modified"

        i += 1

    return status_map


def _build_tree(
    root: Path,
    workspace_root: Path,
    depth: int,
    git_status: dict[str, str] | None = None,
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
        children = _build_tree(d, workspace_root, depth - 1, git_status) if depth > 1 else None

        # Directory git status: check direct match first, then inherit from children
        dir_status = None
        if git_status:
            # Check if this directory itself has a git status entry (e.g., symlink flat-path)
            if rel_path in git_status:
                dir_status = git_status[rel_path]
            # Also check if any child file has a git status (prefix scan).
            # Note: if children have status, we upgrade to "modified" even if
            # the directory itself had a more specific status (e.g., "untracked").
            # This is intentional — "modified" is the correct aggregate indicator.
            prefix = rel_path + "/"
            for gpath, gstatus in git_status.items():
                if gpath.startswith(prefix):
                    dir_status = "modified"
                    break

        node: dict = {
            "name": d.name,
            "path": rel_path,
            "type": "directory",
            "children": children,
        }
        if dir_status:
            node["git_status"] = dir_status
        result.append(node)

    for f in files:
        rel_path = str(f.relative_to(workspace_root)).replace("\\", "/")
        node: dict = {
            "name": f.name,
            "path": rel_path,
            "type": "file",
            "children": None,
        }
        if git_status and rel_path in git_status:
            node["git_status"] = git_status[rel_path]
        result.append(node)

    return result


@router.get("/workspace/tree")
async def get_workspace_tree(
    depth: int = Query(default=8, ge=1, le=10),
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
    - children: list[node] (for directories, if expanded)

    All files are user-manageable — no lock badges or system-managed
    restrictions.

    Requirements: 10.1, 11.5, 15.1
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)

    if not workspace_root.is_dir():
        raise HTTPException(
            status_code=500,
            detail="Workspace root directory does not exist",
        )

    # Compute ETag from git status + filesystem structure
    # Git status captures modifications; file listing captures adds/deletes
    git_status = _get_git_status(workspace_root)
    git_hash = hashlib.md5(json.dumps(sorted(git_status.items())).encode()).hexdigest()

    # Quick filesystem fingerprint: sorted list of all entry names at each level
    # This changes when files are added, deleted, or renamed
    def _fs_fingerprint(root: Path, depth: int) -> str:
        if depth <= 0 or not root.is_dir():
            return ""
        try:
            names = sorted(e.name for e in root.iterdir() if _should_include(e.name))
        except OSError:
            return ""
        parts = [",".join(names)]
        for name in names:
            child = root / name
            if child.is_dir() and depth > 1:
                parts.append(f"{name}:{_fs_fingerprint(child, depth - 1)}")
        return "|".join(parts)

    fs_hash = hashlib.md5(_fs_fingerprint(workspace_root, depth).encode()).hexdigest()[:8]
    etag = hashlib.md5(f"{git_hash}:{fs_hash}:{depth}".encode()).hexdigest()
    etag_value = f'"{etag}"'

    # Check conditional request
    if if_none_match and if_none_match.strip() == etag_value:
        return Response(status_code=304, headers={"ETag": etag_value})

    tree = _build_tree(workspace_root, workspace_root, depth, git_status)

    return Response(
        content=json.dumps(tree),
        media_type="application/json",
        headers={"ETag": etag_value},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Workspace file content endpoint
# ─────────────────────────────────────────────────────────────────────────────


def _is_readonly_context_file(relative_path: str) -> bool:
    """Check if a file path corresponds to a readonly system-default context file.

    Only applies to files in the ``.context/`` directory.  Returns ``True``
    when the file matches a ``ContextFileSpec`` with ``user_customized=False``
    (system default → readonly).  Returns ``False`` for all other files,
    including user-customized context files and non-context files.

    Falls back to ``False`` on any error (permissive default per Req 9.4).
    """
    try:
        normalized = relative_path.replace("\\", "/")
        if not normalized.startswith(".context/"):
            return False
        filename = normalized.split("/")[-1]
        for spec in CONTEXT_FILES:
            if spec.filename == filename and not spec.user_customized:
                return True
        return False
    except Exception:
        return False


@router.get("/workspace/file")
async def get_workspace_file(
    path: str = Query(..., description="Relative path within the workspace"),
):
    """Read a file's text content by its workspace-relative path.

    Used by the explorer's file editor modal to open files for viewing
    and editing.  The ``path`` parameter is the same relative path
    returned by ``GET /workspace/tree``.

    Returns ``{ "content": "<utf-8 text>" }`` on success.
    Returns 404 if the file does not exist or is outside the workspace.
    Returns 400 if the path attempts directory traversal.
    """
    # Reject obvious traversal attempts
    if ".." in path.split("/"):
        raise HTTPException(status_code=400, detail=f"Path traversal not allowed: {path}")

    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target = (workspace_root / path).resolve()

    # Ensure resolved path is still under workspace root.
    # Exception: .claude/skills/ contains projected skill files that may
    # resolve outside the workspace for legacy symlinks. After copytree
    # migration, skill files are real files inside the workspace, but we
    # keep this escape hatch for backward compatibility with any remaining
    # legacy symlinks. Allow reading these as read-only, but ONLY if the
    # path originates from within the workspace and the resolved target is
    # a regular file (not a directory or special file).
    ws_resolved = str(workspace_root.resolve())
    is_skill_file = path.startswith(".claude/skills/") or path.startswith(".claude\\skills\\")
    if not str(target).startswith(ws_resolved):
        if not is_skill_file:
            raise HTTPException(status_code=400, detail=f"Path outside workspace: {path}")
        # Skill file escape hatch: handles both legacy symlinks and
        # copytree'd files that may resolve outside the workspace.
        skill_path = (workspace_root / path)
        if not skill_path.exists():
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        if not str(skill_path.parent.resolve()).startswith(ws_resolved):
            raise HTTPException(status_code=400, detail=f"Path outside workspace: {path}")
        # Only allow regular files (not directories, devices, etc.)
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"Not a regular file: {path}")

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not valid UTF-8 text")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {exc}")

    # Projected skill files are always readonly (managed by the system)
    is_readonly = _is_readonly_context_file(path) or is_skill_file
    return {"content": content, "path": path, "name": target.name, "readonly": is_readonly}


@router.get("/workspace/file/committed")
async def get_workspace_file_committed(
    path: str = Query(..., description="Relative path within the workspace"),
):
    """Return the last committed version of a file via ``git show HEAD:<path>``.

    Used by the file editor modal to compute diffs between the committed
    version and the current disk content for files with git changes.

    Returns ``{"content": "<committed text>"}`` for tracked files.
    Returns ``{"content": ""}`` for untracked files (no committed version).
    Returns 400 for binary files or path traversal attempts.
    Returns 404 if the file doesn't exist in the workspace.
    """
    # Reject traversal attempts
    if ".." in path.split("/"):
        raise HTTPException(status_code=400, detail=f"Path traversal not allowed: {path}")

    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)

    # Verify the file exists on disk
    target = (workspace_root / path).resolve()
    ws_resolved = str(workspace_root.resolve())
    is_skill_file = path.startswith(".claude/skills/") or path.startswith(".claude\\skills\\")
    if not str(target).startswith(ws_resolved) and not is_skill_file:
        raise HTTPException(status_code=400, detail=f"Path outside workspace: {path}")

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Check if workspace is a git repo
    git_dir = workspace_root / ".git"
    if not git_dir.is_dir():
        return {"content": ""}

    try:
        result = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            cwd=str(workspace_root),
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"content": ""}

    if result.returncode != 0:
        # File is untracked or not in HEAD — return empty string
        return {"content": ""}

    # Decode manually to catch binary files
    try:
        content = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not valid UTF-8 text (binary file)")

    return {"content": content}


@router.put("/workspace/file")
async def put_workspace_file(
    path: str = Query(..., description="Relative path within the workspace"),
    body: dict = None,
):
    """Write text content to a file by its workspace-relative path.

    Used by the explorer's file editor modal to save edited files.
    Expects ``{ "content": "<utf-8 text>" }`` in the request body.
    """
    if body is None or "content" not in body:
        raise HTTPException(status_code=400, detail="Request body must include 'content'")

    if ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    # Skill files are read-only (managed by ProjectionLayer, overwritten on each launch)
    if path.startswith(".claude/skills/") or path.startswith(".claude\\skills\\"):
        raise HTTPException(status_code=403, detail="Skill files are read-only")

    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target = (workspace_root / path).resolve()

    if not str(target).startswith(str(workspace_root.resolve())):
        raise HTTPException(status_code=400, detail="Path outside workspace")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body["content"], encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {exc}")

    return {"success": True, "path": path}


# ─────────────────────────────────────────────────────────────────────────────
# Project endpoints — REMOVED
# Legacy project CRUD endpoints have been extracted to the dedicated
# ``routers/projects.py`` router (registered separately in main.py).
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Folder / file operations
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/workspace/folders")
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

    logger.info("Created folder: %s", request.path)
    return {"path": request.path}




@router.delete("/workspace/folders", status_code=204)
async def delete_folder(request: FolderDeleteRequest):
    """Delete a folder or file inside the workspace.

    Returns HTTP 403 if the target is a system-managed directory
    (Requirement 12.9).
    """
    expanded_path = await _get_workspace_path()
    target = _validate_relative_path(request.path, expanded_path)

    # Reject delete on system-managed folders (Req 12.9)
    rel_path = request.path.replace("\\", "/").strip("/")
    if rel_path in SYSTEM_MANAGED_FOLDERS:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot delete/rename system-managed directory: {rel_path}",
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
    """Rename or move an item inside the workspace.

    Increments project_files_version for context cache invalidation
    when project files are renamed or moved (Requirement 34.2).

    Returns HTTP 403 if the source is a system-managed directory
    (Requirement 12.9).
    """
    expanded_path = await _get_workspace_path()

    # Reject rename on system-managed folders (Req 12.9)
    normalized_old = request.old_path.replace("\\", "/").strip("/")
    if normalized_old in SYSTEM_MANAGED_FOLDERS:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot delete/rename system-managed directory: {normalized_old}",
        )

    old_target = _validate_relative_path(request.old_path, expanded_path)
    new_target = _validate_relative_path(request.new_path, expanded_path)

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

    logger.info("Renamed '%s' → '%s'", request.old_path, request.new_path)
    return {"old_path": request.old_path, "new_path": request.new_path}

