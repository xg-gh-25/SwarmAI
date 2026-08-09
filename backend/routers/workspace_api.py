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

- ``_should_include``      — Root-level filter: hide infrastructure, pass system items + user dirs
- ``_get_git_status``      — Run ``git status --porcelain`` and return {path: status} dict
- ``_build_tree``          — Recursive tree builder with depth bounding, sorting, and git status
- ``_is_readonly_context_file`` — Check if a path is a readonly system-default context file

Helper models (request bodies):

- ``FolderCreateRequest``  — ``path: str``
- ``FolderDeleteRequest``  — ``path: str``
- ``FolderRenameRequest``  — ``old_path: str``, ``new_path: str``
"""

import base64
import functools
import hashlib
import json
import logging
import asyncio
import mimetypes
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from core.context_directory_loader import CONTEXT_FILES
from core.swarm_workspace_manager import SYSTEM_MANAGED_FOLDERS, swarm_workspace_manager
from database import db
from utils.diff_parser import parse_unified_diff, format_human_summary
from schemas.workspace_config import (
    WorkspaceConfigResponse,
    WorkspaceConfigUpdate,
)

logger = logging.getLogger(__name__)

# ─── ETag / tree cache ────────────────────────────────────────────────────────
# The frontend polls /workspace/tree every 30s.  Running `git status` +
# recursive iterdir on every poll wastes ~50ms CPU per call — the #1 source
# of idle CPU burn.  This cache short-circuits the work:
#   - _etag_cache stores (etag_value, response_bytes, timestamp)
#   - If <5s have passed since last computation, reuse cached ETag directly
#   - Even when recomputing, git+fs work is offloaded to a thread
_ETAG_CACHE_TTL = 5.0  # seconds — at most 1 real scan per 5s; frontend polls every 30s
_etag_cache: dict[str, tuple[str, bytes, float]] = {}  # key=depth → (etag, body, time)
# Thread safety: _etag_cache is read from the event loop (fast path) and written
# from asyncio.to_thread (slow path).  Python's GIL makes individual dict ops
# atomic, and _invalidate_tree_cache().clear() is also atomic.  The worst case
# is serving one stale response after a mutation — acceptable for a polling cache.


def _invalidate_tree_cache() -> None:
    """Clear the ETag cache after a workspace mutation (create/delete/rename/save).

    Forces the next ``/workspace/tree`` poll to recompute git+fs state.
    """
    _etag_cache.clear()

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
_HIDDEN_DIRS = frozenset({"chats", ".git", "Services"})

# Root-level items that the frontend displays in a separate "System" section.
# These MUST pass through _should_include so the frontend can extract them.
_SYSTEM_ITEMS = frozenset({".context", ".claude", "config.json", "proactive_state.json"})

# Root-level directories that are system/infrastructure — hidden from explorer.
# Not in _SYSTEM_ITEMS (frontend doesn't use them), not user content.
_HIDDEN_ROOT_DIRS = frozenset({
    ".pytest_cache",  # Dev artifact
    "config-backup",  # System backup
    "db-export",      # Database export (system)
    "output",         # Legacy output dir
    "workspace",      # System workspace dir
})


def _should_include(
    name: str, *, is_root: bool = False, is_dir: bool = False
) -> bool:
    """Return True if a file/directory name should appear in the tree.

    Filtering strategy:
    - At root level: show _SYSTEM_ITEMS (frontend extracts to System section),
      show user content directories (Knowledge, Projects, Attachments, etc.),
      hide everything else (infrastructure files, system-only dirs).
    - At all levels: hide directories in ``_HIDDEN_DIRS`` (chats, .git, Services).
    - Below root: show everything (user content lives in subdirectories).
    """
    if name in _HIDDEN_DIRS:
        return False
    if is_root:
        # System items always pass through (frontend needs them)
        if name in _SYSTEM_ITEMS:
            return True
        if not is_dir:
            # All other root-level files are infrastructure artifacts
            return False
        if name in _HIDDEN_ROOT_DIRS:
            return False
    return True


def _get_git_status(workspace_root: Path, pathspec: str | None = None) -> dict[str, str]:
    """Run ``git status --porcelain -z`` and return a dict of {relative_path: status}.

    Uses ``-z`` for NUL-separated output to avoid quoting of paths with spaces
    or special characters.

    Status values match the GitStatus type on the frontend:
    - 'added', 'modified', 'deleted', 'renamed', 'untracked', 'conflicting'

    *pathspec* (optional): when provided, git scopes the scan to that
    workspace-relative path (``git status ... -- <pathspec>``) instead of the
    whole repo. Returned keys stay workspace-relative regardless — git always
    reports paths relative to the repo root, not to the pathspec — so
    ``_build_tree`` prefix-matching is unaffected. Used by the lazy-expand
    endpoint to avoid an O(whole-repo) scan on every directory expand.

    Returns an empty dict if the workspace is not a git repo or git fails.
    """
    git_dir = workspace_root / ".git"
    if not git_dir.is_dir():
        return {}

    try:
        # Use -unormal (default) instead of -uall.  -uall recursively
        # enumerates every untracked file in every directory — extremely
        # expensive on large repos (100ms+).  -unormal shows untracked
        # *directories* as a single entry, which is sufficient for the
        # explorer's change indicators and costs ~5ms instead.
        cmd = ["git", "status", "--porcelain", "-z", "-unormal"]
        if pathspec:
            # Everything after ``--`` is a pathspec, not a flag — safe even if
            # the path begins with a dash. Scopes the scan to this subtree.
            cmd += ["--", pathspec]
        result = subprocess.run(
            cmd,
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
    subrepo_status_fn: "Callable[[Path], dict[str, str]] | None" = None,
) -> list[dict]:
    """Build a nested tree of workspace entries.

    Walks *root* up to *depth* levels, excluding hidden entries (except
    ``.project.json``).  Directories are sorted before files; both groups
    are sorted alphabetically.

    *subrepo_status_fn* is an optional cached git-status getter that avoids
    duplicate subprocess spawns for symlinked sub-repos across calls.

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
        entry_is_dir = entry.is_dir()
        if not _should_include(entry.name, is_root=is_root, is_dir=entry_is_dir):
            continue
        if entry_is_dir:
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
            # Use cached getter if available to avoid duplicate subprocess spawns
            sub_status = subrepo_status_fn(resolved) if subrepo_status_fn else _get_git_status(resolved)
            # Re-key sub-repo paths relative to the workspace root
            child_git_status = dict(git_status) if git_status else {}
            for sub_path, sub_st in sub_status.items():
                child_git_status[f"{rel_path}/{sub_path}"] = sub_st

        children = _build_tree(d, workspace_root, depth - 1, child_git_status, subrepo_status_fn) if depth > 1 else None

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


