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

import base64
import hashlib
import json
import logging
import asyncio
import mimetypes
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from core.context_directory_loader import CONTEXT_FILES
from core.swarm_workspace_manager import SYSTEM_MANAGED_FOLDERS, swarm_workspace_manager
from database import db
from utils.diff_parser import parse_unified_diff, format_human_summary
from schemas.workspace_config import (
    TreeNodeResponse,
    WorkspaceConfigResponse,
    WorkspaceConfigUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workspace-api"])

MAX_PREVIEW_SIZE = 50 * 1024 * 1024  # 50 MB


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

# Root-level files that are infrastructure/tooling artifacts — hidden from the
# explorer tree but fully functional on disk (git-tracked, used by tools, etc.).
_HIDDEN_ROOT_FILES = frozenset({
    ".gitignore",
    ".legacy_cleaned",
    "package-lock.json",
    "package.json",
})


def _should_include(name: str, *, is_root: bool = False) -> bool:
    """Return True if a file/directory name should appear in the tree.

    Shows all files and directories including dot-files (like Kiro IDE).
    Only excludes internal runtime directories listed in ``_HIDDEN_DIRS``
    and infrastructure files at the workspace root listed in ``_HIDDEN_ROOT_FILES``.
    """
    if name in _HIDDEN_DIRS:
        return False
    if is_root and name in _HIDDEN_ROOT_FILES:
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
    is_root = root == workspace_root
    dirs: list[Path] = []
    files: list[Path] = []
    for entry in entries:
        if not _should_include(entry.name, is_root=is_root):
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

        # Detect symlinked directories that are separate git repos.
        # The parent workspace's git status won't cover files inside these —
        # we need to run git status from the symlink target's own repo root.
        child_git_status = git_status
        resolved = d.resolve()
        git_path = resolved / ".git"
        if d.is_symlink() and (git_path.is_dir() or git_path.is_file()):
            sub_status = _get_git_status(resolved)
            # Re-key sub-repo paths relative to the workspace root
            child_git_status = dict(git_status) if git_status else {}
            for sub_path, sub_st in sub_status.items():
                child_git_status[f"{rel_path}/{sub_path}"] = sub_st

        children = _build_tree(d, workspace_root, depth - 1, child_git_status) if depth > 1 else None

        # Directory git status: check direct match first, then inherit from children
        dir_status = None
        effective_status = child_git_status or git_status
        if effective_status:
            # Check if this directory itself has a git status entry (e.g., symlink flat-path)
            if rel_path in effective_status:
                dir_status = effective_status[rel_path]
            # Also check if any child file has a git status (prefix scan).
            # Note: if children have status, we upgrade to "modified" even if
            # the directory itself had a more specific status (e.g., "untracked").
            # This is intentional — "modified" is the correct aggregate indicator.
            prefix = rel_path + "/"
            for gpath, gstatus in effective_status.items():
                if gpath.startswith(prefix):
                    dir_status = "modified"
                    break

        node: dict = {
            "name": d.name,
            "path": rel_path,
            "type": "directory",
            "children": children,
        }
        if d.is_symlink():
            node["is_symlink"] = True
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


def _collect_subrepo_status(
    entry: Path, rel_prefix: str, items: list[tuple[str, str]]
) -> None:
    """If *entry* is a symlink pointing to a separate git repo, run git status
    on that repo and append results (re-keyed under *rel_prefix*) to *items*.

    Handles both standard repos (.git is a directory) and worktrees (.git is
    a file containing ``gitdir: ...``).
    """
    if entry.is_symlink() and entry.is_dir():
        try:
            resolved = entry.resolve()
            git_path = resolved / ".git"
            if git_path.is_dir() or git_path.is_file():
                sub_status = _get_git_status(resolved)
                for sp, ss in sub_status.items():
                    items.append((f"{rel_prefix}/{sp}", ss))
        except OSError:
            pass


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

    # Also include git status from symlinked sub-repos (e.g., Projects/AIDLC)
    # so ETag changes when files in those repos are modified.
    # Scans up to 2 levels (root and one level down, e.g., Projects/*).
    all_status_items = list(git_status.items())
    try:
        for entry in workspace_root.iterdir():
            _collect_subrepo_status(entry, entry.name, all_status_items)
            if entry.is_dir():
                try:
                    for child in entry.iterdir():
                        _collect_subrepo_status(child, f"{entry.name}/{child.name}", all_status_items)
                except OSError:
                    pass
    except OSError:
        pass
    git_hash = hashlib.md5(json.dumps(sorted(all_status_items)).encode()).hexdigest()

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


def _is_path_under(child: Path, parent: Path) -> bool:
    """Return True if *child* is equal to or a descendant of *parent*.

    Uses ``Path.parts`` comparison instead of ``str().startswith()`` to avoid
    prefix-collision attacks (e.g., ``/workspace-evil`` matching ``/workspace``).
    Both paths should be resolved before calling.
    """
    child_parts = child.resolve().parts
    parent_parts = parent.resolve().parts
    return child_parts[: len(parent_parts)] == parent_parts


def _is_symlink_traversal(workspace_root: Path, relative_path: str) -> bool:
    """Return True if *relative_path* reaches outside the workspace through a
    symlink that itself lives inside the workspace (e.g., Projects/SwarmAI/...).

    **Security model — write-through-symlinks:**
    This function intentionally allows reads/writes to files outside the
    workspace IF reached through a trusted symlink (e.g.,
    ``Projects/SwarmAI → ~/Desktop/SwarmAI-Workspace/swarmai``).  This is a
    deliberate security surface expansion required for the project-linking
    feature (``s_project-manager``).  The trust boundary is:

    1. The symlink itself must live inside the workspace (not injected from outside).
    2. The final resolved target must be a descendant of the symlink's resolved
       target — i.e., you can't use the symlink to escape *above* the linked
       directory via ``..`` segments that survive after resolution.
    3. Only the first symlink hop is trusted; nested symlinks inside the target
       are not given additional escape privileges.
    """
    parts = Path(relative_path).parts
    ws_resolved = workspace_root.resolve()
    for i in range(1, len(parts)):
        ancestor = workspace_root / Path(*parts[:i])
        if ancestor.is_symlink():
            # The symlink itself must be inside the workspace
            symlink_parent = ancestor.parent.resolve()
            if not _is_path_under(symlink_parent, ws_resolved):
                return False
            # The final target must be under the symlink's resolved root
            symlink_target = ancestor.resolve()
            full_target = (workspace_root / relative_path).resolve()
            if not _is_path_under(full_target, symlink_target):
                return False
            return True
    return False


@router.get("/workspace/file")
async def get_workspace_file(
    path: str = Query(..., description="Relative path within the workspace"),
):
    """Read a file's content by its workspace-relative path.

    Used by the explorer's file editor modal and binary preview modal.
    The ``path`` parameter is the same relative path returned by
    ``GET /workspace/tree``.

    Returns ``{ "content": "...", "encoding": "utf-8" }`` for text files.
    Returns ``{ "content": "<base64>", "encoding": "base64", "mime_type": "...", "size": N }`` for binary files.
    Returns 404 if the file does not exist or is outside the workspace.
    Returns 400 if the path attempts directory traversal.
    Returns 413 if the file exceeds 50 MB.
    """
    # Reject obvious traversal attempts
    if ".." in path.split("/"):
        raise HTTPException(status_code=400, detail=f"Path traversal not allowed: {path}")

    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target = (workspace_root / path).resolve()

    # Ensure resolved path is still under workspace root OR reached via a
    # symlink that lives inside the workspace (e.g., Projects/SwarmAI → ...).
    if not _is_path_under(target, workspace_root):
        if not _is_symlink_traversal(workspace_root, path):
            raise HTTPException(status_code=400, detail=f"Path outside workspace: {path}")
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"Not a regular file: {path}")

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Check file size BEFORE reading (prevents loading huge files into memory)
    file_size = target.stat().st_size
    if file_size > MAX_PREVIEW_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large to preview ({file_size // (1024 * 1024)} MB). Maximum is 50 MB.",
        )

    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Binary fallback: base64 encode
        logger.info("Binary file fallback for %s (size=%d, not valid UTF-8)", path, file_size)
        raw = target.read_bytes()
        mime_type, _ = mimetypes.guess_type(target.name)
        if mime_type is None:
            mime_type = "application/octet-stream"
        return {
            "content": base64.b64encode(raw).decode("ascii"),
            "path": path,
            "name": target.name,
            "encoding": "base64",
            "mime_type": mime_type,
            "size": file_size,
        }
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {exc}")

    # Projected skill files and context files are always readonly
    path_parts = Path(path).parts
    is_skill_file = len(path_parts) >= 2 and path_parts[0] == ".claude" and path_parts[1] == "skills"
    is_readonly = _is_readonly_context_file(path) or is_skill_file
    return {
        "content": content,
        "path": path,
        "name": target.name,
        "readonly": is_readonly,
        "encoding": "utf-8",
    }


@router.get("/workspace/file/raw")
async def get_workspace_file_raw(
    path: str = Query(..., description="Relative path within the workspace"),
):
    """Serve a workspace file as raw binary with proper Content-Type.

    Used by the markdown preview to render local images directly via
    ``<img src="http://localhost:{port}/api/workspace/file/raw?path=...">``.
    """
    if ".." in path.split("/"):
        raise HTTPException(status_code=400, detail=f"Path traversal not allowed: {path}")

    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target = (workspace_root / path).resolve()

    if not _is_path_under(target, workspace_root):
        if not _is_symlink_traversal(workspace_root, path):
            raise HTTPException(status_code=400, detail=f"Path outside workspace: {path}")
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"Not a regular file: {path}")

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Limit to 50 MB
    file_size = target.stat().st_size
    if file_size > MAX_PREVIEW_SIZE:
        raise HTTPException(status_code=413, detail="File too large")

    mime_type, _ = mimetypes.guess_type(target.name)
    return FileResponse(target, media_type=mime_type or "application/octet-stream")


