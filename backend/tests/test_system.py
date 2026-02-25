"""Unit tests for System Status API endpoints.

Tests specific examples and edge cases for the /api/system/status endpoint.

**Validates: Requirements 1.6, 1.7**
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime


class TestSystemStatusEndpoint:
    """Tests for GET /api/system/status endpoint."""

    def test_endpoint_returns_200_status_code(self, client: TestClient):
        """Test that the endpoint returns 200 status code.

        **Validates: Requirements 1.1**
        """
        response = client.get("/api/system/status")
        assert response.status_code == 200

    def test_response_contains_all_required_fields(self, client: TestClient):
        """Test that response contains all required fields.

        **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**
        """
        response = client.get("/api/system/status")
        assert response.status_code == 200
        data = response.json()

        # Verify top-level fields exist
        assert "database" in data
        assert "agent" in data
        assert "channel_gateway" in data
        assert "initialized" in data
        assert "timestamp" in data

        # Verify database object structure
        assert "healthy" in data["database"]
        assert isinstance(data["database"]["healthy"], bool)

        # Verify agent object structure
        assert "ready" in data["agent"]
        assert "skills_count" in data["agent"]
        assert "mcp_servers_count" in data["agent"]
        assert isinstance(data["agent"]["ready"], bool)
        assert isinstance(data["agent"]["skills_count"], int)
        assert isinstance(data["agent"]["mcp_servers_count"], int)

        # Verify channel_gateway object structure
        assert "running" in data["channel_gateway"]
        assert isinstance(data["channel_gateway"]["running"], bool)

        # Verify initialized is boolean
        assert isinstance(data["initialized"], bool)

        # Verify timestamp is a string
        assert isinstance(data["timestamp"], str)

    def test_timestamp_is_valid_iso_format(self, client: TestClient):
        """Test that timestamp is in valid ISO 8601 format.

        **Validates: Requirements 2.5**
        """
        response = client.get("/api/system/status")
        assert response.status_code == 200
        data = response.json()

        # Should be parseable as ISO 8601 datetime
        timestamp = data["timestamp"]
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            assert parsed is not None
        except ValueError:
            pytest.fail(f"Timestamp '{timestamp}' is not valid ISO 8601 format")

    def test_agent_name_present_when_ready(self, client: TestClient):
        """Test that agent name is present when agent is ready.

        **Validates: Requirements 1.3, 2.2**
        """
        response = client.get("/api/system/status")
        assert response.status_code == 200
        data = response.json()

        if data["agent"]["ready"]:
            assert "name" in data["agent"]
            assert data["agent"]["name"] is not None


class TestDatabaseErrorHandling:
    """Tests for database error handling scenarios.

    **Validates: Requirements 1.6**
    """

    def test_database_failure_returns_healthy_false(self, client: TestClient):
        """Test that database failure returns healthy=false.

        **Validates: Requirements 1.6**
        """
        async def mock_health_check_failure():
            return False

        with patch("routers.system.db") as mock_db:
            mock_db.health_check = mock_health_check_failure

            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["database"]["healthy"] is False

    def test_database_exception_returns_healthy_false_with_error(
        self, client: TestClient
    ):
        """Test that database exception returns healthy=false with error message.

        **Validates: Requirements 1.6**
        """
        async def mock_health_check_exception():
            raise Exception("Database connection failed")

        with patch("routers.system.db") as mock_db:
            mock_db.health_check = mock_health_check_exception

            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["database"]["healthy"] is False
            assert data["database"]["error"] is not None
            assert "Database connection failed" in data["database"]["error"]

    def test_database_failure_sets_initialized_false(self, client: TestClient):
        """Test that database failure causes initialized=false.

        **Validates: Requirements 1.5, 1.6**
        """
        async def mock_health_check_failure():
            return False

        # Mock agent as ready
        async def mock_get_default_agent():
            return {"name": "SwarmAgent", "skill_ids": [], "mcp_ids": []}

        # Mock gateway as running
        mock_gateway = MagicMock()
        mock_gateway._shutting_down = False

        with patch("routers.system.db") as mock_db, \
             patch("routers.system.get_default_agent", mock_get_default_agent), \
             patch("routers.system.channel_gateway", mock_gateway):

            mock_db.health_check = mock_health_check_failure

            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            # Even with agent and gateway ready, initialized should be false
            assert data["initialized"] is False


class TestMissingAgentHandling:
    """Tests for missing agent handling scenarios.

    **Validates: Requirements 1.7**
    """

    def test_missing_agent_returns_ready_false(self, client: TestClient):
        """Test that missing agent returns ready=false.

        **Validates: Requirements 1.7**
        """
        async def mock_get_default_agent():
            return None

        with patch("routers.system.get_default_agent", mock_get_default_agent):
            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["agent"]["ready"] is False

    def test_missing_agent_has_null_name(self, client: TestClient):
        """Test that missing agent has null name.

        **Validates: Requirements 1.7, 2.2**
        """
        async def mock_get_default_agent():
            return None

        with patch("routers.system.get_default_agent", mock_get_default_agent):
            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["agent"]["name"] is None

    def test_missing_agent_has_zero_counts(self, client: TestClient):
        """Test that missing agent has zero skills and MCP counts.

        **Validates: Requirements 1.7, 2.2**
        """
        async def mock_get_default_agent():
            return None

        with patch("routers.system.get_default_agent", mock_get_default_agent):
            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["agent"]["skills_count"] == 0
            assert data["agent"]["mcp_servers_count"] == 0

    def test_agent_exception_returns_ready_false(self, client: TestClient):
        """Test that agent retrieval exception returns ready=false.

        **Validates: Requirements 1.7**
        """
        async def mock_get_default_agent():
            raise Exception("Agent retrieval failed")

        with patch("routers.system.get_default_agent", mock_get_default_agent):
            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["agent"]["ready"] is False

    def test_missing_agent_sets_initialized_false(self, client: TestClient):
        """Test that missing agent causes initialized=false.

        **Validates: Requirements 1.5, 1.7**
        """
        # Mock database as healthy
        async def mock_health_check():
            return True

        # Mock agent as not found
        async def mock_get_default_agent():
            return None

        # Mock gateway as running
        mock_gateway = MagicMock()
        mock_gateway._shutting_down = False

        with patch("routers.system.db") as mock_db, \
             patch("routers.system.get_default_agent", mock_get_default_agent), \
             patch("routers.system.channel_gateway", mock_gateway):

            mock_db.health_check = mock_health_check

            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            # Even with database and gateway ready, initialized should be false
            assert data["initialized"] is False


class TestChannelGatewayStatus:
    """Tests for channel gateway status reporting."""

    def test_gateway_not_running_returns_running_false(self, client: TestClient):
        """Test that stopped gateway returns running=false."""
        mock_gateway = MagicMock()
        mock_gateway._shutting_down = True

        with patch("routers.system.channel_gateway", mock_gateway):
            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["channel_gateway"]["running"] is False

    def test_gateway_running_returns_running_true(self, client: TestClient):
        """Test that running gateway returns running=true."""
        mock_gateway = MagicMock()
        mock_gateway._shutting_down = False

        with patch("routers.system.channel_gateway", mock_gateway):
            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["channel_gateway"]["running"] is True


class TestAgentResourceCounts:
    """Tests for agent skill and MCP server counts."""

    def test_agent_with_skills_reports_correct_count(self, client: TestClient):
        """Test that agent with skills reports correct skills_count."""
        async def mock_get_default_agent():
            return {
                "name": "SwarmAgent",
                "skill_ids": ["skill-1", "skill-2", "skill-3"],
                "mcp_ids": []
            }

        with patch("routers.system.get_default_agent", mock_get_default_agent):
            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["agent"]["skills_count"] == 3

    def test_agent_with_mcp_servers_reports_correct_count(self, client: TestClient):
        """Test that agent with MCP servers reports correct mcp_servers_count."""
        async def mock_get_default_agent():
            return {
                "name": "SwarmAgent",
                "skill_ids": [],
                "mcp_ids": ["mcp-1", "mcp-2"]
            }

        with patch("routers.system.get_default_agent", mock_get_default_agent):
            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["agent"]["mcp_servers_count"] == 2

    def test_agent_with_null_skill_ids_reports_zero(self, client: TestClient):
        """Test that agent with null skill_ids reports zero count."""
        async def mock_get_default_agent():
            return {
                "name": "SwarmAgent",
                "skill_ids": None,
                "mcp_ids": None
            }

        with patch("routers.system.get_default_agent", mock_get_default_agent):
            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["agent"]["skills_count"] == 0
            assert data["agent"]["mcp_servers_count"] == 0


class TestAllComponentsReady:
    """Tests for when all components are ready."""

    def test_all_components_ready_returns_initialized_true(self, client: TestClient):
        """Test that all components ready returns initialized=true.

        **Validates: Requirements 1.5, 2.4**
        """
        # Mock database as healthy
        async def mock_health_check():
            return True

        # Mock agent as ready
        async def mock_get_default_agent():
            return {
                "name": "SwarmAgent",
                "skill_ids": ["skill-1"],
                "mcp_ids": ["mcp-1"]
            }

        # Mock swarm workspace as ready
        async def mock_get_default_workspace():
            return {
                "name": "SwarmWS",
                "file_path": "{app_data_dir}/swarm-workspaces/SwarmWS"
            }

        # Mock gateway as running
        mock_gateway = MagicMock()
        mock_gateway._shutting_down = False

        # Create mock db with proper async methods
        mock_db = MagicMock()
        mock_db.health_check = mock_health_check
        mock_db.workspace_config.get_config = mock_get_default_workspace

        with patch("routers.system.db", mock_db), \
             patch("routers.system.get_default_agent", mock_get_default_agent), \
             patch("routers.system.channel_gateway", mock_gateway):

            response = client.get("/api/system/status")

            assert response.status_code == 200
            data = response.json()
            assert data["database"]["healthy"] is True
            assert data["agent"]["ready"] is True
            assert data["channel_gateway"]["running"] is True
            assert data["swarm_workspace"]["ready"] is True
            assert data["initialized"] is True
