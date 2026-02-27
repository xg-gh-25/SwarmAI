"""Unit and property-based tests for SwarmWorkspaceManager.

This module contains both unit tests and Hypothesis property-based tests for
``SwarmWorkspaceManager`` in ``backend/core/swarm_workspace_manager.py``.

Unit tests cover:
- Constants (FOLDER_STRUCTURE, DEFAULT_WORKSPACE_CONFIG, etc.)
- ``validate_path()`` and ``expand_path()`` methods
- ``create_folder_structure()`` method (new hierarchical layout)
- ``read_context_files()`` backward-compat method
- ``ensure_default_workspace()`` with workspace_config DB interface
- ``is_system_managed()`` for Knowledge/Memory path recognition
- ``verify_integrity()`` for Knowledge/Memory recreation
- Sample memory content creation during first-time init

Property-based tests (Hypothesis):
- ``TestInitializationIdempotence`` — Property 3: running
  ``ensure_default_workspace()`` twice produces an equivalent filesystem
  structure, preserving user files and not overwriting existing content.

**Validates: Requirements 2.1, 2.4, 2.5, 3.2, 8.1, 25.7, 29.1, 30.1, 31.2, 32.1, 32.2, 32.3**
"""
import os
import pytest
import tempfile
import shutil
from pathlib import Path
from core.swarm_workspace_manager import (
    SwarmWorkspaceManager,
    swarm_workspace_manager,
    FOLDER_STRUCTURE,
    SYSTEM_MANAGED_ROOT_FILES,
    SYSTEM_MANAGED_SECTION_FILES,
    SYSTEM_MANAGED_FOLDERS,
    DEFAULT_WORKSPACE_CONFIG,
)


class TestSwarmWorkspaceManagerConstants:
    """Tests for SwarmWorkspaceManager constants after single-workspace refactor."""

    def test_folder_structure_contains_required_directories(self):
        """Verify FOLDER_STRUCTURE contains all required directories.

        Validates: Requirements 2.1, 2.3
        """
        required_dirs = [
            "Knowledge",
            "Knowledge/Knowledge Base",
            "Knowledge/Notes",
            "Knowledge/Memory",
            "Projects",
        ]
        assert SwarmWorkspaceManager.FOLDER_STRUCTURE == required_dirs

    def test_default_workspace_config_has_required_fields(self):
        """Verify DEFAULT_WORKSPACE_CONFIG has all required fields."""
        config = SwarmWorkspaceManager.DEFAULT_WORKSPACE_CONFIG
        assert config["name"] == "SwarmWS"
        assert config["file_path"] == "{app_data_dir}/SwarmWS"
        assert "icon" in config

    def test_system_managed_folders_match_folder_structure(self):
        """Verify SYSTEM_MANAGED_FOLDERS covers all FOLDER_STRUCTURE entries."""
        assert SYSTEM_MANAGED_FOLDERS == set(FOLDER_STRUCTURE)

    def test_system_managed_root_files(self):
        """Verify SYSTEM_MANAGED_ROOT_FILES has expected files."""
        expected = {"system-prompts.md", "context-L0.md", "context-L1.md"}
        assert SYSTEM_MANAGED_ROOT_FILES == expected

    def test_system_managed_section_files(self):
        """Verify SYSTEM_MANAGED_SECTION_FILES has context files for Knowledge and Projects."""
        expected = {
            "Knowledge/context-L0.md", "Knowledge/context-L1.md",
            "Knowledge/index.md", "Knowledge/knowledge-map.md",
            "Projects/context-L0.md", "Projects/context-L1.md",
        }
        assert SYSTEM_MANAGED_SECTION_FILES == expected

    def test_depth_limits_has_all_sections(self):
        """Verify DEPTH_LIMITS covers all section types."""
        limits = SwarmWorkspaceManager.DEPTH_LIMITS
        assert "knowledge" in limits
        assert "project_system" in limits
        assert "project_user" in limits


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
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("/usr/local/bin") is True
        assert manager.validate_path("/home/user/workspace") is True

    def test_valid_tilde_path(self):
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("~/Desktop/SwarmAI") is True
        assert manager.validate_path("~/workspace") is True
        assert manager.validate_path("~") is True

    def test_reject_path_traversal_double_dot(self):
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("/home/user/../etc/passwd") is False
        assert manager.validate_path("~/Desktop/../.ssh") is False
        assert manager.validate_path("..") is False
        assert manager.validate_path("../secret") is False

    def test_reject_relative_path(self):
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("relative/path") is False
        assert manager.validate_path("workspace") is False
        assert manager.validate_path("./current") is False

    def test_reject_empty_path(self):
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("") is False

    def test_reject_path_with_embedded_traversal(self):
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("/home/user/workspace/../../../etc") is False
        assert manager.validate_path("~/safe/../../unsafe") is False