@router.get("/workspace/file/diff")
async def get_workspace_file_diff(
    path: str = Query(..., description="Relative path within the workspace"),
):
    """Return a structured diff summary of uncommitted changes for a file.

    Used by the file editor panel's auto-diff feature (L2) to inject an
    edit summary into the chat input after saving. Runs ``git diff`` on the
    file and parses the output into hunks with section-aware descriptions.

    Returns ``{"path": ..., "hunks": [...], "summary": "...", "raw_diff": "..."}``.
    """
    if ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="Path traversal not allowed")

    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target = (workspace_root / path).resolve()

    if not _is_path_under(target, workspace_root):
        if not _is_symlink_traversal(workspace_root, path):
            raise HTTPException(status_code=400, detail="Path outside workspace")

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    git_dir = workspace_root / ".git"
    if not git_dir.is_dir():
        return {"path": path, "hunks": [], "summary": "", "raw_diff": ""}

    try:
        result = subprocess.run(
            ["git", "diff", "--unified=3", "--", path],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"path": path, "hunks": [], "summary": "", "raw_diff": ""}

    raw_diff = result.stdout or ""

    hunks = parse_unified_diff(raw_diff)

    # Read current file content for section-aware summary
    try:
        file_content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        file_content = ""

    summary = format_human_summary(hunks, file_content)

    # Serialize hunks to dicts for JSON response
    hunk_dicts = [
        {
            "old_start": h.old_start,
            "old_count": h.old_count,
            "new_start": h.new_start,
            "new_count": h.new_count,
            "added_lines": h.added_lines,
            "removed_lines": h.removed_lines,
        }
        for h in hunks
    ]

    return {"path": path, "hunks": hunk_dicts, "summary": summary, "raw_diff": raw_diff}


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
    if not _is_path_under(target, workspace_root):
        if not _is_symlink_traversal(workspace_root, path):
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

    # System-default context files are read-only (0o444, overwritten on startup)
    if _is_readonly_context_file(path):
        raise HTTPException(status_code=403, detail="System-default context files are read-only")

    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target = (workspace_root / path).resolve()

    if not _is_path_under(target, workspace_root):
        if not _is_symlink_traversal(workspace_root, path):
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


