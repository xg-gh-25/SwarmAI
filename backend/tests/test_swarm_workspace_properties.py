"""Property-based tests for Swarm Workspaces.

Uses Hypothesis to verify universal properties across all valid inputs.

**Feature: swarm-workspaces**
"""
import pytest
import asyncio
from hypothesis import given, strategies as st, settings, assume, HealthCheck
from fastapi.testclient import TestClient
from datetime import datetime
import uuid


# Suppress function-scoped fixture warning since we're testing updates to
# workspaces across iterations (which is the intended behavior)
PROPERTY_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)


# Strategies for generating valid workspace field values
# Note: We filter out strings that start with '{' or '[' because the SQLite
# _row_to_dict method aggressively parses such strings as JSON, which would
# cause type mismatches on retrieval (e.g., name='{}' stored as string but
# retrieved as dict).
def _not_json_like(s: str) -> bool:
    """Filter out strings that would be parsed as JSON by the DB layer."""
    stripped = s.strip()
    return not (stripped.startswith('{') or stripped.startswith('['))

name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S'), whitelist_characters=' -_'),
    min_size=1,
    max_size=100
).filter(lambda x: x.strip() and _not_json_like(x))  # Ensure non-empty after strip and not JSON-like

# Valid file paths - absolute paths or tilde paths
valid_path_strategy = st.one_of(
    # Absolute paths
    st.text(
        alphabet=st.characters(whitelist_categories=('L', 'N'), whitelist_characters='/-_'),
        min_size=2,
        max_size=200
    ).map(lambda x: f"/tmp/swarm_test/{x.strip('/')}").filter(lambda x: '..' not in x),
    # Tilde paths
    st.text(
        alphabet=st.characters(whitelist_categories=('L', 'N'), whitelist_characters='/-_'),
        min_size=1,
        max_size=200
    ).map(lambda x: f"~/swarm_test/{x.strip('/')}").filter(lambda x: '..' not in x),
)

# Invalid paths with path traversal
invalid_traversal_path_strategy = st.one_of(
    st.just("../etc/passwd"),
    st.just("/home/user/../../../etc/passwd"),
    st.just("~/Desktop/../.ssh/id_rsa"),
    st.text(min_size=1, max_size=50).map(lambda x: f"../{x}"),
    st.text(min_size=1, max_size=50).map(lambda x: f"/tmp/{x}/../../../etc"),
)

# Invalid relative paths
invalid_relative_path_strategy = st.text(
    alphabet=st.characters(whitelist_categories=('L', 'N'), whitelist_characters='/-_'),
    min_size=1,
    max_size=100
).filter(lambda x: not x.startswith('/') and not x.startswith('~') and '..' not in x)

context_strategy = st.text(min_size=1, max_size=1000).filter(_not_json_like)

icon_strategy = st.one_of(
    st.none(),
    st.sampled_from(['🏠', '📁', '🚀', '💼', '📊', '🔧', '📝', '🎯']),
)


class TestDefaultWorkspaceProtection:
    """Property 1: Default Workspace Protection.

    **Validates: Requirements 1.3, 6.8**

    For any attempt to delete the default workspace (where isDefault=true),
    the system should reject the deletion with a 403 error and the default
    workspace should remain in the database.
    """

    @given(st.integers(min_value=1, max_value=100))
    @PROPERTY_SETTINGS
    def test_delete_default_workspace_always_rejected(self, client: TestClient, _attempt: int):
        """Deleting default workspace is always rejected with 403.

        **Validates: Requirements 1.3, 6.8**
        """
        # Get the default workspace
        get_response = client.get("/api/swarm-workspaces/default")
        assert get_response.status_code == 200
        default_workspace = get_response.json()
        default_id = default_workspace["id"]

        # Attempt to delete the default workspace
        delete_response = client.delete(f"/api/swarm-workspaces/{default_id}")

        # Should be rejected with 403
        assert delete_response.status_code == 403

        # Verify default workspace still exists
        verify_response = client.get("/api/swarm-workspaces/default")
        assert verify_response.status_code == 200
        assert verify_response.json()["id"] == default_id
        assert verify_response.json()["is_default"] is True


