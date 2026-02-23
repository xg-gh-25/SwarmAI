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
        """Verify FOLDER_STRUCTURE contains all required directories.

        Validates: Requirements 2.3, 2.7, 35.1-35.6
        """
        required_dirs = [
            "Artifacts",
            "Artifacts/Plans",
            "Artifacts/Reports",
            "Artifacts/Docs",
            "Artifacts/Decisions",
            "ContextFiles",
            "Transcripts",
        ]
        assert SwarmWorkspaceManager.FOLDER_STRUCTURE == required_dirs

    def test_default_workspace_config_has_required_fields(self):
        """Verify DEFAULT_WORKSPACE_CONFIG has all required fields."""
        config = SwarmWorkspaceManager.DEFAULT_WORKSPACE_CONFIG
        assert config["name"] == "SwarmWS"
        assert config["file_path"] == "{app_data_dir}/SwarmWS"
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
        assert len(swarm_workspace_manager.FOLDER_STRUCTURE) == 7


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

        # Collect all created directories relative to workspace root
        created_dirs = []
        for dirpath, dirnames, _ in os.walk(workspace_path):
            for d in dirnames:
                rel = os.path.relpath(os.path.join(dirpath, d), workspace_path)
                created_dirs.append(rel)

        # Should match exactly
        assert set(created_dirs) == set(SwarmWorkspaceManager.FOLDER_STRUCTURE)


