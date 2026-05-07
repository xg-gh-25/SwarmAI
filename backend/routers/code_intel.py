"""Code Intelligence API — project-scoped codebase health and reindex.

Endpoints:
    GET  /api/code-intel/{project}/summary  — codebase stats + freshness
    POST /api/code-intel/{project}/reindex  — trigger background re-index
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from core.code_intel import load_project_graph, get_code_intel_db_path

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Constants ────────────────────────────────────────────────────────────────

_STALE_THRESHOLD_DAYS = 7


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
    """
    graph = load_project_graph(project)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Code intelligence not found for project '{project}'")

    summary = graph.get_codebase_summary()

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


@router.post("/{project}/reindex", response_model=ReindexResponse, status_code=202)
async def trigger_reindex(project: str, background_tasks: BackgroundTasks):
    """Trigger a background re-index for the given project.

    Returns 202 Accepted immediately; indexing runs in background.
    Returns 404 if project has no code_intel.db.
    """
    graph = load_project_graph(project)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"Code intelligence not found for project '{project}'")

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
    """Run incremental re-index for a project. Called as background task."""
    try:
        from core.code_intel import get_code_intel_db_path, invalidate_cache
        from core.code_intel.graph_store import GraphStore
        from core.code_intel.parser import parse_repository

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

        import re
        content = tech_md.read_text(encoding="utf-8")
        match = re.search(r"\*\*Repo Path:\*\*\s*`([^`]+)`", content)
        if not match:
            logger.warning(f"Cannot reindex {project}: no repo_path in TECH.md")
            return

        repo_path = match.group(1)
        logger.info(f"Re-indexing {project} from {repo_path}")

        graph = GraphStore(db_path)
        result = parse_repository(repo_path)
        graph.bulk_upsert(result.nodes, result.edges)
        graph.set_meta("last_full_index", datetime.now(timezone.utc).isoformat())
        graph.close()

        # Invalidate cache so next read gets fresh data
        invalidate_cache(project)
        logger.info(f"Re-index complete for {project}")

    except Exception as e:
        logger.error(f"Re-index failed for {project}: {e}", exc_info=True)