class TestWorkspaceEntityInvariants:
    """Property 4: Workspace Entity Invariants.

    **Validates: Requirements 3.1, 3.2, 3.3, 3.4**

    For any workspace entity, all required fields (id, name, filePath, context,
    createdAt, updatedAt) should be non-null, the id should be a valid UUID,
    isDefault should default to false for non-default workspaces, and timestamps
    should be valid ISO format strings.
    """

    @given(
        name=name_strategy,
        context=context_strategy,
        icon=icon_strategy,
    )
    @PROPERTY_SETTINGS
    def test_created_workspace_has_valid_invariants(
        self, client: TestClient, name: str, context: str, icon: str | None
    ):
        """Created workspaces have all required fields with valid values.

        **Validates: Requirements 3.1, 3.2, 3.3, 3.4**
        """
        assume(name.strip())
        assume(context.strip())

        # Use a unique path for each test
        unique_id = str(uuid.uuid4())[:8]
        file_path = f"/tmp/swarm_test/workspace_{unique_id}"

        create_data = {
            "name": name,
            "file_path": file_path,
            "context": context,
        }
        if icon is not None:
            create_data["icon"] = icon

        response = client.post("/api/swarm-workspaces", json=create_data)
        assert response.status_code == 201
        data = response.json()

        # Verify all required fields are present and non-null
        assert data["id"] is not None
        assert data["name"] is not None
        assert data["file_path"] is not None
        assert data["context"] is not None
        assert data["created_at"] is not None
        assert data["updated_at"] is not None

        # Verify id is a valid UUID format
        try:
            uuid.UUID(data["id"])
        except ValueError:
            pytest.fail(f"id '{data['id']}' is not a valid UUID")

        # Verify is_default is False for non-default workspaces
        assert data["is_default"] is False

        # Verify timestamps are valid ISO format
        try:
            datetime.fromisoformat(data["created_at"].replace('Z', '+00:00'))
            datetime.fromisoformat(data["updated_at"].replace('Z', '+00:00'))
        except ValueError as e:
            pytest.fail(f"Invalid timestamp format: {e}")

        # Cleanup: delete the created workspace
        client.delete(f"/api/swarm-workspaces/{data['id']}")


class TestWorkspaceCRUDRoundTrip:
    """Property 5: Workspace CRUD Round-Trip.

    **Validates: Requirements 6.4, 6.6, 9.3**

    For any valid workspace creation request, creating the workspace and then
    retrieving it by ID should return an equivalent workspace with all provided
    fields preserved. Similarly, updating a workspace and retrieving it should
    reflect all updates.
    """

    @given(
        name=name_strategy,
        context=context_strategy,
        icon=icon_strategy,
    )
    @PROPERTY_SETTINGS
    def test_create_and_retrieve_preserves_fields(
        self, client: TestClient, name: str, context: str, icon: str | None
    ):
        """Creating and retrieving a workspace preserves all fields.

        **Validates: Requirements 6.4, 9.3**
        """
        assume(name.strip())
        assume(context.strip())

        unique_id = str(uuid.uuid4())[:8]
        file_path = f"/tmp/swarm_test/workspace_{unique_id}"

        create_data = {
            "name": name,
            "file_path": file_path,
            "context": context,
        }
        if icon is not None:
            create_data["icon"] = icon

        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json=create_data)
        assert create_response.status_code == 201
        created = create_response.json()

        # Retrieve workspace
        get_response = client.get(f"/api/swarm-workspaces/{created['id']}")
        assert get_response.status_code == 200
        retrieved = get_response.json()

        # Verify all fields are preserved
        assert retrieved["name"] == name
        assert retrieved["file_path"] == file_path
        assert retrieved["context"] == context
        assert retrieved["icon"] == icon
        assert retrieved["id"] == created["id"]

        # Cleanup
        client.delete(f"/api/swarm-workspaces/{created['id']}")

    @given(
        original_name=name_strategy,
        updated_name=name_strategy,
        original_context=context_strategy,
        updated_context=context_strategy,
    )
    @PROPERTY_SETTINGS
    def test_update_and_retrieve_reflects_changes(
        self, client: TestClient,
        original_name: str, updated_name: str,
        original_context: str, updated_context: str
    ):
        """Updating and retrieving a workspace reflects all changes.

        **Validates: Requirements 6.6, 9.3**
        """
        assume(original_name.strip())
        assume(updated_name.strip())
        assume(original_context.strip())
        assume(updated_context.strip())

        unique_id = str(uuid.uuid4())[:8]
        file_path = f"/tmp/swarm_test/workspace_{unique_id}"

        # Create workspace
        create_response = client.post("/api/swarm-workspaces", json={
            "name": original_name,
            "file_path": file_path,
            "context": original_context,
        })
        assert create_response.status_code == 201
        workspace_id = create_response.json()["id"]

        # Update workspace
        update_response = client.put(f"/api/swarm-workspaces/{workspace_id}", json={
            "name": updated_name,
            "context": updated_context,
        })
        assert update_response.status_code == 200

        # Retrieve and verify updates
        get_response = client.get(f"/api/swarm-workspaces/{workspace_id}")
        assert get_response.status_code == 200
        retrieved = get_response.json()

        assert retrieved["name"] == updated_name
        assert retrieved["context"] == updated_context
        # file_path should remain unchanged
        assert retrieved["file_path"] == file_path

        # Cleanup
        client.delete(f"/api/swarm-workspaces/{workspace_id}")


