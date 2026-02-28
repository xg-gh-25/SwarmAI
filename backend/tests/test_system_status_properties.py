"""Property-based tests for System Status API.

Uses Hypothesis to verify universal properties across all valid inputs.

**Feature: swarm-init-status-display**
"""
import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from hypothesis import given, strategies as st, settings, HealthCheck
from fastapi.testclient import TestClient


# Suppress function-scoped fixture warning since we're testing with mocked
# component states across iterations
PROPERTY_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)


class TestInitializedFieldConsistency:
    """Property 2: Initialized Field Consistency.

    **Feature: swarm-init-status-display, Property 2: Initialized Field Consistency**
    **Validates: Requirements 1.5, 2.4**

    For any system status response, the `initialized` field SHALL be `true`
    if and only if `database.healthy` is `true` AND `agent.ready` is `true`
    AND `channel_gateway.running` is `true`.
    """

    @given(
        db_healthy=st.booleans(),
        agent_ready=st.booleans(),
        gateway_running=st.booleans()
    )
    @PROPERTY_SETTINGS
    def test_initialized_equals_all_components_ready(
        self,
        client: TestClient,
        db_healthy: bool,
        agent_ready: bool,
        gateway_running: bool
    ):
        """The initialized field is true iff all components are ready.

        **Validates: Requirements 1.5, 2.4**
        """
        # Expected value based on the property definition
        expected_initialized = db_healthy and agent_ready and gateway_running

        # Mock the database health check
        async def mock_health_check():
            return db_healthy

        # Mock the agent retrieval
        async def mock_get_default_agent():
            if agent_ready:
                return {
                    "name": "SwarmAgent",
                    "skill_ids": ["skill-1", "skill-2"],
                    "mcp_ids": ["mcp-1"]
                }
            return None

        # Mock the channel gateway
        mock_gateway = MagicMock()
        mock_gateway._shutting_down = not gateway_running

        with patch("routers.system.db") as mock_db, \
             patch("routers.system.get_default_agent", mock_get_default_agent), \
             patch("routers.system.channel_gateway", mock_gateway):

            mock_db.health_check = mock_health_check

            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()

            # Verify the initialized field matches the expected value
            assert data["initialized"] == expected_initialized, (
                f"Expected initialized={expected_initialized} for "
                f"db_healthy={db_healthy}, agent_ready={agent_ready}, "
                f"gateway_running={gateway_running}, but got {data['initialized']}"
            )

            # Also verify the individual component states are correctly reported
            assert data["database"]["healthy"] == db_healthy
            assert data["agent"]["ready"] == agent_ready
            assert data["channel_gateway"]["running"] == gateway_running

    @given(
        db_healthy=st.booleans(),
        agent_ready=st.booleans(),
        gateway_running=st.booleans()
    )
    @PROPERTY_SETTINGS
    def test_initialized_false_when_any_component_not_ready(
        self,
        client: TestClient,
        db_healthy: bool,
        agent_ready: bool,
        gateway_running: bool
    ):
        """The initialized field is false when any single component is not ready.

        **Validates: Requirements 1.5, 2.4**

        This is the contrapositive of the property: if initialized is true,
        then all components must be ready.
        """
        # Mock the database health check
        async def mock_health_check():
            return db_healthy

        # Mock the agent retrieval
        async def mock_get_default_agent():
            if agent_ready:
                return {
                    "name": "SwarmAgent",
                    "skill_ids": [],
                    "mcp_ids": []
                }
            return None

        # Mock the channel gateway
        mock_gateway = MagicMock()
        mock_gateway._shutting_down = not gateway_running

        with patch("routers.system.db") as mock_db, \
             patch("routers.system.get_default_agent", mock_get_default_agent), \
             patch("routers.system.channel_gateway", mock_gateway):

            mock_db.health_check = mock_health_check

            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()

            # If any component is not ready, initialized must be false
            any_not_ready = not db_healthy or not agent_ready or not gateway_running
            if any_not_ready:
                assert data["initialized"] is False, (
                    f"Expected initialized=False when any component not ready: "
                    f"db_healthy={db_healthy}, agent_ready={agent_ready}, "
                    f"gateway_running={gateway_running}"
                )

            # If all components are ready, initialized must be true
            all_ready = db_healthy and agent_ready and gateway_running
            if all_ready:
                assert data["initialized"] is True, (
                    f"Expected initialized=True when all components ready"
                )

    def test_initialized_true_only_when_all_components_ready(
        self,
        client: TestClient
    ):
        """Explicit test: initialized is true only when all three are true.

        **Validates: Requirements 1.5, 2.4**
        """
        # Test all 8 combinations explicitly
        test_cases = [
            # (db_healthy, agent_ready, gateway_running, expected_initialized)
            (False, False, False, False),
            (False, False, True, False),
            (False, True, False, False),
            (False, True, True, False),
            (True, False, False, False),
            (True, False, True, False),
            (True, True, False, False),
            (True, True, True, True),  # Only case where initialized is True
        ]

        for db_healthy, agent_ready, gateway_running, expected in test_cases:
            async def mock_health_check():
                return db_healthy

            async def mock_get_default_agent():
                if agent_ready:
                    return {"name": "SwarmAgent", "skill_ids": [], "mcp_ids": []}
                return None

            mock_gateway = MagicMock()
            mock_gateway._shutting_down = not gateway_running

            with patch("routers.system.db") as mock_db, \
                 patch("routers.system.get_default_agent", mock_get_default_agent), \
                 patch("routers.system.channel_gateway", mock_gateway):

                mock_db.health_check = mock_health_check

                response = client.get("/api/system/status")

                assert response.status_code == 200
                data = response.json()

                assert data["initialized"] == expected, (
                    f"Case ({db_healthy}, {agent_ready}, {gateway_running}): "
                    f"expected initialized={expected}, got {data['initialized']}"
                )

    @given(
        skills_count=st.integers(min_value=0, max_value=100),
        mcp_count=st.integers(min_value=0, max_value=50)
    )
    @PROPERTY_SETTINGS
    def test_initialized_independent_of_resource_counts(
        self,
        client: TestClient,
        skills_count: int,
        mcp_count: int
    ):
        """The initialized field depends only on component readiness, not counts.

        **Validates: Requirements 1.5, 2.4**

        Even with varying numbers of skills and MCP servers, the initialized
        field should only depend on whether the components are ready.
        """
        # All components ready
        async def mock_health_check():
            return True

        async def mock_get_default_agent():
            return {
                "name": "SwarmAgent",
                "skill_ids": [f"skill-{i}" for i in range(skills_count)],
                "mcp_ids": [f"mcp-{i}" for i in range(mcp_count)]
            }

        mock_gateway = MagicMock()
        mock_gateway._shutting_down = False

        with patch("routers.system.db") as mock_db, \
             patch("routers.system.get_default_agent", mock_get_default_agent), \
             patch("routers.system.channel_gateway", mock_gateway):

            mock_db.health_check = mock_health_check

            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()

            # With all components ready, initialized should be true
            # regardless of the number of skills/MCPs
            assert data["initialized"] is True, (
                f"Expected initialized=True with {skills_count} skills and "
                f"{mcp_count} MCPs when all components ready"
            )

            # Verify the counts are correctly reported
            assert data["agent"]["skills_count"] == skills_count
            assert data["agent"]["mcp_servers_count"] == mcp_count
