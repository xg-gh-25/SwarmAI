"""Unit tests for Artifact API router endpoints.

Tests CRUD operations, pagination, filtering by artifact_type,
versioning, error responses, and snake_case response format
for the /api/workspaces/{id}/artifacts endpoints.

Requirements: 27.8
"""
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace_id(client: TestClient) -> str:
    """Return the singleton workspace ID after seeding workspace_config."""
    import asyncio, tempfile
    from tests.helpers import ensure_default_workspace
    return asyncio.get_event_loop().run_until_complete(ensure_default_workspace())


@pytest.fixture
def second_workspace_id(client: TestClient) -> str:
    """Return the singleton workspace ID (same as workspace_id in single-workspace model)."""
    import asyncio
    from tests.helpers import ensure_default_workspace
    return asyncio.get_event_loop().run_until_complete(ensure_default_workspace())


def _create_artifact(client: TestClient, workspace_id: str, **overrides) -> dict:
    """Helper to create an artifact and return the response JSON."""
    payload = {
        "workspace_id": workspace_id,
        "title": overrides.pop("title", "Test Artifact"),
        "file_path": overrides.pop("file_path", "Artifacts/Docs/test-artifact_v001.md"),
        "created_by": overrides.pop("created_by", "test-user"),
        **overrides,
    }
    resp = client.post(f"/api/workspaces/{workspace_id}/artifacts", json=payload)
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------

