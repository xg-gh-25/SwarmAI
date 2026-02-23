"""Unit tests for Communication API router endpoints.

Tests CRUD operations, pagination, filtering by status and channel_type,
and error responses for the /api/workspaces/{id}/communications endpoints.

Requirements: 23.8
"""
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace_id(client: TestClient) -> str:
    """Create a workspace and return its ID for communication tests."""
    import tempfile
    temp_path = tempfile.mkdtemp()
    resp = client.post("/api/swarm-workspaces", json={
        "name": "CommTestWS",
        "file_path": temp_path,
        "context": "Workspace for communication router tests",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture
def second_workspace_id(client: TestClient) -> str:
    """Create a second workspace for filtering tests."""
    import tempfile
    temp_path = tempfile.mkdtemp()
    resp = client.post("/api/swarm-workspaces", json={
        "name": "CommTestWS2",
        "file_path": temp_path,
        "context": "Second workspace for communication filtering tests",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_communication(client: TestClient, workspace_id: str, **overrides) -> dict:
    """Helper to create a communication and return the response JSON."""
    payload = {
        "workspace_id": workspace_id,
        "title": overrides.pop("title", "Test Communication"),
        "recipient": overrides.pop("recipient", "alice@example.com"),
        **overrides,
    }
    resp = client.post(f"/api/workspaces/{workspace_id}/communications", json=payload)
    assert resp.status_code == 201
    return resp.json()


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------

class TestCreateCommunication:
    """Tests for POST /api/workspaces/{id}/communications. Validates: Requirement 23.8"""

    def test_create_success(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/communications", json={
            "workspace_id": workspace_id,
            "title": "Follow up on design review",
            "description": "Need feedback on the new API design",
            "recipient": "bob@example.com",
            "channel_type": "email",
            "priority": "high",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Follow up on design review"
        assert data["description"] == "Need feedback on the new API design"
        assert data["recipient"] == "bob@example.com"
        assert data["channel_type"] == "email"
        assert data["priority"] == "high"
        assert data["status"] == "pending_reply"
        assert data["workspace_id"] == workspace_id
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_minimal(self, client: TestClient, workspace_id: str):
        """Only workspace_id, title, and recipient are required."""
        resp = client.post(f"/api/workspaces/{workspace_id}/communications", json={
            "workspace_id": workspace_id,
            "title": "Quick sync",
            "recipient": "carol@example.com",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Quick sync"
        assert data["recipient"] == "carol@example.com"
        assert data["status"] == "pending_reply"
        assert data["channel_type"] == "other"
        assert data["priority"] == "none"

    def test_create_with_ai_draft(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/communications", json={
            "workspace_id": workspace_id,
            "title": "AI drafted email",
            "recipient": "dave@example.com",
            "status": "ai_draft",
            "ai_draft_content": "Dear Dave, I wanted to follow up on...",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "ai_draft"
        assert data["ai_draft_content"] == "Dear Dave, I wanted to follow up on..."

    def test_create_with_linked_task(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/communications", json={
            "workspace_id": workspace_id,
            "title": "Task update",
            "recipient": "eve@example.com",
            "source_task_id": "some-task-id",
        })
        assert resp.status_code == 201
        assert resp.json()["source_task_id"] == "some-task-id"

    def test_create_with_linked_todo(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/communications", json={
            "workspace_id": workspace_id,
            "title": "Signal follow-up",
            "recipient": "frank@example.com",
            "source_todo_id": "some-todo-id",
        })
        assert resp.status_code == 201
        assert resp.json()["source_todo_id"] == "some-todo-id"

    def test_create_uses_path_workspace_id(self, client: TestClient, workspace_id: str):
        """Path workspace_id should override body workspace_id."""
        resp = client.post(f"/api/workspaces/{workspace_id}/communications", json={
            "workspace_id": "different-id",
            "title": "Path wins",
            "recipient": "grace@example.com",
        })
        assert resp.status_code == 201
        assert resp.json()["workspace_id"] == workspace_id

    def test_create_sent_sets_sent_at(self, client: TestClient, workspace_id: str):
        """Creating with status=sent should auto-set sent_at."""
        resp = client.post(f"/api/workspaces/{workspace_id}/communications", json={
            "workspace_id": workspace_id,
            "title": "Already sent",
            "recipient": "heidi@example.com",
            "status": "sent",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "sent"
        assert data["sent_at"] is not None


# ---------------------------------------------------------------------------
# Update Tests
# ---------------------------------------------------------------------------

class TestUpdateCommunication:
    """Tests for PUT /api/workspaces/{id}/communications/{comm_id}. Validates: Requirement 23.8"""

    def test_update_title(self, client: TestClient, workspace_id: str):
        created = _create_communication(client, workspace_id, title="Old title")
        resp = client.put(
            f"/api/workspaces/{workspace_id}/communications/{created['id']}",
            json={"title": "New title"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New title"

    def test_update_status(self, client: TestClient, workspace_id: str):
        created = _create_communication(client, workspace_id)
        resp = client.put(
            f"/api/workspaces/{workspace_id}/communications/{created['id']}",
            json={"status": "follow_up"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "follow_up"

    def test_update_status_to_sent_sets_sent_at(self, client: TestClient, workspace_id: str):
        """Requirement 23.6: sent_at is set when status changes to sent."""
        created = _create_communication(client, workspace_id)
        assert created["sent_at"] is None
        resp = client.put(
            f"/api/workspaces/{workspace_id}/communications/{created['id']}",
            json={"status": "sent"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "sent"
        assert data["sent_at"] is not None

    def test_update_channel_type(self, client: TestClient, workspace_id: str):
        created = _create_communication(client, workspace_id, channel_type="other")
        resp = client.put(
            f"/api/workspaces/{workspace_id}/communications/{created['id']}",
            json={"channel_type": "slack"},
        )
        assert resp.status_code == 200
        assert resp.json()["channel_type"] == "slack"

    def test_update_priority(self, client: TestClient, workspace_id: str):
        created = _create_communication(client, workspace_id)
        resp = client.put(
            f"/api/workspaces/{workspace_id}/communications/{created['id']}",
            json={"priority": "high"},
        )
        assert resp.status_code == 200
        assert resp.json()["priority"] == "high"

    def test_update_not_found(self, client: TestClient, workspace_id: str):
        resp = client.put(
            f"/api/workspaces/{workspace_id}/communications/nonexistent-id",
            json={"title": "Nope"},
        )
        assert resp.status_code == 404

    def test_update_partial(self, client: TestClient, workspace_id: str):
        """Partial update should only change provided fields."""
        created = _create_communication(
            client, workspace_id,
            title="Keep me",
            recipient="alice@example.com",
            priority="high",
            channel_type="email",
        )
        resp = client.put(
            f"/api/workspaces/{workspace_id}/communications/{created['id']}",
            json={"description": "Added desc"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Keep me"
        assert data["recipient"] == "alice@example.com"
        assert data["priority"] == "high"
        assert data["channel_type"] == "email"
        assert data["description"] == "Added desc"

    def test_update_ai_draft_content(self, client: TestClient, workspace_id: str):
        created = _create_communication(client, workspace_id)
        resp = client.put(
            f"/api/workspaces/{workspace_id}/communications/{created['id']}",
            json={"ai_draft_content": "Here is the AI-generated draft..."},
        )
        assert resp.status_code == 200
        assert resp.json()["ai_draft_content"] == "Here is the AI-generated draft..."


# ---------------------------------------------------------------------------
# Delete Tests
# ---------------------------------------------------------------------------

class TestDeleteCommunication:
    """Tests for DELETE /api/workspaces/{id}/communications/{comm_id}. Validates: Requirement 23.8"""

    def test_delete_success(self, client: TestClient, workspace_id: str):
        created = _create_communication(client, workspace_id)
        resp = client.delete(
            f"/api/workspaces/{workspace_id}/communications/{created['id']}"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        assert resp.json()["communication_id"] == created["id"]

    def test_delete_removes_item(self, client: TestClient, workspace_id: str):
        """Deleted item should no longer appear in list."""
        created = _create_communication(client, workspace_id, title="Gone soon")
        client.delete(f"/api/workspaces/{workspace_id}/communications/{created['id']}")
        resp = client.get(f"/api/workspaces/{workspace_id}/communications")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()]
        assert created["id"] not in ids

    def test_delete_not_found(self, client: TestClient, workspace_id: str):
        resp = client.delete(
            f"/api/workspaces/{workspace_id}/communications/nonexistent-id"
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# List, Pagination, and Filtering
# ---------------------------------------------------------------------------

class TestListCommunications:
    """Tests for GET /api/workspaces/{id}/communications. Validates: Requirement 23.8"""

    def test_list_empty(self, client: TestClient, workspace_id: str):
        resp = client.get(f"/api/workspaces/{workspace_id}/communications")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_returns_created_items(self, client: TestClient, workspace_id: str):
        _create_communication(client, workspace_id, title="First")
        _create_communication(client, workspace_id, title="Second")
        resp = client.get(f"/api/workspaces/{workspace_id}/communications")
        assert resp.status_code == 200
        titles = [item["title"] for item in resp.json()]
        assert "First" in titles
        assert "Second" in titles

    def test_list_scoped_to_workspace(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        _create_communication(client, workspace_id, title="WS1 comm")
        _create_communication(client, second_workspace_id, title="WS2 comm")

        resp = client.get(f"/api/workspaces/{workspace_id}/communications")
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["workspace_id"] == workspace_id for item in data)
        assert any(item["title"] == "WS1 comm" for item in data)
        assert not any(item["title"] == "WS2 comm" for item in data)

    def test_list_filter_by_status(self, client: TestClient, workspace_id: str):
        _create_communication(client, workspace_id, title="Pending one")
        item2 = _create_communication(client, workspace_id, title="Follow up one")
        client.put(
            f"/api/workspaces/{workspace_id}/communications/{item2['id']}",
            json={"status": "follow_up"},
        )

        resp = client.get(
            f"/api/workspaces/{workspace_id}/communications?status=pending_reply"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["status"] == "pending_reply" for item in data)

    def test_list_filter_by_channel_type(self, client: TestClient, workspace_id: str):
        _create_communication(
            client, workspace_id, title="Email comm", channel_type="email"
        )
        _create_communication(
            client, workspace_id, title="Slack comm", channel_type="slack"
        )

        resp = client.get(
            f"/api/workspaces/{workspace_id}/communications?channel_type=email"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert all(item["channel_type"] == "email" for item in data)
        assert any(item["title"] == "Email comm" for item in data)
        assert not any(item["title"] == "Slack comm" for item in data)

    def test_list_pagination_limit(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_communication(client, workspace_id, title=f"Comm {i}")

        resp = client.get(f"/api/workspaces/{workspace_id}/communications?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_list_pagination_offset(self, client: TestClient, workspace_id: str):
        for i in range(5):
            _create_communication(client, workspace_id, title=f"Comm {i}")

        all_resp = client.get(
            f"/api/workspaces/{workspace_id}/communications?limit=100"
        )
        total = len(all_resp.json())

        offset_resp = client.get(
            f"/api/workspaces/{workspace_id}/communications?offset=2&limit=100"
        )
        assert offset_resp.status_code == 200
        assert len(offset_resp.json()) == total - 2


# ---------------------------------------------------------------------------
# Response format (snake_case)
# ---------------------------------------------------------------------------

class TestResponseFormat:
    """Validates snake_case field names in responses."""

    def test_response_uses_snake_case(self, client: TestClient, workspace_id: str):
        _create_communication(client, workspace_id, channel_type="email")
        resp = client.get(f"/api/workspaces/{workspace_id}/communications")
        data = resp.json()[0]
        # Verify snake_case keys
        assert "workspace_id" in data
        assert "channel_type" in data
        assert "ai_draft_content" in data
        assert "source_task_id" in data
        assert "source_todo_id" in data
        assert "due_date" in data
        assert "sent_at" in data
        assert "created_at" in data
        assert "updated_at" in data
        # Verify no camelCase keys
        assert "workspaceId" not in data
        assert "channelType" not in data
        assert "aiDraftContent" not in data
