"""Unit tests for SwarmWorkspaceManager.

Tests for Task 3.1: validate_path() and expand_path() methods.
Tests for Task 3.2: create_folder_structure() method.
"""
import os
import pytest
import tempfile
import shutil
from pathlib import Path
from core.swarm_workspace_manager import SwarmWorkspaceManager, swarm_workspace_manager


class TestSwarmWorkspaceManagerConstants:
    """Tests for SwarmWorkspaceManager constants."""

    def test_folder_structure_contains_required_directories(self):
        """Verify FOLDER_STRUCTURE contains all required directories."""
        required_dirs = [
            "Context",
            "Docs",
            "Projects",
            "Tasks",
            "ToDos",
            "Plans",
            "Historical-Chats",
            "Reports",
        ]
        assert SwarmWorkspaceManager.FOLDER_STRUCTURE == required_dirs

    def test_default_workspace_config_has_required_fields(self):
        """Verify DEFAULT_WORKSPACE_CONFIG has all required fields."""
        config = SwarmWorkspaceManager.DEFAULT_WORKSPACE_CONFIG
        assert config["name"] == "SwarmWS"
        assert config["file_path"] == "{app_data_dir}/swarm-workspaces/SwarmWS"
        assert config["is_default"] is True
        assert "context" in config
        assert "icon" in config


class TestExpandPath:
    """Tests for expand_path() method."""

    def test_expand_tilde_to_home_directory(self):
        """Verify ~ is expanded to user home directory."""
        manager = SwarmWorkspaceManager()
        result = manager.expand_path("~/Desktop/test")
        expected = os.path.expanduser("~/Desktop/test")
        assert result == expected
        assert not result.startswith("~")

    def test_expand_path_preserves_absolute_path(self):
        """Verify absolute paths are preserved."""
        manager = SwarmWorkspaceManager()
        absolute_path = "/usr/local/bin"
        result = manager.expand_path(absolute_path)
        assert result == absolute_path

    def test_expand_path_handles_tilde_only(self):
        """Verify ~ alone expands to home directory."""
        manager = SwarmWorkspaceManager()
        result = manager.expand_path("~")
        assert result == os.path.expanduser("~")

    def test_expand_path_handles_nested_tilde_path(self):
        """Verify nested paths with ~ are expanded correctly."""
        manager = SwarmWorkspaceManager()
        result = manager.expand_path("~/a/b/c/d")
        expected = os.path.expanduser("~/a/b/c/d")
        assert result == expected


class TestValidatePath:
    """Tests for validate_path() method.

    Validates: Requirements 8.1, 8.5
    """

    def test_valid_absolute_path(self):
        """Verify absolute paths are accepted."""
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("/usr/local/bin") is True
        assert manager.validate_path("/home/user/workspace") is True

    def test_valid_tilde_path(self):
        """Verify paths starting with ~ are accepted."""
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("~/Desktop/SwarmAI") is True
        assert manager.validate_path("~/workspace") is True
        assert manager.validate_path("~") is True

    def test_reject_path_traversal_double_dot(self):
        """Verify paths with .. are rejected.

        Validates: Requirement 8.1
        """
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("/home/user/../etc/passwd") is False
        assert manager.validate_path("~/Desktop/../.ssh") is False
        assert manager.validate_path("..") is False
        assert manager.validate_path("../secret") is False

    def test_reject_relative_path(self):
        """Verify relative paths (not starting with ~ or /) are rejected.

        Validates: Requirement 8.5
        """
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("relative/path") is False
        assert manager.validate_path("workspace") is False
        assert manager.validate_path("./current") is False

    def test_reject_empty_path(self):
        """Verify empty paths are rejected."""
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("") is False

    def test_reject_path_with_embedded_traversal(self):
        """Verify paths with embedded .. sequences are rejected."""
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("/home/user/workspace/../../../etc") is False
        assert manager.validate_path("~/safe/../../unsafe") is False


class TestGlobalInstance:
    """Tests for the global swarm_workspace_manager instance."""

    def test_global_instance_exists(self):
        """Verify global instance is created."""
        assert swarm_workspace_manager is not None
        assert isinstance(swarm_workspace_manager, SwarmWorkspaceManager)

    def test_global_instance_has_folder_structure(self):
        """Verify global instance has FOLDER_STRUCTURE."""
        assert len(swarm_workspace_manager.FOLDER_STRUCTURE) == 8


