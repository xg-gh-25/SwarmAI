"""Search API endpoints for global search across entity types.

Provides three endpoints:
- GET /api/search — search across all entity types (ToDos, Tasks, PlanItems,
  Communications, Artifacts, Reflections, and ChatThreads via ThreadSummary).
- GET /api/search/threads — dedicated thread search via ThreadSummary.
- GET /api/search/sessions — full-text search over chat MESSAGE CONTENT,
  returning matching chat sessions (powers the History overlay).

Both entity/thread endpoints support scope filtering (workspace_id or "all")
and limit results to 50 per entity type.

CRITICAL: /threads queries ThreadSummary.summary_text, NOT raw
ChatMessages.content (Requirement 31.1). /sessions is the one path that DOES
FTS the raw message content — it returns only session metadata (id/title/agent/
timestamps), never message bodies, and applies the sent-filter + workspace scope.

Requirements: 31.7, 38.10
"""
import logging
from typing import Optional, List

from fastapi import APIRouter, Query

from schemas.message import ChatSessionResponse
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


@router.get("/sessions", response_model=List[ChatSessionResponse])
async def search_sessions(
    query: str = Query(..., min_length=1, description="Search query string"),
    workspace_id: Optional[str] = Query(
        None,
        description="Optional workspace scope. Omit for app-wide (matches the "
                    "workspace-blind session list the History empty-query view uses).",
    ),
    limit: int = Query(50, ge=1, le=100, description="Max sessions to return."),
):
    """Full-text search over chat MESSAGE CONTENT, returning matching sessions.

    Powers the History overlay's search box. Unlike /threads (which searches
    ThreadSummary text), this searches the raw message content via FTS5, so a
    session whose *title* doesn't contain the term still surfaces when its
    conversation body does.

    Returns session metadata only (id/agent/title/timestamps) — never message
    bodies. Unsent drafts are excluded and results are workspace-scoped by the
    underlying SessionRecall.search_session_list (which JOINs sessions).
    """
    from database import db
    from core.session_recall import SessionRecall

    recall = SessionRecall(db_path=db.db_path)
    rows = recall.search_session_list(query, limit=limit, workspace_id=workspace_id)
    # Map DB column `last_accessed` → response field `last_accessed_at`
    # (same mapping list_sessions does — do NOT `**row`).
    return [
        ChatSessionResponse(
            id=r["id"],
            agent_id=r["agent_id"] or "",
            title=r["title"] or "",
            created_at=r["created_at"] or "",
            last_accessed_at=r["last_accessed"] or "",
        )
        for r in rows
    ]
