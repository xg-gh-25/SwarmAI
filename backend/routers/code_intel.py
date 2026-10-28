"""Code Intelligence API — project-scoped codebase health and reindex.

Endpoints:
    GET  /api/code-intel/{project}/summary   — codebase stats + freshness
    GET  /api/code-intel/{project}/analysis  — codebase analysis, computed on demand
    GET  /api/code-intel/{project}/graph     — top nodes + edges for the graph view
    POST /api/code-intel/{project}/reindex   — trigger background re-index (every graph)

The read endpoints show one graph: ``?repo=<binding repo>`` picks a bound repo's,
default the project's first (``core.code_intel.project_graph``).
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, BackgroundTasks, Response
from pydantic import BaseModel

from core.code_intel import (
    get_code_intel_db_path,
    project_graph,
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

@router.get("/{project}/analysis")
async def get_code_intel_analysis(project: str, layers: str = "", repo: str | None = None):
    """The codebase analysis of a project's graph, computed now (never stored).

    Shape and versioning: ``core.code_intel.analysis`` (``schema`` + ``version``).

    ``layers``: comma-separated subset of ``core.code_intel.analysis.LAYERS``
    (structure, graph_clusters, graph_analysis, domain_rules); empty = all. 400 on an
    unknown layer or project name, 404 when the project has no code graph. A layer
    that fails is listed under ``errors`` and the rest are returned.
    """
    if not _SAFE_PROJECT_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    from core.code_intel.analysis import analyze_project, resolve_layers
    try:
        names = resolve_layers(layers.split(",") if layers else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    def _run() -> str | None:
        result = analyze_project(project, names, repo=repo)
        # Serialize here too: a large repo's analysis is megabytes of JSON, and
        # encoding it on the event loop would stall every other request.
        return None if result is None else json.dumps(result, ensure_ascii=False)

    body = await asyncio.to_thread(_run)
    if body is None:
        raise HTTPException(status_code=404, detail=f"Code intelligence not found for project '{project}'")
    return Response(content=body, media_type="application/json")


@router.get("/{project}/summary", response_model=CodeIntelSummary)
async def get_code_intel_summary(project: str, repo: str | None = None):
    """Return compact codebase stats for the given project.

    Returns 404 if no code_intel.db exists for the project.
    Uses asyncio.to_thread to avoid blocking the event loop (F1 fix:
    get_codebase_summary() calls find_dead_code() which is a full table scan).
    """
    graph = await _graph_or_404(project, repo)

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
async def get_code_intel_graph(project: str, limit: int = 300, repo: str | None = None):
    """Return top-N nodes + edges for force-directed graph visualization.

    Nodes ranked by connectivity (most-connected first).
    Only edges between included nodes are returned.
    """
    graph = await _graph_or_404(project, repo)

    # Cap limit to prevent full-graph dump (min 1 for testing, max 1000)
    limit = min(max(limit, 1), 1000)

    data = await asyncio.to_thread(graph.get_graph_data, limit)
    return data


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

async def _graph_or_404(project: str, repo: str | None):
    """The graph a read endpoint shows (400 on a bad name, 404 when there is none).
    The lookup reads bindings.yaml and may open the db: it runs off the loop."""
    if not _SAFE_PROJECT_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    found = await asyncio.to_thread(project_graph, project, repo)
    if found is None:
        what = f"repo '{repo}' of project '{project}'" if repo else f"project '{project}'"
        raise HTTPException(status_code=404, detail=f"Code intelligence not found for {what}")
    return found[1]


def _compute_freshness(last_indexed: str | None) -> str:
    """Determine freshness status from last_indexed timestamp."""
    from core.code_intel.graph_store import parse_index_time
    ts = parse_index_time(last_indexed)
    if ts is None:
        return "unknown"
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(ts, timezone.utc)
    return "stale" if age > timedelta(days=_STALE_THRESHOLD_DAYS) else "fresh"


def _run_reindex(project: str) -> None:
    """Re-index every code graph of a project (its own db and each bound repo's).
    Called as a background task.

    Releases _reindex_in_progress guard on completion (success or failure).
    """
    try:
        from jobs.paths import PROJECTS_DIR
        from core.code_intel import load_project_graphs
        project_dir = PROJECTS_DIR / project
        # Rebuild through the cached graphs (the ones recall and the watchers use),
        # each under its write lock: readers see the old graph or the new one, never
        # an emptied one, and nothing closes a store a watcher holds.
        graphs = load_project_graphs(project)
        if not graphs:
            logger.warning(f"Cannot reindex {project}: no code_intel.db could be opened")
            return
        for ref, graph in graphs:
            _reindex_one(project, project_dir, ref, graph)
        logger.info(f"Re-index complete for {project}")

    except Exception as e:
        logger.error(f"Re-index failed for {project}: {e}", exc_info=True)
    finally:
        # F3: Always release the concurrency guard
        _reindex_in_progress.pop(project, None)


def _reindex_one(project: str, project_dir, ref, graph) -> None:
    """Rebuild one of the project's graphs from the repo it holds."""
    import contextlib
    from pathlib import Path as _Path

    from core.code_intel import repo_root_is_owned, resolve_owned_repo_root
    # The repo to parse. A bind-managed db is ONLY ever its own clone; never fall
    # back to another repo, which would mix two repos in one graph. The project's
    # own db: the repo it records if the project owns it, else its owned repo.
    name = ref.repo or project
    if ref.repo_root is not None:
        repo_path = str(ref.repo_root) if ref.repo_root.is_dir() else None
    else:
        stored = graph.get_meta("repo_root")
        if stored and repo_root_is_owned(project_dir, stored):
            repo_path = stored
        else:
            repo_path = resolve_owned_repo_root(project_dir)
    if not repo_path:
        logger.warning(f"Cannot reindex {name}: it owns no local repo "
                       "(TECH.md / bindings.yaml)")
        return
    logger.info(f"Re-indexing {name} from {repo_path}")
    # A bind-managed clone may be mid-bind (cleared, re-cloning, rebuilt by the bind
    # itself): take its bind lock, and skip rather than parse a half-cloned tree.
    from core.ddd_bindings import BindInProgress
    guard = contextlib.nullcontext()
    if ref.repo_root is not None:
        from core.ddd_bindings import _bind_lock
        guard = _bind_lock(ref.repo_root, name)
    try:
        with guard:
            # HEAD before the parse: a commit landing while it runs stays unindexed
            # (the incremental path picks it up) instead of being stamped as done.
            from core.code_intel.freshness import GitError, _git
            try:
                head = _git(_Path(repo_path), ["rev-parse", "HEAD"]).strip() or None
            except GitError:
                head = None
            # The one full rebuild: it replaces the graph under the store's write
            # lock, stores routes and coverage, and leaves an empty parse unstamped.
            from core.code_intel.build import rebuild
            built = rebuild(graph, repo_path, head=head)
            if built.empty:
                logger.warning(f"Re-index of {name}: the parse produced nothing; "
                               "the graph was left as it was")
    except BindInProgress as exc:
        logger.warning(f"Cannot reindex {name} now: {exc}")