class TestCreateFolderStructure:
    """Tests for create_folder_structure() method.

    Validates: Requirements 2.1, 2.4
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        # Cleanup after test
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.mark.asyncio
    async def test_creates_all_subdirectories(self, temp_dir):
        """Verify all required subdirectories are created.

        Validates: Requirement 2.1
        """
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "test_workspace")

        await manager.create_folder_structure(workspace_path)

        # Verify all subdirectories exist
        for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
            folder_path = os.path.join(workspace_path, folder_name)
            assert os.path.isdir(folder_path), f"Directory {folder_name} should exist"

    @pytest.mark.asyncio
    async def test_creates_root_directory_if_not_exists(self, temp_dir):
        """Verify root directory is created if it doesn't exist.

        Validates: Requirement 2.4
        """
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "new_workspace")

        # Ensure path doesn't exist
        assert not os.path.exists(workspace_path)

        await manager.create_folder_structure(workspace_path)

        # Verify root was created
        assert os.path.isdir(workspace_path)

    @pytest.mark.asyncio
    async def test_creates_nested_root_directory(self, temp_dir):
        """Verify deeply nested root directories are created."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "a", "b", "c", "workspace")

        await manager.create_folder_structure(workspace_path)

        assert os.path.isdir(workspace_path)
        # Verify subdirectories also exist
        for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
            folder_path = os.path.join(workspace_path, folder_name)
            assert os.path.isdir(folder_path)

    @pytest.mark.asyncio
    async def test_idempotent_folder_creation(self, temp_dir):
        """Verify calling create_folder_structure twice doesn't fail."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "test_workspace")

        # Create twice
        await manager.create_folder_structure(workspace_path)
        await manager.create_folder_structure(workspace_path)

        # Should still have all directories
        for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
            folder_path = os.path.join(workspace_path, folder_name)
            assert os.path.isdir(folder_path)

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, temp_dir):
        """Verify paths with .. are rejected."""
        manager = SwarmWorkspaceManager()
        invalid_path = os.path.join(temp_dir, "..", "escape_attempt")

        with pytest.raises(ValueError) as exc_info:
            await manager.create_folder_structure(invalid_path)

        assert "Invalid workspace path" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_rejects_relative_path(self):
        """Verify relative paths are rejected."""
        manager = SwarmWorkspaceManager()

        with pytest.raises(ValueError) as exc_info:
            await manager.create_folder_structure("relative/path/workspace")

        assert "Invalid workspace path" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handles_tilde_expansion(self):
        """Verify ~ paths are expanded and directories created."""
        manager = SwarmWorkspaceManager()
        # Use a unique temp directory under home
        unique_name = f"swarm_test_{os.getpid()}"
        workspace_path = f"~/tmp_swarm_test/{unique_name}"
        expanded_path = os.path.expanduser(workspace_path)

        try:
            await manager.create_folder_structure(workspace_path)

            # Verify directories were created at expanded path
            assert os.path.isdir(expanded_path)
            for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
                folder_path = os.path.join(expanded_path, folder_name)
                assert os.path.isdir(folder_path)
        finally:
            # Cleanup
            parent_dir = os.path.expanduser("~/tmp_swarm_test")
            if os.path.exists(parent_dir):
                shutil.rmtree(parent_dir)

    @pytest.mark.asyncio
    async def test_creates_exact_folder_structure(self, temp_dir):
        """Verify exactly the expected folders are created, no more, no less."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "test_workspace")

        await manager.create_folder_structure(workspace_path)

        # Get list of created subdirectories
        created_dirs = [
            d for d in os.listdir(workspace_path)
            if os.path.isdir(os.path.join(workspace_path, d))
        ]

        # Should match exactly
        assert set(created_dirs) == set(SwarmWorkspaceManager.FOLDER_STRUCTURE)


