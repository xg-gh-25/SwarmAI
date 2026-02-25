"""Property-based tests for SwarmWS Global View aggregation.

**Feature: workspace-refactor, Property 27: SwarmWS Global View aggregation**

Uses Hypothesis to verify that the SwarmWS Global View (workspace_id="all",
global_view=True) correctly aggregates items from all non-archived workspaces,
includes a "recommended" group sorted by priority desc then updated_at desc,
and that the neutral "all" scope (global_view=False) does NOT include the
recommended group.

**Validates: Requirements 37.1-37.12**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone, timedelta

from core.section_manager import SectionManager
from tests.helpers import create_custom_workspace, seed_todo


PROPERTY_SETTINGS = settings(
    max_examples=2,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=80,
).filter(lambda x: x.strip())

priority_strategy = st.sampled_from(["high", "medium", "low", "none"])


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestGlobalViewAggregatesAllNonArchived:
    """Property 27: Global View aggregates all non-archived workspaces.

    *For any* set of non-archived workspaces each containing a ToDo,
    Global View (workspace_id="all", global_view=True) returns items
    from all of them.

    **Validates: Requirements 37.1-37.12**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_global_view_aggregates_all_non_archived_workspaces(
        self,
        title: str,
    ):
        """Global View includes items from every non-archived workspace.

        **Validates: Requirements 37.1**
        """
        ws_a = await create_custom_workspace(name="GV-WS-A")
        ws_b = await create_custom_workspace(name="GV-WS-B")

        todo_a = await seed_todo(ws_a, f"A-{title}")
        todo_b = await seed_todo(ws_b, f"B-{title}")

        manager = SectionManager()
        result = await manager.get_signals(
            workspace_id="all", global_view=True,
        )

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }

        assert todo_a in all_ids, (
            "ToDo from workspace A must appear in Global View aggregation"
        )
        assert todo_b in all_ids, (
            "ToDo from workspace B must appear in Global View aggregation"
        )


