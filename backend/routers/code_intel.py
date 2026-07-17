"""Code Intelligence API — project-scoped codebase health and reindex.

Endpoints:
    GET  /api/code-intel/{project}/summary  — codebase stats + freshness
    POST /api/code-intel/{project}/reindex  — trigger background re-index
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from core.code_intel import (
    load_project_graph,
    get_code_intel_db_path,
    invalidate_cache,
    extract_repo_path,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Constants ────────────────────────────────────────────────────────────────

_STALE_THRESHOLD_DAYS = 7

# Project name validation: alphanumeric, hyphens, underscores only.
# Prevents path traversal (../, /) in the {project} path parameter.
_SAFE_PROJECT_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

# Concurrency guard: prevent multiple parallel reindex for the same project (F3)
# Keyed by project → start timestamp. TTL prevents permanent stall on crash.
# NOTE: This is process-local — a daemon restart clears the dict. This is safe
# because the background thread running _run_reindex dies with the process,
# so no orphan indexing survives a restart. The TTL exists for the edge case
# where _run_reindex raises before reaching its `finally` cleanup.
_reindex_in_progress: dict[str, float] = {}
_REINDEX_TTL_SECONDS = 600  # 10 min max


# ── Response Models ──────────────────────────────────────────────────────────

class CodeIntelSummary(BaseModel):
    """Compact codebase health response for BottomBar indicator."""
    symbol_count: int
    edge_count: int
    file_count: int
    unused_exports_count: int
    unused_exports_pct: float
    entry_points: int
    languages: dict[str, int]
    modules_top5: list[dict]
    last_indexed_at: str | None
    freshness_status: str  # "fresh" | "stale" | "unknown"


class ReindexResponse(BaseModel):
    """Reindex request accepted."""
    status: str
    project: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/{project}/summary", response_model=CodeIntelSummary)
async def get_code_intel_summary(project: str):
    """Return compact codebase stats for the given project.

    Returns 404 if no code_intel.db exists for the project.
    Uses asyncio.to_thread to avoid blocking the event loop (F1 fix:
    get_codebase_summary() calls find_dead_code() which is a full table scan).
    """
    if not _SAFE_PROJECT_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    graph = load_project_graph(project)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Code intelligence not found for project '{project}'")

    # F1: Offload sync SQLite I/O to threadpool — find_dead_code() is O(n) scan
    summary = await asyncio.to_thread(graph.get_codebase_summary)

    total_nodes = summary.get("total_nodes", 0)
    dead_count = summary.get("dead_code_count", 0)
    unused_pct = (dead_count / total_nodes * 100) if total_nodes > 0 else 0.0

    # Top 5 modules by function count
    modules = summary.get("modules", {})
    modules_sorted = sorted(
        modules.items(),
        key=lambda x: x[1].get("function_count", 0),
        reverse=True,
    )[:5]
    modules_top5 = [
        {"name": name, **stats} for name, stats in modules_sorted
    ]

    # Freshness calculation
    last_indexed = summary.get("last_indexed")
    freshness_status = _compute_freshness(last_indexed)

    return CodeIntelSummary(
        symbol_count=total_nodes,
        edge_count=summary.get("total_edges", 0),
        file_count=summary.get("total_files", 0),
        unused_exports_count=dead_count,
        unused_exports_pct=round(unused_pct, 1),
        entry_points=summary.get("entry_point_count", 0),
        languages=summary.get("languages", {}),
        modules_top5=modules_top5,
        last_indexed_at=last_indexed,
        freshness_status=freshness_status,
    )


@router.get("/{project}/graph")
async def get_code_intel_graph(project: str, limit: int = 300):
    """Return top-N nodes + edges for force-directed graph visualization.

    Nodes ranked by connectivity (most-connected first).
    Only edges between included nodes are returned.
    """
    if not _SAFE_PROJECT_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    graph = load_project_graph(project)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Code intelligence not found for project '{project}'")

    # Cap limit to prevent full-graph dump (min 1 for testing, max 1000)
    limit = min(max(limit, 1), 1000)

    data = await asyncio.to_thread(graph.get_graph_data, limit)
    return data


@router.get("/{project}/routes")
async def get_code_intel_routes(project: str):
    """Return all detected HTTP routes for a project.

    Returns JSON list of routes with method, path, handler, framework,
    file_path, and line_number.
    """
    if not _SAFE_PROJECT_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    graph = load_project_graph(project)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Code intelligence not found for project '{project}'")

    routes = await asyncio.to_thread(graph.get_routes)
    return [
        {
            "method": r["method"],
            "path": r["path"],
            "handler": r["handler_node_id"],
            "framework": r["framework"],
            "file_path": r["file_path"],
            "line_number": r["line_number"],
        }
        for r in routes
    ]


@router.post("/{project}/reindex", response_model=ReindexResponse, status_code=202)
async def trigger_reindex(project: str, background_tasks: BackgroundTasks):
    """Trigger a background re-index for the given project.

    Returns 202 Accepted immediately; indexing runs in background.
    Returns 404 if project has no code_intel.db.
    Returns 409 if reindex already in progress for this project.
    """
    if not _SAFE_PROJECT_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")

    # F2: Check existence via path, not by loading full GraphStore
    db_path = get_code_intel_db_path(project)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail=f"Code intelligence not found for project '{project}'")

    # F3: Concurrency guard with TTL — prevent stale locks on crash
    if project in _reindex_in_progress:
        if time.time() - _reindex_in_progress[project] < _REINDEX_TTL_SECONDS:
            return ReindexResponse(status="already_indexing", project=project)
        # Stale lock — allow retry
        logger.warning(f"Stale reindex lock for {project}, allowing retry")

    _reindex_in_progress[project] = time.time()
    background_tasks.add_task(_run_reindex, project)

    return ReindexResponse(status="indexing", project=project)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _compute_freshness(last_indexed: str | None) -> str:
    """Determine freshness status from last_indexed timestamp."""
    if not last_indexed:
        return "unknown"
    try:
        # Parse ISO format — handle both timezone-aware and naive
        indexed_dt = datetime.fromisoformat(last_indexed.replace("Z", "+00:00"))
        if indexed_dt.tzinfo is None:
            indexed_dt = indexed_dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - indexed_dt
        return "stale" if age > timedelta(days=_STALE_THRESHOLD_DAYS) else "fresh"
    except (ValueError, TypeError):
        return "unknown"


def _run_reindex(project: str) -> None:
    """Run incremental re-index for a project. Called as background task.

    Releases _reindex_in_progress guard on completion (success or failure).
    """
    try:
        from core.code_intel.graph_store import GraphStore
        from core.code_intel.parser import parse_repo

        db_path = get_code_intel_db_path(project)
        if not db_path.exists():
            logger.warning(f"Cannot reindex {project}: no code_intel.db")
            return

        # Read repo path from TECH.md
        projects_dir = db_path.parent
        tech_md = projects_dir / "TECH.md"
        if not tech_md.exists():
            logger.warning(f"Cannot reindex {project}: no TECH.md with repo path")
            return

        content = tech_md.read_text(encoding="utf-8")
        # Multi-format parse via the shared helper (core/code_intel.extract_repo_path).
        # The old inline single-format regex ('**Repo Path:**') matched ZERO of the
        # real project TECH.md files (they use '**Local:**' / a bare '## Codebase
        # Location' path line), so reindex was silently dead for every project.
        raw_repo_path = extract_repo_path(content)
        if not raw_repo_path:
            logger.warning(f"Cannot reindex {project}: no repo_path in TECH.md")
            return

        from pathlib import Path as _Path
        # Normalize (helper is pure — dir-validation/normalization is the caller's job).
        repo_path = str(_Path(raw_repo_path.rstrip("/")).resolve())
        logger.info(f"Re-indexing {project} from {repo_path}")

        graph = GraphStore(db_path)
        parse_results = parse_repo(_Path(repo_path))
        graph.bulk_insert(parse_results)
        graph.set_meta("last_full_index", datetime.now(timezone.utc).isoformat())
        graph.close()

        # Invalidate cache so next read gets fresh data
        invalidate_cache(project)
        logger.info(f"Re-index complete for {project}")

    except Exception as e:
        logger.error(f"Re-index failed for {project}: {e}", exc_info=True)
    finally:
        # F3: Always release the concurrency guard
        _reindex_in_progress.pop(project, None)