@router.post("/workspace/file")
async def create_file(request: FolderCreateRequest):
    """Create an empty file inside the workspace.

    Creates parent directories as needed.  Returns HTTP 409 if the file
    already exists to prevent accidental overwrites.
    Returns HTTP 403 if the target is inside a system-managed directory.
    """
    expanded_path = await _get_workspace_path()
    target = _validate_relative_path(request.path, expanded_path)

    # Reject creation inside system-managed folders
    rel_path = request.path.replace("\\", "/").strip("/")
    rel_parts = rel_path.split("/")
    for i in range(len(rel_parts)):
        prefix = "/".join(rel_parts[: i + 1])
        if prefix in SYSTEM_MANAGED_FOLDERS:
            raise HTTPException(
                status_code=403,
                detail=f"Cannot create inside system-managed directory: {prefix}",
            )

    if target.exists():
        raise HTTPException(status_code=409, detail="File already exists")

    # Reject creation of system-default context files (readonly, overwritten on startup)
    if _is_readonly_context_file(rel_path):
        raise HTTPException(status_code=403, detail="System-default context files are read-only")

    # Validate depth
    is_valid, error_msg = swarm_workspace_manager.validate_depth(request.path)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()

    logger.info("Created file: %s", request.path)
    return {"path": request.path}


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




