"""Property-based tests for SwarmWS deletion prevention.

**Feature: workspace-refactor, Property 2: SwarmWS deletion prevention**

Uses Hypothesis to verify that ``SwarmWorkspaceManager.delete()`` raises
``PermissionError`` when called on a default workspace (is_default=True),
and that the workspace remains in the database after the failed attempt.
Also verifies that non-default workspaces can be deleted successfully.

**Validates: Requirements 1.2**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from database import db
from core.swarm_workspace_manager import SwarmWorkspaceManager
from tests.helpers import create_default_workspace, create_custom_workspace


PROPERTY_SETTINGS = settings(
    max_examples=2,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestSwarmWSDeletionPrevention:
    """Property 2: SwarmWS deletion prevention.

    **Validates: Requirements 1.2**
    """

    @pytest.mark.asyncio
    async def test_delete_default_workspace_raises_permission_error(self):
        """Attempting to delete a workspace with is_default=True raises PermissionError.

        **Validates: Requirements 1.2**
        """
        ws_id = await create_default_workspace()
        manager = SwarmWorkspaceManager()

        with pytest.raises(PermissionError):
            await manager.delete(ws_id, db)

    @pytest.mark.asyncio
    async def test_default_workspace_exists_after_failed_deletion(self):
        """The default workspace still exists in the DB after the failed deletion attempt.

        **Validates: Requirements 1.2**
        """
        ws_id = await create_default_workspace()
        manager = SwarmWorkspaceManager()

        with pytest.raises(PermissionError):
            await manager.delete(ws_id, db)

        # Verify workspace still exists
        workspace = await db.swarm_workspaces.get(ws_id)
        assert workspace is not None, "Default workspace should still exist after failed deletion"
        assert workspace.get("is_default"), "Workspace should still be marked as default"
        assert workspace.get("name") == "SwarmWS", "Workspace name should be unchanged"

    @given(title=st.text(min_size=1, max_size=30).filter(lambda t: t.strip()))
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_delete_non_default_workspace_succeeds(self, title: str):
        """Deleting a non-default workspace succeeds (contrast test).

        **Validates: Requirements 1.2**
        """
        safe_name = f"CustomWS-{title[:15].strip()}"
        ws_id = await create_custom_workspace(name=safe_name)
        manager = SwarmWorkspaceManager()

        result = await manager.delete(ws_id, db)
        assert result is True, "Deleting a non-default workspace should return True"

        # Verify workspace no longer exists
        workspace = await db.swarm_workspaces.get(ws_id)
        assert workspace is None, "Non-default workspace should be deleted from DB"
