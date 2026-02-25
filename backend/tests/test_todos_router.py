"""Unit tests for ToDo/Signal API router endpoints.

Tests CRUD operations, conversion to task, pagination, filtering,
and error responses for the /api/todos endpoints.

Requirements: 6.1-6.8
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


@pytest.fixture
def sample_todo(workspace_id: str) -> dict:
    """Sample todo creation payload."""
    return {
        "workspace_id": workspace_id,
        "title": "Review PR #42",
        "description": "Review the pull request for the auth module",
        "source": "github",
        "source_type": "integration",
        "priority": "high",
    }


def _create_todo(client: TestClient, workspace_id: str, **overrides) -> dict:
    """Helper to create a todo and return the response JSON."""
    payload = {
        "workspace_id": workspace_id,
        "title": overrides.pop("title", "Test ToDo"),
        **overrides,
    }
    resp = client.post("/api/todos", json=payload)
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------

class TestCreateTodo:
    """Tests for POST /api/todos. Validates: Requirement 6.2"""

    def test_create_todo_success(self, client: TestClient, sample_todo: dict):
        resp = client.post("/api/todos", json=sample_todo)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == sample_todo["title"]
        assert data["description"] == sample_todo["description"]
        assert data["source_type"] == "integration"
        assert data["priority"] == "high"
        assert data["status"] == "pending"
        assert data["workspace_id"] == sample_todo["workspace_id"]
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_todo_minimal(self, client: TestClient, workspace_id: str):
        """Only workspace_id and title are required."""
        resp = client.post("/api/todos", json={
            "workspace_id": workspace_id,
            "title": "Minimal todo",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Minimal todo"
        assert data["source_type"] == "manual"
        assert data["priority"] == "none"
        assert data["status"] == "pending"
        assert data["description"] is None

    def test_create_todo_with_due_date(self, client: TestClient, workspace_id: str):
        resp = client.post("/api/todos", json={
            "workspace_id": workspace_id,
            "title": "Due soon",
            "due_date": "2099-12-31T23:59:59Z",
        })
        assert resp.status_code == 201
        assert resp.json()["due_date"] is not None

    def test_create_todo_missing_title(self, client: TestClient, workspace_id: str):
        resp = client.post("/api/todos", json={"workspace_id": workspace_id})
        assert resp.status_code in (400, 422)

    def test_create_todo_missing_workspace_id(self, client: TestClient):
        resp = client.post("/api/todos", json={"title": "No workspace"})
        assert resp.status_code in (400, 422)


class TestGetTodo:
    """Tests for GET /api/todos/{id}. Validates: Requirement 6.3"""

    def test_get_todo_success(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, title="Fetch me")
        resp = client.get(f"/api/todos/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["id"] == created["id"]
        assert resp.json()["title"] == "Fetch me"

    def test_get_todo_not_found(self, client: TestClient):
        resp = client.get("/api/todos/nonexistent-id-999")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


class TestUpdateTodo:
    """Tests for PUT /api/todos/{id}. Validates: Requirement 6.4"""

    def test_update_todo_title(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, title="Old title")
        resp = client.put(f"/api/todos/{created['id']}", json={"title": "New title"})
        assert resp.status_code == 200
        assert resp.json()["title"] == "New title"

    def test_update_todo_status(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id)
        resp = client.put(f"/api/todos/{created['id']}", json={"status": "in_discussion"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_discussion"

    def test_update_todo_priority(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id)
        resp = client.put(f"/api/todos/{created['id']}", json={"priority": "medium"})
        assert resp.status_code == 200
        assert resp.json()["priority"] == "medium"

    def test_update_todo_not_found(self, client: TestClient):
        resp = client.put("/api/todos/nonexistent-id-999", json={"title": "Nope"})
        assert resp.status_code == 404

    def test_update_todo_partial(self, client: TestClient, workspace_id: str):
        """Partial update should only change provided fields."""
        created = _create_todo(client, workspace_id, title="Keep me", priority="high")
        resp = client.put(f"/api/todos/{created['id']}", json={"description": "Added desc"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Keep me"
        assert data["priority"] == "high"
        assert data["description"] == "Added desc"


class TestDeleteTodo:
    """Tests for DELETE /api/todos/{id}. Validates: Requirement 6.5"""

    def test_delete_todo_success(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id)
        resp = client.delete(f"/api/todos/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_todo_soft_deletes(self, client: TestClient, workspace_id: str):
        """Delete should set status to 'deleted', not remove the record."""
        created = _create_todo(client, workspace_id)
        client.delete(f"/api/todos/{created['id']}")
        # The todo should still be retrievable
        resp = client.get(f"/api/todos/{created['id']}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_todo_not_found(self, client: TestClient):
        resp = client.delete("/api/todos/nonexistent-id-999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Convert to Task
# ---------------------------------------------------------------------------

class TestConvertToTask:
    """Tests for POST /api/todos/{id}/convert-to-task. Validates: Requirement 6.6"""

    def test_convert_success(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, title="Convert me", priority="high")
        resp = client.post(f"/api/todos/{created['id']}/convert-to-task", json={
            "agent_id": "default",
        })
        assert resp.status_code == 200
        task = resp.json()
        assert task["title"] == "Convert me"
        assert task["status"] == "draft"
        assert task["source_todo_id"] == created["id"]
        assert task["workspace_id"] == workspace_id

    def test_convert_updates_todo_status(self, client: TestClient, workspace_id: str):
        """After conversion, the ToDo status should be 'handled' with task_id set."""
        created = _create_todo(client, workspace_id, title="Handle me")
        resp = client.post(f"/api/todos/{created['id']}/convert-to-task", json={
            "agent_id": "default",
        })
        assert resp.status_code == 200
        task_id = resp.json()["id"]

        todo_resp = client.get(f"/api/todos/{created['id']}")
        assert todo_resp.status_code == 200
        todo = todo_resp.json()
        assert todo["status"] == "handled"
        assert todo["task_id"] == task_id

    def test_convert_with_overrides(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, title="Original title")
        resp = client.post(f"/api/todos/{created['id']}/convert-to-task", json={
            "agent_id": "default",
            "title": "Overridden title",
            "description": "Custom description",
            "priority": "low",
        })
        assert resp.status_code == 200
        task = resp.json()
        assert task["title"] == "Overridden title"
        assert task["description"] == "Custom description"

    def test_convert_not_found(self, client: TestClient):
        resp = client.post("/api/todos/nonexistent-id-999/convert-to-task", json={
            "agent_id": "default",
        })
        assert resp.status_code == 404

    def test_convert_invalid_agent(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id)
        resp = client.post(f"/api/todos/{created['id']}/convert-to-task", json={
            "agent_id": "nonexistent-agent-xyz",
        })
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# List, Pagination, and Filtering
# ---------------------------------------------------------------------------

class TestListTodos:
    """Tests for GET /api/todos. Validates: Requirements 6.1, 6.8"""

    def test_list_empty(self, client: TestClient):
        resp = client.get("/api/todos")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created_todos(self, client: TestClient, workspace_id: str):
        _create_todo(client, workspace_id, title="First")
        _create_todo(client, workspace_id, title="Second")
        resp = client.get("/api/todos")
        assert resp.status_code == 200
        titles = [t["title"] for t in resp.json()]
        assert "First" in titles
        assert "Second" in titles

    def test_list_filter_by_workspace(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        _create_todo(client, workspace_id, title="WS1 todo")
        _create_todo(client, second_workspace_id, title="WS2 todo")

        resp = client.get(f"/api/todos?workspace_id={workspace_id}")
        assert resp.status_code == 200
        data = resp.json()
        # In singleton model, both IDs resolve to the same workspace,
        # so all todos are visible.
        assert all(t["workspace_id"] == workspace_id for t in data)
        assert any(t["title"] == "WS1 todo" for t in data)
        assert any(t["title"] == "WS2 todo" for t in data)

    def test_list_filter_by_status(self, client: TestClient, workspace_id: str):
        todo1 = _create_todo(client, workspace_id, title="Pending one")
        _create_todo(client, workspace_id, title="Discussed one")
        # Update second to in_discussion
        client.put(f"/api/todos/{_create_todo(client, workspace_id, title='Disc')['id']}",
                    json={"status": "in_discussion"})

        resp = client.get("/api/todos?status=pending")
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["status"] == "pending" for t in data)

    def test_list_pagination_limit(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_todo(client, workspace_id, title=f"Todo {i}")

        resp = client.get("/api/todos?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_list_pagination_offset(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_todo(client, workspace_id, title=f"Todo {i}")

        all_resp = client.get("/api/todos?limit=100")
        total = len(all_resp.json())

        offset_resp = client.get(f"/api/todos?offset=2&limit=100")
        assert offset_resp.status_code == 200
        assert len(offset_resp.json()) == total - 2

    def test_list_pagination_limit_and_offset(self, client: TestClient, workspace_id: str):
        for i in range(10):
            _create_todo(client, workspace_id, title=f"Page todo {i}")

        resp = client.get("/api/todos?limit=3&offset=2")
        assert resp.status_code == 200
        assert len(resp.json()) <= 3


# ---------------------------------------------------------------------------
# Response format (snake_case)
# ---------------------------------------------------------------------------

class TestResponseFormat:
    """Validates: Requirement 6.7 - snake_case field names."""

    def test_response_uses_snake_case(self, client: TestClient, workspace_id: str):
        created = _create_todo(client, workspace_id, source_type="email")
        resp = client.get(f"/api/todos/{created['id']}")
        data = resp.json()
        # Verify snake_case keys
        assert "workspace_id" in data
        assert "source_type" in data
        assert "created_at" in data
        assert "updated_at" in data
        assert "due_date" in data
        assert "task_id" in data
        # Verify no camelCase keys
        assert "workspaceId" not in data
        assert "sourceType" not in data
        assert "createdAt" not in data
