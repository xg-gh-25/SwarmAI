"""FastAPI router for the Artifacts API.

Two artifact systems coexist:

1. **Git-derived artifacts** (``GET /artifacts/recent``) — recently modified
   files from the workspace git tree.  Read-only, no new database tables.

2. **Pipeline artifacts** (``GET/POST /artifacts/pipeline/*``) — typed skill
   output chaining via the ``ArtifactRegistry``.  Filesystem-backed under
   ``Projects/<project>/.artifacts/``.  Supports publish, discover,
   pipeline state, and supersede operations.

Public endpoints:

- ``GET  /artifacts/recent``              — Git-derived recent files
- ``GET  /artifacts/pipeline/projects``   — Pipeline status for all projects
- ``GET  /artifacts/pipeline/discover``   — Discover artifacts by type
- ``GET  /artifacts/pipeline/state``      — Get pipeline state for a project
- ``POST /artifacts/pipeline/publish``    — Publish a new artifact
- ``POST /artifacts/pipeline/advance``    — Advance pipeline state
- ``POST /artifacts/pipeline/supersede``  — Mark artifact as superseded
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


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Artifact Endpoints (ArtifactRegistry)
# ─────────────────────────────────────────────────────────────────────────────

from core.artifact_registry import ArtifactRegistry, ARTIFACT_TYPES, PIPELINE_STATES


def _get_registry() -> ArtifactRegistry:
    """Lazy-init the singleton ArtifactRegistry."""
    workspace_path = swarm_workspace_manager.expand_path(
        swarm_workspace_manager.DEFAULT_WORKSPACE_CONFIG.get(
            "file_path", "~/.swarm-ai/SwarmWS"
        )
    )
    return ArtifactRegistry(Path(workspace_path))


class PipelineProjectStatus(BaseModel):
    """Pipeline status for a single project."""
    project: str
    pipeline_state: str
    artifact_count: int
    active_artifact_count: int
    latest_artifact: Optional[str]


class PipelineArtifactResponse(BaseModel):
    """A pipeline artifact returned from discovery."""
    id: str
    type: str
    producer: str
    created: str
    file: str
    summary: str
    superseded_by: Optional[str]


class PublishRequest(BaseModel):
    """Request body for publishing a new artifact."""
    project: str
    artifact_type: str
    data: dict
    producer: str
    summary: str
    topic: str = ""


class AdvanceRequest(BaseModel):
    """Request body for advancing pipeline state."""
    project: str
    state: str


class SupersedeRequest(BaseModel):
    """Request body for superseding an artifact."""
    project: str
    old_id: str
    new_id: str


@router.get(
    "/artifacts/pipeline/projects",
    response_model=list[PipelineProjectStatus],
)
async def get_pipeline_projects() -> list[PipelineProjectStatus]:
    """Return pipeline status for all projects."""
    reg = _get_registry()

    def _list():
        return reg.list_projects()

    statuses = await anyio.to_thread.run_sync(_list)
    return [
        PipelineProjectStatus(
            project=s.project,
            pipeline_state=s.pipeline_state,
            artifact_count=s.artifact_count,
            active_artifact_count=s.active_artifact_count,
            latest_artifact=s.latest_artifact,
        )
        for s in statuses
    ]


@router.get(
    "/artifacts/pipeline/discover",
    response_model=list[PipelineArtifactResponse],
)
async def discover_pipeline_artifacts(
    project: str = Query(..., description="Project name"),
    types: str = Query(..., description="Comma-separated artifact types"),
) -> list[PipelineArtifactResponse]:
    """Discover active artifacts of given types for a project."""
    reg = _get_registry()
    type_list = [t.strip() for t in types.split(",") if t.strip()]

    def _discover():
        return reg.discover(project, *type_list)

    artifacts = await anyio.to_thread.run_sync(_discover)
    return [
        PipelineArtifactResponse(
            id=a.id, type=a.type, producer=a.producer,
            created=a.created, file=a.file, summary=a.summary,
            superseded_by=a.superseded_by,
        )
        for a in artifacts
    ]


@router.get("/artifacts/pipeline/state")
async def get_pipeline_state(
    project: str = Query(..., description="Project name"),
) -> dict:
    """Get the current pipeline state for a project."""
    reg = _get_registry()

    def _get():
        return reg.get_pipeline_state(project)

    state = await anyio.to_thread.run_sync(_get)
    return {"project": project, "pipeline_state": state}


@router.post("/artifacts/pipeline/publish")
async def publish_pipeline_artifact(req: PublishRequest) -> dict:
    """Publish a new artifact for a project."""
    reg = _get_registry()

    def _publish():
        return reg.publish(
            project=req.project,
            artifact_type=req.artifact_type,
            data=req.data,
            producer=req.producer,
            summary=req.summary,
            topic=req.topic,
        )

    try:
        artifact_id = await anyio.to_thread.run_sync(_publish)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"artifact_id": artifact_id, "project": req.project}


@router.post("/artifacts/pipeline/advance")
async def advance_pipeline(req: AdvanceRequest) -> dict:
    """Advance a project's pipeline state."""
    reg = _get_registry()

    def _advance():
        reg.advance_pipeline(req.project, req.state)

    try:
        await anyio.to_thread.run_sync(_advance)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"project": req.project, "pipeline_state": req.state}


@router.post("/artifacts/pipeline/supersede")
async def supersede_artifact(req: SupersedeRequest) -> dict:
    """Mark an artifact as superseded by a newer one."""
    reg = _get_registry()

    def _supersede():
        reg.supersede(req.project, req.old_id, req.new_id)

    await anyio.to_thread.run_sync(_supersede)
    return {"old_id": req.old_id, "new_id": req.new_id, "project": req.project}


class LearnRequest(BaseModel):
    """Request body for recording pipeline outcome."""
    project: str
    evaluation_id: str
    outcome: str  # success, partial, failure, cancelled
    actual_effort: Optional[str] = None
    lessons: list[str] = []


@router.post("/artifacts/pipeline/learn")
async def record_learn_outcome(req: LearnRequest) -> dict:
    """Record pipeline outcome for learning feedback loop."""
    reg = _get_registry()

    def _learn():
        reg.record_outcome(
            project=req.project,
            evaluation_id=req.evaluation_id,
            outcome=req.outcome,
            actual_effort=req.actual_effort,
            lessons=req.lessons or None,
        )

    await anyio.to_thread.run_sync(_learn)
    return {"project": req.project, "outcome": req.outcome, "recorded": True}