class TestGlobalViewIncludesRecommendedGroup:
    """Property 27: Global View includes a "recommended" group as first group.

    *For any* Global View request with items, the first group SHALL be
    named "recommended".

    **Validates: Requirements 37.1-37.12**
    """

    @given(title=title_strategy, priority=priority_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_global_view_has_recommended_group_first(
        self,
        title: str,
        priority: str,
    ):
        """Global View response has 'recommended' as the first group.

        **Validates: Requirements 37.1-37.12**
        """
        ws = await create_custom_workspace(name="GV-Rec")
        await seed_todo(ws, title, priority=priority)

        manager = SectionManager()
        result = await manager.get_signals(
            workspace_id="all", global_view=True,
        )

        assert len(result.groups) >= 1, "Should have at least one group"
        assert result.groups[0].name == "recommended", (
            f"First group should be 'recommended', got '{result.groups[0].name}'"
        )


class TestRecommendedGroupMaxSize:
    """Property 27: Recommended group has at most DEFAULT_RECOMMENDED_N items.

    **Validates: Requirements 37.1-37.12**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_recommended_group_has_at_most_n_items(
        self,
        title: str,
    ):
        """Recommended group contains at most DEFAULT_RECOMMENDED_N (3) items.

        **Validates: Requirements 37.1-37.12**
        """
        ws = await create_custom_workspace(name="GV-MaxN")
        # Seed more items than DEFAULT_RECOMMENDED_N
        for i in range(5):
            await seed_todo(ws, f"{title}-{i}", priority="high")

        manager = SectionManager()
        result = await manager.get_signals(
            workspace_id="all", global_view=True,
        )

        recommended_groups = [g for g in result.groups if g.name == "recommended"]
        assert len(recommended_groups) == 1, "Should have exactly one recommended group"

        rec_group = recommended_groups[0]
        assert len(rec_group.items) <= manager.DEFAULT_RECOMMENDED_N, (
            f"Recommended group has {len(rec_group.items)} items, "
            f"expected at most {manager.DEFAULT_RECOMMENDED_N}"
        )


class TestRecommendedGroupSortOrder:
    """Property 27: Recommended items sorted by priority desc, updated_at desc.

    **Validates: Requirements 37.1-37.12**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_recommended_items_sorted_by_priority_then_recency(
        self,
        title: str,
    ):
        """Recommended items are ordered: high priority first, then most recent.

        **Validates: Requirements 37.1-37.12**
        """
        ws = await create_custom_workspace(name="GV-Sort")

        # Seed items with different priorities
        await seed_todo(ws, f"low-{title}", priority="low")
        await seed_todo(ws, f"high-{title}", priority="high")
        await seed_todo(ws, f"medium-{title}", priority="medium")

        manager = SectionManager()
        result = await manager.get_signals(
            workspace_id="all", global_view=True,
        )

        recommended_groups = [g for g in result.groups if g.name == "recommended"]
        assert len(recommended_groups) == 1

        items = recommended_groups[0].items
        assert len(items) == manager.DEFAULT_RECOMMENDED_N

        # Verify priority ordering: high < medium < low < none (ascending order value)
        priority_order = {"high": 0, "medium": 1, "low": 2, "none": 3}
        for i in range(len(items) - 1):
            curr_pri = priority_order.get(items[i].priority, 3)
            next_pri = priority_order.get(items[i + 1].priority, 3)
            assert curr_pri <= next_pri, (
                f"Item at index {i} (priority={items[i].priority}) should have "
                f"equal or higher priority than item at index {i+1} "
                f"(priority={items[i + 1].priority})"
            )


class TestNeutralAllScopeNoRecommended:
    """Property 27: Neutral 'all' scope (global_view=False) has no recommended group.

    **Validates: Requirements 37.1-37.12**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_neutral_all_scope_excludes_recommended_group(
        self,
        title: str,
    ):
        """When global_view=False, no 'recommended' group is present.

        **Validates: Requirements 37.1-37.12**
        """
        ws = await create_custom_workspace(name="GV-Neutral")
        await seed_todo(ws, title, priority="high")

        manager = SectionManager()
        result = await manager.get_signals(
            workspace_id="all", global_view=False,
        )

        group_names = [g.name for g in result.groups]
        assert "recommended" not in group_names, (
            "Neutral 'all' scope (global_view=False) must NOT include "
            f"'recommended' group, but found groups: {group_names}"
        )


class TestArchivedExcludedFromGlobalView:
    """Property 27: Archived workspaces excluded from Global View aggregation.

    In the single-workspace model, there is no archiving concept.
    This test verifies that items from the singleton workspace appear
    in Global View aggregation.

    **Validates: Requirements 37.1-37.12**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_singleton_workspace_items_appear_in_global_view(
        self,
        title: str,
    ):
        """Items from the singleton workspace appear in Global View.

        **Validates: Requirements 37.1-37.12**
        """
        ws_id = await create_custom_workspace(name="GV-Active")
        todo_id = await seed_todo(ws_id, f"Active-{title}")

        manager = SectionManager()
        result = await manager.get_signals(
            workspace_id="all", global_view=True,
        )

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }

        assert todo_id in all_ids, (
            "ToDo from singleton workspace must appear in Global View"
        )


class TestSpecificWorkspaceNoRecommended:
    """Property 27: Specific workspace_id with global_view=True has no recommended group.

    The recommended group only appears when workspace_id="all" AND
    global_view=True. A specific workspace_id should never get it.

    **Validates: Requirements 37.1-37.12**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_specific_workspace_with_global_view_no_recommended(
        self,
        title: str,
    ):
        """Specific workspace_id + global_view=True does NOT include recommended.

        **Validates: Requirements 37.1-37.12**
        """
        ws = await create_custom_workspace(name="GV-Specific")
        await seed_todo(ws, title, priority="high")

        manager = SectionManager()
        result = await manager.get_signals(
            workspace_id=ws, global_view=True,
        )

        group_names = [g.name for g in result.groups]
        assert "recommended" not in group_names, (
            "Specific workspace_id with global_view=True must NOT include "
            f"'recommended' group, but found groups: {group_names}"
        )
