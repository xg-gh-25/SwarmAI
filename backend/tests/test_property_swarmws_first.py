"""Property-based tests for SwarmWS always first in workspace list.

**Feature: workspace-refactor, Property 1: SwarmWS always first in workspace list**

Uses Hypothesis to verify that both ``list_non_archived(db)`` and
``list_all(db)`` always return the default workspace (SwarmWS) at
index 0, regardless of how many custom workspaces exist.

**Validates: Requirements 1.1**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from database import db
from core.swarm_workspace_manager import SwarmWorkspaceManager


PROPERTY_SETTINGS = settings(
    max_examples=2,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Helpers (shared)
# ---------------------------------------------------------------------------

from tests.helpers import create_default_workspace, create_custom_workspace


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestSwarmWSAlwaysFirst:
    """Property 1: SwarmWS always first in workspace list.

    **Validates: Requirements 1.1**
    """

    @given(num_custom=st.integers(min_value=1, max_value=5))
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_list_non_archived_has_default_at_index_0(
        self,
        num_custom: int,
    ):
        """After creating N custom workspaces + 1 default, list_non_archived
        always returns the default workspace at index 0.

        **Validates: Requirements 1.1**
        """
        await create_default_workspace()

        for i in range(num_custom):
            await create_custom_workspace(index=i)

        manager = SwarmWorkspaceManager()
        result = await manager.list_non_archived(db)

        assert len(result) >= num_custom + 1, (
            f"Expected at least {num_custom + 1} workspaces, got {len(result)}"
        )
        assert result[0].get("is_default"), (
            f"First workspace should have is_default truthy, got {result[0]}"
        )
        # The key property: index 0 is always the default workspace
        assert result[0].get("name") == "SwarmWS", (
            f"First workspace should be named SwarmWS, got {result[0].get('name')}"
        )

    @given(num_custom=st.integers(min_value=1, max_value=5))
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_list_all_has_default_at_index_0(
        self,
        num_custom: int,
    ):
        """After creating N custom workspaces + 1 default, list_all
        always returns the default workspace at index 0.

        **Validates: Requirements 1.1**
        """
        await create_default_workspace()

        for i in range(num_custom):
            await create_custom_workspace(index=i)

        manager = SwarmWorkspaceManager()
        result = await manager.list_all(db, include_archived=True)

        assert len(result) >= num_custom + 1, (
            f"Expected at least {num_custom + 1} workspaces, got {len(result)}"
        )
        assert result[0].get("is_default"), (
            f"First workspace should have is_default truthy, got {result[0]}"
        )
        # The key property: index 0 is always the default workspace
        assert result[0].get("name") == "SwarmWS", (
            f"First workspace should be named SwarmWS, got {result[0].get('name')}"
        )