def _collect_subrepo_status_cached(
    entry: Path,
    rel_prefix: str,
    items: list[tuple[str, str]],
    get_status: Callable[[Path], dict[str, str]],
) -> None:
    """Like _collect_subrepo_status but uses a cached git-status getter.

    The *get_status* callable should be a per-invocation cached function
    that deduplicates git status calls across ETag computation and tree building.
    """
    if entry.is_symlink() and entry.is_dir():
        try:
            resolved = entry.resolve()
            git_path = resolved / ".git"
            if git_path.is_dir() or git_path.is_file():
                sub_status = get_status(resolved)
                for sp, ss in sub_status.items():
                    items.append((f"{rel_prefix}/{sp}", ss))
        except OSError:
            pass


def _tree_fingerprint(nodes: list[dict]) -> str:
    """Derive a deterministic structure fingerprint from an ALREADY-BUILT tree.

    Replaces the old ``_fs_fingerprint`` second filesystem walk: ``_build_tree``
    has already visited (and filtered + sorted) every included entry, so the tree
    it returns encodes the exact same visible structure. Walking it in-memory
    costs zero extra ``iterdir()`` syscalls.

    Encodes ``name:type`` per node (so a file↔directory type-flip at the same name
    changes the fingerprint) and recurses into children. A depth-truncated dir
    (``children is None``) contributes its own ``name:type`` — its presence is the
    signal — and recursion stops there, exactly where the fs walk stopped at its
    depth bound. Detects adds/deletes (name set changes), renames (name changes),
    and type-flips (type token changes). ETag value need not match the old scheme —
    ETags are opaque and reset on deploy; only determinism + change-sensitivity matter.
    """
    parts: list[str] = []
    for n in nodes:
        token = f"{n['name']}:{n['type']}"
        children = n.get("children")
        if isinstance(children, list):
            token += "(" + _tree_fingerprint(children) + ")"
        parts.append(token)
    return ",".join(parts)


def _compute_etag_and_tree_sync(workspace_root: Path, depth: int) -> tuple[str, bytes]:
    """Compute ETag + serialised tree JSON in a worker thread.

    This is the EXPENSIVE function that runs ``git status``, iterdir(),
    and tree building.  By running it in ``asyncio.to_thread`` we keep
    the event loop free for SSE heartbeats and other I/O.

    Uses a per-invocation sub-repo cache so each symlinked project is
    scanned at most once (shared between ETag computation and tree building).
    """
    git_status = _get_git_status(workspace_root)

    # Per-invocation cache: resolved_path → {relative_path: status}
    # Prevents duplicate git status calls for symlinked sub-repos
    # (called once during ETag scan, reused during _build_tree).
    subrepo_cache: dict[str, dict[str, str]] = {}

    def _get_subrepo_status_cached(resolved: Path) -> dict[str, str]:
        """Return git status for a sub-repo, caching by resolved path."""
        key = str(resolved)
        if key not in subrepo_cache:
            subrepo_cache[key] = _get_git_status(resolved)
        return subrepo_cache[key]

    # Collect sub-repo status (symlinked projects)
    all_status_items = list(git_status.items())
    try:
        for entry in workspace_root.iterdir():
            _collect_subrepo_status_cached(entry, entry.name, all_status_items, _get_subrepo_status_cached)
            if entry.is_dir():
                try:
                    for child in entry.iterdir():
                        _collect_subrepo_status_cached(
                            child, f"{entry.name}/{child.name}", all_status_items, _get_subrepo_status_cached,
                        )
                except OSError:
                    pass
    except OSError:
        pass
    git_hash = hashlib.md5(json.dumps(sorted(all_status_items)).encode(), usedforsecurity=False).hexdigest()

    # Build the tree ONCE, then derive the structure fingerprint from the
    # in-memory result — no second filesystem walk. _build_tree has already
    # filtered + sorted every visible entry, so _tree_fingerprint(tree) reflects
    # the same structure the old _fs_fingerprint fs-walk did, at zero extra
    # iterdir() cost. (Previously this function walked the FS twice per cache
    # miss: once for the fingerprint, once for the tree.)
    tree = _build_tree(workspace_root, workspace_root, depth, git_status, _get_subrepo_status_cached)
    body = json.dumps(tree).encode()

    fs_hash = hashlib.md5(_tree_fingerprint(tree).encode(), usedforsecurity=False).hexdigest()[:8]
    etag = hashlib.md5(f"{git_hash}:{fs_hash}:{depth}".encode(), usedforsecurity=False).hexdigest()
    etag_value = f'"{etag}"'

    return etag_value, body