class TestGlobalInstance:
    """Tests for the global swarm_workspace_manager instance."""

    def test_global_instance_exists(self):
        assert swarm_workspace_manager is not None
        assert isinstance(swarm_workspace_manager, SwarmWorkspaceManager)

    def test_global_instance_has_folder_structure(self):
        assert len(swarm_workspace_manager.FOLDER_STRUCTURE) == 5


class TestKnowledgeMemorySystemManaged:
    """Tests for Knowledge/Memory path recognition in is_system_managed().

    Validates: Requirements 2.4, 3.2, 3.6, 3.7
    """

    def test_knowledge_memory_is_system_managed(self):
        """Verify Knowledge/Memory is recognized as system-managed."""
        manager = SwarmWorkspaceManager()
        assert manager.is_system_managed("Knowledge/Memory") is True

    def test_knowledge_memory_in_system_managed_folders(self):
        """Verify Knowledge/Memory is in SYSTEM_MANAGED_FOLDERS set."""
        assert "Knowledge/Memory" in SYSTEM_MANAGED_FOLDERS

    def test_knowledge_memory_in_folder_structure(self):
        """Verify Knowledge/Memory is in FOLDER_STRUCTURE list."""
        assert "Knowledge/Memory" in FOLDER_STRUCTURE

    def test_knowledge_memory_user_files_not_system_managed(self):
        """Verify user files inside Knowledge/Memory/ are not system-managed."""
        manager = SwarmWorkspaceManager()
        assert manager.is_system_managed("Knowledge/Memory/my-notes.md") is False
        assert manager.is_system_managed("Knowledge/Memory/subfolder") is False


class TestCreateFolderStructure:
    """Tests for create_folder_structure() method (new hierarchical layout).

    Validates: Requirements 2.1, 2.2, 2.4, 3.2, 8.1, 8.2, 30.1
    """

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.mark.asyncio
    async def test_creates_all_subdirectories(self, temp_dir):
        """Verify all required subdirectories are created."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "test_workspace")

        await manager.create_folder_structure(workspace_path)

        for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
            folder_path = os.path.join(workspace_path, folder_name)
            assert os.path.isdir(folder_path), f"Directory {folder_name} should exist"

    @pytest.mark.asyncio
    async def test_creates_knowledge_memory_directory(self, temp_dir):
        """Verify Knowledge/Memory/ nested directory is created.

        Validates: Requirements 2.1, 2.4, 3.2
        """
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "test_workspace")

        await manager.create_folder_structure(workspace_path)

        memory_path = os.path.join(workspace_path, "Knowledge", "Memory")
        assert os.path.isdir(memory_path), "Knowledge/Memory/ directory should exist"

    @pytest.mark.asyncio
    async def test_creates_root_directory_if_not_exists(self, temp_dir):
        """Verify root directory is created if it doesn't exist."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "new_workspace")
        assert not os.path.exists(workspace_path)

        await manager.create_folder_structure(workspace_path)

        assert os.path.isdir(workspace_path)

    @pytest.mark.asyncio
    async def test_creates_nested_root_directory(self, temp_dir):
        """Verify deeply nested root directories are created."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "a", "b", "c", "workspace")

        await manager.create_folder_structure(workspace_path)

        assert os.path.isdir(workspace_path)
        for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
            folder_path = os.path.join(workspace_path, folder_name)
            assert os.path.isdir(folder_path)

    @pytest.mark.asyncio
    async def test_idempotent_folder_creation(self, temp_dir):
        """Verify calling create_folder_structure twice doesn't fail."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "test_workspace")

        await manager.create_folder_structure(workspace_path)
        await manager.create_folder_structure(workspace_path)

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
        unique_name = f"swarm_test_{os.getpid()}"
        workspace_path = f"~/tmp_swarm_test/{unique_name}"
        expanded_path = os.path.expanduser(workspace_path)

        try:
            await manager.create_folder_structure(workspace_path)

            assert os.path.isdir(expanded_path)
            for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
                folder_path = os.path.join(expanded_path, folder_name)
                assert os.path.isdir(folder_path)
        finally:
            parent_dir = os.path.expanduser("~/tmp_swarm_test")
            if os.path.exists(parent_dir):
                shutil.rmtree(parent_dir)

    @pytest.mark.asyncio
    async def test_creates_root_level_system_files(self, temp_dir):
        """Verify root-level system files are created."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "test_workspace")

        await manager.create_folder_structure(workspace_path)

        for filename in SYSTEM_MANAGED_ROOT_FILES:
            file_path = os.path.join(workspace_path, filename)
            assert os.path.isfile(file_path), f"System file {filename} should exist"

    @pytest.mark.asyncio
    async def test_creates_section_level_context_files(self, temp_dir):
        """Verify section-level context files are created for Artifacts, Notebooks, Projects."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "test_workspace")

        await manager.create_folder_structure(workspace_path)

        for section_file in SYSTEM_MANAGED_SECTION_FILES:
            file_path = os.path.join(workspace_path, section_file)
            assert os.path.isfile(file_path), f"Section file {section_file} should exist"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_files(self, temp_dir):
        """Verify existing files are not overwritten on second call."""
        manager = SwarmWorkspaceManager()
        workspace_path = os.path.join(temp_dir, "test_workspace")

        await manager.create_folder_structure(workspace_path)

        # Modify a system file
        system_file = os.path.join(workspace_path, "system-prompts.md")
        custom_content = "# Custom content"
        with open(system_file, "w") as f:
            f.write(custom_content)

        # Run again
        await manager.create_folder_structure(workspace_path)

        # File should retain custom content
        with open(system_file, "r") as f:
            assert f.read() == custom_content


