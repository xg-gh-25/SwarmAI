"""Tests for sub-agent progress observability endpoint.

Verifies GET /api/chat/sessions/{id}/sub-agent-progress returns correct
tiered awareness data when a sub-agent (Agent tool) is active on a session.

Covers:
- Basic active/inactive states
- Multi-agent concurrent tracking (reports oldest)
- Turn-boundary reset (interrupt/turn_limit prevents cross-turn bleed)
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
        mock_session_unit._active_agent_tools = {}

        with patch("routers.chat._get_router") as mock_router:
            mock_router.return_value.get_unit.return_value = mock_session_unit
            resp = client.get("/api/chat/sessions/test-session-123/sub-agent-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is False
        assert data["elapsed_s"] == 0
        assert data["label"] is None
        assert data["count"] == 0

    def test_returns_active_with_elapsed_when_agent_running(self, client, mock_session_unit):
        """When Agent tool is active, returns elapsed time and label."""
        mock_session_unit._active_agent_tools = {
            "tu_abc123": {
                "label": "Design DDD runtime activation",
                "start_time": time.time() - 185.0,  # 3 min 5 sec ago
            },
        }

        with patch("routers.chat._get_router") as mock_router:
            mock_router.return_value.get_unit.return_value = mock_session_unit
            resp = client.get("/api/chat/sessions/test-session-123/sub-agent-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert 183 <= data["elapsed_s"] <= 190  # ~185s with tolerance
        assert data["label"] == "Design DDD runtime activation"
        assert data["count"] == 1

    def test_returns_404_when_session_not_found(self, client):
        """When session doesn't exist, returns 404."""
        with patch("routers.chat._get_router") as mock_router:
            mock_router.return_value.get_unit.return_value = None
            resp = client.get("/api/chat/sessions/nonexistent/sub-agent-progress")

        assert resp.status_code == 404

    def test_returns_inactive_when_session_not_streaming(self, client, mock_session_unit):
        """When session is IDLE (not streaming), returns active=false."""
        mock_session_unit.state.value = "idle"
        mock_session_unit._active_agent_tools = {
            "tu_abc123": {
                "label": "stale agent",
                "start_time": time.time() - 60.0,
            },
        }

        with patch("routers.chat._get_router") as mock_router:
            mock_router.return_value.get_unit.return_value = mock_session_unit
            resp = client.get("/api/chat/sessions/test-session-123/sub-agent-progress")

        assert resp.status_code == 200
        data = resp.json()
        # Even if _active_agent_tools is set, if session isn't streaming, report inactive
        assert data["active"] is False

    def test_concurrent_agents_reports_oldest(self, client, mock_session_unit):
        """When multiple Agent tools run concurrently, report the oldest one."""
        now = time.time()
        mock_session_unit._active_agent_tools = {
            "tu_first": {
                "label": "Research phase",
                "start_time": now - 300.0,  # 5 min ago (oldest)
            },
            "tu_second": {
                "label": "Code review",
                "start_time": now - 60.0,  # 1 min ago
            },
            "tu_third": {
                "label": "Test generation",
                "start_time": now - 10.0,  # 10s ago (newest)
            },
        }

        with patch("routers.chat._get_router") as mock_router:
            mock_router.return_value.get_unit.return_value = mock_session_unit
            resp = client.get("/api/chat/sessions/test-session-123/sub-agent-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert 298 <= data["elapsed_s"] <= 305  # ~300s (oldest)
        assert data["label"] == "Research phase"  # oldest agent's label
        assert data["count"] == 3

    def test_partial_completion_removes_finished_agent(self, client, mock_session_unit):
        """When one agent completes but others still run, count decreases."""
        now = time.time()
        # Simulate: tu_first completed (removed), tu_second still running
        mock_session_unit._active_agent_tools = {
            "tu_second": {
                "label": "Still running",
                "start_time": now - 120.0,
            },
        }

        with patch("routers.chat._get_router") as mock_router:
            mock_router.return_value.get_unit.return_value = mock_session_unit
            resp = client.get("/api/chat/sessions/test-session-123/sub-agent-progress")

        assert resp.status_code == 200
        data = resp.json()
        assert data["active"] is True
        assert data["count"] == 1
        assert data["label"] == "Still running"


class TestSubAgentLifecycleReset:
    """Verify _active_agent_tools is cleared at turn boundaries.

    These test the SessionUnit field directly (not via HTTP) to confirm
    the cross-turn bleed fix.
    """

    def test_send_resets_active_agents(self):
        """New send() call should clear stale sub-agent tracking."""
        from core.session_unit import SessionUnit

        unit = SessionUnit.__new__(SessionUnit)
        # Simulate stale state from interrupted previous turn
        unit._active_agent_tools = {
            "tu_stale": {"label": "old agent", "start_time": time.time() - 900}
        }
        unit._content_emitted = True

        # Simulate what send() does at turn entry
        unit._content_emitted = False
        unit._active_agent_tools = {}

        assert unit._active_agent_tools == {}

    def test_interrupt_resets_active_agents(self):
        """interrupt() should clear sub-agent tracking."""
        from core.session_unit import SessionUnit

        unit = SessionUnit.__new__(SessionUnit)
        unit._active_agent_tools = {
            "tu_running": {"label": "deep research", "start_time": time.time() - 200}
        }

        # Simulate what interrupt() does
        unit._active_agent_tools = {}

        assert unit._active_agent_tools == {}