@router.get("/workspace/tree")
async def get_workspace_tree(
    depth: int = Query(default=3, ge=1, le=10),
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

    Performance: git+fs scan is offloaded to a thread pool and cached
    for 5s to match the 30s frontend poll interval. Sub-repo git status
    calls are deduplicated via per-invocation cache.

    Requirements: 10.1, 11.5, 15.1
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)

    if not workspace_root.is_dir():
        raise HTTPException(
            status_code=500,
            detail="Workspace root directory does not exist",
        )

    cache_key = str(depth)
    now = time.monotonic()

    # Fast path: serve from cache if fresh (< 2s old)
    cached = _etag_cache.get(cache_key)
    if cached:
        cached_etag, cached_body, cached_time = cached
        if now - cached_time < _ETAG_CACHE_TTL:
            if if_none_match and if_none_match.strip() == cached_etag:
                return Response(status_code=304, headers={"ETag": cached_etag})
            return Response(
                content=cached_body,
                media_type="application/json",
                headers={"ETag": cached_etag},
            )

    # Slow path: offload git+fs scan to thread pool (frees event loop)
    etag_value, body = await asyncio.to_thread(
        _compute_etag_and_tree_sync, workspace_root, depth,
    )

    # Update cache
    _etag_cache[cache_key] = (etag_value, body, time.monotonic())

    if if_none_match and if_none_match.strip() == etag_value:
        return Response(status_code=304, headers={"ETag": etag_value})

    return Response(
        content=body,
        media_type="application/json",
        headers={"ETag": etag_value},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Lazy-expand: fetch children of a single directory
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/workspace/tree/expand")
async def expand_tree_directory(
    path: str = Query(..., description="Relative path of the directory to expand"),
    depth: int = Query(default=2, ge=1, le=5),
) -> list[dict]:
    """Expand a single directory in the workspace tree (lazy loading).

    Returns the children of the specified directory at the given depth.
    Used by the frontend when the user expands a directory that was
    previously returned with ``children: null`` (depth-truncated).
    """
    workspace_root = Path(await _get_workspace_path())
    target = workspace_root / path

    # Security: ensure path doesn't escape workspace root (BEFORE any fs operation)
    try:
        target.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Path escapes workspace root")

    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")

    # Scope the git-status scan to the target subtree only. The full-repo scan
    # (~230ms on a large repo) dominated per-expand latency, yet the expand
    # response only contains this subtree — ancestor status propagation (the
    # reason the full-tree endpoint scans everything) does not apply here.
    # Keys stay workspace-relative, so _build_tree prefix-matching is unaffected.
    git_status = await asyncio.to_thread(_get_git_status, workspace_root, path)

    # Local subrepo cache for this request (mirrors the closure in _compute_etag_and_tree_sync)
    subrepo_cache: dict[str, dict[str, str]] = {}

    def _subrepo_status_cached(resolved: Path) -> dict[str, str]:
        key = str(resolved)
        if key not in subrepo_cache:
            subrepo_cache[key] = _get_git_status(resolved)
        return subrepo_cache[key]

    children = await asyncio.to_thread(
        _build_tree, target, workspace_root, depth, git_status, _subrepo_status_cached,
    )
    return children


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


def _resolve_file_path(
    path: str, workspace_root: Path
) -> tuple[Path, bool]:
    """Resolve a file path to an absolute target with external flag.

    Handles both absolute paths (``/Users/.../foo.py``) and workspace-relative
    paths (``Knowledge/Notes/foo.md``).

    Args:
        path: Absolute or workspace-relative file path.
        workspace_root: Resolved workspace root directory.

    Returns:
        ``(target, is_external)`` — *target* is the resolved absolute path,
        *is_external* is True when the file lives outside the workspace
        (either an absolute path pointing elsewhere, or a symlink traversal).

    Raises:
        HTTPException 400: For relative paths with ``..`` traversal that
            don't resolve through a trusted workspace symlink.
    """
    normalized = os.path.normpath(path)

    # ── Absolute path — allow only under user's home directory ──
    if os.path.isabs(normalized):
        target = Path(normalized).resolve()
        home = Path.home().resolve()
        if not _is_path_under(target, home):
            raise HTTPException(
                status_code=400,
                detail=f"Absolute path must be under user home directory: {path}",
            )
        is_external = not _is_path_under(target, workspace_root)
        return target, is_external

    # ── Relative path — apply traversal guard ──
    if ".." in path.split("/"):
        raise HTTPException(status_code=400, detail=f"Path traversal not allowed: {path}")

    target = (workspace_root / path).resolve()
    is_external = not _is_path_under(target, workspace_root)

    # Relative path that escapes the workspace must go through a symlink
    if is_external and not _is_symlink_traversal(workspace_root, path):
        raise HTTPException(status_code=400, detail=f"Path outside workspace: {path}")

    return target, is_external


@functools.lru_cache(maxsize=64)
def _find_git_root_cached(parent_dir: str) -> Optional[str]:
    """Cached git root lookup by parent directory string.

    Caching by *str* (not Path) so the LRU key is hashable.
    Returns the git root as a string, or None.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=parent_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _find_git_root(file_path: Path) -> Optional[Path]:
    """Find the git repository root containing the given file.

    Uses an LRU cache keyed on parent directory to avoid repeated
    ``git rev-parse`` subprocess calls for files in the same repo.
    """
    parent = file_path.parent if file_path.is_file() else file_path
    if not parent.is_dir():
        return None
    root_str = _find_git_root_cached(str(parent))
    return Path(root_str) if root_str else None


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
    path: str = Query(..., description="Absolute or workspace-relative file path"),
):
    """Read a file's content by path.

    Accepts both absolute paths (``/Users/.../foo.py``) and workspace-relative
    paths (``Knowledge/Notes/foo.md``).  Absolute paths allow the file editor
    to open any file on the user's machine — essential for opening source
    files referenced in chat messages.

    Returns ``{ "content": "...", "encoding": "utf-8" }`` for text files.
    Returns ``{ "content": "<base64>", "encoding": "base64", ... }`` for binary files.
    Returns 404 if the file does not exist.
    Returns 400 if a relative path attempts directory traversal.
    Returns 413 if the file exceeds 50 MB.
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target, is_external = _resolve_file_path(path, workspace_root)

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
        # Offload the blocking read off the event loop (a large file blocks all
        # concurrent HTTP otherwise — the file's established pattern, cf :632/:682).
        content = await asyncio.to_thread(target.read_text, encoding="utf-8")
    except UnicodeDecodeError:
        # Binary fallback: base64 encode
        logger.info("Binary file fallback for %s (size=%d, not valid UTF-8)", path, file_size)
        raw = await asyncio.to_thread(target.read_bytes)
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

    # Projected skill files and context files are always readonly (workspace-internal only)
    is_readonly = False
    if not is_external:
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


def resolve_path_to_physical(path: str, workspace_root: Path) -> dict | None:
    """Resolve a partial/relative/absolute path to BOTH a workspace-relative
    display path and its PHYSICAL absolute path.

    Extracted (Extract≠Extend, run_e626e121) from ``resolve_workspace_file`` so
    non-HTTP callers (the streaming orchestrator, resolving a written file's real
    absolute path for the unified Canvas file-change event + copy-path) reuse the
    exact same cascade without an HTTP round-trip. The HTTP endpoint delegates here.

    Cascade: absolute → direct → project-symlink (Projects/*/{path}) → bare-name
    recursive walk (depth-8, prunes node_modules/.git/etc) → bound-worktree (Stage 5).
    Stage 5 (run_1e791215) resolves a MULTI-SEGMENT relative path under the code-repo
    worktrees a DDD GOVERNs but does NOT contain (declared in Projects/*/bindings.yaml,
    allowlist-scoped via needs_human_review._worktree_roots) — e.g. a link to SwarmAI's own
    source that lives outside the workspace and is not symlinked in. Stages 0-4 are
    behavior-preserving; Stage 5 is additive + last (only reached when 0-4 miss).

    Returns ``{"relative": <ws-relative-or-absolute-str>, "absolute": <physical abs>}``
    on success, or ``None`` if not found / invalid. **Fails SAFE to None** — unlike
    the endpoint it NEVER raises (it runs on the streaming hot path; a null byte /
    traversal / miss must not crash the turn, just skip the surface).
    """
    if "\x00" in path:
        return None

    # --- Stage 0: Absolute path handling ---
    normalized = os.path.normpath(path)
    if os.path.isabs(normalized):
        abs_path = Path(normalized).resolve()
        projects_dir = workspace_root / "Projects"
        if projects_dir.is_dir():
            for project in sorted(projects_dir.iterdir()):
                if not project.is_dir():
                    continue
                try:
                    symlink_target = project.resolve()
                    rel = abs_path.relative_to(symlink_target)
                    return {
                        "relative": f"Projects/{project.name}/{rel}",
                        "absolute": str(abs_path),
                    }
                except ValueError:
                    continue
        # No project match — accept the absolute path as-is if the file exists.
        if abs_path.is_file():
            return {"relative": str(abs_path), "absolute": str(abs_path)}
        return None

    # Reject ".." traversal after normalization (relative paths only)
    if normalized.startswith(".."):
        return None

    # --- Stage 1: Direct lookup (already workspace-relative) ---
    direct = (workspace_root / path).resolve()
    if direct.is_file() and (
        _is_path_under(direct, workspace_root) or _is_symlink_traversal(workspace_root, path)
    ):
        return {"relative": path, "absolute": str(direct)}

    # --- Stage 2: Try under each project in Projects/{name}/{path} ---
    projects_dir = workspace_root / "Projects"
    if projects_dir.is_dir():
        for project in sorted(projects_dir.iterdir()):
            if not project.is_dir():
                continue
            candidate_rel = f"Projects/{project.name}/{path}"
            candidate = (workspace_root / candidate_rel).resolve()
            if candidate.is_file() and (
                _is_path_under(candidate, workspace_root)
                or _is_symlink_traversal(workspace_root, candidate_rel)
            ):
                return {"relative": candidate_rel, "absolute": str(candidate)}

    # --- Stage 3 & 4: Bare filename → recursive search (depth-capped, pruned) ---
    _MAX_DEPTH = 8
    _EXCLUDED_DIRS = {'.git', 'node_modules', '__pycache__', '.pytest_cache', '.venv', '.mypy_cache'}
    # G4 (run_5a7be540): a wall-time budget on the bare-name walk. Depth-8 + dir
    # pruning already bound the DEPTH, but a very WIDE tree (many dirs, 100K files
    # spread across them) could still take seconds — and this runs on the streaming
    # file-change hot path (per emitted deliverable). The check fires BETWEEN os.walk
    # yields (per-directory), so it bounds the common wide-tree-of-directories case.
    # KNOWN RESIDUAL (Gate-2): a single PATHOLOGICAL directory with ~1M entries in ONE
    # dir can still block inside a single listdir() past the budget before the next
    # check — os.walk gives no mid-listdir hook, so bounding that would mean replacing
    # os.walk (ceremony for an exotic case). Accepted: the common case is bounded; the
    # exotic single-mega-dir case degrades to the pre-G4 behavior, never worse. On
    # budget-exceed we bail to None = "not found" (safe: caller drops an unresolvable
    # write). Never raises. monotonic() so a wall-clock change can't skew it.
    _WALK_BUDGET_S = 2.0
    _walk_deadline = time.monotonic() + _WALK_BUDGET_S
    if "/" not in path and "\\" not in path:
        # Stage 3: inside Projects/ (symlinked repos)
        if projects_dir.is_dir():
            for project in sorted(projects_dir.iterdir()):
                if not project.is_dir():
                    continue
                project_resolved = project.resolve()
                for root, dirs, files in os.walk(project_resolved):
                    if time.monotonic() > _walk_deadline:
                        return None  # G4: walk budget exceeded → treat as not-found
                    dirs[:] = sorted(d for d in dirs if d not in _EXCLUDED_DIRS)
                    try:
                        rel_root = Path(root).relative_to(project_resolved)
                    except ValueError:
                        continue
                    if len(rel_root.parts) >= _MAX_DEPTH:
                        dirs.clear()
                        continue
                    if path in files:
                        return {
                            "relative": f"Projects/{project.name}/{rel_root / path}",
                            "absolute": str(Path(root) / path),
                        }
        # Stage 4: workspace root (Knowledge/, .context/, Services/, …), excl Projects/
        _STAGE4_EXCLUDE = _EXCLUDED_DIRS | {'Projects'}
        for root, dirs, files in os.walk(workspace_root):
            if time.monotonic() > _walk_deadline:
                return None  # G4: walk budget exceeded → treat as not-found
            dirs[:] = sorted(d for d in dirs if d not in _STAGE4_EXCLUDE)
            try:
                rel_root = Path(root).relative_to(workspace_root)
            except ValueError:
                continue
            if len(rel_root.parts) >= _MAX_DEPTH:
                dirs.clear()
                continue
            if path in files:
                return {"relative": str(rel_root / path), "absolute": str(Path(root) / path)}

    # --- Stage 5: governed-but-not-CONTAINED repo (bindings.yaml worktree) ---
    # A DDD may GOVERN a code-repo whose source lives OUTSIDE the workspace and is
    # NOT symlinked into Projects/<X>/ (the "GOVERNs, never CONTAINS" paradigm). Try
    # a MULTI-SEGMENT relative path under each declared bound-worktree root. Null-byte
    # + ".." were already rejected above; absolute paths were handled by Stage 0 — so
    # `path` here is a safe relative path. Direct {worktree}/{path} join is O(1) (no
    # walk on the hot path); bare names are left to Stages 3/4 (already tried above).
    #
    # Worktree roots are read from the SINGLE-SOURCE bindings cache in
    # needs_human_review._worktree_roots (already lru-cached AND invalidated on
    # bind/unbind via clear_worktree_cache() in ddd_bindings.bind_repo:319) —
    # NOT a second private cache here (that would silt a stale allowlist that
    # never sees a new binding until daemon restart; C042/R25 — reuse, don't
    # duplicate). Roots are pre-resolved + longest-first sorted (nested worktree
    # wins). We still re-`.resolve()` the candidate so an in-worktree symlink
    # pointing outside is caught by the _is_path_under containment check.
    if "/" in path or "\\" in path:
        from core.needs_human_review import _worktree_roots

        for wt_abs, _repo in _worktree_roots(str(workspace_root)):
            root = Path(wt_abs)
            candidate = (root / path).resolve()
            # Containment: the resolved file must stay UNDER the declared worktree
            # (a symlink inside the worktree pointing outside is rejected here).
            if candidate.is_file() and _is_path_under(candidate, root):
                # Outside the workspace → return the absolute path as display too
                # (mirrors Stage 0 line ~970; the content-fetch endpoint accepts an
                # absolute path under $HOME).
                return {"relative": str(candidate), "absolute": str(candidate)}

    return None


@router.get("/workspace/file/resolve")
async def resolve_workspace_file(
    path: str = Query(..., description="Partial or relative file path to resolve", max_length=1024),
):
    """Resolve a partial file path to a workspace-relative path.

    Used by clickable file links in chat messages. The agent often outputs
    paths relative to the source codebase (e.g., ``backend/routers/foo.py``)
    rather than the workspace root. This endpoint searches:

    1. Direct: ``{wsRoot}/{path}`` (already workspace-relative)
    2. Under each project symlink: ``Projects/*/{path}``

    Returns ``{ "resolved_path": "Projects/SwarmAI/backend/routers/foo.py" }``
    on success, or 404 if not found anywhere.
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)

    # Reject null bytes early — they crash Path.resolve() and are a classic
    # injection vector. The HTTP contract distinguishes 400 (bad input) from 404
    # (not found); the shared helper collapses both to None (it runs on the hot
    # path and must never raise), so we re-assert the 400 cases here before
    # delegating the cascade.
    if "\x00" in path:
        raise HTTPException(status_code=400, detail="Invalid path: contains null byte")
    normalized = os.path.normpath(path)
    if not os.path.isabs(normalized) and normalized.startswith(".."):
        raise HTTPException(status_code=400, detail=f"Path traversal not allowed: {path}")

    # The bare-name branch walks the tree — keep it off the event loop.
    resolved = await asyncio.to_thread(resolve_path_to_physical, path, workspace_root)
    if resolved is not None:
        # Endpoint contract is unchanged: it returns the workspace-relative
        # display path under "resolved_path" (the helper additionally exposes the
        # physical absolute path for non-HTTP callers).
        return {"resolved_path": resolved["relative"]}

    raise HTTPException(status_code=404, detail=f"Could not resolve file: {path}")


@router.get("/workspace/file/meta")
async def get_workspace_file_meta(
    path: str = Query(..., description="Absolute or workspace-relative file path"),
):
    """Return lightweight file metadata (size, mime type, absolute path) without
    reading content.

    Used by the FileViewer's UnsupportedRenderer (PPTX/DOCX/XLSX/…) to show file
    size + type and to power the OS-level actions (Open in Default App / Reveal in
    Finder / Copy Path) — those need the PHYSICAL absolute path, which a
    workspace-relative path can't provide. Content is never read here (the point is
    to avoid fetching a potentially large binary just to show a metadata card).
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target, _is_external = _resolve_file_path(path, workspace_root)

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    try:
        stat = target.stat()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot stat file: {exc}")

    mime_type, _ = mimetypes.guess_type(target.name)
    if mime_type is None:
        mime_type = "application/octet-stream"

    return {
        "name": target.name,
        "path": path,
        "size": stat.st_size,
        "mime_type": mime_type,
        # PHYSICAL absolute path (run_405d221c). `target` is the resolved on-disk
        # Path; the FileViewer's UnsupportedRenderer needs it for Open-in-System-App
        # / Reveal-in-Finder / Copy-Path — a workspace-relative path is meaningless
        # to the OS opener. Additive: existing fields unchanged.
        "absolute_path": str(target),
    }