class TestReadContextFiles:
    """Tests for read_context_files() backward-compat method.

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
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.mark.asyncio
    async def test_reads_context_file(self, temp_workspace):
        """Verify context.md is read correctly."""
        manager = SwarmWorkspaceManager()
        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        with open(context_path, "w", encoding="utf-8") as f:
            f.write("# Test Context")

        result = await manager.read_context_files(temp_workspace)
        assert "# Test Context" in result

    @pytest.mark.asyncio
    async def test_reads_compressed_context_file(self, temp_workspace):
        """Verify compressed-context.md is read correctly."""
        manager = SwarmWorkspaceManager()
        compressed_path = os.path.join(temp_workspace, "ContextFiles", "compressed-context.md")
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write("# Compressed Context")

        result = await manager.read_context_files(temp_workspace)
        assert "# Compressed Context" in result

    @pytest.mark.asyncio
    async def test_combines_both_context_files(self, temp_workspace):
        """Verify both context files are combined."""
        manager = SwarmWorkspaceManager()
        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        compressed_path = os.path.join(temp_workspace, "ContextFiles", "compressed-context.md")
        with open(context_path, "w", encoding="utf-8") as f:
            f.write("Main context")
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write("Compressed context")

        result = await manager.read_context_files(temp_workspace)
        assert "Main context" in result
        assert "Compressed context" in result

    @pytest.mark.asyncio
    async def test_handles_missing_context_file(self, temp_workspace):
        """Verify graceful handling when context.md is missing."""
        manager = SwarmWorkspaceManager()
        compressed_path = os.path.join(temp_workspace, "ContextFiles", "compressed-context.md")
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write("Only compressed")

        result = await manager.read_context_files(temp_workspace)
        assert "Only compressed" in result

    @pytest.mark.asyncio
    async def test_handles_missing_compressed_context(self, temp_workspace):
        """Verify graceful handling when compressed-context.md is missing."""
        manager = SwarmWorkspaceManager()
        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        with open(context_path, "w", encoding="utf-8") as f:
            f.write("Only main")

        result = await manager.read_context_files(temp_workspace)
        assert "Only main" in result

    @pytest.mark.asyncio
    async def test_handles_both_files_missing(self, temp_workspace):
        """Verify empty string returned when both files are missing."""
        manager = SwarmWorkspaceManager()
        result = await manager.read_context_files(temp_workspace)
        assert result == ""

    @pytest.mark.asyncio
    async def test_handles_missing_context_directory(self):
        """Verify graceful handling when ContextFiles dir doesn't exist."""
        manager = SwarmWorkspaceManager()
        temp_path = tempfile.mkdtemp()
        try:
            result = await manager.read_context_files(temp_path)
            assert result == ""
        finally:
            shutil.rmtree(temp_path)

    @pytest.mark.asyncio
    async def test_handles_empty_context_files(self, temp_workspace):
        """Verify empty files result in empty string."""
        manager = SwarmWorkspaceManager()
        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        compressed_path = os.path.join(temp_workspace, "ContextFiles", "compressed-context.md")
        with open(context_path, "w", encoding="utf-8") as f:
            f.write("")
        with open(compressed_path, "w", encoding="utf-8") as f:
            f.write("")

        result = await manager.read_context_files(temp_workspace)
        assert result == ""

    @pytest.mark.asyncio
    async def test_handles_tilde_path(self):
        """Verify ~ paths are expanded correctly."""
        manager = SwarmWorkspaceManager()
        unique_name = f"swarm_context_test_{os.getpid()}"
        workspace_path = f"~/tmp_swarm_test/{unique_name}"
        expanded_path = os.path.expanduser(workspace_path)
        context_path = os.path.join(expanded_path, "ContextFiles")

        try:
            os.makedirs(context_path, exist_ok=True)
            ctx_file = os.path.join(context_path, "context.md")
            with open(ctx_file, "w", encoding="utf-8") as f:
                f.write("# Tilde Test")

            result = await manager.read_context_files(workspace_path)
            assert "# Tilde Test" in result
        finally:
            parent_dir = os.path.expanduser("~/tmp_swarm_test")
            if os.path.exists(parent_dir):
                shutil.rmtree(parent_dir)

    @pytest.mark.asyncio
    async def test_preserves_file_content_formatting(self, temp_workspace):
        """Verify file content formatting is preserved."""
        manager = SwarmWorkspaceManager()
        content = "# Header\n\n## Section\n\n- Item 1\n- Item 2\n\n```python\nprint('hello')\n```\n"
        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        with open(context_path, "w", encoding="utf-8") as f:
            f.write(content)

        result = await manager.read_context_files(temp_workspace)
        assert content in result

    @pytest.mark.asyncio
    async def test_handles_unicode_content(self, temp_workspace):
        """Verify unicode content is handled correctly."""
        manager = SwarmWorkspaceManager()
        content = "# 日本語テスト\n\nこんにちは世界 🌍\n\nÉmoji: 🎉🚀"
        context_path = os.path.join(temp_workspace, "ContextFiles", "context.md")
        with open(context_path, "w", encoding="utf-8") as f:
            f.write(content)

        result = await manager.read_context_files(temp_workspace)
        assert content in result


