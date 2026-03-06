"""Unit tests for system-managed folder protection in workspace API.

Tests that the ``DELETE /workspace/folders`` and ``PUT /workspace/rename``
endpoints reject operations on paths listed in ``SYSTEM_MANAGED_FOLDERS``
with HTTP 403 (Requirement 12.9).

Testing methodology: unit tests using FastAPI TestClient with mocked
workspace path to avoid touching real filesystem.
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from core.swarm_workspace_manager import SYSTEM_MANAGED_FOLDERS


class TestSystemManagedFolderProtection:
    """Verify delete and rename are rejected for system-managed folders."""

    def test_delete_system_managed_folder_returns_403(self, client: TestClient):
        """Deleting a system-managed folder returns 403 Forbidden."""
        for folder in sorted(SYSTEM_MANAGED_FOLDERS):
            response = client.request(
                "DELETE",
                "/api/workspace/folders",
                json={"path": folder},
            )
            assert response.status_code == 403, (
                f"Expected 403 for deleting '{folder}', got {response.status_code}"
            )
            assert "Cannot delete/rename system-managed directory" in response.json()["detail"]

    def test_rename_system_managed_folder_returns_403(self, client: TestClient):
        """Renaming a system-managed folder returns 403 Forbidden."""
        for folder in sorted(SYSTEM_MANAGED_FOLDERS):
            response = client.put(
                "/api/workspace/rename",
                json={"old_path": folder, "new_path": f"{folder}_renamed"},
            )
            assert response.status_code == 403, (
                f"Expected 403 for renaming '{folder}', got {response.status_code}"
            )
            assert "Cannot delete/rename system-managed directory" in response.json()["detail"]

    def test_delete_error_message_includes_path(self, client: TestClient):
        """Error detail includes the normalized path that was rejected."""
        response = client.request(
            "DELETE",
            "/api/workspace/folders",
            json={"path": "Knowledge/Notes"},
        )
        assert response.status_code == 403
        assert "Knowledge/Notes" in response.json()["detail"]

    def test_rename_error_message_includes_path(self, client: TestClient):
        """Error detail includes the normalized path that was rejected."""
        response = client.put(
            "/api/workspace/rename",
            json={"old_path": "Knowledge/Notes", "new_path": "Knowledge/MyNotes"},
        )
        assert response.status_code == 403
        assert "Knowledge/Notes" in response.json()["detail"]

    def test_delete_backslash_path_normalized(self, client: TestClient):
        """Backslash paths are normalized before checking protection."""
        response = client.request(
            "DELETE",
            "/api/workspace/folders",
            json={"path": "Knowledge\\Notes"},
        )
        assert response.status_code == 403

    def test_rename_backslash_path_normalized(self, client: TestClient):
        """Backslash paths are normalized before checking protection."""
        response = client.put(
            "/api/workspace/rename",
            json={"old_path": "Knowledge\\Library", "new_path": "Knowledge\\Lib"},
        )
        assert response.status_code == 403

    def test_delete_trailing_slash_stripped(self, client: TestClient):
        """Trailing slashes are stripped before checking protection."""
        response = client.request(
            "DELETE",
            "/api/workspace/folders",
            json={"path": "Knowledge/Notes/"},
        )
        assert response.status_code == 403

    def test_delete_non_managed_folder_not_blocked(self, client: TestClient):
        """Deleting a non-managed path is NOT blocked by the protection check.

        It may still fail for other reasons (404 etc.), but not 403.
        """
        response = client.request(
            "DELETE",
            "/api/workspace/folders",
            json={"path": "SomeUserFolder/stuff"},
        )
        # Should NOT be 403 — the protection check should not fire
        assert response.status_code != 403

    def test_rename_non_managed_folder_not_blocked(self, client: TestClient):
        """Renaming a non-managed path is NOT blocked by the protection check."""
        response = client.put(
            "/api/workspace/rename",
            json={"old_path": "SomeUserFolder", "new_path": "RenamedFolder"},
        )
        assert response.status_code != 403