class TestCreateContextFiles:
    """Tests for create_context_files() method.

    Validates: Requirements 2.2, 2.3, 7.1, 7.2, 7.3, 7.4
    """

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace with Context folder for testing."""
        temp_path = tempfile.mkdtemp()
        workspace_path = os.path.join(temp_path, "test_workspace")
        context_path = os.path.join(workspace_path, "Context")
        os.makedirs(context_path)
        yield workspace_path
        # Cleanup after test
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.mark.asyncio
    async def test_creates_overall_context_file(self, temp_workspace):
        """Verify overall-context.md is created.

        Validates: Requirement 2.2
        """
        manager = SwarmWorkspaceManager()
        workspace_name = "TestWorkspace"

        await manager.create_context_files(temp_workspace, workspace_name)

        overall_context_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        assert os.path.isfile(overall_context_path)

    @pytest.mark.asyncio
    async def test_creates_compressed_context_file(self, temp_workspace):
        """Verify compressed-context.md is created.

        Validates: Requirement 2.3
        """
        manager = SwarmWorkspaceManager()
        workspace_name = "TestWorkspace"

        await manager.create_context_files(temp_workspace, workspace_name)

        compressed_context_path = os.path.join(temp_workspace, "Context", "compressed-context.md")
        assert os.path.isfile(compressed_context_path)

    @pytest.mark.asyncio
    async def test_compressed_context_is_empty(self, temp_workspace):
        """Verify compressed-context.md is created as empty file.

        Validates: Requirement 7.3
        """
        manager = SwarmWorkspaceManager()
        workspace_name = "TestWorkspace"

        await manager.create_context_files(temp_workspace, workspace_name)

        compressed_context_path = os.path.join(temp_workspace, "Context", "compressed-context.md")
        with open(compressed_context_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert content == ""

    @pytest.mark.asyncio
    async def test_overall_context_contains_workspace_name(self, temp_workspace):
        """Verify overall-context.md contains the workspace name.

        Validates: Requirement 7.1
        """
        manager = SwarmWorkspaceManager()
        workspace_name = "MyProjectWorkspace"

        await manager.create_context_files(temp_workspace, workspace_name)

        overall_context_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        with open(overall_context_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert workspace_name in content
        assert f"# {workspace_name} Workspace Context" in content

    @pytest.mark.asyncio
    async def test_overall_context_contains_required_sections(self, temp_workspace):
        """Verify overall-context.md contains all required sections.

        Validates: Requirement 7.2
        """
        manager = SwarmWorkspaceManager()
        workspace_name = "TestWorkspace"

        await manager.create_context_files(temp_workspace, workspace_name)

        overall_context_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        with open(overall_context_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check for required sections
        assert "## Workspace Purpose" in content
        assert "## Key Goals" in content
        assert "## Important Context" in content
        assert "## Notes" in content

    @pytest.mark.asyncio
    async def test_handles_tilde_path(self):
        """Verify ~ paths are expanded correctly."""
        manager = SwarmWorkspaceManager()
        unique_name = f"swarm_context_test_{os.getpid()}"
        workspace_path = f"~/tmp_swarm_test/{unique_name}"
        expanded_path = os.path.expanduser(workspace_path)
        context_path = os.path.join(expanded_path, "Context")

        try:
            # Create the Context directory first
            os.makedirs(context_path, exist_ok=True)

            await manager.create_context_files(workspace_path, "TildeTestWorkspace")

            # Verify files were created at expanded path
            overall_path = os.path.join(context_path, "overall-context.md")
            compressed_path = os.path.join(context_path, "compressed-context.md")
            assert os.path.isfile(overall_path)
            assert os.path.isfile(compressed_path)
        finally:
            # Cleanup
            parent_dir = os.path.expanduser("~/tmp_swarm_test")
            if os.path.exists(parent_dir):
                shutil.rmtree(parent_dir)

    @pytest.mark.asyncio
    async def test_does_not_raise_on_missing_context_dir(self, temp_workspace):
        """Verify method doesn't raise exception if Context dir doesn't exist.

        Validates: Requirement 7.4 - graceful error handling
        """
        manager = SwarmWorkspaceManager()
        # Remove the Context directory
        context_path = os.path.join(temp_workspace, "Context")
        shutil.rmtree(context_path)

        # Should not raise, just log warning
        await manager.create_context_files(temp_workspace, "TestWorkspace")

        # Files should not exist since Context dir was removed
        overall_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        assert not os.path.exists(overall_path)

    @pytest.mark.asyncio
    async def test_idempotent_context_file_creation(self, temp_workspace):
        """Verify calling create_context_files twice overwrites files correctly."""
        manager = SwarmWorkspaceManager()

        # Create files twice with different workspace names
        await manager.create_context_files(temp_workspace, "FirstName")
        await manager.create_context_files(temp_workspace, "SecondName")

        # Should have the second workspace name
        overall_context_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        with open(overall_context_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "SecondName" in content
        assert "FirstName" not in content

    @pytest.mark.asyncio
    async def test_special_characters_in_workspace_name(self, temp_workspace):
        """Verify workspace names with special characters are handled."""
        manager = SwarmWorkspaceManager()
        workspace_name = "My Project (2024) - Test"

        await manager.create_context_files(temp_workspace, workspace_name)

        overall_context_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        with open(overall_context_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert workspace_name in content


class TestOverallContextTemplate:
    """Tests for OVERALL_CONTEXT_TEMPLATE constant."""

    def test_template_has_workspace_name_placeholder(self):
        """Verify template contains {workspace_name} placeholder."""
        template = SwarmWorkspaceManager.OVERALL_CONTEXT_TEMPLATE
        assert "{workspace_name}" in template

    def test_template_has_required_sections(self):
        """Verify template contains all required sections.

        Validates: Requirement 7.2
        """
        template = SwarmWorkspaceManager.OVERALL_CONTEXT_TEMPLATE
        assert "## Workspace Purpose" in template
        assert "## Key Goals" in template
        assert "## Important Context" in template
        assert "## Notes" in template

    def test_template_format_with_name(self):
        """Verify template can be formatted with workspace name."""
        template = SwarmWorkspaceManager.OVERALL_CONTEXT_TEMPLATE
        result = template.format(workspace_name="TestProject")
        assert "TestProject" in result
        assert "{workspace_name}" not in result


class TestReadContextFiles:
    """Tests for read_context_files() method.

    Validates: Requirement 5.3
    """

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace with Context folder for testing."""
        temp_path = tempfile.mkdtemp()
        workspace_path = os.path.join(temp_path, "test_workspace")
        context_path = os.path.join(workspace_path, "Context")
        os.makedirs(context_path)
        yield workspace_path
        # Cleanup after test
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.mark.asyncio
    async def test_reads_overall_context_file(self, temp_workspace):
        """Verify overall-context.md content is read."""
        manager = SwarmWorkspaceManager()
        overall_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        expected_content = "# Test Workspace\n\nThis is the overall context."
        with open(overall_path, "w", encoding="utf-8") as f:
            f.write(expected_content)

        result = await manager.read_context_files(temp_workspace)

        assert expected_content in result

    @pytest.mark.asyncio
    async def test_reads_compressed_context_file(self, temp_workspace):
        """Verify compressed-context.md content is read."""
        manager = SwarmWorkspaceManager()
        compressed_path = os.path.join(temp_workspace, "Context", "compressed-context.md")
        expected_content = "Compressed context summary."
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write(expected_content)

        result = await manager.read_context_files(temp_workspace)

        assert expected_content in result

    @pytest.mark.asyncio
    async def test_combines_both_context_files(self, temp_workspace):
        """Verify both context files are combined."""
        manager = SwarmWorkspaceManager()
        overall_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        compressed_path = os.path.join(temp_workspace, "Context", "compressed-context.md")

        overall_content = "# Overall Context"
        compressed_content = "Compressed summary"

        with open(overall_path, "w", encoding="utf-8") as f:
            f.write(overall_content)
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write(compressed_content)

        result = await manager.read_context_files(temp_workspace)

        assert overall_content in result
        assert compressed_content in result
        # Verify separator is present
        assert "---" in result

    @pytest.mark.asyncio
    async def test_handles_missing_overall_context(self, temp_workspace):
        """Verify missing overall-context.md is handled gracefully."""
        manager = SwarmWorkspaceManager()
        compressed_path = os.path.join(temp_workspace, "Context", "compressed-context.md")
        compressed_content = "Only compressed content"
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write(compressed_content)

        result = await manager.read_context_files(temp_workspace)

        assert result == compressed_content

    @pytest.mark.asyncio
    async def test_handles_missing_compressed_context(self, temp_workspace):
        """Verify missing compressed-context.md is handled gracefully."""
        manager = SwarmWorkspaceManager()
        overall_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        overall_content = "Only overall content"
        with open(overall_path, "w", encoding="utf-8") as f:
            f.write(overall_content)

        result = await manager.read_context_files(temp_workspace)

        assert result == overall_content

    @pytest.mark.asyncio
    async def test_handles_both_files_missing(self, temp_workspace):
        """Verify both files missing returns empty string."""
        manager = SwarmWorkspaceManager()

        result = await manager.read_context_files(temp_workspace)

        assert result == ""

    @pytest.mark.asyncio
    async def test_handles_missing_context_directory(self):
        """Verify missing Context directory is handled gracefully."""
        manager = SwarmWorkspaceManager()
        temp_path = tempfile.mkdtemp()
        workspace_path = os.path.join(temp_path, "workspace_no_context")
        os.makedirs(workspace_path)

        try:
            result = await manager.read_context_files(workspace_path)
            assert result == ""
        finally:
            shutil.rmtree(temp_path)

    @pytest.mark.asyncio
    async def test_handles_empty_context_files(self, temp_workspace):
        """Verify empty context files return empty string."""
        manager = SwarmWorkspaceManager()
        overall_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        compressed_path = os.path.join(temp_workspace, "Context", "compressed-context.md")

        # Create empty files
        with open(overall_path, "w", encoding="utf-8") as f:
            f.write("")
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write("")

        result = await manager.read_context_files(temp_workspace)

        assert result == ""

    @pytest.mark.asyncio
    async def test_handles_tilde_path(self):
        """Verify ~ paths are expanded correctly."""
        manager = SwarmWorkspaceManager()
        unique_name = f"swarm_read_test_{os.getpid()}"
        workspace_path = f"~/tmp_swarm_test/{unique_name}"
        expanded_path = os.path.expanduser(workspace_path)
        context_path = os.path.join(expanded_path, "Context")

        try:
            os.makedirs(context_path, exist_ok=True)
            overall_path = os.path.join(context_path, "overall-context.md")
            with open(overall_path, "w", encoding="utf-8") as f:
                f.write("Tilde path content")

            result = await manager.read_context_files(workspace_path)

            assert "Tilde path content" in result
        finally:
            parent_dir = os.path.expanduser("~/tmp_swarm_test")
            if os.path.exists(parent_dir):
                shutil.rmtree(parent_dir)

    @pytest.mark.asyncio
    async def test_preserves_file_content_formatting(self, temp_workspace):
        """Verify file content formatting is preserved."""
        manager = SwarmWorkspaceManager()
        overall_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        content_with_formatting = """# Header

## Subheader

- List item 1
- List item 2

```python
code_block = True
```
"""
        with open(overall_path, "w", encoding="utf-8") as f:
            f.write(content_with_formatting)

        result = await manager.read_context_files(temp_workspace)

        assert "# Header" in result
        assert "## Subheader" in result
        assert "- List item 1" in result
        assert "```python" in result

    @pytest.mark.asyncio
    async def test_handles_unicode_content(self, temp_workspace):
        """Verify unicode content is handled correctly."""
        manager = SwarmWorkspaceManager()
        overall_path = os.path.join(temp_workspace, "Context", "overall-context.md")
        unicode_content = "Unicode test: 日本語 中文 한국어 🚀 émojis"
        with open(overall_path, "w", encoding="utf-8") as f:
            f.write(unicode_content)

        result = await manager.read_context_files(temp_workspace)

        assert unicode_content in result


