"""Unit tests for Reflection API router endpoints.

Tests CRUD operations, pagination, filtering by reflection_type,
error responses, and snake_case response format for the
/api/workspaces/{id}/reflections endpoints.

Requirements: 28.9
"""
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace_id(client: TestClient) -> str:
    """Create a workspace and return its ID for reflection tests."""
    import tempfile
    temp_path = tempfile.mkdtemp()
    resp = client.post("/api/swarm-workspaces", json={
        "name": "ReflectionTestWS",
        "file_path": temp_path,
        "context": "Workspace for reflection router tests",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture
def second_workspace_id(client: TestClient) -> str:
    """Create a second workspace for filtering tests."""
    import tempfile
    temp_path = tempfile.mkdtemp()
    resp = client.post("/api/swarm-workspaces", json={
        "name": "ReflectionTestWS2",
        "file_path": temp_path,
        "context": "Second workspace for reflection filtering tests",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_reflection(client: TestClient, workspace_id: str, **overrides) -> dict:
    """Helper to create a reflection and return the response JSON."""
    payload = {
        "workspace_id": workspace_id,
        "title": overrides.pop("title", "Test Reflection"),
        "reflection_type": overrides.pop("reflection_type", "daily_recap"),
        "file_path": overrides.pop("file_path", "Artifacts/Reports/daily_recap_2025-02-21.md"),
        "period_start": overrides.pop("period_start", "2025-02-21T00:00:00Z"),
        "period_end": overrides.pop("period_end", "2025-02-21T23:59:59Z"),
        **overrides,
    }
    resp = client.post(f"/api/workspaces/{workspace_id}/reflections", json=payload)
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------

class TestCreateReflection:
    """Tests for POST /api/workspaces/{id}/reflections. Validates: Requirement 28.9"""

    def test_create_success(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/reflections", json={
            "workspace_id": workspace_id,
            "title": "Daily Recap - 2025-02-21",
            "reflection_type": "daily_recap",
            "file_path": "Artifacts/Reports/daily_recap_2025-02-21.md",
            "period_start": "2025-02-21T00:00:00Z",
            "period_end": "2025-02-21T23:59:59Z",
            "generated_by": "system",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Daily Recap - 2025-02-21"
        assert data["reflection_type"] == "daily_recap"
        assert data["generated_by"] == "system"
        assert data["workspace_id"] == workspace_id
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_minimal(self, client: TestClient, workspace_id: str):
        """Only required fields: workspace_id, title, reflection_type, file_path, period_start, period_end."""
        resp = client.post(f"/api/workspaces/{workspace_id}/reflections", json={
            "workspace_id": workspace_id,
            "title": "Quick recap",
            "reflection_type": "daily_recap",
            "file_path": "Artifacts/Reports/daily_recap_2025-03-01.md",
            "period_start": "2025-03-01T00:00:00Z",
            "period_end": "2025-03-01T23:59:59Z",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Quick recap"
        assert data["generated_by"] == "user"  # default

    def test_create_weekly_summary(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/reflections", json={
            "workspace_id": workspace_id,
            "title": "Weekly Summary - 2025-02-17",
            "reflection_type": "weekly_summary",
            "file_path": "Artifacts/Reports/weekly_summary_2025-02-17.md",
            "period_start": "2025-02-17T00:00:00Z",
            "period_end": "2025-02-23T23:59:59Z",
            "generated_by": "agent",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["reflection_type"] == "weekly_summary"
        assert data["generated_by"] == "agent"

    def test_create_lessons_learned(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/reflections", json={
            "workspace_id": workspace_id,
            "title": "Lessons from sprint 5",
            "reflection_type": "lessons_learned",
            "file_path": "Artifacts/Reports/lessons_learned_2025-02-21.md",
            "period_start": "2025-02-10T00:00:00Z",
            "period_end": "2025-02-21T23:59:59Z",
        })
        assert resp.status_code == 201
        assert resp.json()["reflection_type"] == "lessons_learned"

    def test_create_uses_path_workspace_id(self, client: TestClient, workspace_id: str):
        """Path workspace_id should override body workspace_id."""
        resp = client.post(f"/api/workspaces/{workspace_id}/reflections", json={
            "workspace_id": "different-id",
            "title": "Path wins",
            "reflection_type": "daily_recap",
            "file_path": "Artifacts/Reports/daily_recap_2025-04-01.md",
            "period_start": "2025-04-01T00:00:00Z",
            "period_end": "2025-04-01T23:59:59Z",
        })
        assert resp.status_code == 201
        assert resp.json()["workspace_id"] == workspace_id

    def test_create_all_generated_by_values(self, client: TestClient, workspace_id: str):
        for gen_by in ["user", "agent", "system"]:
            resp = client.post(f"/api/workspaces/{workspace_id}/reflections", json={
                "workspace_id": workspace_id,
                "title": f"Reflection by {gen_by}",
                "reflection_type": "daily_recap",
                "file_path": f"Artifacts/Reports/daily_recap_{gen_by}.md",
                "period_start": "2025-05-01T00:00:00Z",
                "period_end": "2025-05-01T23:59:59Z",
                "generated_by": gen_by,
            })
            assert resp.status_code == 201
            assert resp.json()["generated_by"] == gen_by


# ---------------------------------------------------------------------------
# Update Tests
# ---------------------------------------------------------------------------

class TestUpdateReflection:
    """Tests for PUT /api/workspaces/{id}/reflections/{reflection_id}. Validates: Requirement 28.9"""

    def test_update_title(self, client: TestClient, workspace_id: str):
        created = _create_reflection(client, workspace_id, title="Old title")
        resp = client.put(
            f"/api/workspaces/{workspace_id}/reflections/{created['id']}",
            json={"title": "New title"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New title"

    def test_update_reflection_type(self, client: TestClient, workspace_id: str):
        created = _create_reflection(client, workspace_id, reflection_type="daily_recap")
        resp = client.put(
            f"/api/workspaces/{workspace_id}/reflections/{created['id']}",
            json={"reflection_type": "lessons_learned"},
        )
        assert resp.status_code == 200
        assert resp.json()["reflection_type"] == "lessons_learned"

    def test_update_generated_by(self, client: TestClient, workspace_id: str):
        created = _create_reflection(client, workspace_id)
        resp = client.put(
            f"/api/workspaces/{workspace_id}/reflections/{created['id']}",
            json={"generated_by": "agent"},
        )
        assert resp.status_code == 200
        assert resp.json()["generated_by"] == "agent"

    def test_update_not_found(self, client: TestClient, workspace_id: str):
        resp = client.put(
            f"/api/workspaces/{workspace_id}/reflections/nonexistent-id",
            json={"title": "Nope"},
        )
        assert resp.status_code == 404

    def test_update_partial(self, client: TestClient, workspace_id: str):
        """Partial update should only change provided fields."""
        created = _create_reflection(
            client, workspace_id,
            title="Keep me",
            reflection_type="weekly_summary",
            generated_by="system",
        )
        resp = client.put(
            f"/api/workspaces/{workspace_id}/reflections/{created['id']}",
            json={"title": "Updated title"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated title"
        assert data["reflection_type"] == "weekly_summary"
        assert data["generated_by"] == "system"


# ---------------------------------------------------------------------------
# Delete Tests
# ---------------------------------------------------------------------------

class TestDeleteReflection:
    """Tests for DELETE /api/workspaces/{id}/reflections/{reflection_id}. Validates: Requirement 28.9"""

    def test_delete_success(self, client: TestClient, workspace_id: str):
        created = _create_reflection(client, workspace_id)
        resp = client.delete(
            f"/api/workspaces/{workspace_id}/reflections/{created['id']}"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert resp.json()["reflection_id"] == created["id"]

    def test_delete_removes_item(self, client: TestClient, workspace_id: str):
        """Deleted item should no longer appear in list."""
        created = _create_reflection(client, workspace_id, title="Gone soon")
        client.delete(f"/api/workspaces/{workspace_id}/reflections/{created['id']}")
        resp = client.get(f"/api/workspaces/{workspace_id}/reflections")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()]
        assert created["id"] not in ids

    def test_delete_not_found(self, client: TestClient, workspace_id: str):
        resp = client.delete(
            f"/api/workspaces/{workspace_id}/reflections/nonexistent-id"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List, Pagination, and Filtering
# ---------------------------------------------------------------------------

class TestListReflections:
    """Tests for GET /api/workspaces/{id}/reflections. Validates: Requirement 28.9"""

    def test_list_empty(self, client: TestClient, workspace_id: str):
        resp = client.get(f"/api/workspaces/{workspace_id}/reflections")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created_items(self, client: TestClient, workspace_id: str):
        _create_reflection(client, workspace_id, title="First")
        _create_reflection(
            client, workspace_id, title="Second",
            period_start="2025-02-22T00:00:00Z",
            period_end="2025-02-22T23:59:59Z",
        )
        resp = client.get(f"/api/workspaces/{workspace_id}/reflections")
        assert resp.status_code == 200
        titles = [item["title"] for item in resp.json()]
        assert "First" in titles
        assert "Second" in titles

    def test_list_scoped_to_workspace(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        _create_reflection(client, workspace_id, title="WS1 reflection")
        _create_reflection(client, second_workspace_id, title="WS2 reflection")

        resp = client.get(f"/api/workspaces/{workspace_id}/reflections")
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["workspace_id"] == workspace_id for item in data)
        assert any(item["title"] == "WS1 reflection" for item in data)
        assert not any(item["title"] == "WS2 reflection" for item in data)

    def test_list_filter_by_reflection_type(self, client: TestClient, workspace_id: str):
        _create_reflection(
            client, workspace_id, title="Daily one", reflection_type="daily_recap",
        )
        _create_reflection(
            client, workspace_id, title="Weekly one", reflection_type="weekly_summary",
            period_start="2025-02-17T00:00:00Z",
            period_end="2025-02-23T23:59:59Z",
        )

        resp = client.get(
            f"/api/workspaces/{workspace_id}/reflections?reflection_type=daily_recap"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["reflection_type"] == "daily_recap" for item in data)
        assert any(item["title"] == "Daily one" for item in data)
        assert not any(item["title"] == "Weekly one" for item in data)

    def test_list_pagination_limit(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_reflection(
                client, workspace_id, title=f"Reflection {i}",
                period_start=f"2025-03-{i+1:02d}T00:00:00Z",
                period_end=f"2025-03-{i+1:02d}T23:59:59Z",
            )

        resp = client.get(f"/api/workspaces/{workspace_id}/reflections?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_list_pagination_offset(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_reflection(
                client, workspace_id, title=f"Reflection {i}",
                period_start=f"2025-04-{i+1:02d}T00:00:00Z",
                period_end=f"2025-04-{i+1:02d}T23:59:59Z",
            )

        all_resp = client.get(
            f"/api/workspaces/{workspace_id}/reflections?limit=100"
        )
        total = len(all_resp.json())

        offset_resp = client.get(
            f"/api/workspaces/{workspace_id}/reflections?offset=2&limit=100"
        )
        assert offset_resp.status_code == 200
        assert len(offset_resp.json()) == total - 2


# ---------------------------------------------------------------------------
# Response format (snake_case)
# ---------------------------------------------------------------------------

class TestResponseFormat:
    """Validates snake_case field names in responses."""

    def test_response_uses_snake_case(self, client: TestClient, workspace_id: str):
        _create_reflection(client, workspace_id)
        resp = client.get(f"/api/workspaces/{workspace_id}/reflections")
        data = resp.json()[0]
        # Verify snake_case keys
        assert "workspace_id" in data
        assert "reflection_type" in data
        assert "file_path" in data
        assert "period_start" in data
        assert "period_end" in data
        assert "generated_by" in data
        assert "created_at" in data
        assert "updated_at" in data
        # Verify no camelCase keys
        assert "workspaceId" not in data
        assert "reflectionType" not in data
        assert "filePath" not in data
        assert "periodStart" not in data
        assert "generatedBy" not in data
