"""Unit tests for workspace archive/unarchive API endpoints.

Tests archive, unarchive, include_archived filtering, and 403 on
write operations against archived workspaces.

Requirements: 36.1-36.11
"""
import pytest
import tempfile
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_workspace_id(client: TestClient) -> str:
    """Create the default SwarmWS workspace and return its ID."""
    resp = client.get("/api/swarm-workspaces/default")
    if resp.status_code == 200:
        return resp.json()["id"]
    # Create it if not present
    temp_path = tempfile.mkdtemp()
    from database import db
    import asyncio, uuid
    from datetime import datetime, timezone

    ws_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    asyncio.get_event_loop().run_until_complete(db.swarm_workspaces.put({
        "id": ws_id,
        "name": "SwarmWS",
        "file_path": temp_path,
        "context": "Default workspace",
        "icon": "🏠",
        "is_default": True,
        "is_archived": 0,
        "archived_at": None,
        "created_at": now,
        "updated_at": now,
    }))
    return ws_id


@pytest.fixture
def workspace_id(client: TestClient) -> str:
    """Create a custom workspace and return its ID."""
    temp_path = tempfile.mkdtemp()
    resp = client.post("/api/swarm-workspaces", json={
        "name": "ArchiveTestWS",
        "file_path": temp_path,
        "context": "Workspace for archive tests",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture
def second_workspace_id(client: TestClient) -> str:
    """Create a second custom workspace."""
    temp_path = tempfile.mkdtemp()
    resp = client.post("/api/swarm-workspaces", json={
        "name": "ArchiveTestWS2",
        "file_path": temp_path,
        "context": "Second workspace for archive tests",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


def _archive(client: TestClient, ws_id: str):
    """Helper to archive a workspace."""
    return client.post(f"/api/swarm-workspaces/{ws_id}/archive")


def _unarchive(client: TestClient, ws_id: str):
    """Helper to unarchive a workspace."""
    return client.post(f"/api/swarm-workspaces/{ws_id}/unarchive")


# ---------------------------------------------------------------------------
# Archive endpoint tests
# ---------------------------------------------------------------------------

class TestArchiveWorkspace:
    """Tests for POST /api/swarm-workspaces/{id}/archive.

    Validates: Requirements 36.1, 36.2
    """

    def test_archive_custom_workspace(self, client: TestClient, workspace_id: str):
        resp = _archive(client, workspace_id)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_archived"] is True
        assert data["archived_at"] is not None

    def test_archive_sets_timestamp(self, client: TestClient, workspace_id: str):
        resp = _archive(client, workspace_id)
        assert resp.status_code == 200
        assert resp.json()["archived_at"] is not None

    def test_archive_default_workspace_forbidden(
        self, client: TestClient, default_workspace_id: str
    ):
        resp = _archive(client, default_workspace_id)
        assert resp.status_code == 403

    def test_archive_nonexistent_workspace(self, client: TestClient):
        resp = _archive(client, "nonexistent-id")
        assert resp.status_code == 404

    def test_archive_already_archived(self, client: TestClient, workspace_id: str):
        """Archiving an already-archived workspace should still succeed."""
        _archive(client, workspace_id)
        resp = _archive(client, workspace_id)
        assert resp.status_code == 200
        assert resp.json()["is_archived"] is True


# ---------------------------------------------------------------------------
# Unarchive endpoint tests
# ---------------------------------------------------------------------------

class TestUnarchiveWorkspace:
    """Tests for POST /api/swarm-workspaces/{id}/unarchive.

    Validates: Requirements 36.10
    """

    def test_unarchive_workspace(self, client: TestClient, workspace_id: str):
        _archive(client, workspace_id)
        resp = _unarchive(client, workspace_id)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_archived"] is False
        assert data["archived_at"] is None

    def test_unarchive_restores_functionality(self, client: TestClient, workspace_id: str):
        _archive(client, workspace_id)
        # Update should fail while archived
        resp = client.put(f"/api/swarm-workspaces/{workspace_id}", json={"name": "Renamed"})
        assert resp.status_code == 403
        # Unarchive
        _unarchive(client, workspace_id)
        # Update should succeed now
        resp = client.put(f"/api/swarm-workspaces/{workspace_id}", json={"name": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_unarchive_nonexistent_workspace(self, client: TestClient):
        resp = _unarchive(client, "nonexistent-id")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List filtering tests
# ---------------------------------------------------------------------------

class TestListWithArchiveFilter:
    """Tests for GET /api/swarm-workspaces with include_archived param.

    Validates: Requirements 36.3, 36.4
    """

    def test_default_list_excludes_archived(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        _archive(client, workspace_id)
        resp = client.get("/api/swarm-workspaces")
        assert resp.status_code == 200
        ids = [w["id"] for w in resp.json()]
        assert workspace_id not in ids
        assert second_workspace_id in ids

    def test_include_archived_true(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        _archive(client, workspace_id)
        resp = client.get("/api/swarm-workspaces", params={"include_archived": True})
        assert resp.status_code == 200
        ids = [w["id"] for w in resp.json()]
        assert workspace_id in ids
        assert second_workspace_id in ids

    def test_include_archived_false_explicit(
        self, client: TestClient, workspace_id: str
    ):
        _archive(client, workspace_id)
        resp = client.get("/api/swarm-workspaces", params={"include_archived": False})
        assert resp.status_code == 200
        ids = [w["id"] for w in resp.json()]
        assert workspace_id not in ids


# ---------------------------------------------------------------------------
# Write-operation guard tests
# ---------------------------------------------------------------------------

class TestArchivedWorkspaceWriteGuard:
    """Tests that write operations return 403 on archived workspaces.

    Validates: Requirements 36.6, 36.7, 36.8
    """

    def test_update_archived_workspace_forbidden(
        self, client: TestClient, workspace_id: str
    ):
        _archive(client, workspace_id)
        resp = client.put(
            f"/api/swarm-workspaces/{workspace_id}",
            json={"name": "Should Fail"},
        )
        assert resp.status_code == 403

    def test_read_archived_workspace_allowed(
        self, client: TestClient, workspace_id: str
    ):
        _archive(client, workspace_id)
        resp = client.get(f"/api/swarm-workspaces/{workspace_id}")
        assert resp.status_code == 200
        assert resp.json()["is_archived"] is True


# ---------------------------------------------------------------------------
# Response format tests
# ---------------------------------------------------------------------------

class TestArchiveResponseFormat:
    """Tests that archive responses include expected fields."""

    def test_archive_response_has_required_fields(
        self, client: TestClient, workspace_id: str
    ):
        resp = _archive(client, workspace_id)
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "name" in data
        assert "is_archived" in data
        assert "archived_at" in data
        assert "is_default" in data

    def test_unarchive_response_clears_archived_at(
        self, client: TestClient, workspace_id: str
    ):
        _archive(client, workspace_id)
        resp = _unarchive(client, workspace_id)
        data = resp.json()
        assert data["is_archived"] is False
        assert data["archived_at"] is None