class TestCreateContextFiles:
    """Tests for create_context_files() method.

    Validates: Requirements 2.3, 29.1-29.10, 35.1
    """

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace with ContextFiles folder for testing."""
        temp_path = tempfile.mkdtemp()
        workspace_path = os.path.join(temp_path, "test_workspace")
        context_path = os.path.join(workspace_path, "ContextFiles")
        os.makedirs(context_path)
        yield workspace_path
        # Cleanup after test
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.mark.asyncio
    async def test_creates_context_file(self, temp_workspace):
        """Verify context.md is created.

        Validates: Requirement 2.3
        """
        manager = SwarmWorkspaceManager()
        workspace_name = "TestWorkspace"

        await manager.create_context_files(temp_workspace, workspace_name)

        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        assert os.path.isfile(context_path)

    @pytest.mark.asyncio
    async def test_creates_compressed_context_file(self, temp_workspace):
        """Verify compressed-context.md is created.

        Validates: Requirement 35.1
        """
        manager = SwarmWorkspaceManager()
        workspace_name = "TestWorkspace"

        await manager.create_context_files(temp_workspace, workspace_name)

        compressed_context_path = os.path.join(temp_workspace, "ContextFiles", "compressed-context.md")
        assert os.path.isfile(compressed_context_path)

    @pytest.mark.asyncio
    async def test_compressed_context_is_empty(self, temp_workspace):
        """Verify compressed-context.md is created as empty file."""
        manager = SwarmWorkspaceManager()
        workspace_name = "TestWorkspace"

        await manager.create_context_files(temp_workspace, workspace_name)

        compressed_context_path = os.path.join(temp_workspace, "ContextFiles", "compressed-context.md")
        with open(compressed_context_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert content == ""

    @pytest.mark.asyncio
    async def test_context_contains_workspace_name(self, temp_workspace):
        """Verify context.md contains the workspace name."""
        manager = SwarmWorkspaceManager()
        workspace_name = "MyProjectWorkspace"

        await manager.create_context_files(temp_workspace, workspace_name)

        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        with open(context_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert workspace_name in content
        assert f"# {workspace_name} Workspace Context" in content

    @pytest.mark.asyncio
    async def test_context_contains_required_sections(self, temp_workspace):
        """Verify context.md contains all required sections."""
        manager = SwarmWorkspaceManager()
        workspace_name = "TestWorkspace"

        await manager.create_context_files(temp_workspace, workspace_name)

        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        with open(context_path, "r", encoding="utf-8") as f:
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
        context_path = os.path.join(expanded_path, "ContextFiles")

        try:
            # Create the ContextFiles directory first
            os.makedirs(context_path, exist_ok=True)

            await manager.create_context_files(workspace_path, "TildeTestWorkspace")

            # Verify files were created at expanded path
            ctx_path = os.path.join(context_path, "context.md")
            compressed_path = os.path.join(context_path, "compressed-context.md")
            assert os.path.isfile(ctx_path)
            assert os.path.isfile(compressed_path)
        finally:
            # Cleanup
            parent_dir = os.path.expanduser("~/tmp_swarm_test")
            if os.path.exists(parent_dir):
                shutil.rmtree(parent_dir)

    @pytest.mark.asyncio
    async def test_does_not_raise_on_missing_context_dir(self, temp_workspace):
        """Verify method doesn't raise exception if ContextFiles dir doesn't exist.

        Graceful error handling.
        """
        manager = SwarmWorkspaceManager()
        # Remove the ContextFiles directory
        context_path = os.path.join(temp_workspace, "ContextFiles")
        shutil.rmtree(context_path)

        # Should not raise, just log warning
        await manager.create_context_files(temp_workspace, "TestWorkspace")

        # Files should not exist since ContextFiles dir was removed
        ctx_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        assert not os.path.exists(ctx_path)

    @pytest.mark.asyncio
    async def test_idempotent_context_file_creation(self, temp_workspace):
        """Verify calling create_context_files twice overwrites files correctly."""
        manager = SwarmWorkspaceManager()

        # Create files twice with different workspace names
        await manager.create_context_files(temp_workspace, "FirstName")
        await manager.create_context_files(temp_workspace, "SecondName")

        # Should have the second workspace name
        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        with open(context_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "SecondName" in content
        assert "FirstName" not in content

    @pytest.mark.asyncio
    async def test_special_characters_in_workspace_name(self, temp_workspace):
        """Verify workspace names with special characters are handled."""
        manager = SwarmWorkspaceManager()
        workspace_name = "My Project (2024) - Test"

        await manager.create_context_files(temp_workspace, workspace_name)

        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        with open(context_path, "r", encoding="utf-8") as f:
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

    Validates: Requirement 14.2
    """

    @pytest.fixture
    def temp_workspace(self):
        """Create a temporary workspace with ContextFiles folder for testing."""
        temp_path = tempfile.mkdtemp()
        workspace_path = os.path.join(temp_path, "test_workspace")
        context_path = os.path.join(workspace_path, "ContextFiles")
        os.makedirs(context_path)
        yield workspace_path
        # Cleanup after test
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.mark.asyncio
    async def test_reads_context_file(self, temp_workspace):
        """Verify context.md content is read."""
        manager = SwarmWorkspaceManager()
        ctx_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        expected_content = "# Test Workspace\n\nThis is the overall context."
        with open(ctx_path, "w", encoding="utf-8") as f:
            f.write(expected_content)

        result = await manager.read_context_files(temp_workspace)

        assert expected_content in result

    @pytest.mark.asyncio
    async def test_reads_compressed_context_file(self, temp_workspace):
        """Verify compressed-context.md content is read."""
        manager = SwarmWorkspaceManager()
        compressed_path = os.path.join(temp_workspace, "ContextFiles", "compressed-context.md")
        expected_content = "Compressed context summary."
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write(expected_content)

        result = await manager.read_context_files(temp_workspace)

        assert expected_content in result

    @pytest.mark.asyncio
    async def test_combines_both_context_files(self, temp_workspace):
        """Verify both context files are combined."""
        manager = SwarmWorkspaceManager()
        ctx_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        compressed_path = os.path.join(temp_workspace, "ContextFiles", "compressed-context.md")

        context_content = "# Overall Context"
        compressed_content = "Compressed summary"

        with open(ctx_path, "w", encoding="utf-8") as f:
            f.write(context_content)
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write(compressed_content)

        result = await manager.read_context_files(temp_workspace)

        assert context_content in result
        assert compressed_content in result
        # Verify separator is present
        assert "---" in result

    @pytest.mark.asyncio
    async def test_handles_missing_context_file(self, temp_workspace):
        """Verify missing context.md is handled gracefully."""
        manager = SwarmWorkspaceManager()
        compressed_path = os.path.join(temp_workspace, "ContextFiles", "compressed-context.md")
        compressed_content = "Only compressed content"
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write(compressed_content)

        result = await manager.read_context_files(temp_workspace)

        assert result == compressed_content

    @pytest.mark.asyncio
    async def test_handles_missing_compressed_context(self, temp_workspace):
        """Verify missing compressed-context.md is handled gracefully."""
        manager = SwarmWorkspaceManager()
        ctx_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        context_content = "Only context content"
        with open(ctx_path, "w", encoding="utf-8") as f:
            f.write(context_content)

        result = await manager.read_context_files(temp_workspace)

        assert result == context_content

    @pytest.mark.asyncio
    async def test_handles_both_files_missing(self, temp_workspace):
        """Verify both files missing returns empty string."""
        manager = SwarmWorkspaceManager()

        result = await manager.read_context_files(temp_workspace)

        assert result == ""

    @pytest.mark.asyncio
    async def test_handles_missing_context_directory(self):
        """Verify missing ContextFiles directory is handled gracefully."""
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
        ctx_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        compressed_path = os.path.join(temp_workspace, "ContextFiles", "compressed-context.md")

        # Create empty files
        with open(ctx_path, "w", encoding="utf-8") as f:
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
        context_path = os.path.join(expanded_path, "ContextFiles")

        try:
            os.makedirs(context_path, exist_ok=True)
            ctx_path = os.path.join(context_path, "context.md")
            with open(ctx_path, "w", encoding="utf-8") as f:
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
        ctx_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        content_with_formatting = """# Header

## Subheader

- List item 1
- List item 2

```python
code_block = True
```
"""
        with open(ctx_path, "w", encoding="utf-8") as f:
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
        ctx_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        unicode_content = "Unicode test: 日本語 中文 한국어 🚀 émojis"
        with open(ctx_path, "w", encoding="utf-8") as f:
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

        Validates: Requirements 2.3, 29.1
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

        # Verify context files were created in ContextFiles/
        context_path = os.path.join(test_path, "ContextFiles", "context.md")
        compressed_context_path = os.path.join(test_path, "ContextFiles", "compressed-context.md")
        assert os.path.isfile(context_path)
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
        
        # Verify context files were created in ContextFiles/
        context_path = os.path.join(workspace_path, "ContextFiles", "context.md")
        compressed_context_path = os.path.join(workspace_path, "ContextFiles", "compressed-context.md")
        assert os.path.isfile(context_path)
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
        artifacts_path = os.path.join(workspace_path, "Artifacts")
        assert not os.path.exists(artifacts_path)

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


