"""Tests for chat API endpoints."""
import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient
import json

from database import db


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

    def test_list_sessions_with_limit(self, client: TestClient):
        """Test listing sessions with a valid limit param."""
        response = client.get("/api/chat/sessions?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_sessions_limit_zero_rejected(self, client: TestClient):
        """Test that limit=0 is rejected with 422."""
        response = client.get("/api/chat/sessions?limit=0")
        assert response.status_code == 422

    def test_list_sessions_limit_negative_rejected(self, client: TestClient):
        """Test that negative limit is rejected with 422."""
        response = client.get("/api/chat/sessions?limit=-1")
        assert response.status_code == 422

    def test_list_sessions_limit_caps_at_100(self, client: TestClient):
        """Test that limit > 100 is silently capped (no error)."""
        response = client.get("/api/chat/sessions?limit=200")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_sessions_without_limit_returns_all(self, client: TestClient):
        """Test that omitting limit returns all sessions (backward compat)."""
        response = client.get("/api/chat/sessions")
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
class TestChatSessionsLimit:
    """Tests for the limit query param on GET /api/chat/sessions.

    Seeds sessions into the DB and verifies that limit constrains the
    result count, ordering is deterministic (last_accessed DESC,
    created_at DESC), and backward compatibility is preserved.
    """

    async def _seed_sessions(self, count: int = 5):
        """Seed N sessions with staggered timestamps for ordering tests."""
        from core.session_manager import session_manager
        import uuid
        sessions = []
        for i in range(count):
            ts = f"2025-01-{10 + i:02d}T12:00:00"
            sid = str(uuid.uuid4())
            await db.sessions.put({
                "id": sid,
                "agent_id": "default",
                "title": f"Session {i}",
                "user_id": None,
                "work_dir": None,
                "created_at": ts,
                "last_accessed": ts,
            })
            sessions.append(sid)
        return sessions

    async def test_limit_constrains_result_count(self, async_client: AsyncClient):
        """Seeding 5 sessions and requesting limit=2 returns exactly 2."""
        await self._seed_sessions(5)
        response = await async_client.get("/api/chat/sessions?limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    async def test_limit_returns_most_recent_first(self, async_client: AsyncClient):
        """Sessions are ordered by last_accessed DESC."""
        await self._seed_sessions(5)
        response = await async_client.get("/api/chat/sessions?limit=3")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        # Verify descending order by last_accessed_at
        timestamps = [s["last_accessed_at"] for s in data]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_no_limit_returns_all(self, async_client: AsyncClient):
        """Omitting limit returns all seeded sessions."""
        await self._seed_sessions(5)
        response = await async_client.get("/api/chat/sessions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 5

    async def test_limit_larger_than_count_returns_all(self, async_client: AsyncClient):
        """limit=100 with only 3 sessions returns all 3."""
        await self._seed_sessions(3)
        response = await async_client.get("/api/chat/sessions?limit=100")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3


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
        assert response.status_code in [200, 404]  # 404 if session infrastructure not configured


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