class TestCreateArtifact:
    """Tests for POST /api/workspaces/{id}/artifacts. Validates: Requirement 27.8"""

    def test_create_success(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/artifacts", json={
            "workspace_id": workspace_id,
            "title": "Project Plan",
            "file_path": "Artifacts/Plans/project-plan_v001.md",
            "artifact_type": "plan",
            "created_by": "test-user",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Project Plan"
        assert data["artifact_type"] == "plan"
        assert data["created_by"] == "test-user"
        assert data["version"] == 1
        assert data["workspace_id"] == workspace_id
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_minimal(self, client: TestClient, workspace_id: str):
        """workspace_id, title, file_path, and created_by are required."""
        resp = client.post(f"/api/workspaces/{workspace_id}/artifacts", json={
            "workspace_id": workspace_id,
            "title": "Minimal artifact",
            "file_path": "Artifacts/Docs/minimal_v001.md",
            "created_by": "user",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Minimal artifact"
        assert data["artifact_type"] == "other"
        assert data["version"] == 1

    def test_create_with_tags(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/artifacts", json={
            "workspace_id": workspace_id,
            "title": "Tagged artifact",
            "file_path": "Artifacts/Docs/tagged_v001.md",
            "created_by": "user",
            "tags": ["important", "review"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert set(data["tags"]) == {"important", "review"}

    def test_create_with_task_id(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/artifacts", json={
            "workspace_id": workspace_id,
            "title": "Task output",
            "file_path": "Artifacts/Reports/task-output_v001.md",
            "artifact_type": "report",
            "created_by": "agent",
            "task_id": "some-task-id",
        })
        assert resp.status_code == 201
        assert resp.json()["task_id"] == "some-task-id"

    def test_create_uses_path_workspace_id(self, client: TestClient, workspace_id: str):
        """Path workspace_id should override body workspace_id."""
        resp = client.post(f"/api/workspaces/{workspace_id}/artifacts", json={
            "workspace_id": "different-id",
            "title": "Path wins",
            "file_path": "Artifacts/Docs/path-wins_v001.md",
            "created_by": "user",
        })
        assert resp.status_code == 201
        assert resp.json()["workspace_id"] == workspace_id

    def test_create_all_types(self, client: TestClient, workspace_id: str):
        """Verify all artifact_type values are accepted."""
        for art_type in ["plan", "report", "doc", "decision", "other"]:
            resp = client.post(f"/api/workspaces/{workspace_id}/artifacts", json={
                "workspace_id": workspace_id,
                "title": f"{art_type} artifact",
                "file_path": f"Artifacts/Docs/{art_type}_v001.md",
                "artifact_type": art_type,
                "created_by": "user",
            })
            assert resp.status_code == 201
            assert resp.json()["artifact_type"] == art_type


# ---------------------------------------------------------------------------
# Update Tests
# ---------------------------------------------------------------------------

class TestUpdateArtifact:
    """Tests for PUT /api/workspaces/{id}/artifacts/{artifact_id}. Validates: Requirement 27.8"""

    def test_update_title(self, client: TestClient, workspace_id: str):
        created = _create_artifact(client, workspace_id, title="Old title")
        resp = client.put(
            f"/api/workspaces/{workspace_id}/artifacts/{created['id']}",
            json={"title": "New title"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New title"

    def test_update_artifact_type(self, client: TestClient, workspace_id: str):
        created = _create_artifact(client, workspace_id, artifact_type="doc")
        resp = client.put(
            f"/api/workspaces/{workspace_id}/artifacts/{created['id']}",
            json={"artifact_type": "report"},
        )
        assert resp.status_code == 200
        assert resp.json()["artifact_type"] == "report"

    def test_update_tags(self, client: TestClient, workspace_id: str):
        created = _create_artifact(client, workspace_id, tags=["old-tag"])
        resp = client.put(
            f"/api/workspaces/{workspace_id}/artifacts/{created['id']}",
            json={"tags": ["new-tag", "another"]},
        )
        assert resp.status_code == 200
        assert set(resp.json()["tags"]) == {"new-tag", "another"}

    def test_update_not_found(self, client: TestClient, workspace_id: str):
        resp = client.put(
            f"/api/workspaces/{workspace_id}/artifacts/nonexistent-id",
            json={"title": "Nope"},
        )
        assert resp.status_code == 404

    def test_update_partial(self, client: TestClient, workspace_id: str):
        """Partial update should only change provided fields."""
        created = _create_artifact(
            client, workspace_id,
            title="Keep me",
            artifact_type="plan",
            created_by="original-user",
        )
        resp = client.put(
            f"/api/workspaces/{workspace_id}/artifacts/{created['id']}",
            json={"task_id": "linked-task"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Keep me"
        assert data["artifact_type"] == "plan"
        assert data["created_by"] == "original-user"
        assert data["task_id"] == "linked-task"


# ---------------------------------------------------------------------------
# Versioning Tests
# ---------------------------------------------------------------------------

class TestArtifactVersioning:
    """Tests for artifact versioning behavior. Validates: Requirements 27.4, 27.5"""

    def test_create_with_explicit_version(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/artifacts", json={
            "workspace_id": workspace_id,
            "title": "Versioned doc",
            "file_path": "Artifacts/Docs/versioned_v002.md",
            "version": 2,
            "created_by": "user",
        })
        assert resp.status_code == 201
        assert resp.json()["version"] == 2

    def test_default_version_is_one(self, client: TestClient, workspace_id: str):
        created = _create_artifact(client, workspace_id)
        assert created["version"] == 1


# ---------------------------------------------------------------------------
# Delete Tests
# ---------------------------------------------------------------------------

class TestDeleteArtifact:
    """Tests for DELETE /api/workspaces/{id}/artifacts/{artifact_id}. Validates: Requirement 27.8"""

    def test_delete_success(self, client: TestClient, workspace_id: str):
        created = _create_artifact(client, workspace_id)
        resp = client.delete(
            f"/api/workspaces/{workspace_id}/artifacts/{created['id']}"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert resp.json()["artifact_id"] == created["id"]

    def test_delete_removes_item(self, client: TestClient, workspace_id: str):
        """Deleted item should no longer appear in list."""
        created = _create_artifact(client, workspace_id, title="Gone soon")
        client.delete(f"/api/workspaces/{workspace_id}/artifacts/{created['id']}")
        resp = client.get(f"/api/workspaces/{workspace_id}/artifacts")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()]
        assert created["id"] not in ids

    def test_delete_not_found(self, client: TestClient, workspace_id: str):
        resp = client.delete(
            f"/api/workspaces/{workspace_id}/artifacts/nonexistent-id"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List, Pagination, and Filtering
# ---------------------------------------------------------------------------

class TestListArtifacts:
    """Tests for GET /api/workspaces/{id}/artifacts. Validates: Requirement 27.8"""

    def test_list_empty(self, client: TestClient, workspace_id: str):
        resp = client.get(f"/api/workspaces/{workspace_id}/artifacts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created_items(self, client: TestClient, workspace_id: str):
        _create_artifact(client, workspace_id, title="First")
        _create_artifact(client, workspace_id, title="Second")
        resp = client.get(f"/api/workspaces/{workspace_id}/artifacts")
        assert resp.status_code == 200
        titles = [item["title"] for item in resp.json()]
        assert "First" in titles
        assert "Second" in titles

    def test_list_scoped_to_workspace(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        _create_artifact(client, workspace_id, title="WS1 artifact")
        _create_artifact(client, second_workspace_id, title="WS2 artifact")

        resp = client.get(f"/api/workspaces/{workspace_id}/artifacts")
        assert resp.status_code == 200
        data = resp.json()
        # In singleton model, both workspace_id and second_workspace_id
        # resolve to the same ID, so all artifacts are visible.
        assert all(item["workspace_id"] == workspace_id for item in data)
        assert any(item["title"] == "WS1 artifact" for item in data)
        assert any(item["title"] == "WS2 artifact" for item in data)

    def test_list_filter_by_artifact_type(self, client: TestClient, workspace_id: str):
        _create_artifact(client, workspace_id, title="A plan", artifact_type="plan")
        _create_artifact(client, workspace_id, title="A report", artifact_type="report")

        resp = client.get(
            f"/api/workspaces/{workspace_id}/artifacts?artifact_type=plan"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["artifact_type"] == "plan" for item in data)
        assert any(item["title"] == "A plan" for item in data)
        assert not any(item["title"] == "A report" for item in data)

    def test_list_pagination_limit(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_artifact(client, workspace_id, title=f"Artifact {i}")

        resp = client.get(f"/api/workspaces/{workspace_id}/artifacts?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_list_pagination_offset(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_artifact(client, workspace_id, title=f"Artifact {i}")

        all_resp = client.get(f"/api/workspaces/{workspace_id}/artifacts?limit=100")
        total = len(all_resp.json())

        offset_resp = client.get(
            f"/api/workspaces/{workspace_id}/artifacts?offset=2&limit=100"
        )
        assert offset_resp.status_code == 200
        assert len(offset_resp.json()) == total - 2


# ---------------------------------------------------------------------------
# Response format (snake_case)
# ---------------------------------------------------------------------------

class TestResponseFormat:
    """Validates snake_case field names in responses."""

    def test_response_uses_snake_case(self, client: TestClient, workspace_id: str):
        _create_artifact(client, workspace_id, artifact_type="plan")
        resp = client.get(f"/api/workspaces/{workspace_id}/artifacts")
        data = resp.json()[0]
        # Verify snake_case keys
        assert "workspace_id" in data
        assert "artifact_type" in data
        assert "file_path" in data
        assert "created_by" in data
        assert "task_id" in data
        assert "created_at" in data
        assert "updated_at" in data
        # Verify no camelCase keys
        assert "workspaceId" not in data
        assert "artifactType" not in data
        assert "filePath" not in data
        assert "createdBy" not in data