class TestArchiveWorkspace:
    """Tests for archive() method.

    Validates: Requirements 36.1, 36.2
    """

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with swarm_workspaces table."""
        class MockSwarmWorkspacesTable:
            def __init__(self):
                self.workspaces = {}

            async def get(self, workspace_id):
                return self.workspaces.get(workspace_id)

            async def update(self, workspace_id, updates):
                ws = self.workspaces.get(workspace_id)
                if ws is None:
                    return None
                ws.update(updates)
                return ws

            async def list(self):
                return list(self.workspaces.values())

        class MockDB:
            def __init__(self):
                self._swarm_workspaces = MockSwarmWorkspacesTable()

            @property
            def swarm_workspaces(self):
                return self._swarm_workspaces

        return MockDB()

    @pytest.mark.asyncio
    async def test_archive_sets_is_archived_and_timestamp(self, mock_db):
        """Verify archive sets is_archived=1 and archived_at timestamp.

        Validates: Requirement 36.2
        """
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces["ws-1"] = {
            "id": "ws-1",
            "name": "TestWS",
            "is_default": False,
            "is_archived": 0,
            "archived_at": None,
        }

        result = await manager.archive("ws-1", mock_db)

        assert result["is_archived"] == 1
        assert result["archived_at"] is not None

    @pytest.mark.asyncio
    async def test_archive_default_workspace_raises_permission_error(self, mock_db):
        """Verify archiving SwarmWS (is_default=true) raises PermissionError.

        Validates: Requirement 36.1
        """
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces["swarmws"] = {
            "id": "swarmws",
            "name": "SwarmWS",
            "is_default": True,
            "is_archived": 0,
            "archived_at": None,
        }

        with pytest.raises(PermissionError, match="Cannot archive the default workspace"):
            await manager.archive("swarmws", mock_db)

    @pytest.mark.asyncio
    async def test_archive_nonexistent_workspace_raises_value_error(self, mock_db):
        """Verify archiving a non-existent workspace raises ValueError."""
        manager = SwarmWorkspaceManager()

        with pytest.raises(ValueError, match="Workspace not found"):
            await manager.archive("nonexistent", mock_db)

    @pytest.mark.asyncio
    async def test_archive_does_not_modify_default_flag(self, mock_db):
        """Verify archive does not change is_default."""
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces["ws-1"] = {
            "id": "ws-1",
            "name": "TestWS",
            "is_default": False,
            "is_archived": 0,
            "archived_at": None,
        }

        result = await manager.archive("ws-1", mock_db)

        assert result["is_default"] is False


class TestUnarchiveWorkspace:
    """Tests for unarchive() method.

    Validates: Requirement 36.10
    """

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with swarm_workspaces table."""
        class MockSwarmWorkspacesTable:
            def __init__(self):
                self.workspaces = {}

            async def get(self, workspace_id):
                return self.workspaces.get(workspace_id)

            async def update(self, workspace_id, updates):
                ws = self.workspaces.get(workspace_id)
                if ws is None:
                    return None
                ws.update(updates)
                return ws

        class MockDB:
            def __init__(self):
                self._swarm_workspaces = MockSwarmWorkspacesTable()

            @property
            def swarm_workspaces(self):
                return self._swarm_workspaces

        return MockDB()

    @pytest.mark.asyncio
    async def test_unarchive_clears_archived_fields(self, mock_db):
        """Verify unarchive sets is_archived=0 and archived_at=None.

        Validates: Requirement 36.10
        """
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces["ws-1"] = {
            "id": "ws-1",
            "name": "TestWS",
            "is_default": False,
            "is_archived": 1,
            "archived_at": "2024-01-01T00:00:00+00:00",
        }

        result = await manager.unarchive("ws-1", mock_db)

        assert result["is_archived"] == 0
        assert result["archived_at"] is None

    @pytest.mark.asyncio
    async def test_unarchive_nonexistent_workspace_raises_value_error(self, mock_db):
        """Verify unarchiving a non-existent workspace raises ValueError."""
        manager = SwarmWorkspaceManager()

        with pytest.raises(ValueError, match="Workspace not found"):
            await manager.unarchive("nonexistent", mock_db)

    @pytest.mark.asyncio
    async def test_unarchive_already_active_workspace(self, mock_db):
        """Verify unarchiving an already-active workspace is idempotent."""
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces["ws-1"] = {
            "id": "ws-1",
            "name": "TestWS",
            "is_default": False,
            "is_archived": 0,
            "archived_at": None,
        }

        result = await manager.unarchive("ws-1", mock_db)

        assert result["is_archived"] == 0
        assert result["archived_at"] is None


