"""Tests for MCP server API endpoints."""
import pytest
from fastapi.testclient import TestClient


class TestMCPList:
    """Tests for GET /api/mcp endpoint."""

    def test_list_mcp_servers_success(self, client: TestClient):
        """Test listing MCP servers returns 200 and list."""
        response = client.get("/api/mcp")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestGetMCP:
    """Tests for GET /api/mcp/{mcp_id} endpoint."""

    def test_get_mcp_server_success(self, client: TestClient, sample_mcp_data: dict):
        """Test getting an existing MCP server returns 200."""
        # First create a server
        create_response = client.post("/api/mcp", json=sample_mcp_data)
        assert create_response.status_code == 201
        mcp_id = create_response.json()["id"]

        # Now get it
        response = client.get(f"/api/mcp/{mcp_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == mcp_id
        assert data["name"] == sample_mcp_data["name"]

    def test_get_mcp_server_not_found(self, client: TestClient, invalid_mcp_id: str):
        """Test getting non-existent MCP server returns 404."""
        response = client.get(f"/api/mcp/{invalid_mcp_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "MCP_SERVER_NOT_FOUND"
        assert "suggested_action" in data


class TestCreateMCP:
    """Tests for POST /api/mcp endpoint."""

    def test_create_mcp_stdio_success(self, client: TestClient, sample_mcp_data: dict):
        """Test creating stdio MCP server returns 201."""
        response = client.post("/api/mcp", json=sample_mcp_data)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == sample_mcp_data["name"]
        assert data["connection_type"] == "stdio"
        assert "id" in data
        assert "endpoint" in data

    def test_create_mcp_sse_success(self, client: TestClient):
        """Test creating SSE MCP server returns 201."""
        mcp_data = {
            "name": "SSE MCP Server",
            "connection_type": "sse",
            "config": {"url": "http://localhost:8080/sse"}
        }
        response = client.post("/api/mcp", json=mcp_data)
        assert response.status_code == 201
        data = response.json()
        assert data["connection_type"] == "sse"

    def test_create_mcp_http_success(self, client: TestClient):
        """Test creating HTTP MCP server returns 201."""
        mcp_data = {
            "name": "HTTP MCP Server",
            "connection_type": "http",
            "config": {"url": "http://localhost:9000"}
        }
        response = client.post("/api/mcp", json=mcp_data)
        assert response.status_code == 201
        data = response.json()
        assert data["connection_type"] == "http"

    def test_create_mcp_stdio_missing_command(self, client: TestClient):
        """Test creating stdio MCP without command returns error."""
        mcp_data = {
            "name": "Invalid MCP",
            "connection_type": "stdio",
            "config": {}  # Missing command
        }
        response = client.post("/api/mcp", json=mcp_data)
        assert response.status_code in [400, 422]
        data = response.json()
        assert "code" in data

    def test_create_mcp_sse_missing_url(self, client: TestClient):
        """Test creating SSE MCP without URL returns error."""
        mcp_data = {
            "name": "Invalid MCP",
            "connection_type": "sse",
            "config": {}  # Missing url
        }
        response = client.post("/api/mcp", json=mcp_data)
        assert response.status_code in [400, 422]
        data = response.json()
        assert "code" in data


class TestUpdateMCP:
    """Tests for PUT /api/mcp/{mcp_id} endpoint."""

    def test_update_mcp_success(self, client: TestClient, sample_mcp_data: dict):
        """Test updating MCP server returns 200."""
        # Create server
        create_response = client.post("/api/mcp", json=sample_mcp_data)
        mcp_id = create_response.json()["id"]

        # Update it
        response = client.put(
            f"/api/mcp/{mcp_id}",
            json={"name": "Updated Server Name"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Server Name"

    def test_update_mcp_not_found(self, client: TestClient, invalid_mcp_id: str):
        """Test updating non-existent MCP server returns 404."""
        response = client.put(
            f"/api/mcp/{invalid_mcp_id}",
            json={"name": "New Name"}
        )
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "MCP_SERVER_NOT_FOUND"


class TestDeleteMCP:
    """Tests for DELETE /api/mcp/{mcp_id} endpoint."""

    def test_delete_mcp_success(self, client: TestClient, sample_mcp_data: dict):
        """Test deleting MCP server returns 204."""
        # Create server
        create_response = client.post("/api/mcp", json=sample_mcp_data)
        mcp_id = create_response.json()["id"]

        # Delete it
        response = client.delete(f"/api/mcp/{mcp_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_response = client.get(f"/api/mcp/{mcp_id}")
        assert get_response.status_code == 404

    def test_delete_mcp_not_found(self, client: TestClient, invalid_mcp_id: str):
        """Test deleting non-existent MCP server returns 404."""
        response = client.delete(f"/api/mcp/{invalid_mcp_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "MCP_SERVER_NOT_FOUND"


class TestMCPTestConnection:
    """Tests for POST /api/mcp/{mcp_id}/test endpoint."""

    def test_test_connection_success(self, client: TestClient, sample_mcp_data: dict):
        """Test MCP connection test returns result."""
        # Create server
        create_response = client.post("/api/mcp", json=sample_mcp_data)
        mcp_id = create_response.json()["id"]

        # Test connection
        response = client.post(f"/api/mcp/{mcp_id}/test")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ["success", "error"]

    def test_test_connection_not_found(self, client: TestClient, invalid_mcp_id: str):
        """Test connection test on non-existent server returns 404."""
        response = client.post(f"/api/mcp/{invalid_mcp_id}/test")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "MCP_SERVER_NOT_FOUND"