class TestEnsureDefaultWorkspace:
    """Tests for ensure_default_workspace() method.

    Validates: Requirements 1.1, 1.2, 1.5
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        # Cleanup after test
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with swarm_workspaces table."""
        class MockSwarmWorkspacesTable:
            def __init__(self):
                self.workspaces = {}
                self.default_workspace = None

            async def get_default(self):
                return self.default_workspace

            async def put(self, item):
                self.workspaces[item["id"]] = item
                if item.get("is_default"):
                    self.default_workspace = item
                return item

        class MockDB:
            def __init__(self):
                self._swarm_workspaces = MockSwarmWorkspacesTable()

            @property
            def swarm_workspaces(self):
                return self._swarm_workspaces

        return MockDB()

    @pytest.mark.asyncio
    async def test_creates_default_workspace_when_not_exists(self, mock_db, temp_dir, monkeypatch):
        """Verify default workspace is created when it doesn't exist.

        Validates: Requirement 1.1
        """
        manager = SwarmWorkspaceManager()
        # Override the default path to use temp directory
        test_path = os.path.join(temp_dir, "SwarmWS")
        monkeypatch.setattr(
            SwarmWorkspaceManager,
            "DEFAULT_WORKSPACE_CONFIG",
            {
                "name": "SwarmWS",
                "file_path": test_path,
                "context": "Default SwarmAI workspace for general tasks and projects.",
                "icon": "🏠",
                "is_default": True,
            }
        )

        result = await manager.ensure_default_workspace(mock_db)

        assert result is not None
        assert result["name"] == "SwarmWS"
        assert result["is_default"] is True

    @pytest.mark.asyncio
    async def test_returns_existing_default_workspace(self, mock_db):
        """Verify existing default workspace is returned without creating new one.

        Validates: Requirement 1.5
        """
        manager = SwarmWorkspaceManager()
        # Pre-populate with existing default workspace
        existing_workspace = {
            "id": "existing-id-123",
            "name": "SwarmWS",
            "file_path": "/existing/path",
            "context": "Existing context",
            "icon": "🏠",
            "is_default": True,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
        mock_db._swarm_workspaces.default_workspace = existing_workspace

        result = await manager.ensure_default_workspace(mock_db)

        assert result["id"] == "existing-id-123"
        assert result["name"] == "SwarmWS"
        # Should not have created a new workspace
        assert len(mock_db._swarm_workspaces.workspaces) == 0

    @pytest.mark.asyncio
    async def test_default_workspace_has_is_default_true(self, mock_db, temp_dir, monkeypatch):
        """Verify created default workspace has is_default=True.

        Validates: Requirement 1.2
        """
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        monkeypatch.setattr(
            SwarmWorkspaceManager,
            "DEFAULT_WORKSPACE_CONFIG",
            {
                "name": "SwarmWS",
                "file_path": test_path,
                "context": "Default SwarmAI workspace for general tasks and projects.",
                "icon": "🏠",
                "is_default": True,
            }
        )

        result = await manager.ensure_default_workspace(mock_db)

        assert result["is_default"] is True

    @pytest.mark.asyncio
    async def test_default_workspace_has_correct_name(self, mock_db, temp_dir, monkeypatch):
        """Verify created default workspace has correct name.

        Validates: Requirement 1.1
        """
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        monkeypatch.setattr(
            SwarmWorkspaceManager,
            "DEFAULT_WORKSPACE_CONFIG",
            {
                "name": "SwarmWS",
                "file_path": test_path,
                "context": "Default SwarmAI workspace for general tasks and projects.",
                "icon": "🏠",
                "is_default": True,
            }
        )

        result = await manager.ensure_default_workspace(mock_db)

        assert result["name"] == "SwarmWS"

    @pytest.mark.asyncio
    async def test_creates_folder_structure_for_default_workspace(self, mock_db, temp_dir, monkeypatch):
        """Verify folder structure is created for default workspace.

        Validates: Requirements 1.1, 2.1
        """
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        monkeypatch.setattr(
            SwarmWorkspaceManager,
            "DEFAULT_WORKSPACE_CONFIG",
            {
                "name": "SwarmWS",
                "file_path": test_path,
                "context": "Default SwarmAI workspace for general tasks and projects.",
                "icon": "🏠",
                "is_default": True,
            }
        )

        await manager.ensure_default_workspace(mock_db)

        # Verify folder structure was created
        assert os.path.isdir(test_path)
        for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
            folder_path = os.path.join(test_path, folder_name)
            assert os.path.isdir(folder_path), f"Directory {folder_name} should exist"

    @pytest.mark.asyncio
    async def test_creates_context_files_for_default_workspace(self, mock_db, temp_dir, monkeypatch):
        """Verify context files are created for default workspace.

        Validates: Requirements 2.2, 2.3
        """
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        monkeypatch.setattr(
            SwarmWorkspaceManager,
            "DEFAULT_WORKSPACE_CONFIG",
            {
                "name": "SwarmWS",
                "file_path": test_path,
                "context": "Default SwarmAI workspace for general tasks and projects.",
                "icon": "🏠",
                "is_default": True,
            }
        )

        await manager.ensure_default_workspace(mock_db)

        # Verify context files were created
        overall_context_path = os.path.join(test_path, "Context", "overall-context.md")
        compressed_context_path = os.path.join(test_path, "Context", "compressed-context.md")
        assert os.path.isfile(overall_context_path)
        assert os.path.isfile(compressed_context_path)

    @pytest.mark.asyncio
    async def test_default_workspace_stored_in_database(self, mock_db, temp_dir, monkeypatch):
        """Verify default workspace is stored in database.

        Validates: Requirement 1.5
        """
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        monkeypatch.setattr(
            SwarmWorkspaceManager,
            "DEFAULT_WORKSPACE_CONFIG",
            {
                "name": "SwarmWS",
                "file_path": test_path,
                "context": "Default SwarmAI workspace for general tasks and projects.",
                "icon": "🏠",
                "is_default": True,
            }
        )

        result = await manager.ensure_default_workspace(mock_db)

        # Verify workspace was stored in database
        assert result["id"] in mock_db._swarm_workspaces.workspaces
        stored = mock_db._swarm_workspaces.workspaces[result["id"]]
        assert stored["name"] == "SwarmWS"
        assert stored["is_default"] is True

    @pytest.mark.asyncio
    async def test_default_workspace_has_valid_uuid(self, mock_db, temp_dir, monkeypatch):
        """Verify created default workspace has a valid UUID."""
        import uuid
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        monkeypatch.setattr(
            SwarmWorkspaceManager,
            "DEFAULT_WORKSPACE_CONFIG",
            {
                "name": "SwarmWS",
                "file_path": test_path,
                "context": "Default SwarmAI workspace for general tasks and projects.",
                "icon": "🏠",
                "is_default": True,
            }
        )

        result = await manager.ensure_default_workspace(mock_db)

        # Verify id is a valid UUID
        try:
            uuid.UUID(result["id"])
        except ValueError:
            pytest.fail(f"Invalid UUID: {result['id']}")

    @pytest.mark.asyncio
    async def test_default_workspace_has_timestamps(self, mock_db, temp_dir, monkeypatch):
        """Verify created default workspace has created_at and updated_at timestamps."""
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        monkeypatch.setattr(
            SwarmWorkspaceManager,
            "DEFAULT_WORKSPACE_CONFIG",
            {
                "name": "SwarmWS",
                "file_path": test_path,
                "context": "Default SwarmAI workspace for general tasks and projects.",
                "icon": "🏠",
                "is_default": True,
            }
        )

        result = await manager.ensure_default_workspace(mock_db)

        assert "created_at" in result
        assert "updated_at" in result
        assert result["created_at"] is not None
        assert result["updated_at"] is not None

    @pytest.mark.asyncio
    async def test_default_workspace_has_all_required_fields(self, mock_db, temp_dir, monkeypatch):
        """Verify created default workspace has all required fields.

        Validates: Requirement 3.1
        """
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        monkeypatch.setattr(
            SwarmWorkspaceManager,
            "DEFAULT_WORKSPACE_CONFIG",
            {
                "name": "SwarmWS",
                "file_path": test_path,
                "context": "Default SwarmAI workspace for general tasks and projects.",
                "icon": "🏠",
                "is_default": True,
            }
        )

        result = await manager.ensure_default_workspace(mock_db)

        # Verify all required fields are present
        required_fields = ["id", "name", "file_path", "context", "is_default", "created_at", "updated_at"]
        for field in required_fields:
            assert field in result, f"Missing required field: {field}"

    @pytest.mark.asyncio
    async def test_idempotent_ensure_default_workspace(self, mock_db, temp_dir, monkeypatch):
        """Verify calling ensure_default_workspace twice returns same workspace."""
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        monkeypatch.setattr(
            SwarmWorkspaceManager,
            "DEFAULT_WORKSPACE_CONFIG",
            {
                "name": "SwarmWS",
                "file_path": test_path,
                "context": "Default SwarmAI workspace for general tasks and projects.",
                "icon": "🏠",
                "is_default": True,
            }
        )

        # Call twice
        result1 = await manager.ensure_default_workspace(mock_db)
        result2 = await manager.ensure_default_workspace(mock_db)

        # Should return the same workspace
        assert result1["id"] == result2["id"]
        # Should only have one workspace in database
        assert len(mock_db._swarm_workspaces.workspaces) == 1


class TestExpandPathWithAppDataDir:
    """Tests for expand_path() method with {app_data_dir} placeholder.

    Validates: Requirements 4.2
    """

    def test_expand_app_data_dir_placeholder(self, monkeypatch):
        """Verify {app_data_dir} is expanded to platform-specific path."""
        from pathlib import Path
        import config
        manager = SwarmWorkspaceManager()
        
        # Mock get_app_data_dir to return a predictable path
        mock_app_data = Path("/mock/app/data/SwarmAI")
        monkeypatch.setattr(config, "get_app_data_dir", lambda: mock_app_data)
        
        result = manager.expand_path("{app_data_dir}/swarm-workspaces/SwarmWS")
        
        assert result == "/mock/app/data/SwarmAI/swarm-workspaces/SwarmWS"
        assert "{app_data_dir}" not in result

    def test_expand_app_data_dir_only(self, monkeypatch):
        """Verify {app_data_dir} alone is expanded correctly."""
        from pathlib import Path
        import config
        manager = SwarmWorkspaceManager()
        
        mock_app_data = Path("/mock/app/data/SwarmAI")
        monkeypatch.setattr(config, "get_app_data_dir", lambda: mock_app_data)
        
        result = manager.expand_path("{app_data_dir}")
        
        assert result == "/mock/app/data/SwarmAI"

    def test_expand_path_preserves_tilde_expansion(self):
        """Verify ~ expansion still works alongside {app_data_dir}."""
        manager = SwarmWorkspaceManager()
        
        result = manager.expand_path("~/Desktop/test")
        expected = os.path.expanduser("~/Desktop/test")
        
        assert result == expected
        assert not result.startswith("~")

    def test_expand_path_handles_both_placeholders(self, monkeypatch):
        """Verify path with both ~ and {app_data_dir} is handled (edge case)."""
        from pathlib import Path
        import config
        manager = SwarmWorkspaceManager()
        
        mock_app_data = Path("/mock/app/data/SwarmAI")
        monkeypatch.setattr(config, "get_app_data_dir", lambda: mock_app_data)
        
        # This is an unusual case but should work
        result = manager.expand_path("{app_data_dir}/test")
        
        assert result == "/mock/app/data/SwarmAI/test"


class TestValidatePathWithAppDataDir:
    """Tests for validate_path() method with {app_data_dir} placeholder.

    Validates: Requirements 8.1, 8.5
    """

    def test_valid_app_data_dir_path(self):
        """Verify paths starting with {app_data_dir} are accepted."""
        manager = SwarmWorkspaceManager()
        
        assert manager.validate_path("{app_data_dir}/swarm-workspaces/SwarmWS") is True
        assert manager.validate_path("{app_data_dir}") is True
        assert manager.validate_path("{app_data_dir}/nested/path") is True

    def test_reject_app_data_dir_with_path_traversal(self):
        """Verify {app_data_dir} paths with .. are rejected.

        Validates: Requirement 8.1
        """
        manager = SwarmWorkspaceManager()
        
        assert manager.validate_path("{app_data_dir}/../escape") is False
        assert manager.validate_path("{app_data_dir}/safe/../unsafe") is False


class TestEnsureWorkspaceFoldersExist:
    """Tests for ensure_workspace_folders_exist() method.

    Validates: Requirements 4.2, 4.3, 4.4
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        # Cleanup after test
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with swarm_workspaces table."""
        class MockSwarmWorkspacesTable:
            def __init__(self):
                self.workspaces = {}
                self.default_workspace = None

            async def get_default(self):
                return self.default_workspace

            async def put(self, item):
                self.workspaces[item["id"]] = item
                if item.get("is_default"):
                    self.default_workspace = item
                return item

        class MockDB:
            def __init__(self):
                self._swarm_workspaces = MockSwarmWorkspacesTable()

            @property
            def swarm_workspaces(self):
                return self._swarm_workspaces

        return MockDB()

    @pytest.mark.asyncio
    async def test_creates_folders_when_not_exist(self, mock_db, temp_dir):
        """Verify folders are created when they don't exist.

        Validates: Requirements 4.2, 4.3
        """
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "swarm-workspaces", "SwarmWS")
        
        # Set up mock database with default workspace
        mock_db._swarm_workspaces.default_workspace = {
            "id": "test-id",
            "name": "SwarmWS",
            "file_path": workspace_path,
            "context": "Test context",
            "icon": "🏠",
            "is_default": True,
        }
        
        # Ensure path doesn't exist
        assert not os.path.exists(workspace_path)
        
        await manager.ensure_workspace_folders_exist(mock_db)
        
        # Verify folders were created
        assert os.path.isdir(workspace_path)
        for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
            folder_path = os.path.join(workspace_path, folder_name)
            assert os.path.isdir(folder_path), f"Directory {folder_name} should exist"

    @pytest.mark.asyncio
    async def test_creates_context_files(self, mock_db, temp_dir):
        """Verify context files are created.

        Validates: Requirement 4.4
        """
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "swarm-workspaces", "SwarmWS")
        
        mock_db._swarm_workspaces.default_workspace = {
            "id": "test-id",
            "name": "SwarmWS",
            "file_path": workspace_path,
            "context": "Test context",
            "icon": "🏠",
            "is_default": True,
        }
        
        await manager.ensure_workspace_folders_exist(mock_db)
        
        # Verify context files were created
        overall_context_path = os.path.join(workspace_path, "Context", "overall-context.md")
        compressed_context_path = os.path.join(workspace_path, "Context", "compressed-context.md")
        assert os.path.isfile(overall_context_path)
        assert os.path.isfile(compressed_context_path)

    @pytest.mark.asyncio
    async def test_skips_when_folders_exist(self, mock_db, temp_dir):
        """Verify no action when folders already exist."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "swarm-workspaces", "SwarmWS")
        
        # Pre-create the workspace folder
        os.makedirs(workspace_path)
        marker_file = os.path.join(workspace_path, "marker.txt")
        with open(marker_file, "w") as f:
            f.write("existing content")
        
        mock_db._swarm_workspaces.default_workspace = {
            "id": "test-id",
            "name": "SwarmWS",
            "file_path": workspace_path,
            "context": "Test context",
            "icon": "🏠",
            "is_default": True,
        }
        
        await manager.ensure_workspace_folders_exist(mock_db)
        
        # Verify marker file still exists (folder wasn't recreated)
        assert os.path.isfile(marker_file)
        # Verify subdirectories were NOT created (since root existed)
        context_path = os.path.join(workspace_path, "Context")
        assert not os.path.exists(context_path)

    @pytest.mark.asyncio
    async def test_handles_no_default_workspace(self, mock_db):
        """Verify no error when no default workspace exists."""
        manager = SwarmWorkspaceManager()
        
        # No default workspace set
        mock_db._swarm_workspaces.default_workspace = None
        
        # Should not raise
        await manager.ensure_workspace_folders_exist(mock_db)

    @pytest.mark.asyncio
    async def test_expands_app_data_dir_placeholder(self, mock_db, temp_dir, monkeypatch):
        """Verify {app_data_dir} placeholder is expanded.

        Validates: Requirement 4.2
        """
        from pathlib import Path
        import config
        manager = SwarmWorkspaceManager()
        
        # Mock get_app_data_dir to return temp_dir
        mock_app_data = Path(temp_dir)
        monkeypatch.setattr(config, "get_app_data_dir", lambda: mock_app_data)
        
        mock_db._swarm_workspaces.default_workspace = {
            "id": "test-id",
            "name": "SwarmWS",
            "file_path": "{app_data_dir}/swarm-workspaces/SwarmWS",
            "context": "Test context",
            "icon": "🏠",
            "is_default": True,
        }
        
        await manager.ensure_workspace_folders_exist(mock_db)
        
        # Verify folders were created at expanded path
        expected_path = os.path.join(temp_dir, "swarm-workspaces", "SwarmWS")
        assert os.path.isdir(expected_path)
        for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
            folder_path = os.path.join(expected_path, folder_name)
            assert os.path.isdir(folder_path), f"Directory {folder_name} should exist"

    @pytest.mark.asyncio
    async def test_handles_folder_creation_error_gracefully(self, mock_db, monkeypatch):
        """Verify errors during folder creation are handled gracefully."""
        from pathlib import Path
        manager = SwarmWorkspaceManager()
        
        # Use an invalid path that will fail
        mock_db._swarm_workspaces.default_workspace = {
            "id": "test-id",
            "name": "SwarmWS",
            "file_path": "/nonexistent/readonly/path/workspace",
            "context": "Test context",
            "icon": "🏠",
            "is_default": True,
        }
        
        # Should not raise, just log warning
        await manager.ensure_workspace_folders_exist(mock_db)