class TestEnsureDefaultWorkspace:
    """Tests for ensure_default_workspace() with workspace_config DB interface.

    Validates: Requirements 1.1, 1.2, 2.5, 22.3, 31.1, 31.2
    """

    @pytest.fixture
    def temp_dir(self):
        temp_path = tempfile.mkdtemp()
        yield temp_path
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with workspace_config table."""
        class MockWorkspaceConfigTable:
            def __init__(self):
                self.config = None

            async def get_config(self):
                return self.config

            async def put(self, item):
                self.config = item
                return item

        class MockDB:
            def __init__(self):
                self._workspace_config = MockWorkspaceConfigTable()

            @property
            def workspace_config(self):
                return self._workspace_config

        return MockDB()

    @pytest.mark.asyncio
    async def test_creates_default_workspace_when_not_exists(self, mock_db, temp_dir, monkeypatch):
        """Verify default workspace is created when no config exists."""
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = test_path

        try:
            result = await manager.ensure_default_workspace(mock_db)
            assert result is not None
            assert result["name"] == "SwarmWS"
            assert result["id"] == "swarmws"
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)

    @pytest.mark.asyncio
    async def test_returns_existing_workspace_config(self, mock_db, temp_dir):
        """Verify existing workspace config is returned without creating new one."""
        manager = SwarmWorkspaceManager()
        ws_path = os.path.join(temp_dir, "SwarmWS")
        # Pre-create the workspace structure so verify_integrity doesn't fail
        os.makedirs(ws_path, exist_ok=True)
        existing = {
            "id": "swarmws",
            "name": "SwarmWS",
            "file_path": ws_path,
            "icon": "🏠",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }
        mock_db._workspace_config.config = existing

        result = await manager.ensure_default_workspace(mock_db)
        assert result["id"] == "swarmws"
        assert result["name"] == "SwarmWS"

    @pytest.mark.asyncio
    async def test_default_workspace_has_correct_name(self, mock_db, temp_dir):
        """Verify created default workspace has correct name."""
        manager = SwarmWorkspaceManager()
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = os.path.join(temp_dir, "SwarmWS")

        try:
            result = await manager.ensure_default_workspace(mock_db)
            assert result["name"] == "SwarmWS"
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)

    @pytest.mark.asyncio
    async def test_creates_folder_structure_for_default_workspace(self, mock_db, temp_dir):
        """Verify folder structure is created for default workspace."""
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = test_path

        try:
            await manager.ensure_default_workspace(mock_db)
            assert os.path.isdir(test_path)
            for folder_name in SwarmWorkspaceManager.FOLDER_STRUCTURE:
                folder_path = os.path.join(test_path, folder_name)
                assert os.path.isdir(folder_path), f"Directory {folder_name} should exist"
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)

    @pytest.mark.asyncio
    async def test_creates_system_files_for_default_workspace(self, mock_db, temp_dir):
        """Verify system files are created for default workspace."""
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = test_path

        try:
            await manager.ensure_default_workspace(mock_db)
            for filename in SYSTEM_MANAGED_ROOT_FILES:
                file_path = os.path.join(test_path, filename)
                assert os.path.isfile(file_path), f"System file {filename} should exist"
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)

    @pytest.mark.asyncio
    async def test_default_workspace_stored_in_database(self, mock_db, temp_dir):
        """Verify default workspace config is stored in database."""
        manager = SwarmWorkspaceManager()
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = os.path.join(temp_dir, "SwarmWS")

        try:
            result = await manager.ensure_default_workspace(mock_db)
            stored = mock_db._workspace_config.config
            assert stored is not None
            assert stored["name"] == "SwarmWS"
            assert stored["id"] == "swarmws"
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)

    @pytest.mark.asyncio
    async def test_default_workspace_has_timestamps(self, mock_db, temp_dir):
        """Verify created default workspace has created_at and updated_at timestamps."""
        manager = SwarmWorkspaceManager()
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = os.path.join(temp_dir, "SwarmWS")

        try:
            result = await manager.ensure_default_workspace(mock_db)
            assert "created_at" in result
            assert "updated_at" in result
            assert result["created_at"] is not None
            assert result["updated_at"] is not None
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)

    @pytest.mark.asyncio
    async def test_default_workspace_has_all_required_fields(self, mock_db, temp_dir):
        """Verify created default workspace has all required fields."""
        manager = SwarmWorkspaceManager()
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = os.path.join(temp_dir, "SwarmWS")

        try:
            result = await manager.ensure_default_workspace(mock_db)
            required_fields = ["id", "name", "file_path", "icon", "created_at", "updated_at"]
            for field in required_fields:
                assert field in result, f"Missing required field: {field}"
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)

    @pytest.mark.asyncio
    async def test_idempotent_ensure_default_workspace(self, mock_db, temp_dir):
        """Verify calling ensure_default_workspace twice returns same workspace."""
        manager = SwarmWorkspaceManager()
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = os.path.join(temp_dir, "SwarmWS")

        try:
            result1 = await manager.ensure_default_workspace(mock_db)
            result2 = await manager.ensure_default_workspace(mock_db)
            assert result1["id"] == result2["id"]
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)


class TestVerifyIntegrityMemory:
    """Tests for verify_integrity() recreating Knowledge/Memory/ if missing.

    Validates: Requirements 29.1, 29.2, 30.1
    """

    @pytest.fixture
    def temp_dir(self):
        temp_path = tempfile.mkdtemp()
        yield temp_path
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with workspace_config table."""
        class MockWorkspaceConfigTable:
            def __init__(self):
                self.config = None

            async def get_config(self):
                return self.config

            async def put(self, item):
                self.config = item
                return item

        class MockDB:
            def __init__(self):
                self._workspace_config = MockWorkspaceConfigTable()

            @property
            def workspace_config(self):
                return self._workspace_config

        return MockDB()

    @pytest.mark.asyncio
    async def test_verify_integrity_recreates_knowledge_memory(self, mock_db, temp_dir):
        """Verify Knowledge/Memory/ is recreated by verify_integrity() if missing.

        Validates: Requirements 29.1, 29.2
        """
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = test_path

        try:
            # First init — creates full structure
            await manager.ensure_default_workspace(mock_db)
            memory_path = os.path.join(test_path, "Knowledge", "Memory")
            assert os.path.isdir(memory_path), "Knowledge/Memory/ should exist after init"

            # Delete Knowledge/Memory/
            shutil.rmtree(memory_path)
            assert not os.path.exists(memory_path)

            # verify_integrity should recreate it
            recreated = await manager.verify_integrity(test_path)
            assert os.path.isdir(memory_path), (
                "Knowledge/Memory/ should be recreated by verify_integrity()"
            )
            assert "Knowledge/Memory" in recreated
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)

    @pytest.mark.asyncio
    async def test_verify_integrity_does_not_overwrite_memory_content(self, mock_db, temp_dir):
        """Verify verify_integrity() does not overwrite existing Memory/ content.

        Validates: Requirements 29.2, 30.1
        """
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = test_path

        try:
            await manager.ensure_default_workspace(mock_db)

            # Add a user file inside Knowledge/Memory/
            user_file = os.path.join(test_path, "Knowledge", "Memory", "my-prefs.md")
            with open(user_file, "w") as f:
                f.write("# My Preferences\nCustom content.\n")

            # Re-run verify_integrity
            await manager.verify_integrity(test_path)

            # User file should be preserved
            assert os.path.isfile(user_file)
            with open(user_file, "r") as f:
                assert f.read() == "# My Preferences\nCustom content.\n"
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)


