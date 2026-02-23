"""Property-based tests for archived workspace conversion suggestion.

**Feature: workspace-refactor, Property 26: Archived workspace not suggested for conversion**

Uses Hypothesis to verify that:
1. Archived workspaces are excluded from the suggestion list (list_non_archived).
2. Converting a ToDo targeting an archived workspace is blocked.

**Validates: Requirements 32.6, 32.7**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from database import db
from core.swarm_workspace_manager import SwarmWorkspaceManager
from core.todo_manager import ToDoManager
from tests.helpers import create_custom_workspace


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


class TestArchivedNotSuggestedForConversion:
    """Property 26: Archived workspace not suggested for conversion.

    Validates: Requirements 32.6, 32.7
    """

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_list_non_archived_excludes_archived_workspaces(
        self,
        title: str,
    ):
        """Archived workspaces are excluded from the suggestion list.

        list_non_archived() is the backend method used to populate the
        workspace selector during ToDo-to-Task conversion. Archived
        workspaces must never appear in this list.

        **Validates: Requirements 32.6**
        """
        active_ws = await create_custom_workspace(name="ActiveWS")
        archived_ws = await create_custom_workspace(name="ArchivedWS", is_archived=True)

        manager = SwarmWorkspaceManager()
        non_archived = await manager.list_non_archived(db)

        non_archived_ids = {ws["id"] for ws in non_archived}

        assert active_ws in non_archived_ids, (
            "Active workspace must appear in list_non_archived (conversion suggestion list)"
        )
        assert archived_ws not in non_archived_ids, (
            "Archived workspace must NOT appear in list_non_archived "
            "(Requirement 32.6: SHALL NOT suggest archived workspaces as conversion targets)"
        )

    @given(title=title_strategy)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_create_todo_in_archived_workspace_blocked(
        self,
        title: str,
    ):
        """Creating a ToDo in an archived workspace raises PermissionError.

        When a user selects an archived workspace in the conversion dialog,
        the operation must be blocked. The ToDoManager._check_workspace_not_archived
        guard raises PermissionError for archived workspaces, preventing any
        write operations including task creation via conversion.

        **Validates: Requirements 32.7**
        """
        archived_ws = await create_custom_workspace(name="ArchivedWS", is_archived=True)

        from schemas.todo import ToDoCreate, ToDoSourceType, Priority

        todo_data = ToDoCreate(
            workspace_id=archived_ws,
            title=title,
            source_type=ToDoSourceType.MANUAL,
            priority=Priority.NONE,
        )

        todo_manager = ToDoManager()

        with pytest.raises(PermissionError, match="archived"):
            await todo_manager.create(todo_data)