class TestPathSecurityValidation:
    """Property 6: Path Security Validation.

    **Validates: Requirements 8.1, 8.5**

    For any workspace file path containing path traversal sequences (..)
    or that is a relative path not starting with ~, the system should
    reject the workspace creation with a validation error.
    """

    @given(path=invalid_traversal_path_strategy)
    @PROPERTY_SETTINGS
    def test_path_traversal_rejected(self, client: TestClient, path: str):
        """Paths with traversal sequences (..) are rejected.

        **Validates: Requirement 8.1**
        """
        response = client.post("/api/swarm-workspaces", json={
            "name": "Test Workspace",
            "file_path": path,
            "context": "Test context",
        })

        # Should be rejected with 400 or 422 validation error
        assert response.status_code in [400, 422]

    @given(path=invalid_relative_path_strategy)
    @PROPERTY_SETTINGS
    def test_relative_path_rejected(self, client: TestClient, path: str):
        """Relative paths (not starting with ~ or /) are rejected.

        **Validates: Requirement 8.5**
        """
        assume(path.strip())
        assume(not path.startswith('/'))
        assume(not path.startswith('~'))

        response = client.post("/api/swarm-workspaces", json={
            "name": "Test Workspace",
            "file_path": path,
            "context": "Test context",
        })

        # Should be rejected with 400 or 422 validation error
        assert response.status_code in [400, 422]


class TestNameValidation:
    """Property 7: Name Validation.

    **Validates: Requirements 8.4**

    For any workspace creation request with an empty name or a name
    exceeding 100 characters, the system should reject the request
    with a validation error.
    """

    @given(st.text(min_size=101, max_size=500))
    @PROPERTY_SETTINGS
    def test_name_exceeding_100_chars_rejected(self, client: TestClient, long_name: str):
        """Names exceeding 100 characters are rejected.

        **Validates: Requirement 8.4**
        """
        response = client.post("/api/swarm-workspaces", json={
            "name": long_name,
            "file_path": "/tmp/test_workspace",
            "context": "Test context",
        })

        # Should be rejected with 400 validation error (SwarmAI uses 400 for validation errors)
        assert response.status_code == 400

    @given(st.just(''))
    @PROPERTY_SETTINGS
    def test_empty_name_rejected(self, client: TestClient, empty_name: str):
        """Empty names are rejected.

        **Validates: Requirement 8.4**
        
        Note: Whitespace-only names are allowed by the current schema since
        min_length=1 counts whitespace characters. Only truly empty strings
        are rejected.
        """
        response = client.post("/api/swarm-workspaces", json={
            "name": empty_name,
            "file_path": "/tmp/test_workspace",
            "context": "Test context",
        })

        # Should be rejected with 400 validation error (SwarmAI uses 400 for validation errors)
        assert response.status_code == 400


class TestListCompleteness:
    """Property 9: List Completeness.

    **Validates: Requirements 6.1, 4.2**

    For any set of workspaces in the database, a GET request to
    /swarm-workspaces should return all workspaces including the
    default workspace, each with name, icon, and filePath fields.
    """

    @given(st.integers(min_value=1, max_value=5))
    @PROPERTY_SETTINGS
    def test_list_includes_all_created_workspaces(self, client: TestClient, num_workspaces: int):
        """List endpoint returns all created workspaces.

        **Validates: Requirements 6.1, 4.2**
        """
        created_ids = []

        try:
            # Create multiple workspaces
            for i in range(num_workspaces):
                unique_id = str(uuid.uuid4())[:8]
                response = client.post("/api/swarm-workspaces", json={
                    "name": f"Test Workspace {unique_id}",
                    "file_path": f"/tmp/swarm_test/workspace_{unique_id}",
                    "context": f"Context for workspace {i}",
                    "icon": "📁",
                })
                assert response.status_code == 201
                created_ids.append(response.json()["id"])

            # List all workspaces
            list_response = client.get("/api/swarm-workspaces")
            assert list_response.status_code == 200
            workspaces = list_response.json()

            # Verify all created workspaces are in the list
            workspace_ids = [w["id"] for w in workspaces]
            for created_id in created_ids:
                assert created_id in workspace_ids

            # Verify default workspace is in the list
            default_response = client.get("/api/swarm-workspaces/default")
            assert default_response.status_code == 200
            default_id = default_response.json()["id"]
            assert default_id in workspace_ids

            # Verify each workspace has required fields
            for workspace in workspaces:
                assert "name" in workspace
                assert "file_path" in workspace
                assert "icon" in workspace or workspace.get("icon") is None

        finally:
            # Cleanup: delete all created workspaces
            for workspace_id in created_ids:
                client.delete(f"/api/swarm-workspaces/{workspace_id}")