class TestSampleMemoryContent:
    """Tests for sample memory content creation during first-time init.

    Validates: Requirements 2.4, 23.4
    """

    @pytest.fixture
    def temp_dir(self):
        temp_path = tempfile.mkdtemp()
        yield temp_path
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    @pytest.fixture
    def mock_db(self):
        """Create a mock database with workspace_config table."""
        class MockWorkspaceConfigTable:
            def __init__(self):
                self.config = None

            async def get_config(self):
                return self.config

            async def put(self, item):
                self.config = item
                return item

        class MockDB:
            def __init__(self):
                self._workspace_config = MockWorkspaceConfigTable()

            @property
            def workspace_config(self):
                return self._workspace_config

        return MockDB()

    @pytest.mark.asyncio
    async def test_sample_memory_content_created_on_first_init(self, mock_db, temp_dir):
        """Verify sample memory file is created during first-time initialization.

        Validates: Requirements 23.4
        """
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = test_path

        try:
            await manager.ensure_default_workspace(mock_db)

            sample_memory = os.path.join(
                test_path, "Knowledge", "Memory", "communication-style.md"
            )
            assert os.path.isfile(sample_memory), (
                "Sample memory file communication-style.md should exist after first init"
            )

            # Verify it has meaningful content
            with open(sample_memory, "r") as f:
                content = f.read()
            assert "Communication Style" in content
            assert len(content) > 50
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)

    @pytest.mark.asyncio
    async def test_sample_memory_not_overwritten_on_reinit(self, mock_db, temp_dir):
        """Verify sample memory file is not overwritten on re-initialization.

        Validates: Requirements 30.1
        """
        manager = SwarmWorkspaceManager()
        test_path = os.path.join(temp_dir, "SwarmWS")
        import core.swarm_workspace_manager as swm_mod
        original = swm_mod.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_mod.DEFAULT_WORKSPACE_CONFIG["file_path"] = test_path

        try:
            await manager.ensure_default_workspace(mock_db)

            # Modify the sample memory file
            sample_memory = os.path.join(
                test_path, "Knowledge", "Memory", "communication-style.md"
            )
            custom = "# Edited Memory\nUser-edited content.\n"
            with open(sample_memory, "w") as f:
                f.write(custom)

            # Re-init
            await manager.ensure_default_workspace(mock_db)

            # Content should be preserved
            with open(sample_memory, "r") as f:
                assert f.read() == custom
        finally:
            swm_mod.DEFAULT_WORKSPACE_CONFIG.update(original)


