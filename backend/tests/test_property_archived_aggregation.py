"""Property-based tests for singleton workspace aggregation.

**Feature: workspace-refactor, Property 25 (updated for single-workspace model)**

In the single-workspace model, there is no archiving concept. These tests
verify that items from the singleton workspace appear correctly in
scope='all' aggregation and section counts.

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
    """Property 25 (single-workspace): All items appear in scope='all'.

    In the single-workspace model, all items belong to the singleton
    workspace and appear in 'all' aggregation.

    **Validates: Requirements 36.5**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_all_aggregation_includes_singleton_workspace_todos(
        self,
        title: str,
    ):
        """Items from the singleton workspace appear in scope='all' signals.

        **Validates: Requirements 36.5**
        """
        ws_id = await create_custom_workspace(name="ActiveWS")
        todo_id = await seed_todo(ws_id, f"Active {title}")

        manager = SectionManager()
        result = await manager.get_signals(workspace_id="all")

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }

        assert todo_id in all_ids, (
            "ToDo from singleton workspace should appear in 'all' aggregation"
        )


class TestNonArchivedIncludedInAllAggregation:
    """Property 25: Singleton workspace items included in aggregation.

    **Validates: Requirements 36.5**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_all_aggregation_includes_singleton_workspace(
        self,
        title: str,
    ):
        """Items from the singleton workspace appear in scope='all'.

        **Validates: Requirements 36.5**
        """
        ws_id = await create_custom_workspace(name="WS-A")

        todo_id = await seed_todo(ws_id, f"A {title}")

        manager = SectionManager()
        result = await manager.get_signals(workspace_id="all")

        all_ids = {
            item.id
            for group in result.groups
            for item in group.items
        }

        assert todo_id in all_ids, (
            "ToDo from singleton workspace should appear in 'all' aggregation"
        )


class TestArchivedExcludedFromSectionCounts:
    """Property 25 (single-workspace): Section counts include all items.

    In the single-workspace model, all items are counted in scope='all'.

    **Validates: Requirements 36.5**
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_section_counts_include_singleton_workspace_items(
        self,
        title: str,
    ):
        """Section counts for 'all' include items from the singleton workspace.

        **Validates: Requirements 36.5**
        """
        manager = SectionManager()

        # Snapshot counts before seeding
        before_all = await manager.get_section_counts(workspace_id="all")

        ws_id = await create_custom_workspace(name="ActiveWS")
        await seed_todo(ws_id, f"Active {title}")

        # "all" aggregation counts after seeding
        after_all = await manager.get_section_counts(workspace_id="all")

        delta = after_all.signals.total - before_all.signals.total
        assert delta >= 1, (
            f"'all' signal count should increase by at least 1, "
            f"but increased by {delta}. before={before_all.signals.total}, "
            f"after={after_all.signals.total}."
        )