@router.post("/workspace/trash")
async def trash_item(request: FolderDeleteRequest):
    """Move a file or folder to the macOS Trash (recoverable via Finder).

    Never falls back to permanent delete — if trashing fails, the error is
    surfaced to the user so they can decide what to do.

    **Symlink handling:** If the target path is a symlink, only the link
    itself is removed (``os.unlink``).  The real target directory is
    preserved.  This prevents accidental data loss when trashing linked
    project folders (e.g., ``Projects/SwarmAI → ~/real/repo``).

    Returns HTTP 403 if the target is a system-managed directory.
    Returns HTTP 500 if trashing fails (osascript error, permissions, etc.).
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)

    # Build the unresolved path BEFORE _validate_relative_path (which resolves
    # symlinks).  We need the unresolved path to detect symlinks and to pass
    # the correct filesystem entry to osascript / unlink.
    stripped = request.path.replace("\\", "/").strip("/")
    unresolved_path = workspace_root / stripped

    target = _validate_relative_path(request.path, expanded_path)

    # Reject trash on system-managed folders
    rel_path = request.path.replace("\\", "/").strip("/")
    if rel_path in SYSTEM_MANAGED_FOLDERS:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot delete system-managed directory: {rel_path}",
        )

    if not target.exists() and not unresolved_path.is_symlink():
        raise HTTPException(status_code=404, detail="Path not found")

    # Symlink guard: if the path is a symlink, remove the link itself — never
    # trash the real target directory.  This prevents accidental data loss when
    # trashing linked project folders (e.g., Projects/SwarmAI → ~/real/repo).
    # os.unlink removes the symlink without touching the target.
    if unresolved_path.is_symlink():
        try:
            await asyncio.to_thread(os.unlink, str(unresolved_path))
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to remove symlink: {exc}",
            )
        logger.info("Removed symlink (target preserved): %s", request.path)
        return {"path": request.path, "trashed": True, "was_symlink": True}

    # macOS Trash via osascript (recoverable).
    # For directories, "POSIX file" must be coerced to alias — Finder's
    # "delete POSIX file" only reliably handles files on all macOS versions.
    #
    # Escape backslashes and double-quotes for the AppleScript string literal
    # to prevent injection via crafted filenames.
    target_str = str(target).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "").replace("\r", "")

    if target.is_dir():
        applescript = (
            'tell application "Finder"\n'
            f'  delete (POSIX file "{target_str}" as alias)\n'
            'end tell'
        )
    else:
        applescript = (
            'tell application "Finder"\n'
            f'  delete POSIX file "{target_str}"\n'
            'end tell'
        )

    try:
        # Run in thread to avoid blocking the async event loop (osascript
        # talks to Finder via Apple Events and can take seconds).
        result = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", applescript],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="osascript not found — macOS Trash requires osascript",
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=500,
            detail="Trash operation timed out (Finder not responding?)",
        )

    if result.returncode != 0:
        error_msg = result.stderr.strip() or "Unknown osascript error"
        logger.error("Trash failed for %s: %s", request.path, error_msg)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trash: {error_msg}",
        )

    logger.info("Trashed (recoverable): %s", request.path)
    return {"path": request.path, "trashed": True}


@router.put("/workspace/rename")
async def rename_item(request: FolderRenameRequest):
    """Rename or move an item inside the workspace.

    Increments project_files_version for context cache invalidation
    when project files are renamed or moved (Requirement 34.2).

    Returns HTTP 403 if the source or destination is a system-managed
    directory (Requirement 12.9).
    """
    expanded_path = await _get_workspace_path()

    # Reject rename on system-managed folders (Req 12.9)
    normalized_old = request.old_path.replace("\\", "/").strip("/")
    if normalized_old in SYSTEM_MANAGED_FOLDERS:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot delete/rename system-managed directory: {normalized_old}",
        )

    # Reject move INTO a system-managed directory (same check as create_file)
    normalized_new = request.new_path.replace("\\", "/").strip("/")
    new_parts = normalized_new.split("/")
    for i in range(len(new_parts) - 1):  # exclude the item itself
        prefix = "/".join(new_parts[: i + 1])
        if prefix in SYSTEM_MANAGED_FOLDERS:
            raise HTTPException(
                status_code=403,
                detail=f"Cannot move into system-managed directory: {prefix}",
            )

    old_target = _validate_relative_path(request.old_path, expanded_path)
    new_target = _validate_relative_path(request.new_path, expanded_path)

    if not old_target.exists():
        raise HTTPException(status_code=404, detail="Source path not found")

    if new_target.exists():
        raise HTTPException(status_code=409, detail=f"Destination already exists: {request.new_path}")

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

