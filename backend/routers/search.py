"""Search API endpoints for global search across entity types.

Provides two endpoints:
- GET /api/search — search across all entity types (ToDos, Tasks, PlanItems,
  Communications, Artifacts, Reflections, and ChatThreads via ThreadSummary).
- GET /api/search/threads — dedicated thread search via ThreadSummary.

Both endpoints support scope filtering (workspace_id or "all") and limit
results to 50 per entity type.

CRITICAL: Thread search queries ThreadSummary.summary_text, NOT raw
ChatMessages.content (Requirement 31.1).

Requirements: 31.7, 38.10
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, Query

from schemas.search import SearchResults
from core.search_manager import search_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=SearchResults)
async def search(
    query: str = Query(..., min_length=1, description="Search query string"),
    scope: str = Query("all", description="Workspace ID or 'all' for all non-archived workspaces"),
    entity_types: Optional[str] = Query(
        None,
        description="Comma-separated entity types to search (e.g. 'todos,tasks,artifacts'). "
                    "If omitted, searches all types.",
    ),
):
    """Search across entity types with query, scope, and entity_types params.

    Results are grouped by entity type with a maximum of 50 items per type.

    Requirement 38.10: GET /api/search with query, scope, entity_types params.
    Requirement 38.11: Results limited to 50 per entity type.
    """
    parsed_types: Optional[List[str]] = None
    if entity_types:
        parsed_types = [t.strip() for t in entity_types.split(",") if t.strip()]

    return await search_manager.search(
        query=query,
        scope=scope,
        entity_types=parsed_types,
    )


@router.get("/threads", response_model=SearchResults)
async def search_threads(
    query: str = Query(..., min_length=1, description="Search query string"),
    scope: str = Query("all", description="Workspace ID or 'all' for all non-archived workspaces"),
):
    """Search chat threads via ThreadSummary content.

    Queries ThreadSummary.summary_text and key_decisions, NOT raw
    ChatMessages.content.

    Requirement 31.7: GET /api/search/threads with query parameter.
    Requirement 31.5: Search queries ThreadSummary.summary_text and key_decisions.
    """
    return await search_manager.search_threads(
        query=query,
        scope=scope,
    )
