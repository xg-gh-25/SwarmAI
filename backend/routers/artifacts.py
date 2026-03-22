"""FastAPI router for the Artifacts API (git-derived, read-only).

This module exposes a single endpoint that returns recently modified files
from the workspace git tree.  No new database tables are created — artifacts
are derived on-the-fly from ``git log``.

Public endpoints:

- ``GET /artifacts/recent`` — Return recently modified files sorted by
  modification time (newest first).

Helper constants:

- ``EXTENSION_TYPE_MAP``   — Maps file extensions to artifact type categories.

Helper functions:

- ``_get_workspace_path``  — Resolve workspace path from DB config.
- ``_classify_extension``  — Derive artifact type from file extension.
- ``_parse_git_log``       — Parse raw ``git log`` output into deduplicated
  artifact records.

Response models:

- ``ArtifactResponse``     — Pydantic model with snake_case fields.
"""

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

import anyio
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.swarm_workspace_manager import swarm_workspace_manager
from database import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["artifacts"])


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

EXTENSION_TYPE_MAP: dict[str, set[str]] = {
    "code": {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java"},
    "document": {".md", ".txt", ".rst", ".pdf", ".docx"},
    "config": {".json", ".yaml", ".yml", ".toml", ".ini", ".env"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico"},
}

# Build a reverse lookup: extension → type for O(1) classification.
_EXT_TO_TYPE: dict[str, str] = {}
for _type_name, _extensions in EXTENSION_TYPE_MAP.items():
    for _ext in _extensions:
        _EXT_TO_TYPE[_ext] = _type_name

# Directories that contain user-facing session output (positive filter).
# Only files under these prefixes appear in the Radar Artifacts section.
_ARTIFACT_DIRS: tuple[str, ...] = (
    "Knowledge/",
    "Designs/",
    "Notes/",
    "Projects/",
    "Attachments/",
)

# Exact filenames to always exclude even if they match a directory above.
_EXCLUDED_NAMES: set[str] = {
    "L1_SYSTEM_PROMPTS.md",
    "L0_SYSTEM_PROMPTS.md",
}

# Extensions to always exclude (logs, lockfiles, etc.).
_EXCLUDED_EXTENSIONS: set[str] = {".log", ".lock", ".pyc"}


# ─────────────────────────────────────────────────────────────────────────────
# Response model
# ─────────────────────────────────────────────────────────────────────────────

class ArtifactResponse(BaseModel):
    """A recently modified file in the workspace git tree."""

    path: str
    title: str
    type: str
    modified_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_workspace_path() -> str:
    """Resolve the active workspace path from the database config.

    Uses the same singleton workspace config pattern as
    ``workspace_api._get_workspace_path``.

    Returns:
        Expanded absolute path to the workspace root.

    Raises:
        HTTPException: 404 if no workspace config exists.
    """
    config = await db.workspace_config.get_config()
    if config is None:
        raise HTTPException(status_code=404, detail="Workspace not configured")
    return swarm_workspace_manager.expand_path(config["file_path"])


def _classify_extension(file_path: str) -> str:
    """Derive artifact type from file extension (case-insensitive).

    Args:
        file_path: Relative file path from git log.

    Returns:
        One of: ``code``, ``document``, ``config``, ``image``, ``other``.
    """
    ext = Path(file_path).suffix.lower()
    return _EXT_TO_TYPE.get(ext, "other")


def _is_artifact_file(file_path: str) -> bool:
    """Return True if the file is a user-facing session output artifact.

    Filters to files under Knowledge/, Designs/, Notes/, Projects/, or
    Attachments/. Excludes log files, cache files, and system-generated
    files like L1_SYSTEM_PROMPTS.md.
    """
    name = Path(file_path).name
    ext = Path(file_path).suffix.lower()

    # Exclude by extension
    if ext in _EXCLUDED_EXTENSIONS:
        return False

    # Exclude specific system filenames
    if name in _EXCLUDED_NAMES:
        return False

    # Exclude hidden directories (e.g. .context/, .claude/)
    if any(part.startswith(".") for part in Path(file_path).parts):
        return False

    # Positive filter: must be under a known artifact directory.
    # Use Path.parts[0] instead of str.startswith() to avoid false matches
    # on paths like "Knowledge_backup/foo.md" matching "Knowledge/".
    parts = Path(file_path).parts
    if len(parts) < 2:
        # Bare filename (no parent directory) — not under any artifact dir
        return False
    first_dir = parts[0] + "/"
    return first_dir in _ARTIFACT_DIRS


def _parse_git_log(raw_output: str) -> list[dict[str, str]]:
    """Parse raw ``git log`` output into deduplicated artifact records.

    The git log format ``--format=%aI`` produces alternating blocks:
    an ISO timestamp line followed by one or more file path lines,
    separated by blank lines.

    Args:
        raw_output: Raw stdout from ``git log``.

    Returns:
        List of dicts with ``path``, ``title``, ``type``, ``modified_at``
        sorted by ``modified_at`` descending (most recent first).
        Deduplicated by path — only the most recent timestamp is kept.
    """
    seen: dict[str, str] = {}  # path → most recent ISO timestamp
    current_timestamp: Optional[str] = None
    # Strict ISO 8601 pattern: YYYY-MM-DDTHH:MM:SS±HH:MM (or Z)
    iso_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')

    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped:
            # Blank lines separate commit blocks, but do NOT reset the
            # timestamp.  ``git log --format=%aI --name-only`` inserts a
            # blank line between the format output and the file list:
            #
            #   2026-03-22T13:32:02+08:00   ← timestamp
            #   (blank)                      ← separator
            #   Knowledge/DailyActivity/...  ← file
            #
            # Resetting here would drop every file.  This was a
            # regression in the original parser, not a design choice.
            continue

        # ISO 8601 timestamp line (strict match)
        if iso_pattern.match(stripped):
            current_timestamp = stripped
            continue

        # File path line — only record if we have a timestamp
        if current_timestamp and stripped:
            if stripped not in seen and _is_artifact_file(stripped):
                seen[stripped] = current_timestamp

    # Build response sorted by timestamp descending
    artifacts = []
    for file_path, timestamp in seen.items():
        artifacts.append({
            "path": file_path,
            "title": Path(file_path).name,
            "type": _classify_extension(file_path),
            "modified_at": timestamp,
        })

    artifacts.sort(key=lambda a: a["modified_at"], reverse=True)
    return artifacts


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/artifacts/recent", response_model=list[ArtifactResponse])
async def get_recent_artifacts(
    workspace_id: str = Query(..., description="Workspace identifier"),
    limit: int = Query(20, ge=1, le=50, description="Max artifacts to return"),
) -> list[ArtifactResponse]:
    """Return recently modified files from the workspace git tree.

    Derives the file list from ``git log --diff-filter=ACMR --name-only``
    scoped to the active workspace path.  Files are deduplicated by path
    (most recent timestamp wins) and classified by extension.

    Args:
        workspace_id: Required workspace identifier (resolved via DB).
        limit: Maximum number of artifacts to return (1–50, default 20).

    Returns:
        List of ``ArtifactResponse`` sorted by ``modified_at`` descending.

    Raises:
        HTTPException: 404 if workspace is not configured.
        HTTPException: 422 if query params are invalid (handled by FastAPI).
    """
    workspace_path = await _get_workspace_path()

    if not Path(workspace_path).is_dir():
        raise HTTPException(status_code=404, detail="Workspace path not found")

    def _run_git_log() -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                "git", "log",
                "--diff-filter=ACMR",
                "--name-only",
                f"--format=%aI",
                "--since=30.days",
                f"-n{limit * 3}",
                "--no-merges",
            ],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            timeout=5,
        )

    try:
        result = await anyio.to_thread.run_sync(_run_git_log)
    except subprocess.TimeoutExpired:
        logger.warning("git log timed out for workspace %s", workspace_id)
        return []
    except FileNotFoundError:
        logger.warning("git not found on PATH")
        return []
    except Exception:
        logger.exception("Unexpected error running git log")
        return []

    if result.returncode != 0:
        # Not a git repo, no commits, detached HEAD, etc.
        logger.debug(
            "git log returned non-zero (%d) for workspace %s",
            result.returncode,
            workspace_id,
        )
        return []

    artifacts = _parse_git_log(result.stdout)
    return [ArtifactResponse(**a) for a in artifacts[:limit]]
