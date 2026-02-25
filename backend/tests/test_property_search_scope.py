"""Property-based tests for search scope filtering.

**Feature: workspace-refactor, Property 28: Search respects scope**

Updated for the single-workspace model. Verifies that:
1. scope=workspace_id returns items from the singleton workspace
2. scope="all" returns items from the singleton workspace

**Validates: Requirements 38.1-38.12**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
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
    """Property 28: scope=workspace_id returns items from that workspace.

    In the single-workspace model, scoping to 'swarmws' returns all items.

    **Validates: Requirements 38.3**
    """

    @given(marker=marker_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_scope_workspace_returns_only_that_workspace(
        self,
        marker: str,
    ):
        """Scoped search returns items belonging to the singleton workspace.

        **Validates: Requirements 38.3**
        """
        ws = await create_workspace(name="WS-A")
        todo = await _create_todo(ws["id"], f"Item {marker} in A")

        result = await search_manager.search(marker, scope=ws["id"])

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }
        assert todo["id"] in all_ids, (
            f"ToDo in singleton workspace should appear when scope=ws_id"
        )


class TestScopeAllReturnsNonArchived:
    """Property 28: scope='all' returns items from the singleton workspace.

    **Validates: Requirements 38.3**
    """

    @given(marker=marker_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_scope_all_returns_items_from_singleton_workspace(
        self,
        marker: str,
    ):
        """scope='all' returns results from the singleton workspace.

        **Validates: Requirements 38.3**
        """
        ws = await create_workspace(name="WS-A")
        todo = await _create_todo(ws["id"], f"Item {marker} in A")

        result = await search_manager.search(marker, scope="all")

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }
        assert todo["id"] in all_ids, (
            "ToDo in singleton workspace should appear in scope='all'"
        )


class TestScopeAllExcludesArchived:
    """Property 28 (single-workspace): scope='all' includes singleton items.

    In the single-workspace model, there is no archiving concept.
    All items appear in scope='all'.

    **Validates: Requirements 38.3, 38.12**
    """

    @given(marker=marker_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_scope_all_includes_singleton_workspace_items(
        self,
        marker: str,
    ):
        """All singleton workspace items appear in scope='all' search.

        **Validates: Requirements 38.3, 38.12**
        """
        ws = await create_workspace(name="WS-Active")
        todo = await _create_todo(ws["id"], f"Item {marker} active")

        result = await search_manager.search(marker, scope="all")

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }
        assert todo["id"] in all_ids, (
            "ToDo in singleton workspace should appear in scope='all'"
        )


class TestScopeSpecificArchivedWorkspace:
    """Property 28 (single-workspace): scope=workspace_id returns items.

    In the single-workspace model, directly scoping to the singleton
    workspace returns its items.

    **Validates: Requirements 38.12**
    """

    @given(marker=marker_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_scope_singleton_workspace_returns_its_items(
        self,
        marker: str,
    ):
        """Directly scoping to the singleton workspace returns its items.

        **Validates: Requirements 38.12**
        """
        ws = await create_workspace(name="WS-Test")
        todo = await _create_todo(ws["id"], f"Item {marker} in ws")

        result = await search_manager.search(marker, scope=ws["id"])

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }
        assert todo["id"] in all_ids, (
            "ToDo in singleton workspace should appear when scope targets it"
        )
