"""SearchManager for global search across entity types.

Implements search across ToDos, Tasks, and ChatThreads (via ThreadSummary).
Supports workspace scope filtering and limits results to 50 per entity type.

Legacy Operating Loop entities (PlanItems, Communications, Artifacts,
Reflections) were removed as part of the operating-loop-cleanup spec.

CRITICAL: Thread search queries ThreadSummary.summary_text, NOT raw
ChatMessages.content (Requirement 31.1).

Requirements: 31.1-31.7, 38.1-38.12
"""
import logging
from typing import Optional, List

import aiosqlite

from database import db
from schemas.search import (
    EntityTypeResults,
    SearchResultItem,
    SearchResults,
)

logger = logging.getLogger(__name__)

# Maximum results per entity type (Requirement 38.11)
MAX_RESULTS_PER_TYPE = 50

# Entity types that support search
SEARCHABLE_ENTITY_TYPES = [
    "todo",
    "task",
    "thread",
]


# Table config: (table_name, entity_type_label, title_col, description_col)
_ENTITY_TABLE_CONFIG = [
    ("todos", "todo", "title", "description"),
    ("tasks", "task", "title", "description"),
]


class SearchManager:
    """Manages global search across all DB-canonical entity types.

    Validates: Requirements 31.1-31.7, 38.1-38.12
    """

    async def search(
        self,
        query: str,
        scope: str = "all",
        entity_types: Optional[List[str]] = None,
    ) -> SearchResults:
        """Search across entity types using LIKE matching on title/description.

        Args:
            query: Search string (matched via SQL LIKE on title and description).
            scope: A workspace_id to restrict results, or "all" for all
                   non-archived workspaces.
            entity_types: Optional list of entity type names to search.
                          If None, searches all types.

        Returns:
            SearchResults grouped by entity type, max 50 per type.

        Validates: Requirements 38.2, 38.3, 38.4, 38.10, 38.11
        """
        if not query or not query.strip():
            return SearchResults(query=query, scope=scope, groups=[], total=0)

        allowed_types = set(entity_types or SEARCHABLE_ENTITY_TYPES)
        like_pattern = f"%{query}%"

        # Pre-fetch workspace names and archive status for badge display
        workspace_map = await self._get_workspace_map()

        # Determine workspace IDs in scope
        scope_ws_ids = await self._resolve_scope_ids(scope, workspace_map)

        groups: List[EntityTypeResults] = []
        total = 0

        # Search standard entity tables
        for table, entity_type, title_col, desc_col in _ENTITY_TABLE_CONFIG:
            if entity_type not in allowed_types:
                continue
            result = await self._search_table(
                table, entity_type, title_col, desc_col,
                like_pattern, scope_ws_ids, workspace_map,
            )
            if result.total > 0:
                groups.append(result)
                total += result.total

        # Search threads via ThreadSummary (Requirement 31.1, 31.5)
        if "thread" in allowed_types:
            result = await self._search_threads(
                like_pattern, scope_ws_ids, workspace_map,
            )
            if result.total > 0:
                groups.append(result)
                total += result.total

        return SearchResults(
            query=query,
            scope=scope,
            groups=groups,
            total=total,
        )

    async def search_threads(
        self,
        query: str,
        scope: str = "all",
    ) -> SearchResults:
        """Dedicated thread search via ThreadSummary.

        Queries ThreadSummary.summary_text and key_decisions, NOT raw
        ChatMessages.content.

        Args:
            query: Search string.
            scope: workspace_id or "all".

        Returns:
            SearchResults containing only thread results.

        Validates: Requirements 31.1, 31.5, 31.7
        """
        if not query or not query.strip():
            return SearchResults(query=query, scope=scope, groups=[], total=0)

        like_pattern = f"%{query}%"
        workspace_map = await self._get_workspace_map()
        scope_ws_ids = await self._resolve_scope_ids(scope, workspace_map)

        result = await self._search_threads(like_pattern, scope_ws_ids, workspace_map)
        total = result.total

        groups = [result] if total > 0 else []
        return SearchResults(
            query=query,
            scope=scope,
            groups=groups,
            total=total,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_workspace_map(self) -> dict:
        """Return {workspace_id: {name, is_archived}} for the singleton workspace."""
        ws = await db.workspace_config.get_config()
        if not ws:
            return {}
        return {
            ws["id"]: {
                "name": ws.get("name", "SwarmWS"),
                "is_archived": False,
            }
        }

    async def _resolve_scope_ids(
        self,
        scope: str,
        workspace_map: dict,
    ) -> Optional[List[str]]:
        """Resolve scope to a list of workspace IDs, or None for unrestricted.

        - scope="all": returns IDs of all non-archived workspaces.
        - scope=<workspace_id>: returns [workspace_id].
        - Archived workspaces are included in search results but marked
          (Requirement 38.12), so scope="all" excludes them from the
          query set while a direct workspace_id scope includes it.

        Validates: Requirements 38.3, 38.12
        """
        if scope == "all":
            return [
                ws_id
                for ws_id, info in workspace_map.items()
                if not info["is_archived"]
            ]
        # Specific workspace — include even if archived (Req 38.12)
        return [scope]

    async def _search_table(
        self,
        table_name: str,
        entity_type: str,
        title_col: str,
        desc_col: Optional[str],
        like_pattern: str,
        scope_ws_ids: Optional[List[str]],
        workspace_map: dict,
    ) -> EntityTypeResults:
        """Run a LIKE search on a single entity table."""
        # Build WHERE clause
        conditions: List[str] = []
        params: List[str] = []

        # Scope filter
        if scope_ws_ids is not None:
            placeholders = ", ".join("?" for _ in scope_ws_ids)
            conditions.append(f"workspace_id IN ({placeholders})")
            params.extend(scope_ws_ids)

        # Text match on title and optionally description
        if desc_col:
            conditions.append(f"({title_col} LIKE ? OR {desc_col} LIKE ?)")
            params.extend([like_pattern, like_pattern])
        else:
            conditions.append(f"{title_col} LIKE ?")
            params.append(like_pattern)

        where = " AND ".join(conditions)

        # Count query
        count_sql = f"SELECT COUNT(*) FROM {table_name} WHERE {where}"
        # Data query (limited)
        data_sql = (
            f"SELECT * FROM {table_name} WHERE {where} "
            f"ORDER BY updated_at DESC LIMIT {MAX_RESULTS_PER_TYPE}"
        )

        # NOTE: Accessing private _get_connection() — consider adding a public
        # db.get_connection() method for cross-table queries
        async with db.todos._get_connection() as conn:
            conn.row_factory = aiosqlite.Row

            async with conn.execute(count_sql, params) as cur:
                row = await cur.fetchone()
                total_count = row[0] if row else 0

            items: List[SearchResultItem] = []
            if total_count > 0:
                async with conn.execute(data_sql, params) as cur:
                    rows = await cur.fetchall()
                    for r in rows:
                        d = dict(r)
                        ws_info = workspace_map.get(d.get("workspace_id", ""), {})
                        items.append(SearchResultItem(
                            id=d["id"],
                            entity_type=entity_type,
                            title=d.get(title_col, ""),
                            description=d.get(desc_col, "") if desc_col else None,
                            workspace_id=d.get("workspace_id", ""),
                            workspace_name=ws_info.get("name"),
                            is_archived=ws_info.get("is_archived", False),
                            updated_at=d.get("updated_at", ""),
                        ))

        return EntityTypeResults(
            entity_type=entity_type,
            items=items,
            total=total_count,
            has_more=total_count > MAX_RESULTS_PER_TYPE,
        )

    async def _search_threads(
        self,
        like_pattern: str,
        scope_ws_ids: Optional[List[str]],
        workspace_map: dict,
    ) -> EntityTypeResults:
        """Search threads via ThreadSummary, NOT raw ChatMessages.

        Joins thread_summaries → chat_threads to get workspace_id and title,
        then matches on summary_text and key_decisions.

        Validates: Requirements 31.1, 31.2, 31.5
        """
        conditions: List[str] = []
        params: List[str] = []

        # Scope filter on the chat_threads table
        if scope_ws_ids is not None:
            placeholders = ", ".join("?" for _ in scope_ws_ids)
            conditions.append(f"ct.workspace_id IN ({placeholders})")
            params.extend(scope_ws_ids)

        # Text match on summary_text and key_decisions
        conditions.append(
            "(ts.summary_text LIKE ? OR ts.key_decisions LIKE ?)"
        )
        params.extend([like_pattern, like_pattern])

        where = " AND ".join(conditions)

        count_sql = (
            "SELECT COUNT(*) FROM thread_summaries ts "
            "JOIN chat_threads ct ON ts.thread_id = ct.id "
            f"WHERE {where}"
        )
        data_sql = (
            "SELECT ts.id AS summary_id, ts.thread_id, ts.summary_text, "
            "ts.key_decisions, ts.updated_at, "
            "ct.workspace_id, ct.title AS thread_title "
            "FROM thread_summaries ts "
            "JOIN chat_threads ct ON ts.thread_id = ct.id "
            f"WHERE {where} "
            f"ORDER BY ts.updated_at DESC LIMIT {MAX_RESULTS_PER_TYPE}"
        )

        # NOTE: Accessing private _get_connection() — consider adding a public
        # db.get_connection() method for cross-table queries
        async with db.todos._get_connection() as conn:
            conn.row_factory = aiosqlite.Row

            async with conn.execute(count_sql, params) as cur:
                row = await cur.fetchone()
                total_count = row[0] if row else 0

            items: List[SearchResultItem] = []
            if total_count > 0:
                async with conn.execute(data_sql, params) as cur:
                    rows = await cur.fetchall()
                    for r in rows:
                        d = dict(r)
                        ws_info = workspace_map.get(d.get("workspace_id", ""), {})
                        items.append(SearchResultItem(
                            id=d["thread_id"],
                            entity_type="thread",
                            title=d.get("thread_title", ""),
                            description=d.get("summary_text", ""),
                            workspace_id=d.get("workspace_id", ""),
                            workspace_name=ws_info.get("name"),
                            is_archived=ws_info.get("is_archived", False),
                            updated_at=d.get("updated_at", ""),
                        ))

        return EntityTypeResults(
            entity_type="thread",
            items=items,
            total=total_count,
            has_more=total_count > MAX_RESULTS_PER_TYPE,
        )


# Module-level singleton (matches project pattern)
search_manager = SearchManager()
