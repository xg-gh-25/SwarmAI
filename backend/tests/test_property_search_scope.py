"""Property-based tests for search scope filtering.

**Feature: workspace-refactor, Property 28: Search respects scope**

Uses Hypothesis to verify that:
1. scope=workspace_id returns only items from that workspace
2. scope="all" returns items from all non-archived workspaces
3. scope="all" excludes archived workspaces
4. scope=specific_archived_workspace_id still returns items from that workspace

**Validates: Requirements 38.1-38.12**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone
from uuid import uuid4

from database import db
from core.search_manager import search_manager
from tests.helpers import now_iso, create_workspace


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_ALPHA_NUM = st.characters(whitelist_categories=("L", "N"))

marker_strategy = st.text(
    alphabet=_ALPHA_NUM,
    min_size=6,
    max_size=20,
).map(lambda s: f"SCO{s}SCO").filter(lambda s: len(s.strip()) >= 12)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_todo(workspace_id: str, title: str) -> dict:
    now = now_iso()
    todo = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "title": title,
        "description": f"Description for {title}",
        "source_type": "manual",
        "status": "pending",
        "priority": "none",
        "created_at": now,
        "updated_at": now,
    }
    await db.todos.put(todo)
    return todo


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestScopeWorkspaceIdReturnsOnlyMatching:
    """Property 28: scope=workspace_id returns only items from that workspace.

    *For any* two workspaces A and B each containing a ToDo with a shared
    marker, searching with scope=A returns only A's item.

    **Validates: Requirements 38.3**
    """

    @given(marker=marker_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_scope_workspace_returns_only_that_workspace(
        self,
        marker: str,
    ):
        """Scoped search returns only items belonging to the target workspace.

        **Validates: Requirements 38.3**
        """
        ws_a = await create_workspace(name="WS-A")
        ws_b = await create_workspace(name="WS-B")

        todo_a = await _create_todo(ws_a["id"], f"Item {marker} in A")
        todo_b = await _create_todo(ws_b["id"], f"Item {marker} in B")

        result = await search_manager.search(marker, scope=ws_a["id"])

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }
        assert todo_a["id"] in all_ids, (
            f"ToDo in workspace A should appear when scope=ws_a"
        )
        assert todo_b["id"] not in all_ids, (
            f"ToDo in workspace B must NOT appear when scope=ws_a"
        )


class TestScopeAllReturnsNonArchived:
    """Property 28: scope='all' returns items from all non-archived workspaces.

    *For any* two non-archived workspaces each containing a ToDo with a
    shared marker, searching with scope='all' returns both items.

    **Validates: Requirements 38.3**
    """

    @given(marker=marker_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_scope_all_returns_items_from_all_non_archived(
        self,
        marker: str,
    ):
        """scope='all' aggregates results across all non-archived workspaces.

        **Validates: Requirements 38.3**
        """
        ws_a = await create_workspace(name="WS-A")
        ws_b = await create_workspace(name="WS-B")

        todo_a = await _create_todo(ws_a["id"], f"Item {marker} in A")
        todo_b = await _create_todo(ws_b["id"], f"Item {marker} in B")

        result = await search_manager.search(marker, scope="all")

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }
        assert todo_a["id"] in all_ids, (
            "ToDo in non-archived workspace A should appear in scope='all'"
        )
        assert todo_b["id"] in all_ids, (
            "ToDo in non-archived workspace B should appear in scope='all'"
        )


class TestScopeAllExcludesArchived:
    """Property 28: scope='all' excludes items from archived workspaces.

    *For any* archived workspace containing a ToDo with a marker, and a
    non-archived workspace with the same marker, scope='all' returns only
    the non-archived workspace's item.

    **Validates: Requirements 38.3, 38.12**
    """

    @given(marker=marker_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_scope_all_excludes_archived_workspace_items(
        self,
        marker: str,
    ):
        """Archived workspace items are excluded from scope='all' search.

        **Validates: Requirements 38.3, 38.12**
        """
        ws_active = await create_workspace(name="WS-Active")
        ws_archived = await create_workspace(name="WS-Archived", is_archived=True)

        todo_active = await _create_todo(ws_active["id"], f"Item {marker} active")
        todo_archived = await _create_todo(ws_archived["id"], f"Item {marker} archived")

        result = await search_manager.search(marker, scope="all")

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }
        assert todo_active["id"] in all_ids, (
            "ToDo in non-archived workspace should appear in scope='all'"
        )
        assert todo_archived["id"] not in all_ids, (
            "ToDo in archived workspace must NOT appear in scope='all'"
        )


class TestScopeSpecificArchivedWorkspace:
    """Property 28: scope=archived_workspace_id still returns its items.

    *For any* archived workspace containing a ToDo with a marker,
    searching with scope=that_workspace_id returns the item. Direct
    workspace scope includes archived workspaces (Requirement 38.12).

    **Validates: Requirements 38.12**
    """

    @given(marker=marker_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_scope_archived_workspace_returns_its_items(
        self,
        marker: str,
    ):
        """Directly scoping to an archived workspace still returns its items.

        **Validates: Requirements 38.12**
        """
        ws_archived = await create_workspace(name="WS-Archived", is_archived=True)

        todo = await _create_todo(ws_archived["id"], f"Item {marker} in archived")

        result = await search_manager.search(marker, scope=ws_archived["id"])

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }
        assert todo["id"] in all_ids, (
            "ToDo in archived workspace should appear when scope targets "
            "that specific archived workspace (Req 38.12)"
        )
