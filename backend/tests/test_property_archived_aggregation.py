"""Property-based tests for archived workspace aggregation exclusion.

**Feature: workspace-refactor, Property 25: Archived workspace excluded from aggregation**

Uses Hypothesis to verify that "all" aggregation (via SectionManager)
excludes items from workspaces where is_archived=true, while including
items from non-archived workspaces.

**Validates: Requirements 36.5**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

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
    max_size=100,
).filter(lambda x: x.strip())


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestArchivedExcludedFromAllAggregation:
    """Property 25: Archived workspace excluded from aggregation.

    *For any* "All Workspaces" aggregation query, workspaces where
    is_archived=true SHALL NOT be included in the aggregated results.

    **Validates: Requirements 36.5**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_all_aggregation_excludes_archived_workspace_todos(
        self,
        title: str,
    ):
        """Items from archived workspaces are excluded from scope='all' signals.

        **Validates: Requirements 36.5**
        """
        active_ws = await create_custom_workspace(name="ActiveWS")
        archived_ws = await create_custom_workspace(name="ArchivedWS", is_archived=True)

        active_todo_id = await seed_todo(active_ws, f"Active {title}")
        archived_todo_id = await seed_todo(archived_ws, f"Archived {title}")

        manager = SectionManager()
        result = await manager.get_signals(workspace_id="all")

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }

        assert active_todo_id in all_ids, (
            "ToDo from non-archived workspace should appear in 'all' aggregation"
        )
        assert archived_todo_id not in all_ids, (
            "ToDo from archived workspace must NOT appear in 'all' aggregation"
        )


class TestNonArchivedIncludedInAllAggregation:
    """Property 25: Non-archived workspaces included in aggregation.

    *For any* two non-archived workspaces each containing a ToDo,
    scope='all' aggregation returns items from both.

    **Validates: Requirements 36.5**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_all_aggregation_includes_all_non_archived_workspaces(
        self,
        title: str,
    ):
        """Items from all non-archived workspaces appear in scope='all'.

        **Validates: Requirements 36.5**
        """
        ws_a = await create_custom_workspace(name="WS-A")
        ws_b = await create_custom_workspace(name="WS-B")

        todo_a_id = await seed_todo(ws_a, f"A {title}")
        todo_b_id = await seed_todo(ws_b, f"B {title}")

        manager = SectionManager()
        result = await manager.get_signals(workspace_id="all")

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }

        assert todo_a_id in all_ids, (
            "ToDo from workspace A should appear in 'all' aggregation"
        )
        assert todo_b_id in all_ids, (
            "ToDo from workspace B should appear in 'all' aggregation"
        )


class TestArchivedExcludedFromSectionCounts:
    """Property 25: Archived workspace excluded from section counts.

    *For any* archived workspace with items, the section counts for
    scope='all' SHALL NOT include those items.

    **Validates: Requirements 36.5**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_section_counts_exclude_archived_workspace_items(
        self,
        title: str,
    ):
        """Section counts for 'all' exclude items from archived workspaces.

        **Validates: Requirements 36.5**

        Strategy: capture the 'all' count *before* seeding, then seed one
        todo into an active workspace and one into an archived workspace.
        The 'all' count should increase by exactly the active workspace's
        contribution (1), not by the archived workspace's contribution.
        This is resilient to accumulated data from prior Hypothesis examples.
        """
        manager = SectionManager()

        # Snapshot counts before seeding
        before_all = await manager.get_section_counts(workspace_id="all")

        active_ws = await create_custom_workspace(name="ActiveWS")
        archived_ws = await create_custom_workspace(name="ArchivedWS", is_archived=True)

        await seed_todo(active_ws, f"Active {title}")
        await seed_todo(archived_ws, f"Archived {title}")

        # Archived workspace has items when queried directly
        archived_counts = await manager.get_section_counts(workspace_id=archived_ws)
        assert archived_counts.signals.total >= 1, (
            "Archived workspace should have at least 1 signal when queried directly"
        )

        # "all" aggregation counts after seeding
        after_all = await manager.get_section_counts(workspace_id="all")

        # The delta should reflect only the active workspace's new todo (1),
        # NOT the archived workspace's todo.
        delta = after_all.signals.total - before_all.signals.total
        assert delta == 1, (
            f"'all' signal count should increase by 1 (active todo only), "
            f"but increased by {delta}. before={before_all.signals.total}, "
            f"after={after_all.signals.total}. "
            f"Archived workspace items must not be counted."
        )