class TestExpandPathWithAppDataDir:
    """Tests for expand_path() with {app_data_dir} placeholder."""

    def test_expand_app_data_dir_placeholder(self, monkeypatch):
        """Verify {app_data_dir} is expanded to actual data directory."""
        manager = SwarmWorkspaceManager()
        from config import get_app_data_dir
        app_data = get_app_data_dir()

        result = manager.expand_path("{app_data_dir}/SwarmWS")
        expected = os.path.join(app_data, "SwarmWS")
        assert result == expected

    def test_expand_app_data_dir_only(self, monkeypatch):
        """Verify {app_data_dir} alone is expanded."""
        manager = SwarmWorkspaceManager()
        from config import get_app_data_dir
        app_data = str(get_app_data_dir())

        result = manager.expand_path("{app_data_dir}")
        assert result == app_data

    def test_expand_path_preserves_tilde_expansion(self):
        """Verify ~ expansion still works."""
        manager = SwarmWorkspaceManager()
        result = manager.expand_path("~/test")
        assert result == os.path.expanduser("~/test")

    def test_expand_path_handles_both_placeholders(self, monkeypatch):
        """Verify both ~ and {app_data_dir} are handled (only one should be used)."""
        manager = SwarmWorkspaceManager()
        from config import get_app_data_dir
        app_data = get_app_data_dir()

        result = manager.expand_path("{app_data_dir}/nested/path")
        assert result == os.path.join(app_data, "nested", "path")