class TestListNonArchived:
    """Tests for list_non_archived() method.

    Validates: Requirements 36.3, 36.5
    """

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with swarm_workspaces table."""
        class MockSwarmWorkspacesTable:
            def __init__(self):
                self.workspaces = {}

            async def list(self):
                return list(self.workspaces.values())

        class MockDB:
            def __init__(self):
                self._swarm_workspaces = MockSwarmWorkspacesTable()

            @property
            def swarm_workspaces(self):
                return self._swarm_workspaces

        return MockDB()

    @pytest.mark.asyncio
    async def test_excludes_archived_workspaces(self, mock_db):
        """Verify archived workspaces are excluded from the list.

        Validates: Requirement 36.3
        """
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces = {
            "ws-default": {
                "id": "ws-default",
                "name": "SwarmWS",
                "is_default": True,
                "is_archived": 0,
                "created_at": "2024-01-01T00:00:00",
            },
            "ws-active": {
                "id": "ws-active",
                "name": "ActiveWS",
                "is_default": False,
                "is_archived": 0,
                "created_at": "2024-01-02T00:00:00",
            },
            "ws-archived": {
                "id": "ws-archived",
                "name": "ArchivedWS",
                "is_default": False,
                "is_archived": 1,
                "archived_at": "2024-06-01T00:00:00",
                "created_at": "2024-01-03T00:00:00",
            },
        }

        result = await manager.list_non_archived(mock_db)

        ids = [ws["id"] for ws in result]
        assert "ws-archived" not in ids
        assert "ws-default" in ids
        assert "ws-active" in ids

    @pytest.mark.asyncio
    async def test_default_workspace_is_first(self, mock_db):
        """Verify the default workspace (SwarmWS) is always first in the list.

        Validates: Requirement 36.3
        """
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces = {
            "ws-custom": {
                "id": "ws-custom",
                "name": "CustomWS",
                "is_default": False,
                "is_archived": 0,
                "created_at": "2024-01-01T00:00:00",
            },
            "ws-default": {
                "id": "ws-default",
                "name": "SwarmWS",
                "is_default": True,
                "is_archived": 0,
                "created_at": "2024-01-02T00:00:00",
            },
        }

        result = await manager.list_non_archived(mock_db)

        assert result[0]["is_default"] is True
        assert result[0]["name"] == "SwarmWS"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_all_archived(self, mock_db):
        """Verify empty list when all workspaces are archived (except default)."""
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces = {
            "ws-archived": {
                "id": "ws-archived",
                "name": "ArchivedWS",
                "is_default": False,
                "is_archived": 1,
                "archived_at": "2024-06-01T00:00:00",
                "created_at": "2024-01-01T00:00:00",
            },
        }

        result = await manager.list_non_archived(mock_db)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_handles_workspaces_without_is_archived_field(self, mock_db):
        """Verify backward compat: workspaces without is_archived are treated as non-archived."""
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces = {
            "ws-legacy": {
                "id": "ws-legacy",
                "name": "LegacyWS",
                "is_default": False,
                "created_at": "2024-01-01T00:00:00",
            },
        }

        result = await manager.list_non_archived(mock_db)

        assert len(result) == 1
        assert result[0]["id"] == "ws-legacy"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_workspaces(self, mock_db):
        """Verify empty list when no workspaces exist."""
        manager = SwarmWorkspaceManager()

        result = await manager.list_non_archived(mock_db)

        assert result == []


class TestDeleteWorkspace:
    """Tests for delete() method.

    Validates: Requirements 1.2, 2.5
    """

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with swarm_workspaces table including delete support."""
        class MockSwarmWorkspacesTable:
            def __init__(self):
                self.workspaces = {}

            async def get(self, workspace_id):
                return self.workspaces.get(workspace_id)

            async def delete(self, workspace_id):
                if workspace_id in self.workspaces:
                    del self.workspaces[workspace_id]
                    return True
                return False

            async def list(self):
                return list(self.workspaces.values())

        class MockDB:
            def __init__(self):
                self._swarm_workspaces = MockSwarmWorkspacesTable()

            @property
            def swarm_workspaces(self):
                return self._swarm_workspaces

        return MockDB()

    @pytest.mark.asyncio
    async def test_delete_custom_workspace_succeeds(self, mock_db):
        """Verify deleting a non-default workspace returns True and removes it.

        Validates: Requirement 2.5
        """
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces["ws-custom"] = {
            "id": "ws-custom",
            "name": "TestWS",
            "is_default": False,
        }

        result = await manager.delete("ws-custom", mock_db)

        assert result is True
        assert "ws-custom" not in mock_db._swarm_workspaces.workspaces

    @pytest.mark.asyncio
    async def test_delete_default_workspace_raises_permission_error(self, mock_db):
        """Verify deleting SwarmWS (is_default=true) raises PermissionError.

        Validates: Requirement 1.2
        """
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces["ws-default"] = {
            "id": "ws-default",
            "name": "SwarmWS",
            "is_default": True,
        }

        with pytest.raises(PermissionError, match="Cannot delete the default workspace"):
            await manager.delete("ws-default", mock_db)

        # Workspace should still exist
        assert "ws-default" in mock_db._swarm_workspaces.workspaces

    @pytest.mark.asyncio
    async def test_delete_nonexistent_workspace_raises_value_error(self, mock_db):
        """Verify deleting a non-existent workspace raises ValueError."""
        manager = SwarmWorkspaceManager()

        with pytest.raises(ValueError, match="Workspace not found"):
            await manager.delete("ws-nonexistent", mock_db)

    @pytest.mark.asyncio
    async def test_delete_does_not_affect_other_workspaces(self, mock_db):
        """Verify deleting one workspace does not affect others."""
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces["ws-1"] = {
            "id": "ws-1",
            "name": "WS1",
            "is_default": False,
        }
        mock_db._swarm_workspaces.workspaces["ws-2"] = {
            "id": "ws-2",
            "name": "WS2",
            "is_default": False,
        }

        await manager.delete("ws-1", mock_db)

        assert "ws-1" not in mock_db._swarm_workspaces.workspaces
        assert "ws-2" in mock_db._swarm_workspaces.workspaces


