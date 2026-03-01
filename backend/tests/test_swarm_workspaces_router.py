"""Tests for Swarm Workspaces API endpoints.

Tests for Task 4.1: GET endpoints
Tests for Task 4.2: POST endpoint
"""
import os
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def temp_workspace_dir():
    """Create a temporary directory for workspace testing."""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    # Cleanup after test
    if os.path.exists(temp_path):
        shutil.rmtree(temp_path)


@pytest.fixture
def sample_workspace_data(temp_workspace_dir):
    """Sample workspace data for tests."""
    return {
        "name": "Test Workspace",
        "file_path": temp_workspace_dir,
        "context": "Test workspace context for unit tests.",
        "icon": "🧪",
    }


class TestListWorkspaces:
    """Tests for GET /api/swarm-workspaces endpoint."""

    def test_list_workspaces_success(self, client: TestClient):
        """Test listing workspaces returns 200 and list."""
        response = client.get("/api/swarm-workspaces")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestGetWorkspace:
    """Tests for GET /api/swarm-workspaces/{id} endpoint."""

    def test_get_workspace_success(self, client: TestClient, sample_workspace_data: dict):
        """Test getting an existing workspace returns 200."""
        # First create a workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Then get it
        response = client.get(f"/api/swarm-workspaces/{workspace_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == workspace_id
        assert data["name"] == sample_workspace_data["name"]

    def test_get_workspace_not_found(self, client: TestClient):
        """Test getting non-existent workspace returns 404."""
        response = client.get("/api/swarm-workspaces/nonexistent-id-12345")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "SWARM_WORKSPACE_NOT_FOUND"


class TestCreateWorkspace:
    """Tests for POST /api/swarm-workspaces endpoint.

    Validates: Requirements 4.4, 6.4
    """

    def test_create_workspace_success(self, client: TestClient, sample_workspace_data: dict):
        """Test creating workspace returns 201."""
        response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["name"] == sample_workspace_data["name"]
        assert data["file_path"] == sample_workspace_data["file_path"]
        assert data["context"] == sample_workspace_data["context"]
        assert data["icon"] == sample_workspace_data["icon"]
        assert data["is_default"] is False
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_workspace_generates_uuid(self, client: TestClient, sample_workspace_data: dict):
        """Test that created workspace has a valid UUID."""
        response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert response.status_code == 201
        data = response.json()
        # UUID should be a string with dashes
        assert isinstance(data["id"], str)
        assert len(data["id"]) == 36  # UUID format: 8-4-4-4-12

    def test_create_workspace_creates_folder_structure(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that folder structure is created on filesystem.

        Validates: Requirements 2.1, 2.4
        """
        response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert response.status_code == 201

        # Verify folder structure was created
        workspace_path = sample_workspace_data["file_path"]
        expected_folders = [
            "Context",
            "Docs",
            "Projects",
            "Tasks",
            "ToDos",
            "Plans",
            "Historical-Chats",
            "Reports",
        ]
        for folder in expected_folders:
            folder_path = os.path.join(workspace_path, folder)
            assert os.path.isdir(folder_path), f"Folder {folder} should exist"

    def test_create_workspace_creates_context_files(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that context files are created.

        Validates: Requirements 2.2, 2.3, 7.1, 7.2, 7.3
        """
        response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert response.status_code == 201

        workspace_path = sample_workspace_data["file_path"]
        overall_path = os.path.join(workspace_path, "Context", "overall-context.md")
        compressed_path = os.path.join(workspace_path, "Context", "compressed-context.md")

        # Verify files exist
        assert os.path.isfile(overall_path)
        assert os.path.isfile(compressed_path)

        # Verify overall-context.md contains workspace name
        with open(overall_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert sample_workspace_data["name"] in content

        # Verify compressed-context.md is empty
        with open(compressed_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert content == ""

    def test_create_workspace_minimal(self, client: TestClient, temp_workspace_dir: str):
        """Test creating workspace with minimal data (no icon)."""
        minimal_data = {
            "name": "Minimal Workspace",
            "file_path": temp_workspace_dir,
            "context": "Minimal context",
        }
        response = client.post("/api/swarm-workspaces", json=minimal_data)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Minimal Workspace"
        assert data["icon"] is None

    def test_create_workspace_missing_name(self, client: TestClient, temp_workspace_dir: str):
        """Test creating workspace without name returns validation error."""
        invalid_data = {
            "file_path": temp_workspace_dir,
            "context": "Some context",
        }
        response = client.post("/api/swarm-workspaces", json=invalid_data)
        assert response.status_code in (400, 422)  # Validation error

    def test_create_workspace_missing_file_path(self, client: TestClient):
        """Test creating workspace without file_path returns validation error."""
        invalid_data = {
            "name": "Test Workspace",
            "context": "Some context",
        }
        response = client.post("/api/swarm-workspaces", json=invalid_data)
        assert response.status_code in (400, 422)  # Validation error

    def test_create_workspace_missing_context(self, client: TestClient, temp_workspace_dir: str):
        """Test creating workspace without context returns validation error."""
        invalid_data = {
            "name": "Test Workspace",
            "file_path": temp_workspace_dir,
        }
        response = client.post("/api/swarm-workspaces", json=invalid_data)
        assert response.status_code in (400, 422)  # Validation error

    def test_create_workspace_path_traversal_rejected(self, client: TestClient):
        """Test that path traversal is rejected.

        Validates: Requirement 8.1
        """
        invalid_data = {
            "name": "Test Workspace",
            "file_path": "/tmp/../etc/passwd",
            "context": "Some context",
        }
        response = client.post("/api/swarm-workspaces", json=invalid_data)
        assert response.status_code in (400, 422)  # Validation error

    def test_create_workspace_relative_path_rejected(self, client: TestClient):
        """Test that relative paths are rejected.

        Validates: Requirement 8.5
        """
        invalid_data = {
            "name": "Test Workspace",
            "file_path": "relative/path/workspace",
            "context": "Some context",
        }
        response = client.post("/api/swarm-workspaces", json=invalid_data)
        assert response.status_code in (400, 422)  # Validation error

    def test_create_workspace_name_too_long(self, client: TestClient, temp_workspace_dir: str):
        """Test that name exceeding 100 characters is rejected.

        Validates: Requirement 8.4
        """
        invalid_data = {
            "name": "A" * 101,  # 101 characters
            "file_path": temp_workspace_dir,
            "context": "Some context",
        }
        response = client.post("/api/swarm-workspaces", json=invalid_data)
        assert response.status_code in (400, 422)  # Validation error

    def test_create_workspace_stored_in_database(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that created workspace is stored in database and retrievable."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Verify it appears in list
        list_response = client.get("/api/swarm-workspaces")
        assert list_response.status_code == 200
        workspaces = list_response.json()
        workspace_ids = [w["id"] for w in workspaces]
        assert workspace_id in workspace_ids

    def test_create_workspace_is_not_default(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that created workspace has is_default=False."""
        response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert response.status_code == 201
        data = response.json()
        assert data["is_default"] is False

    def test_create_workspace_tilde_path(self, client: TestClient):
        """Test creating workspace with ~ path."""
        unique_name = f"swarm_test_{os.getpid()}"
        workspace_path = f"~/tmp_swarm_router_test/{unique_name}"
        expanded_path = os.path.expanduser(workspace_path)

        try:
            workspace_data = {
                "name": "Tilde Path Workspace",
                "file_path": workspace_path,
                "context": "Testing tilde path expansion",
            }
            response = client.post("/api/swarm-workspaces", json=workspace_data)
            assert response.status_code == 201

            # Verify folder structure was created at expanded path
            assert os.path.isdir(expanded_path)
            assert os.path.isdir(os.path.join(expanded_path, "Context"))
        finally:
            # Cleanup
            parent_dir = os.path.expanduser("~/tmp_swarm_router_test")
            if os.path.exists(parent_dir):
                shutil.rmtree(parent_dir)


class TestUpdateWorkspace:
    """Tests for PUT /api/swarm-workspaces/{id} endpoint.

    Validates: Requirements 3.5, 6.6
    """

    def test_update_workspace_success(self, client: TestClient, sample_workspace_data: dict):
        """Test updating workspace returns 200 with updated data."""
        # First create a workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]
        original_updated_at = create_response.json()["updated_at"]

        # Update the workspace
        update_data = {
            "name": "Updated Workspace Name",
            "context": "Updated context for the workspace.",
        }
        response = client.put(f"/api/swarm-workspaces/{workspace_id}", json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == workspace_id
        assert data["name"] == "Updated Workspace Name"
        assert data["context"] == "Updated context for the workspace."
        # Original fields should be preserved
        assert data["file_path"] == sample_workspace_data["file_path"]
        assert data["icon"] == sample_workspace_data["icon"]

    def test_update_workspace_updates_timestamp(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that updating workspace updates the updated_at timestamp.

        Validates: Requirement 3.5
        """
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]
        original_updated_at = create_response.json()["updated_at"]

        # Update the workspace
        update_data = {"name": "New Name"}
        response = client.put(f"/api/swarm-workspaces/{workspace_id}", json=update_data)
        assert response.status_code == 200
        data = response.json()
        # updated_at should be different (newer)
        assert data["updated_at"] >= original_updated_at

    def test_update_workspace_not_found(self, client: TestClient):
        """Test updating non-existent workspace returns 404."""
        update_data = {"name": "New Name"}
        response = client.put("/api/swarm-workspaces/nonexistent-id-12345", json=update_data)
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "SWARM_WORKSPACE_NOT_FOUND"

    def test_update_workspace_partial_update(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that only provided fields are updated."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Update only the name
        update_data = {"name": "Only Name Updated"}
        response = client.put(f"/api/swarm-workspaces/{workspace_id}", json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Only Name Updated"
        # Other fields should remain unchanged
        assert data["context"] == sample_workspace_data["context"]
        assert data["file_path"] == sample_workspace_data["file_path"]
        assert data["icon"] == sample_workspace_data["icon"]

    def test_update_workspace_empty_body(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test updating workspace with empty body still updates timestamp."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]
        original_data = create_response.json()

        # Update with empty body
        response = client.put(f"/api/swarm-workspaces/{workspace_id}", json={})
        assert response.status_code == 200
        data = response.json()
        # All fields should remain the same except updated_at
        assert data["name"] == original_data["name"]
        assert data["context"] == original_data["context"]
        assert data["file_path"] == original_data["file_path"]
        assert data["icon"] == original_data["icon"]
        assert data["updated_at"] >= original_data["updated_at"]

    def test_update_workspace_update_icon(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test updating workspace icon."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Update the icon
        update_data = {"icon": "🚀"}
        response = client.put(f"/api/swarm-workspaces/{workspace_id}", json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["icon"] == "🚀"

    def test_update_workspace_name_too_long(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that name exceeding 100 characters is rejected.

        Validates: Requirement 8.4
        """
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Try to update with name too long
        update_data = {"name": "A" * 101}
        response = client.put(f"/api/swarm-workspaces/{workspace_id}", json=update_data)
        assert response.status_code in (400, 422)  # Validation error

    def test_update_workspace_path_traversal_rejected(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that path traversal is rejected in update.

        Validates: Requirement 8.1
        """
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Try to update with path traversal
        update_data = {"file_path": "/tmp/../etc/passwd"}
        response = client.put(f"/api/swarm-workspaces/{workspace_id}", json=update_data)
        assert response.status_code in (400, 422)  # Validation error

    def test_update_workspace_relative_path_rejected(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that relative paths are rejected in update.

        Validates: Requirement 8.5
        """
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Try to update with relative path
        update_data = {"file_path": "relative/path/workspace"}
        response = client.put(f"/api/swarm-workspaces/{workspace_id}", json=update_data)
        assert response.status_code in (400, 422)  # Validation error

    def test_update_workspace_preserves_is_default(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that updating workspace preserves is_default flag."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Update the workspace
        update_data = {"name": "Updated Name"}
        response = client.put(f"/api/swarm-workspaces/{workspace_id}", json=update_data)
        assert response.status_code == 200
        data = response.json()
        assert data["is_default"] is False

    def test_update_workspace_persisted(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that updated workspace is persisted and retrievable."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Update the workspace
        update_data = {"name": "Persisted Update", "context": "New context"}
        client.put(f"/api/swarm-workspaces/{workspace_id}", json=update_data)

        # Retrieve and verify
        get_response = client.get(f"/api/swarm-workspaces/{workspace_id}")
        assert get_response.status_code == 200
        data = get_response.json()
        assert data["name"] == "Persisted Update"
        assert data["context"] == "New context"



class TestDeleteWorkspace:
    """Tests for DELETE /api/swarm-workspaces/{id} endpoint.

    Validates: Requirements 1.3, 6.7, 6.8
    """

    def test_delete_workspace_success(self, client: TestClient, sample_workspace_data: dict):
        """Test deleting a custom workspace returns 204.

        Validates: Requirement 6.7
        """
        # First create a workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Delete the workspace
        response = client.delete(f"/api/swarm-workspaces/{workspace_id}")
        assert response.status_code == 204

    def test_delete_workspace_not_found(self, client: TestClient):
        """Test deleting non-existent workspace returns 404."""
        response = client.delete("/api/swarm-workspaces/nonexistent-id-12345")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "SWARM_WORKSPACE_NOT_FOUND"

    def test_delete_workspace_removes_from_database(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that deleted workspace is removed from database."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Verify it exists
        get_response = client.get(f"/api/swarm-workspaces/{workspace_id}")
        assert get_response.status_code == 200

        # Delete the workspace
        delete_response = client.delete(f"/api/swarm-workspaces/{workspace_id}")
        assert delete_response.status_code == 204

        # Verify it no longer exists
        get_response = client.get(f"/api/swarm-workspaces/{workspace_id}")
        assert get_response.status_code == 404

    def test_delete_workspace_not_in_list(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that deleted workspace no longer appears in list."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Verify it appears in list
        list_response = client.get("/api/swarm-workspaces")
        workspace_ids = [w["id"] for w in list_response.json()]
        assert workspace_id in workspace_ids

        # Delete the workspace
        delete_response = client.delete(f"/api/swarm-workspaces/{workspace_id}")
        assert delete_response.status_code == 204

        # Verify it no longer appears in list
        list_response = client.get("/api/swarm-workspaces")
        workspace_ids = [w["id"] for w in list_response.json()]
        assert workspace_id not in workspace_ids

    def test_delete_default_workspace_forbidden(self, client: TestClient):
        """Test that deleting the default workspace returns 403.

        Validates: Requirements 1.3, 6.8
        """
        # Get the default workspace
        default_response = client.get("/api/swarm-workspaces/default")
        if default_response.status_code == 404:
            # Default workspace doesn't exist, skip this test
            pytest.skip("Default workspace not configured")

        default_workspace_id = default_response.json()["id"]

        # Try to delete the default workspace
        response = client.delete(f"/api/swarm-workspaces/{default_workspace_id}")
        assert response.status_code == 403
        data = response.json()
        assert data["code"] == "FORBIDDEN"
        assert "Cannot delete default workspace" in data["message"]

    def test_delete_default_workspace_still_exists(self, client: TestClient):
        """Test that default workspace still exists after failed delete attempt.

        Validates: Requirements 1.3, 6.8
        """
        # Get the default workspace
        default_response = client.get("/api/swarm-workspaces/default")
        if default_response.status_code == 404:
            # Default workspace doesn't exist, skip this test
            pytest.skip("Default workspace not configured")

        default_workspace_id = default_response.json()["id"]

        # Try to delete the default workspace (should fail)
        client.delete(f"/api/swarm-workspaces/{default_workspace_id}")

        # Verify default workspace still exists
        get_response = client.get(f"/api/swarm-workspaces/{default_workspace_id}")
        assert get_response.status_code == 200
        assert get_response.json()["is_default"] is True

    def test_delete_workspace_no_content_body(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that successful delete returns no content body."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Delete the workspace
        response = client.delete(f"/api/swarm-workspaces/{workspace_id}")
        assert response.status_code == 204
        # 204 responses should have no content
        assert response.content == b""


class TestInitWorkspaceFolders:
    """Tests for POST /api/swarm-workspaces/{id}/init-folders endpoint.

    Validates: Requirement 6.10
    """

    def test_init_folders_success(self, client: TestClient, sample_workspace_data: dict):
        """Test initializing folders for existing workspace returns 200."""
        # First create a workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Initialize folders
        response = client.post(f"/api/swarm-workspaces/{workspace_id}/init-folders")
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "Folder structure initialized" in data["message"]

    def test_init_folders_not_found(self, client: TestClient):
        """Test initializing folders for non-existent workspace returns 404."""
        response = client.post("/api/swarm-workspaces/nonexistent-id-12345/init-folders")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "SWARM_WORKSPACE_NOT_FOUND"

    def test_init_folders_creates_structure(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that init-folders creates the folder structure on filesystem.

        Validates: Requirements 2.1, 6.10
        """
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]
        workspace_path = sample_workspace_data["file_path"]

        # Delete some folders to simulate missing structure
        import shutil
        docs_path = os.path.join(workspace_path, "Docs")
        tasks_path = os.path.join(workspace_path, "Tasks")
        if os.path.exists(docs_path):
            shutil.rmtree(docs_path)
        if os.path.exists(tasks_path):
            shutil.rmtree(tasks_path)

        # Verify folders are missing
        assert not os.path.exists(docs_path)
        assert not os.path.exists(tasks_path)

        # Initialize folders
        response = client.post(f"/api/swarm-workspaces/{workspace_id}/init-folders")
        assert response.status_code == 200

        # Verify all folders now exist
        expected_folders = [
            "Context",
            "Docs",
            "Projects",
            "Tasks",
            "ToDos",
            "Plans",
            "Historical-Chats",
            "Reports",
        ]
        for folder in expected_folders:
            folder_path = os.path.join(workspace_path, folder)
            assert os.path.isdir(folder_path), f"Folder {folder} should exist"

    def test_init_folders_idempotent(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that init-folders can be called multiple times without error."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Initialize folders multiple times
        response1 = client.post(f"/api/swarm-workspaces/{workspace_id}/init-folders")
        assert response1.status_code == 200

        response2 = client.post(f"/api/swarm-workspaces/{workspace_id}/init-folders")
        assert response2.status_code == 200

        response3 = client.post(f"/api/swarm-workspaces/{workspace_id}/init-folders")
        assert response3.status_code == 200

    def test_init_folders_returns_workspace_name_in_message(
        self, client: TestClient, sample_workspace_data: dict
    ):
        """Test that success message includes workspace name."""
        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=sample_workspace_data)
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]
        workspace_name = sample_workspace_data["name"]

        # Initialize folders
        response = client.post(f"/api/swarm-workspaces/{workspace_id}/init-folders")
        assert response.status_code == 200
        data = response.json()
        assert workspace_name in data["message"]