class TestValidatePathWithAppDataDir:
    """Tests for validate_path() with {app_data_dir} placeholder."""

    def test_valid_app_data_dir_path(self):
        """Verify {app_data_dir} paths are accepted."""
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("{app_data_dir}/SwarmWS") is True
        assert manager.validate_path("{app_data_dir}") is True

    def test_reject_app_data_dir_with_path_traversal(self):
        """Verify {app_data_dir} paths with .. are rejected."""
        manager = SwarmWorkspaceManager()
        assert manager.validate_path("{app_data_dir}/../escape") is False


# ---------------------------------------------------------------------------
# Property-based test: Initialization Idempotence (Property 3)
# ---------------------------------------------------------------------------

from hypothesis import given, strategies as st, settings, HealthCheck
from database.sqlite import SQLiteDatabase
import core.swarm_workspace_manager as swm_module


# Hypothesis settings for property tests
_PBT_SETTINGS = settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# Strategy: generate a small set of user files to place in the workspace.
_safe_filename_chars = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=20,
)

_user_file_strategy = st.lists(
    st.tuples(
        # relative directory inside the workspace (pick from valid user locations)
        st.sampled_from([
            "Knowledge",
            "Knowledge/Knowledge Base",
            "Knowledge/Notes",
            "Knowledge/Memory",
            "Projects",
        ]),
        # filename
        _safe_filename_chars.map(lambda s: s + ".md"),
        # content
        st.text(min_size=0, max_size=200),
    ),
    min_size=0,
    max_size=5,
)


def _collect_all_files(root: Path) -> dict[str, str]:
    """Walk the workspace and return {relative_path: content} for all files."""
    result = {}
    for file_path in sorted(root.rglob("*")):
        if file_path.is_file():
            rel = str(file_path.relative_to(root))
            try:
                result[rel] = file_path.read_text(encoding="utf-8")
            except Exception:
                result[rel] = ""
    return result


def _collect_all_dirs(root: Path) -> set[str]:
    """Walk the workspace and return set of relative directory paths."""
    result = set()
    for dir_path in sorted(root.rglob("*")):
        if dir_path.is_dir():
            result.add(str(dir_path.relative_to(root)))
    return result


