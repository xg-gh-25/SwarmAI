"""Unit tests for PlanItem API router endpoints.

Tests CRUD operations, pagination, filtering by status and focus_type,
and error responses for the /api/workspaces/{id}/plan-items endpoints.

Requirements: 22.8
"""
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace_id(client: TestClient) -> str:
    """Return the singleton workspace ID after seeding workspace_config."""
    import asyncio
    from tests.helpers import ensure_default_workspace
    return asyncio.get_event_loop().run_until_complete(ensure_default_workspace())


@pytest.fixture
def second_workspace_id(client: TestClient) -> str:
    """Return the singleton workspace ID (same as workspace_id in single-workspace model)."""
    import asyncio
    from tests.helpers import ensure_default_workspace
    return asyncio.get_event_loop().run_until_complete(ensure_default_workspace())


def _create_plan_item(client: TestClient, workspace_id: str, **overrides) -> dict:
    """Helper to create a plan item and return the response JSON."""
    payload = {
        "workspace_id": workspace_id,
        "title": overrides.pop("title", "Test PlanItem"),
        **overrides,
    }
    resp = client.post(f"/api/workspaces/{workspace_id}/plan-items", json=payload)
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------

class TestCreatePlanItem:
    """Tests for POST /api/workspaces/{id}/plan-items. Validates: Requirement 22.8"""

    def test_create_success(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/plan-items", json={
            "workspace_id": workspace_id,
            "title": "Ship auth feature",
            "description": "Complete the authentication module",
            "priority": "high",
            "focus_type": "today",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Ship auth feature"
        assert data["description"] == "Complete the authentication module"
        assert data["priority"] == "high"
        assert data["focus_type"] == "today"
        assert data["status"] == "active"
        assert data["workspace_id"] == workspace_id
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_minimal(self, client: TestClient, workspace_id: str):
        """Only workspace_id and title are required."""
        resp = client.post(f"/api/workspaces/{workspace_id}/plan-items", json={
            "workspace_id": workspace_id,
            "title": "Minimal plan item",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Minimal plan item"
        assert data["status"] == "active"
        assert data["focus_type"] == "upcoming"
        assert data["priority"] == "none"
        assert data["sort_order"] == 0

    def test_create_with_linked_todo(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/plan-items", json={
            "workspace_id": workspace_id,
            "title": "From signal",
            "source_todo_id": "some-todo-id",
        })
        assert resp.status_code == 201
        assert resp.json()["source_todo_id"] == "some-todo-id"

    def test_create_with_linked_task(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/plan-items", json={
            "workspace_id": workspace_id,
            "title": "Linked to task",
            "source_task_id": "some-task-id",
        })
        assert resp.status_code == 201
        assert resp.json()["source_task_id"] == "some-task-id"

    def test_create_uses_path_workspace_id(self, client: TestClient, workspace_id: str):
        """Path workspace_id should override body workspace_id."""
        resp = client.post(f"/api/workspaces/{workspace_id}/plan-items", json={
            "workspace_id": "different-id",
            "title": "Path wins",
        })
        assert resp.status_code == 201
        assert resp.json()["workspace_id"] == workspace_id


class TestUpdatePlanItem:
    """Tests for PUT /api/workspaces/{id}/plan-items/{item_id}. Validates: Requirement 22.8"""

    def test_update_title(self, client: TestClient, workspace_id: str):
        created = _create_plan_item(client, workspace_id, title="Old title")
        resp = client.put(
            f"/api/workspaces/{workspace_id}/plan-items/{created['id']}",
            json={"title": "New title"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New title"

    def test_update_status(self, client: TestClient, workspace_id: str):
        created = _create_plan_item(client, workspace_id)
        resp = client.put(
            f"/api/workspaces/{workspace_id}/plan-items/{created['id']}",
            json={"status": "completed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_update_focus_type(self, client: TestClient, workspace_id: str):
        created = _create_plan_item(client, workspace_id, focus_type="upcoming")
        resp = client.put(
            f"/api/workspaces/{workspace_id}/plan-items/{created['id']}",
            json={"focus_type": "today"},
        )
        assert resp.status_code == 200
        assert resp.json()["focus_type"] == "today"

    def test_update_priority(self, client: TestClient, workspace_id: str):
        created = _create_plan_item(client, workspace_id)
        resp = client.put(
            f"/api/workspaces/{workspace_id}/plan-items/{created['id']}",
            json={"priority": "high"},
        )
        assert resp.status_code == 200
        assert resp.json()["priority"] == "high"

    def test_update_not_found(self, client: TestClient, workspace_id: str):
        resp = client.put(
            f"/api/workspaces/{workspace_id}/plan-items/nonexistent-id",
            json={"title": "Nope"},
        )
        assert resp.status_code == 404

    def test_update_partial(self, client: TestClient, workspace_id: str):
        """Partial update should only change provided fields."""
        created = _create_plan_item(
            client, workspace_id, title="Keep me", priority="high", focus_type="today"
        )
        resp = client.put(
            f"/api/workspaces/{workspace_id}/plan-items/{created['id']}",
            json={"description": "Added desc"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Keep me"
        assert data["priority"] == "high"
        assert data["focus_type"] == "today"
        assert data["description"] == "Added desc"


class TestDeletePlanItem:
    """Tests for DELETE /api/workspaces/{id}/plan-items/{item_id}. Validates: Requirement 22.8"""

    def test_delete_success(self, client: TestClient, workspace_id: str):
        created = _create_plan_item(client, workspace_id)
        resp = client.delete(
            f"/api/workspaces/{workspace_id}/plan-items/{created['id']}"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert resp.json()["plan_item_id"] == created["id"]

    def test_delete_removes_item(self, client: TestClient, workspace_id: str):
        """Deleted item should no longer appear in list."""
        created = _create_plan_item(client, workspace_id, title="Gone soon")
        client.delete(f"/api/workspaces/{workspace_id}/plan-items/{created['id']}")
        resp = client.get(f"/api/workspaces/{workspace_id}/plan-items")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()]
        assert created["id"] not in ids

    def test_delete_not_found(self, client: TestClient, workspace_id: str):
        resp = client.delete(
            f"/api/workspaces/{workspace_id}/plan-items/nonexistent-id"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List, Pagination, and Filtering
# ---------------------------------------------------------------------------

class TestListPlanItems:
    """Tests for GET /api/workspaces/{id}/plan-items. Validates: Requirement 22.8"""

    def test_list_empty(self, client: TestClient, workspace_id: str):
        resp = client.get(f"/api/workspaces/{workspace_id}/plan-items")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created_items(self, client: TestClient, workspace_id: str):
        _create_plan_item(client, workspace_id, title="First")
        _create_plan_item(client, workspace_id, title="Second")
        resp = client.get(f"/api/workspaces/{workspace_id}/plan-items")
        assert resp.status_code == 200
        titles = [item["title"] for item in resp.json()]
        assert "First" in titles
        assert "Second" in titles

    def test_list_scoped_to_workspace(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        _create_plan_item(client, workspace_id, title="WS1 item")
        _create_plan_item(client, second_workspace_id, title="WS2 item")

        resp = client.get(f"/api/workspaces/{workspace_id}/plan-items")
        assert resp.status_code == 200
        data = resp.json()
        # In singleton model, both IDs resolve to the same workspace,
        # so all plan items are visible.
        assert all(item["workspace_id"] == workspace_id for item in data)
        assert any(item["title"] == "WS1 item" for item in data)
        assert any(item["title"] == "WS2 item" for item in data)

    def test_list_filter_by_status(self, client: TestClient, workspace_id: str):
        _create_plan_item(client, workspace_id, title="Active one")
        item2 = _create_plan_item(client, workspace_id, title="Deferred one")
        client.put(
            f"/api/workspaces/{workspace_id}/plan-items/{item2['id']}",
            json={"status": "deferred"},
        )

        resp = client.get(f"/api/workspaces/{workspace_id}/plan-items?status=active")
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["status"] == "active" for item in data)

    def test_list_filter_by_focus_type(self, client: TestClient, workspace_id: str):
        _create_plan_item(client, workspace_id, title="Today item", focus_type="today")
        _create_plan_item(client, workspace_id, title="Upcoming item", focus_type="upcoming")

        resp = client.get(
            f"/api/workspaces/{workspace_id}/plan-items?focus_type=today"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["focus_type"] == "today" for item in data)
        assert any(item["title"] == "Today item" for item in data)

    def test_list_pagination_limit(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_plan_item(client, workspace_id, title=f"Item {i}")

        resp = client.get(f"/api/workspaces/{workspace_id}/plan-items?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_list_pagination_offset(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_plan_item(client, workspace_id, title=f"Item {i}")

        all_resp = client.get(f"/api/workspaces/{workspace_id}/plan-items?limit=100")
        total = len(all_resp.json())

        offset_resp = client.get(
            f"/api/workspaces/{workspace_id}/plan-items?offset=2&limit=100"
        )
        assert offset_resp.status_code == 200
        assert len(offset_resp.json()) == total - 2


# ---------------------------------------------------------------------------
# Response format (snake_case)
# ---------------------------------------------------------------------------

class TestResponseFormat:
    """Validates snake_case field names in responses."""

    def test_response_uses_snake_case(self, client: TestClient, workspace_id: str):
        created = _create_plan_item(client, workspace_id, focus_type="today")
        resp = client.get(f"/api/workspaces/{workspace_id}/plan-items")
        data = resp.json()[0]
        # Verify snake_case keys
        assert "workspace_id" in data
        assert "focus_type" in data
        assert "sort_order" in data
        assert "source_todo_id" in data
        assert "source_task_id" in data
        assert "scheduled_date" in data
        assert "created_at" in data
        assert "updated_at" in data
        # Verify no camelCase keys
        assert "workspaceId" not in data
        assert "focusType" not in data
        assert "sortOrder" not in data
