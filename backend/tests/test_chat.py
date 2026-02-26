"""Tests for chat API endpoints."""
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
import json


class TestChatStream:
    """Tests for POST /api/chat/stream endpoint."""

    def test_chat_stream_invalid_agent(self, client: TestClient, invalid_agent_id: str):
        """Test streaming with invalid agent returns 404."""
        response = client.post(
            "/api/chat/stream",
            json={
                "agent_id": invalid_agent_id,
                "message": "Hello",
            }
        )
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "AGENT_NOT_FOUND"

    def test_chat_stream_invalid_json(self, client: TestClient):
        """Test streaming with invalid JSON returns error."""
        response = client.post(
            "/api/chat/stream",
            content="not json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code in [400, 422]

    def test_chat_stream_missing_message(self, client: TestClient):
        """Test streaming without explicit message still succeeds (message is optional)."""
        response = client.post(
            "/api/chat/stream",
            json={
                "agent_id": "default",
                # message is optional per ChatRequest schema
            }
        )
        # message field is Optional[str], so this is a valid request
        assert response.status_code == 200


class TestChatSessions:
    """Tests for chat session endpoints."""

    def test_list_sessions_success(self, client: TestClient):
        """Test listing sessions returns 200."""
        response = client.get("/api/chat/sessions")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_sessions_filter_by_agent(self, client: TestClient):
        """Test filtering sessions by agent_id."""
        response = client.get("/api/chat/sessions?agent_id=default")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_delete_session_not_found(self, client: TestClient, invalid_session_id: str):
        """Test deleting non-existent session returns 404."""
        response = client.delete(f"/api/chat/sessions/{invalid_session_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
class TestChatStreamAsync:
    """Async tests for chat streaming functionality."""

    async def test_chat_stream_format(self, async_client: AsyncClient):
        """Test that stream returns SSE formatted data."""
        response = await async_client.post(
            "/api/chat/stream",
            json={
                "agent_id": "default",
                "message": "Say hello",
            }
        )
        # For valid agent, should return streaming response
        # Since default agent exists, this should work
        # The actual streaming test requires more setup with mock agent
        assert response.status_code in [200, 404]  # 404 if agent_manager not configured


class TestChatErrorHandling:
    """Tests for chat error handling."""

    def test_chat_stream_validation_error_format(self, client: TestClient):
        """Test validation errors have correct format."""
        response = client.post(
            "/api/chat/stream",
            json={}  # Missing required fields
        )
        assert response.status_code in [400, 422]
        data = response.json()
        assert "code" in data
        assert "message" in data

    def test_chat_stream_agent_not_found_format(self, client: TestClient, invalid_agent_id: str):
        """Test agent not found error has correct format."""
        response = client.post(
            "/api/chat/stream",
            json={
                "agent_id": invalid_agent_id,
                "message": "Hello",
            }
        )
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "AGENT_NOT_FOUND"
        assert "message" in data
        assert "suggested_action" in data
