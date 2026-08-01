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
    return asyncio.run(ensure_default_workspace())


@pytest.fixture
def second_workspace_id(client: TestClient) -> str:
    """Return the singleton workspace ID (same as workspace_id in single-workspace model)."""
    import asyncio
    from tests.helpers import ensure_default_workspace
    return asyncio.run(ensure_default_workspace())


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


# ---------------------------------------------------------------------------
# ToDo Flow-Closure — History, Stats, Review (run_d28de5fd)
# ---------------------------------------------------------------------------

class TestFlowClosureEndpoints:
    """/history, /history/stats, /{id}/review + new response fields (AC1/AC5/AC6)."""

    def test_response_exposes_flow_fields(self, client: TestClient, workspace_id: str):
        """AC1: the 7 new columns reach the API response via _dict_to_response."""
        todo = _create_todo(client, workspace_id, title="flow fields")
        # new fields present (None by default), proving _dict_to_response threads them
        for f in ("review_state", "review_kind", "dispatched_session_id",
                  "dispatched_tab_label", "dispatched_at", "completed_at", "reviewed_at"):
            assert f in todo, f"{f} missing from ToDoResponse"
        assert todo["review_state"] is None

    def test_history_stats_shape(self, client: TestClient, workspace_id: str):
        """AC6: /history/stats returns the 5 aggregations."""
        _create_todo(client, workspace_id, title="h1")
        resp = client.get("/api/todos/history/stats")
        assert resp.status_code == 200
        data = resp.json()
        for k in ("throughput_weekly", "completion_rate", "source_distribution",
                  "confirm_vs_auto", "reject_rate", "totals"):
            assert k in data

    def test_history_returns_todos(self, client: TestClient, workspace_id: str):
        """AC5: /history returns a todos list (not soft-filtered)."""
        _create_todo(client, workspace_id, title="hist item")
        resp = client.get("/api/todos/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "todos" in data and "count" in data
        assert any(t["title"] == "hist item" for t in data["todos"])

    def test_review_confirm_sets_handled(self, client: TestClient, workspace_id: str):
        """AC5: confirm → status=handled + review_state=confirmed (locked invariant)."""
        todo = _create_todo(client, workspace_id, title="to confirm")
        resp = client.post(f"/api/todos/{todo['id']}/review", json={"action": "confirm"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "handled"
        after = client.get(f"/api/todos/{todo['id']}").json()
        assert after["status"] == "handled"
        assert after["review_state"] == "confirmed"
        assert after["review_kind"] == "manual"

    def test_review_reject_creates_new_todo(self, client: TestClient, workspace_id: str):
        """AC5: reject → original closed rejected + a NEW pending todo is created."""
        todo = _create_todo(client, workspace_id, title="to reject")
        resp = client.post(f"/api/todos/{todo['id']}/review", json={"action": "reject"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cancelled"
        assert body["new_todo_id"] and body["new_todo_id"] != todo["id"]
        orig = client.get(f"/api/todos/{todo['id']}").json()
        assert orig["review_state"] == "rejected"
        new = client.get(f"/api/todos/{body['new_todo_id']}").json()
        assert new["status"] == "pending"
        assert new["title"] == "to reject"

    def test_review_invalid_action(self, client: TestClient, workspace_id: str):
        todo = _create_todo(client, workspace_id, title="bad action")
        resp = client.post(f"/api/todos/{todo['id']}/review", json={"action": "nope"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# ToDo Flow-Closure — Dispatch + Retreat (run_5088b841, A2)
# ---------------------------------------------------------------------------

class TestDispatchRetreat:
    """POST /{id}/dispatch writes dispatched_* + keeps status pending; /retreat clears it."""

    def test_dispatch_writes_snapshot_keeps_pending(self, client: TestClient, workspace_id: str):
        """AC3/AC4: dispatch writes tab_label+dispatched_at, status STAYS pending."""
        todo = _create_todo(client, workspace_id, title="to dispatch")
        resp = client.post(f"/api/todos/{todo['id']}/dispatch", json={"tab_label": "Tab 2"})
        assert resp.status_code == 200
        d = resp.json()
        assert d["dispatched_tab_label"] == "Tab 2"
        assert d["dispatched_at"] is not None
        assert d["status"] == "pending"  # locked invariant — NOT in_discussion
        assert d["dispatched_session_id"] is None  # not given → backfilled later

    def test_dispatch_with_session_id(self, client: TestClient, workspace_id: str):
        todo = _create_todo(client, workspace_id, title="dispatch w/ sid")
        resp = client.post(f"/api/todos/{todo['id']}/dispatch", json={"tab_label": "Tab 1", "session_id": "sess-abc"})
        assert resp.status_code == 200
        assert resp.json()["dispatched_session_id"] == "sess-abc"

    def test_retreat_clears_snapshot(self, client: TestClient, workspace_id: str):
        """AC5: retreat clears dispatched_* → back to ① To Do zone."""
        todo = _create_todo(client, workspace_id, title="to retreat")
        client.post(f"/api/todos/{todo['id']}/dispatch", json={"tab_label": "Tab 3", "session_id": "s1"})
        resp = client.post(f"/api/todos/{todo['id']}/retreat")
        assert resp.status_code == 200
        d = resp.json()
        assert d["dispatched_session_id"] is None
        assert d["dispatched_tab_label"] is None
        assert d["dispatched_at"] is None
        assert d["status"] == "pending"

    def test_dispatch_404(self, client: TestClient):
        resp = client.post("/api/todos/nonexistent/dispatch", json={"tab_label": "Tab 1"})
        assert resp.status_code == 404
