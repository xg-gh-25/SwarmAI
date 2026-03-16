"""Unit tests for the TSCC FastAPI router endpoints.

Tests the TSCC API endpoints defined in ``routers/tscc.py``:

- ``GET  /api/chat_threads/{thread_id}/tscc``       — get state
- ``GET  /api/chat/{session_id}/system-prompt``      — get system prompt metadata

Testing methodology: unit tests with mocked TSCCStateManager
injected via ``register_tscc_dependencies()``.

Key invariants verified:
- 200 responses for valid resources
- 404 responses for missing threads/sessions
- All response field names are snake_case
"""

import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from schemas.tscc import (
    TSCCActiveCapabilities,
    TSCCContext,
    TSCCLiveState,
    TSCCState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")


def _all_keys_snake_case(obj) -> bool:
    """Recursively check that every key in a JSON-like object is snake_case."""
    if isinstance(obj, dict):
        for key in obj:
            if not SNAKE_CASE_RE.match(key):
                return False
            if not _all_keys_snake_case(obj[key]):
                return False
    elif isinstance(obj, list):
        for item in obj:
            if not _all_keys_snake_case(item):
                return False
    return True


def _make_tscc_state(thread_id: str = "thread-1") -> TSCCState:
    """Build a minimal valid TSCCState for testing."""
    return TSCCState(
        thread_id=thread_id,
        project_id=None,
        scope_type="workspace",
        last_updated_at=datetime.now(timezone.utc).isoformat(),
        lifecycle_state="active",
        live_state=TSCCLiveState(
            context=TSCCContext(
                scope_label="Workspace: SwarmWS (General)",
                thread_title="Test Thread",
                mode=None,
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_managers():
    """Create mock state manager and register it."""
    state_mgr = AsyncMock()

    import routers.tscc as tscc_mod
    tscc_mod._state_manager = state_mgr

    yield state_mgr

    # Reset to None after test
    tscc_mod._state_manager = None


# ---------------------------------------------------------------------------
# GET /api/chat_threads/{thread_id}/tscc
# ---------------------------------------------------------------------------

class TestGetTSCCState:
    """Tests for the GET tscc state endpoint."""

    def test_returns_200_with_valid_state(self, client: TestClient, mock_managers):
        state_mgr = mock_managers
        state = _make_tscc_state("thread-abc")
        state_mgr.get_state.return_value = state

        resp = client.get("/api/chat_threads/thread-abc/tscc")

        assert resp.status_code == 200
        body = resp.json()
        assert body["thread_id"] == "thread-abc"
        assert body["lifecycle_state"] == "active"

    def test_returns_default_state_for_missing_thread(self, client: TestClient, mock_managers):
        """Missing thread returns 200 with default state (not 404).

        The router intentionally returns a default empty state for
        uninitiated threads to avoid frontend 404 console errors.
        """
        state_mgr = mock_managers
        state_mgr.get_state.return_value = None

        resp = client.get("/api/chat_threads/no-such-thread/tscc")

        assert resp.status_code == 200
        body = resp.json()
        assert body["thread_id"] == "no-such-thread"
        assert body["lifecycle_state"] == "new"

    def test_response_fields_are_snake_case(self, client: TestClient, mock_managers):
        state_mgr = mock_managers
        state_mgr.get_state.return_value = _make_tscc_state()

        resp = client.get("/api/chat_threads/thread-1/tscc")

        assert resp.status_code == 200
        assert _all_keys_snake_case(resp.json())


# ---------------------------------------------------------------------------
# GET /api/chat/{session_id}/system-prompt
# ---------------------------------------------------------------------------

class TestGetSystemPrompt:
    """Tests for the GET system prompt metadata endpoint."""

    def test_returns_200_with_valid_metadata(self, client: TestClient):
        from core.agent_manager import _system_prompt_metadata
        _system_prompt_metadata["session-123"] = {
            "files": [
                {"filename": "SWARMAI.md", "tokens": 500, "truncated": False},
                {"filename": "IDENTITY.md", "tokens": 300, "truncated": False},
            ],
            "total_tokens": 800,
            "full_text": "## SwarmAI\nHello\n\n## Identity\nWorld",
        }
        try:
            resp = client.get("/api/chat/session-123/system-prompt")
            assert resp.status_code == 200
            body = resp.json()
            assert len(body["files"]) == 2
            assert body["total_tokens"] == 800
            assert body["files"][0]["filename"] == "SWARMAI.md"
            assert "full_text" in body
        finally:
            _system_prompt_metadata.pop("session-123", None)

    def test_returns_default_for_missing_session(self, client: TestClient):
        """Missing session returns 200 with empty metadata (not 404).

        The router returns an empty SystemPromptMetadata for sessions
        without cached metadata to avoid frontend errors.
        """
        resp = client.get("/api/chat/nonexistent-session/system-prompt")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_tokens"] == 0
        assert body["files"] == []

    def test_response_fields_are_snake_case(self, client: TestClient):
        from core.agent_manager import _system_prompt_metadata
        _system_prompt_metadata["session-sc"] = {
            "files": [{"filename": "SOUL.md", "tokens": 100, "truncated": False}],
            "total_tokens": 100,
            "full_text": "test",
        }
        try:
            resp = client.get("/api/chat/session-sc/system-prompt")
            assert resp.status_code == 200
            assert _all_keys_snake_case(resp.json())
        finally:
            _system_prompt_metadata.pop("session-sc", None)