@router.get("/workspace/file/raw")
async def get_workspace_file_raw(
    path: str = Query(..., description="Absolute or workspace-relative file path"),
):
    """Serve a file as raw binary with proper Content-Type.

    Used by the markdown preview to render local images directly via
    ``<img src="http://localhost:{port}/api/workspace/file/raw?path=...">``.
    Accepts both absolute and workspace-relative paths.
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target, _is_ext = _resolve_file_path(path, workspace_root)

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
    path: str = Query(..., description="Absolute or workspace-relative file path"),
):
    """Return a structured diff summary of uncommitted changes for a file.

    Used by the file editor panel's auto-diff feature (L2) to inject an
    edit summary into the chat input after saving. Finds the containing git
    repo automatically — works for both workspace files and external source files.

    Returns ``{"path": ..., "hunks": [...], "summary": "...", "raw_diff": "..."}``.
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target, _is_ext = _resolve_file_path(path, workspace_root)

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Find the git repo containing this file (workspace or external)
    git_root = _find_git_root(target)
    if git_root is None:
        return {"path": path, "hunks": [], "summary": "", "raw_diff": ""}

    # Compute path relative to the git root for the diff command
    try:
        git_relative = str(target.resolve().relative_to(git_root))
    except ValueError:
        return {"path": path, "hunks": [], "summary": "", "raw_diff": ""}

    try:
        # Offload the blocking git subprocess off the event loop (cf :632/:682).
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "diff", "--unified=3", "--", git_relative],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"path": path, "hunks": [], "summary": "", "raw_diff": ""}

    raw_diff = result.stdout or ""

    hunks = parse_unified_diff(raw_diff)

    # Read current file content for section-aware summary (offloaded — blocking I/O)
    try:
        file_content = await asyncio.to_thread(target.read_text, encoding="utf-8")
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