class TestListAll:
    """Tests for list_all() method.

    Validates: Requirements 1.1, 36.3
    """

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with swarm_workspaces table."""
        class MockSwarmWorkspacesTable:
            def __init__(self):
                self.workspaces = {}

            async def list(self):
                return list(self.workspaces.values())

        class MockDB:
            def __init__(self):
                self._swarm_workspaces = MockSwarmWorkspacesTable()

            @property
            def swarm_workspaces(self):
                return self._swarm_workspaces

        return MockDB()

    @pytest.mark.asyncio
    async def test_default_workspace_is_first(self, mock_db):
        """Verify SwarmWS (is_default=true) is always first in the list.

        Validates: Requirement 1.1
        """
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces = {
            "ws-custom": {
                "id": "ws-custom",
                "name": "CustomWS",
                "is_default": False,
                "is_archived": 0,
                "created_at": "2024-06-01T00:00:00",
            },
            "ws-default": {
                "id": "ws-default",
                "name": "SwarmWS",
                "is_default": True,
                "is_archived": 0,
                "created_at": "2024-01-01T00:00:00",
            },
        }

        result = await manager.list_all(mock_db)

        assert len(result) == 2
        assert result[0]["id"] == "ws-default"
        assert result[0]["is_default"] is True

    @pytest.mark.asyncio
    async def test_excludes_archived_by_default(self, mock_db):
        """Verify archived workspaces are excluded when include_archived=False.

        Validates: Requirement 36.3
        """
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces = {
            "ws-active": {
                "id": "ws-active",
                "name": "ActiveWS",
                "is_default": False,
                "is_archived": 0,
                "created_at": "2024-01-01T00:00:00",
            },
            "ws-archived": {
                "id": "ws-archived",
                "name": "ArchivedWS",
                "is_default": False,
                "is_archived": 1,
                "created_at": "2024-01-02T00:00:00",
            },
        }

        result = await manager.list_all(mock_db)

        assert len(result) == 1
        assert result[0]["id"] == "ws-active"

    @pytest.mark.asyncio
    async def test_includes_archived_when_requested(self, mock_db):
        """Verify archived workspaces are included when include_archived=True."""
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces = {
            "ws-active": {
                "id": "ws-active",
                "name": "ActiveWS",
                "is_default": False,
                "is_archived": 0,
                "created_at": "2024-01-01T00:00:00",
            },
            "ws-archived": {
                "id": "ws-archived",
                "name": "ArchivedWS",
                "is_default": False,
                "is_archived": 1,
                "created_at": "2024-01-02T00:00:00",
            },
        }

        result = await manager.list_all(mock_db, include_archived=True)

        assert len(result) == 2
        ids = [ws["id"] for ws in result]
        assert "ws-active" in ids
        assert "ws-archived" in ids

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_workspaces(self, mock_db):
        """Verify empty list when no workspaces exist."""
        manager = SwarmWorkspaceManager()

        result = await manager.list_all(mock_db)

        assert result == []

    @pytest.mark.asyncio
    async def test_sort_order_default_first_then_created_at(self, mock_db):
        """Verify sort: default first, then by created_at descending."""
        manager = SwarmWorkspaceManager()
        mock_db._swarm_workspaces.workspaces = {
            "ws-old": {
                "id": "ws-old",
                "name": "OldWS",
                "is_default": False,
                "is_archived": 0,
                "created_at": "2024-01-01T00:00:00",
            },
            "ws-new": {
                "id": "ws-new",
                "name": "NewWS",
                "is_default": False,
                "is_archived": 0,
                "created_at": "2024-06-01T00:00:00",
            },
            "ws-default": {
                "id": "ws-default",
                "name": "SwarmWS",
                "is_default": True,
                "is_archived": 0,
                "created_at": "2024-03-01T00:00:00",
            },
        }

        result = await manager.list_all(mock_db)

        assert result[0]["id"] == "ws-default"
        # Non-default sorted by created_at ascending (string sort)
        assert result[1]["id"] == "ws-old"
        assert result[2]["id"] == "ws-new"


class TestMigrateDefaultWorkspacePath:
    """Unit tests for _migrate_default_workspace_path() edge cases.

    Validates: Requirements 2.1, 2.3, 2.4, 2.5
    """

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

    def test_default_workspace_config_file_path_is_flat(self):
        """Verify DEFAULT_WORKSPACE_CONFIG['file_path'] equals '{app_data_dir}/SwarmWS'.

        Validates: Requirement 2.1
        """
        assert SwarmWorkspaceManager.DEFAULT_WORKSPACE_CONFIG["file_path"] == "{app_data_dir}/SwarmWS"

    @pytest.mark.asyncio
    async def test_old_path_exists_new_does_not_moves(self, tmp_path, mock_db):
        """When old path exists and new does not, contents are moved to new path.

        Validates: Requirements 2.3, 2.4
        """
        old_path = tmp_path / "swarm-workspaces" / "SwarmWS"
        new_path = tmp_path / "SwarmWS"

        # Create old path with a file
        old_path.mkdir(parents=True)
        (old_path / "test_file.txt").write_text("hello")

        manager = SwarmWorkspaceManager()
        workspace = {
            "id": "ws-1",
            "name": "SwarmWS",
            "file_path": "{app_data_dir}/swarm-workspaces/SwarmWS",
            "is_default": True,
        }

        def fake_expand(fp):
            return fp.replace("{app_data_dir}", str(tmp_path))

        from unittest.mock import patch
        with patch.object(manager, "expand_path", side_effect=fake_expand):
            await manager._migrate_default_workspace_path(workspace, mock_db)

        # Old path should be gone, new path should have the file
        assert not old_path.exists()
        assert new_path.exists()
        assert (new_path / "test_file.txt").read_text() == "hello"

    @pytest.mark.asyncio
    async def test_old_path_exists_new_does_not_updates_db(self, tmp_path, mock_db):
        """When old path is moved, DB record is updated to new file_path.

        Validates: Requirements 2.3, 2.5
        """
        old_path = tmp_path / "swarm-workspaces" / "SwarmWS"
        old_path.mkdir(parents=True)
        (old_path / "data.txt").write_text("content")

        manager = SwarmWorkspaceManager()
        workspace = {
            "id": "ws-1",
            "name": "SwarmWS",
            "file_path": "{app_data_dir}/swarm-workspaces/SwarmWS",
            "is_default": True,
        }

        def fake_expand(fp):
            return fp.replace("{app_data_dir}", str(tmp_path))

        from unittest.mock import patch
        with patch.object(manager, "expand_path", side_effect=fake_expand):
            await manager._migrate_default_workspace_path(workspace, mock_db)

        # DB should have the new path pattern
        stored = mock_db._swarm_workspaces.default_workspace
        assert stored is not None
        assert stored["file_path"] == "{app_data_dir}/SwarmWS"

    @pytest.mark.asyncio
    async def test_both_paths_exist_keeps_new_logs_warning(self, tmp_path, mock_db, caplog):
        """When both old and new paths exist, new is kept and warning is logged.

        Validates: Requirements 2.4, 2.5
        """
        old_path = tmp_path / "swarm-workspaces" / "SwarmWS"
        new_path = tmp_path / "SwarmWS"

        old_path.mkdir(parents=True)
        (old_path / "old_file.txt").write_text("old")
        new_path.mkdir(parents=True)
        (new_path / "new_file.txt").write_text("new")

        manager = SwarmWorkspaceManager()
        workspace = {
            "id": "ws-1",
            "name": "SwarmWS",
            "file_path": "{app_data_dir}/swarm-workspaces/SwarmWS",
            "is_default": True,
        }

        def fake_expand(fp):
            return fp.replace("{app_data_dir}", str(tmp_path))

        import logging
        from unittest.mock import patch
        with caplog.at_level(logging.WARNING), \
             patch.object(manager, "expand_path", side_effect=fake_expand):
            await manager._migrate_default_workspace_path(workspace, mock_db)

        # New path kept with its original content
        assert (new_path / "new_file.txt").read_text() == "new"
        # Old path left untouched for manual cleanup
        assert old_path.exists()
        assert (old_path / "old_file.txt").read_text() == "old"
        # Warning was logged
        assert any("Both old" in msg and "new" in msg for msg in caplog.messages)
        # DB still updated to new path
        stored = mock_db._swarm_workspaces.default_workspace
        assert stored["file_path"] == "{app_data_dir}/SwarmWS"

    @pytest.mark.asyncio
    async def test_neither_path_exists_updates_db_no_crash(self, tmp_path, mock_db):
        """When neither old nor new path exists, DB is updated without error.

        Validates: Requirements 2.3, 2.5
        """
        manager = SwarmWorkspaceManager()
        workspace = {
            "id": "ws-1",
            "name": "SwarmWS",
            "file_path": "{app_data_dir}/swarm-workspaces/SwarmWS",
            "is_default": True,
        }

        def fake_expand(fp):
            return fp.replace("{app_data_dir}", str(tmp_path))

        from unittest.mock import patch
        with patch.object(manager, "expand_path", side_effect=fake_expand):
            await manager._migrate_default_workspace_path(workspace, mock_db)

        # Neither path should exist on disk
        assert not (tmp_path / "swarm-workspaces" / "SwarmWS").exists()
        assert not (tmp_path / "SwarmWS").exists()
        # DB record updated to new path
        stored = mock_db._swarm_workspaces.default_workspace
        assert stored is not None
        assert stored["file_path"] == "{app_data_dir}/SwarmWS"
