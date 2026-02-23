"""Property-based tests for custom workspace deletion.

**Feature: workspace-refactor, Property 5: Custom workspace deletion**

Uses Hypothesis to verify that ``SwarmWorkspaceManager.delete()`` succeeds
when called on a custom workspace (is_default=False), that the workspace
is removed from the database, and that other workspaces are unaffected.

**Validates: Requirements 2.5**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from database import db
from core.swarm_workspace_manager import SwarmWorkspaceManager
from tests.helpers import create_custom_workspace


PROPERTY_SETTINGS = settings(
    max_examples=2,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestCustomWorkspaceDeletion:
    """Property 5: Custom workspace deletion.

    **Validates: Requirements 2.5**
    """

    @given(title=st.text(min_size=1, max_size=30).filter(lambda t: t.strip()))
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_delete_custom_workspace_returns_true(self, title: str):
        """delete() on is_default=false workspace returns True.

        **Validates: Requirements 2.5**
        """
        safe_name = f"CustomWS-{title[:15].strip()}"
        ws_id = await create_custom_workspace(name=safe_name)
        manager = SwarmWorkspaceManager()

        result = await manager.delete(ws_id, db)
        assert result is True, "Deleting a custom workspace should return True"

    @given(title=st.text(min_size=1, max_size=30).filter(lambda t: t.strip()))
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_deleted_workspace_no_longer_exists(self, title: str):
        """Deleted workspace no longer exists in the database.

        **Validates: Requirements 2.5**
        """
        safe_name = f"CustomWS-{title[:15].strip()}"
        ws_id = await create_custom_workspace(name=safe_name)
        manager = SwarmWorkspaceManager()

        await manager.delete(ws_id, db)

        workspace = await db.swarm_workspaces.get(ws_id)
        assert workspace is None, "Custom workspace should be removed from DB after deletion"

    @given(title=st.text(min_size=1, max_size=30).filter(lambda t: t.strip()))
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_other_workspaces_unaffected_by_deletion(self, title: str):
        """Other workspaces are not affected by the deletion.

        **Validates: Requirements 2.5**
        """
        safe_name = f"CustomWS-{title[:15].strip()}"

        # Create two custom workspaces
        ws_to_delete = await create_custom_workspace(name=safe_name)
        ws_other = await create_custom_workspace(name="OtherWorkspace")

        manager = SwarmWorkspaceManager()
        await manager.delete(ws_to_delete, db)

        # The other workspace should still exist and be unchanged
        other = await db.swarm_workspaces.get(ws_other)
        assert other is not None, "Other workspace should still exist after deleting a different workspace"
        assert other.get("name") == "OtherWorkspace", "Other workspace name should be unchanged"