# A safe git ref for the diff baseline (run_030dc98e): an abbreviated-or-full sha,
# optionally suffixed with ONE `^` (the parent — this-run's pre-change baseline). This
# is deliberately NARROW: it rejects a leading `-` (so a crafted ref can NEVER be
# parsed by git as an OPTION like `--upload-pack=`, verified: `git show -x:p` errors),
# rejects `:`/spaces/path-traversal, and permits only hex + one trailing `^`. Anything
# else → treated as absent (fall back to HEAD), never passed to git.
_SAFE_GIT_REF = re.compile(r"^[0-9a-fA-F]{7,40}\^?$")


@router.get("/workspace/file/committed")
async def get_workspace_file_committed(
    path: str = Query(..., description="Absolute or workspace-relative file path"),
    ref: str | None = Query(
        None,
        description="Optional git ref to diff AGAINST (e.g. '<sha>^' for a run's "
        "pre-change baseline). Strict-allowlisted (hex sha + optional '^'); an "
        "unsafe/absent ref falls back to HEAD. Default HEAD (back-compat).",
    ),
):
    """Return a committed version of a file via ``git show <ref>:<path>`` (``ref``
    defaults to HEAD).

    Finds the containing git repo automatically — works for both workspace
    files and external source files.

    ``ref`` (run_030dc98e): when a Canvas OUTPUTS row carries a ``baseRef`` (a
    source-final row's ``<sha>^``), it is passed here so the diff baseline is the
    state BEFORE this run's change — otherwise a just-committed file diffs
    HEAD-vs-working-tree (identical) and shows an empty diff. An unsafe or missing
    ref falls back to HEAD (the historical behavior — the git-status badge caller
    never sends a ref, so it is unaffected).

    Returns ``{"content": "<committed text>", "in_head": True}`` for tracked
    text files (the diff baseline).

    Fail-open (no 400) for every case where a text baseline cannot be produced,
    with an ``in_head`` discriminator so callers can tell WHY the content is
    empty (a bare ``{"content": ""}`` conflated three distinct states and caused
    a wrong git-status badge — run_46e7b94c):

    - ``in_head: True``  — file IS in HEAD but has no usable text baseline
      (binary/non-UTF-8). It is TRACKED, so a "modified" badge is correct.
    - ``in_head: False`` — file is definitively NOT in HEAD (git reports it
      untracked). A "new" badge is correct.
    - ``in_head: None``  — cannot be determined (path rejected by the resolver's
      security guard, no containing git repo, or a git error/timeout). Callers
      should render NO badge rather than guess.

    This is a best-effort DIFF BASELINE endpoint, so "no baseline available" is a
    normal answer — NOT an HTTP error the frontend only catches and logs. The
    shared ``_resolve_file_path`` security guard is intentionally NOT weakened
    here: real read/write endpoints still 400 on a rejected path; only this
    derivative baseline endpoint swallows it (verified by
    test_get_workspace_file_still_rejects_outside_home).
    Returns 404 if the file doesn't exist on disk (a genuinely missing path is
    distinct from an un-diffable one — the caller asked for a file that is not
    there, which is a real not-found, not a "no baseline" case).
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    try:
        target, _is_ext = _resolve_file_path(path, workspace_root)
    except HTTPException:
        # Path rejected by the shared guard (outside home / traversal). This
        # endpoint has no baseline to offer for such a path — fail open, don't
        # surface a 400 the caller only wants to swallow anyway. Can't determine
        # tracked-ness (never reached git) → in_head=None (caller shows no badge).
        return {"content": "", "in_head": None}

    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    # Find the git repo containing this file
    git_root = _find_git_root(target)
    if git_root is None:
        return {"content": "", "in_head": None}  # no repo → can't determine

    # Compute path relative to the git root
    try:
        git_relative = str(target.resolve().relative_to(git_root))
    except ValueError:
        return {"content": "", "in_head": None}  # can't relativize → undetermined

    # Resolve the baseline ref: a strict-allowlisted caller ref, else HEAD. An unsafe
    # ref is silently downgraded to HEAD (not a 400 — this is a best-effort baseline
    # endpoint; a bad ref just yields the historical HEAD baseline, never a git error).
    safe_ref = ref if (isinstance(ref, str) and _SAFE_GIT_REF.fullmatch(ref)) else "HEAD"

    try:
        # Offload the blocking git subprocess off the event loop — a burst of N
        # committed-fetches (the rail badges every written file) would otherwise
        # serialize N × ~79ms on the loop, stalling all concurrent HTTP (cf :632/:682).
        # `<rev>:<path>` is a SINGLE gitrevision arg (no `--` separator applies); the
        # regex already bars a dash-leading rev, so it can never be read as an option.
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "show", f"{safe_ref}:{git_relative}"],
            cwd=str(git_root),
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"content": "", "in_head": None}  # git failed → can't determine

    if result.returncode != 0:
        # git could not show HEAD:<path> → file is untracked / not in HEAD.
        # This is a DEFINITIVE answer: not in head → a "new" badge is correct.
        return {"content": "", "in_head": False}

    # Decode manually — a binary/non-UTF-8 file IS in HEAD (tracked) but has no
    # text baseline to diff against, so fail open like the branches above rather
    # than raise a 400 the frontend only catches-and-ignores. in_head=True keeps
    # its badge "modified" (it is tracked), not "new".
    try:
        content = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return {"content": "", "in_head": True}

    return {"content": content, "in_head": True}


@router.put("/workspace/file")
async def put_workspace_file(
    path: str = Query(..., description="Absolute or workspace-relative file path"),
    body: dict = None,
):
    """Write text content to a file by path.

    Accepts both absolute paths and workspace-relative paths so the file
    editor can save any file the user has open.
    Expects ``{ "content": "<utf-8 text>" }`` in the request body.
    """
    if body is None or "content" not in body:
        raise HTTPException(status_code=400, detail="Request body must include 'content'")

    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)
    target, is_external = _resolve_file_path(path, workspace_root)

    # Readonly guards only apply to workspace-internal paths
    if not is_external:
        if path.startswith(".claude/skills/") or path.startswith(".claude\\skills\\"):
            raise HTTPException(status_code=403, detail="Skill files are read-only")
        if _is_readonly_context_file(path):
            raise HTTPException(status_code=403, detail="System-default context files are read-only")

    # mkdir + write_text are blocking FS I/O — off the event loop in one worker
    # thread (run_6ea3cb12).
    def _write():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body["content"], encoding="utf-8")

    try:
        await asyncio.to_thread(_write)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {exc}")

    # Only invalidate tree cache for workspace-internal writes
    if not is_external:
        _invalidate_tree_cache()
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

    # Note: We intentionally allow file creation inside system-managed folders
    # (Knowledge/, Projects/, etc.).  SYSTEM_MANAGED_FOLDERS protects the
    # folder *structure* from being deleted/renamed, not from having files
    # added to it — that's the whole point of these directories.
    rel_path = request.path.replace("\\", "/").strip("/")

    if target.exists():
        raise HTTPException(status_code=409, detail="File already exists")

    # Reject creation of system-default context files (readonly, overwritten on startup)
    if _is_readonly_context_file(rel_path):
        raise HTTPException(status_code=403, detail="System-default context files are read-only")

    # Validate depth
    is_valid, error_msg = swarm_workspace_manager.validate_depth(request.path)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)

    # mkdir + touch are blocking FS I/O — off the loop in one worker thread (run_6ea3cb12).
    def _create():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()

    await asyncio.to_thread(_create)

    _invalidate_tree_cache()
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

    _invalidate_tree_cache()
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

    # rmtree / unlink are blocking FS I/O (rmtree recurses a whole tree) — off the
    # event loop in one worker thread (run_6ea3cb12). is_dir() branch runs inside so
    # the whole delete decision is one dispatch.
    def _delete():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    await asyncio.to_thread(_delete)

    _invalidate_tree_cache()
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
    Returns HTTP 500 if trashing fails (filesystem error, permissions, etc.).
    """
    expanded_path = await _get_workspace_path()
    workspace_root = Path(expanded_path)

    # Build the unresolved path BEFORE _validate_relative_path (which resolves
    # symlinks).  We need the unresolved path to detect symlinks and to pass
    # the correct filesystem entry to shutil.move / unlink.
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

    # Move to macOS Trash via direct filesystem operation (recoverable).
    # Uses shutil.move to ~/.Trash/ — no osascript, no Apple Events, no TCC
    # popup. Uses a UUID suffix to guarantee uniqueness (avoids TOCTOU race
    # between exists() check and shutil.move in the old counter approach).
    trash_dir = Path.home() / ".Trash"
    trash_dir.mkdir(exist_ok=True)  # May not exist on network home dirs
    dest = trash_dir / target.name

    if dest.exists():
        # UUID suffix guarantees no collision without a TOCTOU window
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        stem = target.stem
        suffix = target.suffix
        dest = trash_dir / f"{stem} {unique_id}{suffix}"

    try:
        await asyncio.to_thread(shutil.move, str(target), str(dest))
    except OSError as exc:
        logger.error("Trash failed for %s: %s", request.path, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trash: {exc}",
        )

    _invalidate_tree_cache()
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

    # Note: We intentionally do NOT block moves INTO system-managed folders.
    # SYSTEM_MANAGED_FOLDERS protects the folders themselves from being
    # renamed/deleted (checked above for old_path).  Users should be able
    # to move files into Knowledge/, Knowledge/Designs/, Projects/, etc.
    normalized_new = request.new_path.replace("\\", "/").strip("/")

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

    # mkdir + rename are blocking FS I/O — off the loop in one worker thread (run_6ea3cb12).
    def _rename():
        new_target.parent.mkdir(parents=True, exist_ok=True)
        old_target.rename(new_target)

    await asyncio.to_thread(_rename)

    # Increment project_files_version for context cache invalidation (Req 34.2)

    _invalidate_tree_cache()
    logger.info("Renamed '%s' → '%s'", request.old_path, request.new_path)
    return {"old_path": request.old_path, "new_path": request.new_path}

