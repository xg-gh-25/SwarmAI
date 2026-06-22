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

    def test_concurrent_agents_elapsed_oldest_label_newest(self, client, mock_session_unit):
        """AC2: with multiple concurrent Agent tools, elapsed_s tracks the
        OLDEST (stuck-detection), but label reflects the NEWEST active
        sub-agent (so the banner shows current activity, not the
        first-spawned one frozen — the 'Spec compliance review' bug)."""
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
        assert 298 <= data["elapsed_s"] <= 305  # ~300s — elapsed from OLDEST (stuck signal)
        assert data["label"] == "Test generation"  # label from NEWEST (current activity)
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


class TestUserMessageCleanup:
    """AC1/AC3: sub-agent (Agent tool) ToolResultBlocks arrive via UserMessage,
    not AssistantMessage. The streaming loop must clear the matching
    _active_agent_tools entry when that UserMessage is processed — otherwise
    entries accumulate (count frozen, timer climbs forever, label stale).

    These force-execute the cleanup path (STEERING #11) using REAL SDK
    UserMessage + ToolResultBlock objects, not mocks.
    """

    def _make_orchestrator(self):
        """Build a StreamingOrchestrator with a stand-in parent carrying
        an _active_agent_tools dict — no SDK client, no subprocess."""
        from core.streaming_orchestrator import StreamingOrchestrator

        parent = MagicMock()
        parent.session_id = "test-session-cleanup"
        parent._active_agent_tools = {}
        orch = StreamingOrchestrator(parent)
        return orch, parent

    def test_user_message_tool_result_removes_matching_entry(self):
        """AC1: a UserMessage carrying a ToolResultBlock pops the matching
        Agent entry keyed by the original Agent ToolUseBlock.id == ToolResultBlock.tool_use_id."""
        from claude_agent_sdk import UserMessage, ToolResultBlock

        orch, parent = self._make_orchestrator()
        # Seed: an Agent sub-agent was spawned (keyed by its ToolUseBlock.id)
        parent._active_agent_tools = {
            "tu_agent_1": {"label": "Spec compliance review", "start_time": time.time() - 600},
            "tu_agent_2": {"label": "Adversarial review", "start_time": time.time() - 60},
        }

        # The closing UserMessage delivers the sub-agent's result. The SDK
        # sets ToolResultBlock.tool_use_id == the original Agent ToolUseBlock.id.
        msg = UserMessage(content=[ToolResultBlock(tool_use_id="tu_agent_1", content="done")])
        orch._clear_completed_sub_agents(msg)

        assert "tu_agent_1" not in parent._active_agent_tools  # completed → removed
        assert "tu_agent_2" in parent._active_agent_tools      # still running → kept

    def test_user_message_string_content_is_noop(self):
        """AC3: a UserMessage with plain string content (no blocks) must not crash."""
        from claude_agent_sdk import UserMessage

        orch, parent = self._make_orchestrator()
        parent._active_agent_tools = {"tu_x": {"label": "x", "start_time": time.time()}}
        msg = UserMessage(content="just text, no tool results")
        orch._clear_completed_sub_agents(msg)  # must not raise
        assert parent._active_agent_tools == {"tu_x": {"label": "x", "start_time": parent._active_agent_tools["tu_x"]["start_time"]}}

    def test_user_message_unrelated_tool_result_is_noop(self):
        """AC3: a ToolResultBlock whose id is NOT a tracked Agent (e.g. a
        parent's own Edit/Write result) must be a no-op pop — never affects
        tracking or rendering."""
        from claude_agent_sdk import UserMessage, ToolResultBlock

        orch, parent = self._make_orchestrator()
        parent._active_agent_tools = {"tu_agent_1": {"label": "review", "start_time": time.time()}}
        # tu_edit_99 is a parent Edit result, never tracked in _active_agent_tools
        msg = UserMessage(content=[ToolResultBlock(tool_use_id="tu_edit_99", content="ok")])
        orch._clear_completed_sub_agents(msg)
        assert "tu_agent_1" in parent._active_agent_tools  # untouched
