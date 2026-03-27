"""Integration tests for the projects REST API router endpoints.

Tests the full HTTP request/response cycle for project CRUD operations
through ``backend/routers/projects.py``, exercising:

- ``POST /api/projects``                  — Create with 201, duplicate 409, invalid 400
- ``GET  /api/projects``                  — List sorted by created_at desc
- ``GET  /api/projects?name={name}``      — Name-based lookup
- ``GET  /api/projects/{id}``             — Get by UUID, 404 on missing
- ``PUT  /api/projects/{id}``             — Update fields, history tracking, rename
- ``DELETE /api/projects/{id}``           — Delete with 204, subsequent GET 404
- ``GET  /api/projects/{id}/history``     — Update history array

Each test uses a ``workspace_path`` fixture that creates a temporary
directory with a ``Projects/`` subdirectory and seeds the DB
``workspace_config`` row so the router can resolve the workspace path.

**Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.8, 18.9, 31.6**
"""

import asyncio
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace_path(client: TestClient):
    """Set up a temporary workspace with Projects/ directory for testing.

    Seeds the ``workspace_config`` DB row so the projects router can
    resolve the workspace path via ``_get_workspace_path()``.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        projects_dir = Path(tmpdir) / "Projects"
        projects_dir.mkdir()

        import database as database_module
        asyncio.run(
            database_module.db.workspace_config.put({
                "id": "swarmws",
                "name": "TestWorkspace",
                "file_path": tmpdir,
                "icon": "",
                "context": "",
                "created_at": "2025-01-01T00:00:00+00:00",
                "updated_at": "2025-01-01T00:00:00+00:00",
            })
        )
        yield tmpdir


def _create_project(client: TestClient, name: str = "TestProject") -> dict:
    """Helper: create a project and return the response JSON."""
    resp = client.post("/api/projects", json={"name": name})
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# POST /api/projects
# ---------------------------------------------------------------------------

class TestCreateProject:
    """Tests for POST /api/projects. Validates: Requirements 18.1, 18.2, 18.8"""

    def test_create_returns_201_with_full_metadata(
        self, client: TestClient, workspace_path: str
    ):
        """POST /api/projects returns 201 with all expected metadata fields."""
        resp = client.post("/api/projects", json={"name": "MyProject"})
        assert resp.status_code == 201

        data = resp.json()
        assert data["name"] == "MyProject"
        assert "id" in data
        assert data["status"] == "active"
        assert data["tags"] == []
        assert data["priority"] is None
        assert data["description"] == ""
        assert data["schema_version"] == "1.0.0"
        assert data["version"] == 1
        assert isinstance(data["created_at"], str)
        assert isinstance(data["updated_at"], str)
        assert isinstance(data["update_history"], list)
        assert len(data["update_history"]) == 1
        assert data["update_history"][0]["action"] == "created"

    def test_create_duplicate_name_returns_409(
        self, client: TestClient, workspace_path: str
    ):
        """Creating a project with a duplicate name returns 409."""
        _create_project(client, "DuplicateTest")
        resp = client.post("/api/projects", json={"name": "DuplicateTest"})
        assert resp.status_code == 409

    def test_create_empty_name_returns_400(
        self, client: TestClient, workspace_path: str
    ):
        """Creating a project with an empty name returns 400 (validation error).

        The error handler middleware converts Pydantic 422 to 400 with a
        structured ``VALIDATION_FAILED`` response.
        """
        resp = client.post("/api/projects", json={"name": ""})
        assert resp.status_code == 400
        data = resp.json()
        assert data["code"] == "VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# GET /api/projects
# ---------------------------------------------------------------------------

class TestListProjects:
    """Tests for GET /api/projects. Validates: Requirements 18.3, 18.8"""

    def test_list_empty(self, client: TestClient, workspace_path: str):
        """GET /api/projects returns empty list when no projects exist."""
        resp = client.get("/api/projects")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created_projects(
        self, client: TestClient, workspace_path: str
    ):
        """GET /api/projects returns all created projects."""
        _create_project(client, "ProjectA")
        _create_project(client, "ProjectB")

        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = {p["name"] for p in data}
        assert names == {"ProjectA", "ProjectB"}

    def test_list_sorted_by_created_at_desc(
        self, client: TestClient, workspace_path: str
    ):
        """GET /api/projects returns projects sorted by created_at descending."""
        _create_project(client, "First")
        time.sleep(0.05)  # small delay to ensure different timestamps
        _create_project(client, "Second")

        resp = client.get("/api/projects")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        # Most recently created should be first
        assert data[0]["name"] == "Second"
        assert data[1]["name"] == "First"


# ---------------------------------------------------------------------------
# GET /api/projects?name={name}
# ---------------------------------------------------------------------------

class TestGetProjectByName:
    """Tests for GET /api/projects?name={name}. Validates: Requirement 18.9"""

    def test_name_lookup_returns_matching_project(
        self, client: TestClient, workspace_path: str
    ):
        """GET /api/projects?name=X returns a list with the matching project."""
        created = _create_project(client, "LookupTarget")

        resp = client.get("/api/projects", params={"name": "LookupTarget"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == created["id"]
        assert data[0]["name"] == "LookupTarget"

    def test_name_lookup_no_match_returns_empty_list(
        self, client: TestClient, workspace_path: str
    ):
        """GET /api/projects?name=X returns empty list when no match."""
        resp = client.get("/api/projects", params={"name": "NonExistent"})
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /api/projects/{id}
# ---------------------------------------------------------------------------

class TestGetProject:
    """Tests for GET /api/projects/{id}. Validates: Requirement 18.4"""

    def test_get_by_id_returns_project(
        self, client: TestClient, workspace_path: str
    ):
        """GET /api/projects/{id} returns the project metadata."""
        created = _create_project(client, "GetMe")
        project_id = created["id"]

        resp = client.get(f"/api/projects/{project_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == project_id
        assert data["name"] == "GetMe"

    def test_get_nonexistent_returns_404(
        self, client: TestClient, workspace_path: str
    ):
        """GET /api/projects/{id} returns 404 for unknown UUID."""
        resp = client.get("/api/projects/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/projects/{id}
# ---------------------------------------------------------------------------

class TestUpdateProject:
    """Tests for PUT /api/projects/{id}. Validates: Requirements 18.5, 31.6"""

    def test_update_fields_and_tracks_history(
        self, client: TestClient, workspace_path: str
    ):
        """PUT /api/projects/{id} updates fields, increments version, appends history."""
        created = _create_project(client, "Updatable")
        project_id = created["id"]

        resp = client.put(
            f"/api/projects/{project_id}",
            json={"status": "archived", "description": "Now archived"},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "archived"
        assert data["description"] == "Now archived"
        assert data["version"] == 2
        assert len(data["update_history"]) == 2

        entry = data["update_history"][-1]
        assert entry["version"] == 2
        assert entry["action"] == "status_changed"
        assert "status" in entry["changes"]
        assert entry["changes"]["status"] == {"from": "active", "to": "archived"}

    def test_update_with_name_change_renames_directory(
        self, client: TestClient, workspace_path: str
    ):
        """PUT /api/projects/{id} with name change renames the project directory."""
        created = _create_project(client, "OldName")
        project_id = created["id"]

        resp = client.put(
            f"/api/projects/{project_id}",
            json={"name": "NewName"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "NewName"

        # Verify old directory is gone and new one exists
        old_dir = Path(workspace_path) / "Projects" / "OldName"
        new_dir = Path(workspace_path) / "Projects" / "NewName"
        assert not old_dir.exists()
        assert new_dir.exists()

        # Verify history records the rename
        entry = data["update_history"][-1]
        assert entry["action"] == "renamed"
        assert entry["changes"]["name"] == {"from": "OldName", "to": "NewName"}

    def test_update_nonexistent_returns_404(
        self, client: TestClient, workspace_path: str
    ):
        """PUT /api/projects/{id} returns 404 for unknown UUID."""
        resp = client.put(
            "/api/projects/00000000-0000-0000-0000-000000000000",
            json={"status": "archived"},
        )
        assert resp.status_code == 404

    def test_update_name_conflict_returns_409(
        self, client: TestClient, workspace_path: str
    ):
        """PUT /api/projects/{id} returns 409 when renaming to an existing name."""
        _create_project(client, "Existing")
        created = _create_project(client, "ToRename")

        resp = client.put(
            f"/api/projects/{created['id']}",
            json={"name": "Existing"},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /api/projects/{id}
# ---------------------------------------------------------------------------

class TestDeleteProject:
    """Tests for DELETE /api/projects/{id}. Validates: Requirement 18.6"""

    def test_delete_returns_204_and_removes_project(
        self, client: TestClient, workspace_path: str
    ):
        """DELETE /api/projects/{id} returns 204, subsequent GET returns 404."""
        created = _create_project(client, "ToDelete")
        project_id = created["id"]

        resp = client.delete(f"/api/projects/{project_id}")
        assert resp.status_code == 204

        resp = client.get(f"/api/projects/{project_id}")
        assert resp.status_code == 404

    def test_delete_nonexistent_returns_404(
        self, client: TestClient, workspace_path: str
    ):
        """DELETE /api/projects/{id} returns 404 for unknown UUID."""
        resp = client.delete("/api/projects/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/projects/{id}/history
# ---------------------------------------------------------------------------

class TestGetProjectHistory:
    """Tests for GET /api/projects/{id}/history. Validates: Requirement 31.6"""

    def test_history_returns_update_history_array(
        self, client: TestClient, workspace_path: str
    ):
        """GET /api/projects/{id}/history returns the update_history array."""
        created = _create_project(client, "HistoryProject")
        project_id = created["id"]

        # Perform an update to add a history entry
        client.put(
            f"/api/projects/{project_id}",
            json={"status": "completed"},
        )

        resp = client.get(f"/api/projects/{project_id}/history")
        assert resp.status_code == 200
        data = resp.json()

        assert data["project_id"] == project_id
        assert isinstance(data["history"], list)
        assert len(data["history"]) == 2
        assert data["history"][0]["action"] == "created"
        assert data["history"][1]["action"] == "status_changed"

    def test_history_nonexistent_returns_404(
        self, client: TestClient, workspace_path: str
    ):
        """GET /api/projects/{id}/history returns 404 for unknown UUID."""
        resp = client.get(
            "/api/projects/00000000-0000-0000-0000-000000000000/history"
        )
        assert resp.status_code == 404
