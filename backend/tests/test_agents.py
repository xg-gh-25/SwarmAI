"""Tests for agent API endpoints."""
import pytest
from fastapi.testclient import TestClient


class TestAgentsList:
    """Tests for GET /api/agents endpoint."""

    def test_list_agents_success(self, client: TestClient):
        """Test listing agents returns 200 and list."""
        response = client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_agents_excludes_default(self, client: TestClient):
        """Test that default agent is excluded from list."""
        response = client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        agent_ids = [agent["id"] for agent in data]
        assert "default" not in agent_ids


class TestGetDefaultAgent:
    """Tests for GET /api/agents/default endpoint."""

    def test_get_default_agent_success(self, client: TestClient):
        """Test getting default agent returns 200."""
        response = client.get("/api/agents/default")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "default"
        assert "name" in data


class TestGetAgent:
    """Tests for GET /api/agents/{agent_id} endpoint."""

    def test_get_agent_success(self, client: TestClient):
        """Test getting an existing agent returns 200."""
        # First create an agent
        create_response = client.post(
            "/api/agents",
            json={
                "name": "Test Agent",
                "description": "Test description",
            }
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        # Now get it
        response = client.get(f"/api/agents/{agent_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == agent_id
        assert data["name"] == "Test Agent"

    def test_get_agent_not_found(self, client: TestClient, invalid_agent_id: str):
        """Test getting non-existent agent returns 404."""
        response = client.get(f"/api/agents/{invalid_agent_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "AGENT_NOT_FOUND"
        assert "suggested_action" in data


class TestCreateAgent:
    """Tests for POST /api/agents endpoint."""

    def test_create_agent_success(self, client: TestClient, sample_agent_data: dict):
        """Test creating agent returns 201."""
        response = client.post("/api/agents", json=sample_agent_data)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == sample_agent_data["name"]
        assert "id" in data
        assert "created_at" in data

    def test_create_agent_minimal(self, client: TestClient):
        """Test creating agent with minimal data."""
        response = client.post(
            "/api/agents",
            json={"name": "Minimal Agent"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Minimal Agent"

    def test_create_agent_missing_name(self, client: TestClient):
        """Test creating agent without name returns 400."""
        response = client.post("/api/agents", json={})
        assert response.status_code == 400 or response.status_code == 422
        data = response.json()
        assert "code" in data


class TestUpdateAgent:
    """Tests for PUT /api/agents/{agent_id} endpoint."""

    def test_update_agent_success(self, client: TestClient):
        """Test updating agent returns 200."""
        # Create agent
        create_response = client.post(
            "/api/agents",
            json={"name": "Original Name"}
        )
        agent_id = create_response.json()["id"]

        # Update it
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"name": "Updated Name"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"

    def test_update_agent_not_found(self, client: TestClient, invalid_agent_id: str):
        """Test updating non-existent agent returns 404."""
        response = client.put(
            f"/api/agents/{invalid_agent_id}",
            json={"name": "New Name"}
        )
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "AGENT_NOT_FOUND"


class TestDeleteAgent:
    """Tests for DELETE /api/agents/{agent_id} endpoint."""

    def test_delete_agent_success(self, client: TestClient):
        """Test deleting agent returns 204."""
        # Create agent
        create_response = client.post(
            "/api/agents",
            json={"name": "Agent to Delete"}
        )
        agent_id = create_response.json()["id"]

        # Delete it
        response = client.delete(f"/api/agents/{agent_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_response = client.get(f"/api/agents/{agent_id}")
        assert get_response.status_code == 404

    def test_delete_default_agent_forbidden(self, client: TestClient):
        """Test deleting default agent returns error."""
        response = client.delete("/api/agents/default")
        assert response.status_code in [400, 403, 422]
        data = response.json()
        assert "code" in data

    def test_delete_agent_not_found(self, client: TestClient, invalid_agent_id: str):
        """Test deleting non-existent agent returns 404."""
        response = client.delete(f"/api/agents/{invalid_agent_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "AGENT_NOT_FOUND"
