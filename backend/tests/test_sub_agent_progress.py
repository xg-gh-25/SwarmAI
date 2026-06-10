"""Tests for sub-agent progress observability endpoint.

Verifies GET /api/chat/sessions/{id}/sub-agent-progress returns correct
tiered awareness data when a sub-agent (Agent tool) is active on a session.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client with mocked session router."""
    from main import app
    return TestClient(app)


@pytest.fixture
def mock_session_unit():
    """Create a mock SessionUnit with sub-agent progress tracking."""
    unit = MagicMock()
    unit.session_id = "test-session-123"
    unit.state = MagicMock()
    unit.state.value = "streaming"
    return unit


class TestSubAgentProgressEndpoint:
    """Test GET /api/chat/sessions/{id}/sub-agent-progress."""

    def test_returns_inactive_when_no_agent_tool(self, client, mock_session_unit):
        """When session exists but no Agent tool is active, returns active=false."""
        mock_session_unit._active_agent_tool = None

        with patch("routers.chat._get_router") as mock_router:
            mock_router.return_value.get_unit.return_value = mock_session_unit
            resp = client.get("/api/chat/sessions/test-session-123/sub-agent-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False
        assert data["elapsed_s"] == 0
        assert data["label"] is None

    def test_returns_active_with_elapsed_when_agent_running(self, client, mock_session_unit):
        """When Agent tool is active, returns elapsed time and label."""
        mock_session_unit._active_agent_tool = {
            "tool_use_id": "tu_abc123",
            "label": "Design DDD runtime activation",
            "start_time": time.time() - 185.0,  # 3 min 5 sec ago
        }

        with patch("routers.chat._get_router") as mock_router:
            mock_router.return_value.get_unit.return_value = mock_session_unit
            resp = client.get("/api/chat/sessions/test-session-123/sub-agent-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert 183 <= data["elapsed_s"] <= 190  # ~185s with tolerance
        assert data["label"] == "Design DDD runtime activation"

    def test_returns_404_when_session_not_found(self, client):
        """When session doesn't exist, returns 404."""
        with patch("routers.chat._get_router") as mock_router:
            mock_router.return_value.get_unit.return_value = None
            resp = client.get("/api/chat/sessions/nonexistent/sub-agent-progress")

        assert resp.status_code == 404

    def test_returns_inactive_when_session_not_streaming(self, client, mock_session_unit):
        """When session is IDLE (not streaming), returns active=false."""
        mock_session_unit.state.value = "idle"
        mock_session_unit._active_agent_tool = {
            "tool_use_id": "tu_abc123",
            "label": "stale agent",
            "start_time": time.time() - 60.0,
        }

        with patch("routers.chat._get_router") as mock_router:
            mock_router.return_value.get_unit.return_value = mock_session_unit
            resp = client.get("/api/chat/sessions/test-session-123/sub-agent-progress")

        assert resp.status_code == 200
        data = resp.json()
        # Even if _active_agent_tool is set, if session isn't streaming, report inactive
        assert data["active"] is False