class TestInitializationIdempotence:
    """Property 3: Initialization Idempotence.

    *For any* valid SwarmWS state (including user-created files and modified
    system file content), running ``ensure_default_workspace()`` followed by
    a second ``ensure_default_workspace()`` shall produce an equivalent
    filesystem structure.

    Specifically:
    (a) no existing files are overwritten,
    (b) all user-managed items are preserved,
    (c) any missing system-managed items are recreated with default content,
    (d) the set of files after the second run equals the set after the first run.

    **Validates: Requirements 2.5, 25.7, 31.2, 32.1, 32.2, 32.3**
    """

    @given(user_files=_user_file_strategy)
    @_PBT_SETTINGS
    @pytest.mark.asyncio
    async def test_initialization_idempotence(
        self,
        user_files: list[tuple[str, str, str]],
        tmp_path_factory,
    ):
        """Two consecutive ensure_default_workspace() calls produce equivalent state.

        **Validates: Requirements 2.5, 25.7, 31.2, 32.1, 32.2, 32.3**
        """
        tmp_path = tmp_path_factory.mktemp("idempotence")
        workspace_path = str(tmp_path / "SwarmWS")
        db_path = str(tmp_path / "test.db")

        # Set up isolated DB
        db = SQLiteDatabase(db_path=db_path)
        await db.initialize()

        # Patch DEFAULT_WORKSPACE_CONFIG to use our temp path
        original_config = swm_module.DEFAULT_WORKSPACE_CONFIG.copy()
        swm_module.DEFAULT_WORKSPACE_CONFIG["file_path"] = workspace_path

        try:
            manager = SwarmWorkspaceManager()

            # ── First init ──────────────────────────────────────────────
            await manager.ensure_default_workspace(db)

            # Place user files into the workspace between the two inits
            root = Path(workspace_path)
            for section, filename, content in user_files:
                user_file = root / section / filename
                # Don't overwrite system files
                if not user_file.exists():
                    user_file.parent.mkdir(parents=True, exist_ok=True)
                    user_file.write_text(content, encoding="utf-8")

            # Snapshot state after first init + user files
            files_after_first = _collect_all_files(root)
            dirs_after_first = _collect_all_dirs(root)

            # ── Second init ─────────────────────────────────────────────
            await manager.ensure_default_workspace(db)

            # Snapshot state after second init
            files_after_second = _collect_all_files(root)
            dirs_after_second = _collect_all_dirs(root)

            # ── Assertions ──────────────────────────────────────────────

            # (d) Same file set after second run
            assert set(files_after_second.keys()) == set(files_after_first.keys()), (
                f"File set changed between inits.\n"
                f"Added: {set(files_after_second.keys()) - set(files_after_first.keys())}\n"
                f"Removed: {set(files_after_first.keys()) - set(files_after_second.keys())}"
            )

            # Same directory set
            assert dirs_after_second == dirs_after_first, (
                f"Directory set changed between inits.\n"
                f"Added: {dirs_after_second - dirs_after_first}\n"
                f"Removed: {dirs_after_first - dirs_after_second}"
            )

            # (a) No existing files overwritten — content unchanged
            for rel_path, content_before in files_after_first.items():
                content_after = files_after_second.get(rel_path)
                assert content_after == content_before, (
                    f"File '{rel_path}' was overwritten by second init.\n"
                    f"Before: {content_before[:100]!r}\n"
                    f"After:  {content_after[:100]!r}"
                )

            # (b) User files preserved with same content
            for section, filename, content in user_files:
                rel = f"{section}/{filename}"
                if rel in files_after_first:
                    assert rel in files_after_second, (
                        f"User file '{rel}' disappeared after second init"
                    )
                    assert files_after_second[rel] == files_after_first[rel], (
                        f"User file '{rel}' content changed after second init"
                    )

            # (c) System files still exist
            for sys_file in SYSTEM_MANAGED_ROOT_FILES:
                assert sys_file in files_after_second, (
                    f"System root file '{sys_file}' missing after second init"
                )
            for sys_file in SYSTEM_MANAGED_SECTION_FILES:
                assert sys_file in files_after_second, (
                    f"System section file '{sys_file}' missing after second init"
                )
            for folder in FOLDER_STRUCTURE:
                assert folder in dirs_after_second, (
                    f"System folder '{folder}' missing after second init"
                )

        finally:
            swm_module.DEFAULT_WORKSPACE_CONFIG.update(original_config)
