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

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from core.code_intel import (
    owned_graphs,
    project_graph,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Constants ────────────────────────────────────────────────────────────────

# Project name validation: alphanumeric, hyphens, underscores only.
# Prevents path traversal (../, /) in the {project} path parameter.
_SAFE_PROJECT_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

# A re-index runs in a child process (core.code_intel.build): the daemon never parses
# (design 2026-10-29-code-intel-unification §0.2). project → the child; "in progress"
# means the child is still alive. Two children for one graph cannot both write: the
# build takes the graph's write lock.
_reindex_children: dict = {}


# ── Response Models ──────────────────────────────────────────────────────────

class CodeIntelSummary(BaseModel):
    """The status line of a graph (Brain Hub): what it holds and whether it is behind."""
    symbol_count: int
    edge_count: int
    file_count: int
    languages: dict[str, int]
    last_indexed_at: str | None
    indexed_commit: str | None
    # "fresh" | "stale" | "unknown" (graph commit vs repo HEAD) | "rebuilding" (written
    # by an older parser; a rebuild child was asked for)
    freshness_status: str


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
    """A graph's status line: counts from the graph, and fresh/stale from one
    ``git rev-parse`` against its commit marker. 404 when the project has no graph.
    Dead code and modules are not here (``analysis`` computes them): this endpoint is
    read on every Brain Hub open and must stay cheap. A graph an older parser wrote
    reads "rebuilding": a rebuild child is asked for (at most every ten minutes), so
    after a deploy that changes the parser nobody has to re-index."""
    ref, graph = await _found_or_404(project, repo)

    def _work() -> CodeIntelSummary:
        from core.code_intel.build import parser_outdated, request_rebuild
        from core.code_intel.freshness import head_stamp
        c = graph.counts()
        if parser_outdated(graph):
            request_rebuild(project, ref)
            status = "rebuilding"
        else:
            stale, _head = head_stamp(graph)
            status = "unknown" if stale is None else ("stale" if stale else "fresh")
        return CodeIntelSummary(
            symbol_count=c["nodes"], edge_count=c["edges"], file_count=c["files"],
            languages=c["languages"], last_indexed_at=c["last_indexed"],
            indexed_commit=c["indexed_commit"], freshness_status=status,
        )

    return await asyncio.to_thread(_work)


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
async def trigger_reindex(project: str):
    """Re-index every graph of a project in a child process.

    Returns 202 Accepted at once; ``already_indexing`` while the previous child is
    alive; 404 when the project owns no repo.
    """
    if not _SAFE_PROJECT_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")

    # F2: the project must own a repo (a graph not built yet is built by the re-index)
    from jobs.paths import PROJECTS_DIR
    if not owned_graphs(PROJECTS_DIR / project):
        raise HTTPException(status_code=404, detail=f"Code intelligence not found for project '{project}'")

    # poll() reaps a finished child; finished ones are dropped (no zombie stays)
    for name in [n for n, c in _reindex_children.items() if c.poll() is not None]:
        del _reindex_children[name]
    if project in _reindex_children:
        return ReindexResponse(status="already_indexing", project=project)
    from core.code_intel.build import spawn_build
    try:
        _reindex_children[project] = await asyncio.to_thread(spawn_build, project, full=True)
    except OSError as exc:
        logger.error("Re-index of %s could not start: %s", project, exc)
        raise HTTPException(status_code=500, detail="the re-index could not start") from None
    return ReindexResponse(status="indexing", project=project)


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _graph_or_404(project: str, repo: str | None):
    """The graph a read endpoint shows (400 on a bad name, 404 when there is none)."""
    return (await _found_or_404(project, repo))[1]


async def _found_or_404(project: str, repo: str | None):
    """``(ref, graph)`` of the graph a read endpoint shows. The lookup reads
    bindings.yaml and may open the db: it runs off the loop."""
    if not _SAFE_PROJECT_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    found = await asyncio.to_thread(project_graph, project, repo)
    if found is None:
        what = f"repo '{repo}' of project '{project}'" if repo else f"project '{project}'"
        raise HTTPException(status_code=404, detail=f"Code intelligence not found for {what}")
    return found


