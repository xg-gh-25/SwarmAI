"""Unit tests for the /health endpoint's ``pending_hook_tasks`` field.

This module verifies that the ``/health`` endpoint correctly exposes the
number of in-flight background hook tasks via the ``pending_hook_tasks``
field.  The field reads from ``agent_manager.hook_executor.pending_count``
and returns 0 when the executor is ``None``.

Testing methodology: unit tests using FastAPI's ``TestClient`` with
``unittest.mock.patch`` to control the ``_startup_complete`` flag and
direct attribute assignment on the ``agent_manager`` singleton to swap
the ``_hook_executor``.

Key scenarios tested:

- ``pending_hook_tasks`` is present in a healthy response
- ``pending_hook_tasks`` is 0 when no hooks are running
- ``pending_hook_tasks`` reflects actual pending count
- ``pending_hook_tasks`` is 0 when hook_executor is None
- ``pending_hook_tasks`` is absent from initializing response

**Validates: Requirements 7.3**
"""

import contextlib
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def test_client():
    """Create a synchronous TestClient for endpoint testing."""
    from main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@contextlib.contextmanager
def _patch_hook_executor(executor):
    """Temporarily swap the agent_manager's _hook_executor attribute.

    The ``hook_executor`` property is a simple read-through to
    ``self._hook_executor``, so swapping the private attribute is the
    cleanest way to control what ``/health`` sees without fighting
    ``PropertyMock`` on a singleton.
    """
    from core.agent_manager import agent_manager
    original = agent_manager._hook_executor
    agent_manager._hook_executor = executor
    try:
        yield
    finally:
        agent_manager._hook_executor = original


class TestHealthEndpointPendingHookTasks:
    """Tests for the pending_hook_tasks field in /health response.

    **Validates: Requirements 7.3**
    """

    def test_healthy_response_includes_pending_hook_tasks(self, test_client):
        """Verify ``pending_hook_tasks`` field is present when healthy."""
        mock_executor = MagicMock()
        mock_executor.pending_count = 0

        with patch("main._startup_complete", True), \
             _patch_hook_executor(mock_executor):
            resp = test_client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert "pending_hook_tasks" in body

    def test_pending_hook_tasks_is_zero_when_idle(self, test_client):
        """Verify field is 0 when no hooks are running."""
        mock_executor = MagicMock()
        mock_executor.pending_count = 0

        with patch("main._startup_complete", True), \
             _patch_hook_executor(mock_executor):
            resp = test_client.get("/health")

        assert resp.json()["pending_hook_tasks"] == 0

    def test_pending_hook_tasks_reflects_in_flight_count(self, test_client):
        """Verify field reflects actual pending count when hooks are in flight."""
        mock_executor = MagicMock()
        mock_executor.pending_count = 3

        with patch("main._startup_complete", True), \
             _patch_hook_executor(mock_executor):
            resp = test_client.get("/health")

        assert resp.json()["pending_hook_tasks"] == 3

    def test_pending_hook_tasks_zero_when_executor_is_none(self, test_client):
        """Verify field is 0 when hook_executor is None."""
        with patch("main._startup_complete", True), \
             _patch_hook_executor(None):
            resp = test_client.get("/health")

        assert resp.json()["pending_hook_tasks"] == 0

    def test_initializing_response_has_no_pending_hook_tasks(self, test_client):
        """Verify initializing response does NOT include pending_hook_tasks."""
        with patch("main._startup_complete", False):
            resp = test_client.get("/health")

        body = resp.json()
        assert body["status"] == "initializing"
        assert "pending_hook_tasks" not in body
