"""Unit and property-based tests for SwarmWorkspaceManager.

This module contains both unit tests and Hypothesis property-based tests for
``SwarmWorkspaceManager`` in ``backend/core/swarm_workspace_manager.py``.

Unit tests cover:
- Constants (FOLDER_STRUCTURE, DEFAULT_WORKSPACE_CONFIG, etc.)
- ``validate_path()`` and ``expand_path()`` methods
- ``create_folder_structure()`` method (minimal Knowledge/Projects layout)
- ``read_context_files()`` backward-compat method
- ``ensure_default_workspace()`` with workspace_config DB interface
- ``verify_integrity()`` for Knowledge/Projects recreation

Property-based tests (Hypothesis):
- ``TestInitializationIdempotence`` — Property 3: running
  ``ensure_default_workspace()`` twice produces an equivalent filesystem
  structure, preserving user files and not overwriting existing content.

**Validates: Requirements 2.1, 2.4, 2.5, 3.2, 8.1, 25.7, 29.1, 30.1, 31.2, 32.1, 32.2, 32.3**
"""
import os
import pytest
import subprocess
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch
from core.swarm_workspace_manager import (
    SwarmWorkspaceManager,
    swarm_workspace_manager,
    FOLDER_STRUCTURE,
    SYSTEM_MANAGED_ROOT_FILES,
    SYSTEM_MANAGED_SECTION_FILES,
    SYSTEM_MANAGED_FOLDERS,
    DEFAULT_WORKSPACE_CONFIG,
    GITIGNORE_CONTENT,
)


class TestSwarmWorkspaceManagerConstants:
    """Tests for SwarmWorkspaceManager constants after single-workspace refactor."""

    def test_folder_structure_contains_required_directories(self):
        """Verify FOLDER_STRUCTURE contains Knowledge, Projects, and Attachments.

        Validates: Requirements 1.1
        """
        required_dirs = [
            "Knowledge",
            "Projects",
            "Attachments",
            "Services",
        ]
        assert SwarmWorkspaceManager.FOLDER_STRUCTURE == required_dirs

    def test_default_workspace_config_has_required_fields(self):
        """Verify DEFAULT_WORKSPACE_CONFIG has all required fields."""
        config = SwarmWorkspaceManager.DEFAULT_WORKSPACE_CONFIG
        assert config["name"] == "SwarmWS"
        assert config["file_path"] == "{app_data_dir}/SwarmWS"
        assert "icon" in config

    def test_system_managed_folders_match_folder_structure(self):
        """Verify SYSTEM_MANAGED_FOLDERS covers FOLDER_STRUCTURE and Knowledge subdirs."""
        from core.swarm_workspace_manager import KNOWLEDGE_SUBDIRS
        expected = set(FOLDER_STRUCTURE) | {
            f"Knowledge/{sub}" for sub in KNOWLEDGE_SUBDIRS
        }
        assert SYSTEM_MANAGED_FOLDERS == expected

    def test_system_managed_root_files(self):
        """Verify SYSTEM_MANAGED_ROOT_FILES is empty (no system-managed root files)."""
        assert SYSTEM_MANAGED_ROOT_FILES == set()

    def test_system_managed_section_files(self):
        """Verify SYSTEM_MANAGED_SECTION_FILES is empty (no system-managed section files)."""
        assert SYSTEM_MANAGED_SECTION_FILES == set()

    def test_depth_limits_has_project_user(self):
        """Verify DEPTH_LIMITS has project_user limit."""
        from core.swarm_workspace_manager import DEPTH_LIMITS
        assert "project_user" in DEPTH_LIMITS


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
        assert len(swarm_workspace_manager.FOLDER_STRUCTURE) == 4


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

    @pytest.mark.asyncio
    async def test_ensure_default_project_writes_canonical_docs_under_2understanding(
        self, temp_dir
    ):
        """CREATE (fresh) must place the 4 canonical docs UNDER 2-understanding/,
        NOT at the project root — routed via ddd_write_path (six-section SSOT)."""
        from pathlib import Path
        manager = SwarmWorkspaceManager()
        root = Path(temp_dir) / "SwarmWS"
        (root / "Projects").mkdir(parents=True, exist_ok=True)

        await manager._ensure_default_project(root)

        proj = root / "Projects" / "SwarmAI"
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            assert (proj / "2-understanding" / doc).exists(), \
                f"{doc} should be created under 2-understanding/"
            assert not (proj / doc).exists(), \
                f"{doc} must NOT be created at project root (six-section violation)"

    @pytest.mark.asyncio
    async def test_ensure_default_project_does_not_restub_migrated_docs(self, temp_dir):
        """REGRESSION (run_1db3791d P0): for a MIGRATED DDD (canonical docs already
        under 2-understanding/, root empty), verify_integrity's _ensure_default_project
        must NOT re-create template STUBS at root. Pre-fix it used a bare
        `project_dir / filename` root write whose `if not exists()` guard missed the
        migrated docs and re-stubbed every startup. MUTATION: revert to the bare join
        → this test goes RED (root stub re-appears + real doc untouched)."""
        from pathlib import Path
        manager = SwarmWorkspaceManager()
        root = Path(temp_dir) / "SwarmWS"
        proj = root / "Projects" / "SwarmAI"
        und = proj / "2-understanding"
        und.mkdir(parents=True, exist_ok=True)
        # Simulate the migrated state: real docs under 2-understanding/, root EMPTY.
        real_marker = "# REAL migrated content — do not clobber\n"
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            (und / doc).write_text(real_marker, encoding="utf-8")
        assert not any((proj / d).exists()
                       for d in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"))

        await manager._ensure_default_project(root)

        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            assert not (proj / doc).exists(), \
                f"{doc} was re-stubbed at root — the provision split-brain bug recurred"
            # the real migrated doc is preserved (exists() guard saw it, skipped write)
            assert (und / doc).read_text(encoding="utf-8") == real_marker, \
                f"2-understanding/{doc} was clobbered"

    @pytest.mark.asyncio
    async def test_ensure_default_project_does_not_stub_unmigrated_root_docs(self, temp_dir):
        """REGRESSION (run_1db3791d, Gate-2 CRITICAL): for an UN-MIGRATED DDD (real
        docs at ROOT, no 2-understanding/), _ensure_default_project must NOT write a
        stub into 2-understanding/ and orphan the real root doc. A ddd_write_path GUARD
        (always-new) would return the absent 2-understanding/ path → exists()=False →
        stub there while the real doc sits at root, and the strangler READ then resolves
        to the empty stub = DATA LOSS. The guard must use ddd_path (strangler read: sees
        the root doc, skips). MUTATION: change the guard to ddd_write_path → this goes
        RED (a stub appears in 2-understanding/)."""
        from pathlib import Path
        manager = SwarmWorkspaceManager()
        root = Path(temp_dir) / "SwarmWS"
        proj = root / "Projects" / "SwarmAI"
        proj.mkdir(parents=True, exist_ok=True)
        # Un-migrated: real human-authored docs at ROOT, no 2-understanding/ dir.
        real_marker = "# REAL root content (un-migrated) — do not orphan\n"
        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            (proj / doc).write_text(real_marker, encoding="utf-8")
        assert not (proj / "2-understanding").exists()

        await manager._ensure_default_project(root)

        for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
            # No stub written into 2-understanding/ (guard saw the root doc via strangler)
            assert not (proj / "2-understanding" / doc).exists(), \
                f"2-understanding/{doc} stub written — orphans the real root doc (data loss)"
            # the real root doc is untouched
            assert (proj / doc).read_text(encoding="utf-8") == real_marker, \
                f"root {doc} was clobbered"


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


# Hypothesis settings for property tests — inherit max_examples from the loaded
# profile (conftest: default=30 / ci=100).
#
# deadline=None is DELIBERATE and load-bearing here: each example runs TWO full
# ensure_default_workspace() calls (real filesystem tree build + SQLite init) at
# ~1.6s/example with natural timing variance. Under a per-example deadline, an
# example that runs slightly slower on the initial call than on hypothesis's
# shrink-replay is reported as a FlakyFailure ("Falsified on the first call but did
# not on a subsequent one") — that was the historical flake, a timing artifact, NOT
# a real idempotence bug. Wall-clock is bounded by pytest --timeout, not by a
# per-example hypothesis deadline, so disabling it here loses no protection.
#
# suppress_health_check must include BOTH: function_scoped_fixture (we intentionally
# reuse tmp_path_factory across examples) AND too_slow (real FS+DB I/O is slow by
# nature). NOTE: a settings() object REPLACES the parent profile's
# suppress_health_check list rather than merging it, so too_slow — suppressed in the
# conftest profile — must be repeated here or it silently un-suppresses.
_PBT_SETTINGS = settings(
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)

# Strategy: generate a small set of user files to place in the workspace.
_safe_filename_chars = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=20,
)

# User-file CONTENT must survive a UTF-8 write_text→read_text round-trip, or the
# idempotence assertions become non-deterministic (source of the historical
# hypothesis FlakyFailure). Two content classes broke the round-trip and had NOTHING
# to do with init idempotence:
#   • lone surrogates (Unicode cat 'Cs', e.g. '\ud800') → write_text raises
#     UnicodeEncodeError; the collect helper's `except: ""` then swallows it, so the
#     two inits disagree on content.
#   • carriage return ('\r') → universal-newline translation on read collapses '\r\n'
#     and '\r' to '\n', so files_after_first != files_after_second.
# Both fail only for specific generated bytes → hypothesis couldn't reliably reproduce
# on replay → it reported FlakyFailure instead of a clean failure. Excluding these two
# classes keeps the property honest (arbitrary user text) while testing what this
# property is ABOUT: that a second init preserves existing user content byte-for-byte.
_roundtrip_safe_content = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),   # no surrogates (unencodable in UTF-8)
        blacklist_characters="\r",       # no CR (universal-newline translation)
    ),
    min_size=0,
    max_size=200,
)

_user_file_strategy = st.lists(
    st.tuples(
        # relative directory inside the workspace (pick from valid user locations)
        st.sampled_from([
            "Knowledge",
            "Knowledge/Notes",
            "Knowledge/Reports",
            "Knowledge/Meetings",
            "Knowledge/Library",
            "Knowledge/Archives",
            "Knowledge/DailyActivity",
            "Projects",
        ]),
        # filename
        _safe_filename_chars.map(lambda s: s + ".md"),
        # content (round-trip-safe — see note above)
        _roundtrip_safe_content,
    ),
    min_size=0,
    max_size=5,
)


class TestEnsureGitRepo:
    """Tests for _ensure_git_repo() git initialization.

    Validates: Requirements 2.1, 2.2, 2.3, 2.4
    """

    @pytest.fixture
    def temp_dir(self):
        temp_path = tempfile.mkdtemp()
        yield temp_path
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    def test_initializes_git_repo(self, temp_dir):
        """Verify git init creates .git directory."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(ws)
        manager = SwarmWorkspaceManager()
        result = manager._ensure_git_repo(ws)
        assert result is True
        assert os.path.isdir(os.path.join(ws, ".git"))

    def test_creates_gitignore_if_missing(self, temp_dir):
        """Verify .gitignore is written before git add."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(ws)
        manager = SwarmWorkspaceManager()
        manager._ensure_git_repo(ws)
        gitignore = Path(ws) / ".gitignore"
        assert gitignore.exists()
        assert gitignore.read_text(encoding="utf-8") == GITIGNORE_CONTENT

    def test_does_not_overwrite_existing_gitignore(self, temp_dir):
        """Verify existing .gitignore is preserved."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(ws)
        custom = "# custom\n*.log\n"
        (Path(ws) / ".gitignore").write_text(custom, encoding="utf-8")
        manager = SwarmWorkspaceManager()
        manager._ensure_git_repo(ws)
        assert (Path(ws) / ".gitignore").read_text(encoding="utf-8") == custom

    def test_creates_initial_commit(self, temp_dir):
        """Verify initial commit is created with message."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(ws)
        manager = SwarmWorkspaceManager()
        manager._ensure_git_repo(ws)
        result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=ws, capture_output=True, text=True,
        )
        assert "Initial SwarmWS state" in result.stdout

    def test_skips_if_git_already_exists(self, temp_dir):
        """Verify no-op when .git/ already exists."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(os.path.join(ws, ".git"))
        manager = SwarmWorkspaceManager()
        result = manager._ensure_git_repo(ws)
        assert result is True

    def test_returns_false_when_git_not_installed(self, temp_dir):
        """Verify graceful handling when git binary is missing."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(ws)
        manager = SwarmWorkspaceManager()
        with patch("core.swarm_workspace_manager.subprocess.run",
                    side_effect=FileNotFoundError("git not found")):
            result = manager._ensure_git_repo(ws)
        assert result is False

    def test_returns_false_on_subprocess_error(self, temp_dir):
        """Verify graceful handling when git command fails."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(ws)
        manager = SwarmWorkspaceManager()
        with patch("core.swarm_workspace_manager.subprocess.run",
                    side_effect=subprocess.CalledProcessError(1, "git")):
            result = manager._ensure_git_repo(ws)
        assert result is False

    def test_commits_existing_files(self, temp_dir):
        """Verify existing files are included in initial commit."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(os.path.join(ws, "Knowledge"), exist_ok=True)
        (Path(ws) / "Knowledge" / "notes.md").write_text("hello", encoding="utf-8")
        manager = SwarmWorkspaceManager()
        manager._ensure_git_repo(ws)
        result = subprocess.run(
            ["git", "show", "--stat", "--oneline", "HEAD"],
            cwd=ws, capture_output=True, text=True,
        )
        assert "Knowledge/notes.md" in result.stdout


class TestPrivacyGitignoreAndUntrack:
    """Privacy fix: provisioned .gitignore excludes user-private Projects (all
    except the default SwarmAI sample) + personal .context files, and existing
    workspaces get already-tracked private content untracked via git rm --cached.

    Product invariant: SwarmWS + Projects/SwarmAI ship publicly; every other
    Project + the 5 personal .context files must never enter the public repo.
    """

    @pytest.fixture
    def temp_dir(self):
        temp_path = tempfile.mkdtemp()
        yield temp_path
        if os.path.exists(temp_path):
            shutil.rmtree(temp_path)

    def _init_git(self, ws: str) -> None:
        """Init a temp git repo with a committer identity (for commit tests)."""
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True, timeout=30)
        subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=ws, timeout=30)
        subprocess.run(["git", "config", "user.name", "t"], cwd=ws, timeout=30)

    # ── AC1: template content + pattern behaviour ────────────────────────

    def test_gitignore_content_has_privacy_rules(self):
        """AC1: GITIGNORE_CONTENT carries the Projects + .context privacy rules."""
        assert "Projects/*" in GITIGNORE_CONTENT
        assert "!Projects/SwarmAI/" in GITIGNORE_CONTENT
        for f in ("MEMORY", "USER", "EVOLUTION", "STEERING", "TOOLS"):
            assert f".context/{f}.md" in GITIGNORE_CONTENT

    def test_provision_ignores_private_keeps_swarmai(self, temp_dir):
        """AC1: with the template .gitignore, a private Project is IGNORED while
        Projects/SwarmAI is NOT — verifies the Projects/* + !SwarmAI pattern."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(os.path.join(ws, "Projects", "SwarmAI"))
        os.makedirs(os.path.join(ws, "Projects", "CMHK_Private"))
        (Path(ws) / ".gitignore").write_text(GITIGNORE_CONTENT, encoding="utf-8")
        self._init_git(ws)
        (Path(ws) / "Projects" / "SwarmAI" / "PRODUCT.md").write_text("x", encoding="utf-8")
        (Path(ws) / "Projects" / "CMHK_Private" / "PRODUCT.md").write_text("y", encoding="utf-8")

        def _ignored(rel: str) -> bool:
            return subprocess.run(
                ["git", "check-ignore", "-q", rel], cwd=ws, timeout=30
            ).returncode == 0

        assert _ignored("Projects/CMHK_Private/PRODUCT.md") is True
        assert _ignored("Projects/SwarmAI/PRODUCT.md") is False
        assert _ignored(".context/MEMORY.md") is True

    # ── AC2: untrack already-tracked private content, keep SwarmAI ───────

    def test_migration_untracks_private_keeps_swarmai(self, temp_dir):
        """AC2: _untrack_private_content removes already-tracked private Projects
        + personal .context from the index, while Projects/SwarmAI + Knowledge
        stay tracked."""
        ws = os.path.join(temp_dir, "SwarmWS")
        for d in ("Projects/SwarmAI", "Projects/CMHK_Private", ".context", "Knowledge"):
            os.makedirs(os.path.join(ws, d), exist_ok=True)
        (Path(ws) / "Projects" / "SwarmAI" / "P.md").write_text("s", encoding="utf-8")
        (Path(ws) / "Projects" / "CMHK_Private" / "P.md").write_text("c", encoding="utf-8")
        (Path(ws) / ".context" / "MEMORY.md").write_text("m", encoding="utf-8")
        (Path(ws) / ".context" / "SOUL.md").write_text("soul", encoding="utf-8")
        (Path(ws) / "Knowledge" / "n.md").write_text("k", encoding="utf-8")
        self._init_git(ws)
        # commit EVERYTHING (simulating a pre-fix workspace with no privacy rules)
        subprocess.run(["git", "add", "-A"], cwd=ws, timeout=30)
        subprocess.run(["git", "commit", "-qm", "pre-fix"], cwd=ws, timeout=30)

        manager = SwarmWorkspaceManager()
        manager._untrack_private_content(ws)

        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ws, capture_output=True, text=True, timeout=30
        ).stdout
        # private content untracked
        assert "Projects/CMHK_Private/P.md" not in tracked
        assert ".context/MEMORY.md" not in tracked
        # public / framework content STILL tracked
        assert "Projects/SwarmAI/P.md" in tracked
        assert "Knowledge/n.md" in tracked
        assert ".context/SOUL.md" in tracked  # framework file, NOT untracked

    def test_rm_cached_keeps_disk_files(self, temp_dir):
        """AC4: untracking is --cached only — private files remain on disk."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(os.path.join(ws, "Projects", "CMHK_Private"))
        os.makedirs(os.path.join(ws, ".context"))
        priv = Path(ws) / "Projects" / "CMHK_Private" / "P.md"
        mem = Path(ws) / ".context" / "MEMORY.md"
        priv.write_text("secret", encoding="utf-8")
        mem.write_text("mem", encoding="utf-8")
        self._init_git(ws)
        subprocess.run(["git", "add", "-A"], cwd=ws, timeout=30)
        subprocess.run(["git", "commit", "-qm", "x"], cwd=ws, timeout=30)

        SwarmWorkspaceManager()._untrack_private_content(ws)

        assert priv.exists() and priv.read_text(encoding="utf-8") == "secret"
        assert mem.exists() and mem.read_text(encoding="utf-8") == "mem"

    # ── AC3: fail-open ───────────────────────────────────────────────────

    def test_migration_fail_open_non_git(self, temp_dir):
        """AC3: running the untrack in a non-git dir must not raise."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(os.path.join(ws, "Projects", "CMHK_Private"))
        # no git init
        SwarmWorkspaceManager()._untrack_private_content(ws)  # must not raise

    def test_migration_fail_open_git_missing_binary(self, temp_dir):
        """AC3: git binary missing → fail-open, no raise, no marker."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(os.path.join(ws, ".git"))  # looks like a repo
        manager = SwarmWorkspaceManager()
        with patch("core.swarm_workspace_manager.subprocess.run",
                    side_effect=FileNotFoundError("git not found")):
            manager._untrack_private_content(ws)  # must not raise
        assert not (Path(ws) / ".swarm_privacy_migrated").exists()

    # ── AC5: idempotency ─────────────────────────────────────────────────

    def test_migration_idempotent(self, temp_dir):
        """AC5: a second run is a no-op (marker gate) and does not raise."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(os.path.join(ws, "Projects", "CMHK_Private"))
        (Path(ws) / "Projects" / "CMHK_Private" / "P.md").write_text("c", encoding="utf-8")
        self._init_git(ws)
        subprocess.run(["git", "add", "-A"], cwd=ws, timeout=30)
        subprocess.run(["git", "commit", "-qm", "x"], cwd=ws, timeout=30)
        manager = SwarmWorkspaceManager()
        manager._untrack_private_content(ws)
        marker = Path(ws) / ".swarm_privacy_migrated"
        assert marker.exists()
        # second run — marker gates it, no exception, marker stays
        manager._untrack_private_content(ws)
        assert marker.exists()

    def test_migration_marker_written_on_clean_workspace(self, temp_dir):
        """AC5: a workspace with nothing private tracked still gets the marker
        (born-clean under the new template) so we don't rescan every startup."""
        ws = os.path.join(temp_dir, "SwarmWS")
        os.makedirs(os.path.join(ws, "Projects", "SwarmAI"))
        (Path(ws) / "Projects" / "SwarmAI" / "P.md").write_text("s", encoding="utf-8")
        (Path(ws) / ".gitignore").write_text(GITIGNORE_CONTENT, encoding="utf-8")
        self._init_git(ws)
        subprocess.run(["git", "add", "-A"], cwd=ws, timeout=30)
        subprocess.run(["git", "commit", "-qm", "clean"], cwd=ws, timeout=30)
        SwarmWorkspaceManager()._untrack_private_content(ws)
        assert (Path(ws) / ".swarm_privacy_migrated").exists()

    @pytest.mark.asyncio
    async def test_migration_append_ignores_comment_false_match(self, temp_dir):
        """Gate-2 CRITICAL regression: a privacy rule mentioned only in a COMMENT
        must NOT satisfy the presence check — the real rule must still be appended
        as an actual line, else private files stay committable. Verifies the
        line-level (not substring) match in verify_integrity's append logic."""
        ws = os.path.join(temp_dir, "SwarmWS")
        # Pre-existing .gitignore that MENTIONS the rules only inside comments.
        preexisting = (
            "# custom user rules\n"
            "*.log\n"
            "# privacy notes: we should exclude Projects/* but keep\n"
            "# !Projects/SwarmAI/ and ignore .context/MEMORY.md too\n"
        )
        os.makedirs(ws)
        (Path(ws) / ".gitignore").write_text(preexisting, encoding="utf-8")
        # Minimal structure so verify_integrity runs its .gitignore migration.
        for d in FOLDER_STRUCTURE:
            os.makedirs(os.path.join(ws, d), exist_ok=True)

        await SwarmWorkspaceManager().verify_integrity(ws)

        # Parse the resulting .gitignore into actual (non-comment) rule lines.
        result = (Path(ws) / ".gitignore").read_text(encoding="utf-8")
        rule_lines = {
            ln.strip() for ln in result.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        }
        # The real rules must now be present as ACTUAL lines despite the comments.
        assert "Projects/*" in rule_lines
        assert "!Projects/SwarmAI/" in rule_lines
        assert ".context/MEMORY.md" in rule_lines
        # User's own custom line preserved (append-only).
        assert "*.log" in rule_lines
        # git check-ignore confirms the appended rules actually take effect.
        self._init_git(ws)
        os.makedirs(os.path.join(ws, "Projects", "Private"), exist_ok=True)
        (Path(ws) / "Projects" / "Private" / "x.md").write_text("p", encoding="utf-8")
        assert subprocess.run(
            ["git", "check-ignore", "-q", "Projects/Private/x.md"],
            cwd=ws, timeout=30,
        ).returncode == 0


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
            # Exclude .legacy_cleaned marker — created once on second init
            files_after_first = {
                k: v for k, v in _collect_all_files(root).items()
                if k not in (".legacy_cleaned", ".swarm_privacy_migrated")
            }
            dirs_after_first = _collect_all_dirs(root)

            # ── Second init ─────────────────────────────────────────────
            await manager.ensure_default_workspace(db)

            # Snapshot state after second init
            files_after_second = {
                k: v for k, v in _collect_all_files(root).items()
                if k not in (".legacy_cleaned", ".swarm_privacy_migrated")
            }
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


class TestPruneArchives:
    """Unit tests for SwarmWorkspaceManager.prune_archives().

    Validates Requirement 7.6 (auto-archive older DailyActivity files)
    and Requirement 15.11 (move processed files to Archives/).

    The method deletes archived DailyActivity files older than 90 days,
    parsing dates from YYYY-MM-DD.md filenames and skipping non-date
    filenames gracefully.
    """

    @pytest.fixture
    def temp_workspace(self, tmp_path):
        """Create a temp workspace with Knowledge/Archives/ directory."""
        archives = tmp_path / "Knowledge" / "Archives"
        archives.mkdir(parents=True)
        return tmp_path

    def test_deletes_files_older_than_90_days(self, temp_workspace):
        """Files with dates > 90 days ago should be deleted."""
        from datetime import date, timedelta

        archives = temp_workspace / "Knowledge" / "Archives"
        old_date = date.today() - timedelta(days=91)
        old_file = archives / f"{old_date.isoformat()}.md"
        old_file.write_text("old content")

        mgr = SwarmWorkspaceManager()
        deleted = mgr.prune_archives(str(temp_workspace))

        assert deleted == 1
        assert not old_file.exists()

    def test_preserves_files_within_90_days(self, temp_workspace):
        """Files with dates <= 90 days ago should be kept."""
        from datetime import date, timedelta

        archives = temp_workspace / "Knowledge" / "Archives"
        recent_date = date.today() - timedelta(days=89)
        recent_file = archives / f"{recent_date.isoformat()}.md"
        recent_file.write_text("recent content")

        mgr = SwarmWorkspaceManager()
        deleted = mgr.prune_archives(str(temp_workspace))

        assert deleted == 0
        assert recent_file.exists()
        assert recent_file.read_text() == "recent content"

    def test_preserves_file_exactly_at_90_days(self, temp_workspace):
        """A file exactly 90 days old should NOT be deleted (cutoff is exclusive)."""
        from datetime import date, timedelta

        archives = temp_workspace / "Knowledge" / "Archives"
        boundary_date = date.today() - timedelta(days=90)
        boundary_file = archives / f"{boundary_date.isoformat()}.md"
        boundary_file.write_text("boundary content")

        mgr = SwarmWorkspaceManager()
        deleted = mgr.prune_archives(str(temp_workspace))

        assert deleted == 0
        assert boundary_file.exists()

    def test_skips_non_date_filenames(self, temp_workspace):
        """Files without YYYY-MM-DD stems should be left untouched."""
        archives = temp_workspace / "Knowledge" / "Archives"
        manual_file = archives / "meeting-notes.md"
        manual_file.write_text("important notes")
        readme = archives / "README.md"
        readme.write_text("archive index")

        mgr = SwarmWorkspaceManager()
        deleted = mgr.prune_archives(str(temp_workspace))

        assert deleted == 0
        assert manual_file.exists()
        assert readme.exists()

    def test_skips_non_md_files(self, temp_workspace):
        """Non-.md files should be ignored even if they have date names."""
        from datetime import date, timedelta

        archives = temp_workspace / "Knowledge" / "Archives"
        old_date = date.today() - timedelta(days=100)
        txt_file = archives / f"{old_date.isoformat()}.txt"
        txt_file.write_text("not markdown")

        mgr = SwarmWorkspaceManager()
        deleted = mgr.prune_archives(str(temp_workspace))

        assert deleted == 0
        assert txt_file.exists()

    def test_handles_missing_archives_directory(self, tmp_path):
        """Returns 0 when Knowledge/Archives/ does not exist."""
        mgr = SwarmWorkspaceManager()
        deleted = mgr.prune_archives(str(tmp_path))
        assert deleted == 0

    def test_mixed_old_and_recent_files(self, temp_workspace):
        """Only old files are deleted; recent and non-date files survive."""
        from datetime import date, timedelta

        archives = temp_workspace / "Knowledge" / "Archives"

        old_date = date.today() - timedelta(days=120)
        old_file = archives / f"{old_date.isoformat()}.md"
        old_file.write_text("old")

        recent_date = date.today() - timedelta(days=30)
        recent_file = archives / f"{recent_date.isoformat()}.md"
        recent_file.write_text("recent")

        manual_file = archives / "project-archive.md"
        manual_file.write_text("manual")

        mgr = SwarmWorkspaceManager()
        deleted = mgr.prune_archives(str(temp_workspace))

        assert deleted == 1
        assert not old_file.exists()
        assert recent_file.exists()
        assert manual_file.exists()

    def test_custom_max_age_days(self, temp_workspace):
        """The max_age_days parameter controls the cutoff."""
        from datetime import date, timedelta

        archives = temp_workspace / "Knowledge" / "Archives"
        file_date = date.today() - timedelta(days=10)
        f = archives / f"{file_date.isoformat()}.md"
        f.write_text("content")

        mgr = SwarmWorkspaceManager()
        # With default 90 days, file should survive
        assert mgr.prune_archives(str(temp_workspace)) == 0
        assert f.exists()

        # With 5-day cutoff, file should be pruned
        deleted = mgr.prune_archives(str(temp_workspace), max_age_days=5)
        assert deleted == 1
        assert not f.exists()

    def test_todays_file_preserved(self, temp_workspace):
        """Today's file should never be deleted."""
        from datetime import date

        archives = temp_workspace / "Knowledge" / "Archives"
        today_file = archives / f"{date.today().isoformat()}.md"
        today_file.write_text("today's activity")

        mgr = SwarmWorkspaceManager()
        deleted = mgr.prune_archives(str(temp_workspace))

        assert deleted == 0
        assert today_file.exists()


class TestProgressiveLoadingTOC:
    """Test DDD T6: progressive loading generates section-level TOC for large docs."""

    @pytest.mark.asyncio
    async def test_large_doc_gets_section_toc(self):
        """PROJECTS.md shows section headings + line ranges for docs >30K bytes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "Projects" / "BigProject").mkdir(parents=True)
            (ws / ".context").mkdir(parents=True)

            # Small doc — should NOT get TOC
            (ws / "Projects" / "BigProject" / "PRODUCT.md").write_text(
                "# Product\n\n## Vision\n\nSmall content.\n"
            )
            # Large doc (>30K) — SHOULD get section TOC with line ranges
            large_content = "# Tech\n\n"
            large_content += "## Architecture\n\n" + ("x " * 5000) + "\n\n"
            large_content += "## Conventions\n\n" + ("y " * 5000) + "\n\n"
            large_content += "## Runtime Traps\n\n" + ("z " * 5000) + "\n\n"
            (ws / "Projects" / "BigProject" / "TECH.md").write_text(large_content)

            # Small docs
            (ws / "Projects" / "BigProject" / "IMPROVEMENT.md").write_text(
                "# Improvement\n\n## What Failed\n\n- seed\n"
            )
            (ws / "Projects" / "BigProject" / "PROJECT.md").write_text(
                "# Project\n\n## Current Sprint\n\nDoing stuff.\n"
            )

            mgr = SwarmWorkspaceManager()
            await mgr.refresh_projects_index(str(ws))

            content = (ws / ".context" / "PROJECTS.md").read_text()

            # Large doc should have section TOC with line ranges
            assert "TECH.md" in content
            assert "Architecture" in content
            assert "Conventions" in content
            # F1: verify ranges are shown (L<start>-L<end> format)
            import re
            assert re.search(r"L\d+-L\d+", content), \
                f"Expected line ranges (L<start>-L<end>) in TOC, got: {content[:500]}"
            # Small doc should NOT have section TOC
            assert "PRODUCT.md" in content
            # Verify the on-demand directive exists
            assert "on demand" in content.lower() or "on-demand" in content.lower()

    @pytest.mark.asyncio
    async def test_small_docs_no_toc(self):
        """Docs under 30K bytes don't get section-level TOC."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / "Projects" / "SmallProject").mkdir(parents=True)
            (ws / ".context").mkdir(parents=True)

            for doc in ["PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"]:
                (ws / "Projects" / "SmallProject" / doc).write_text(
                    f"# {doc}\n\n## Section One\n\nSmall.\n"
                )

            mgr = SwarmWorkspaceManager()
            await mgr.refresh_projects_index(str(ws))

            content = (ws / ".context" / "PROJECTS.md").read_text()

            # No section TOC for small docs
            assert "Large doc" not in content
            assert "on demand" not in content.lower() or "read on demand" not in content.lower()


class TestEntityIndexIntegration:
    """Entity Index injection into PROJECTS.md via refresh_projects_index."""

    @pytest.mark.asyncio
    async def test_entity_index_appears_in_projects_md(self):
        """AC1+AC7: Entity Index section injected before Active Projects,
        existing content preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / ".context").mkdir(parents=True)

            # Create 2 projects with shared headings
            for proj in ["ProjA", "ProjB"]:
                (ws / "Projects" / proj).mkdir(parents=True)
                (ws / "Projects" / proj / "TECH.md").write_text(
                    f"# {proj} Tech\n\n## Architecture\n\nStuff.\n\n"
                    f"## Stack\n\nPython.\n"
                )
                (ws / "Projects" / proj / "IMPROVEMENT.md").write_text(
                    f"# {proj} Improvement\n\n## What Worked\n\n- Things.\n"
                )

            mgr = SwarmWorkspaceManager()
            await mgr.refresh_projects_index(str(ws))

            content = (ws / ".context" / "PROJECTS.md").read_text()

            # Entity Index appears
            assert "## Cross-Project Knowledge Index" in content
            # Shared heading "Architecture" routes to both projects
            assert "Architecture" in content
            assert "ProjA/TECH#Architecture" in content
            assert "ProjB/TECH#Architecture" in content
            # Existing sections preserved
            assert "## Active Projects" in content
            assert "ProjA" in content
            assert "ProjB" in content
            # Entity Index comes BEFORE Active Projects
            idx_pos = content.index("Cross-Project Knowledge Index")
            active_pos = content.index("## Active Projects")
            assert idx_pos < active_pos

    @pytest.mark.asyncio
    async def test_entity_index_skipped_when_no_entities(self):
        """Edge case: empty projects → no Entity Index section."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / ".context").mkdir(parents=True)
            (ws / "Projects").mkdir(parents=True)

            mgr = SwarmWorkspaceManager()
            await mgr.refresh_projects_index(str(ws))

            content = (ws / ".context" / "PROJECTS.md").read_text()
            assert "Cross-Project Knowledge Index" not in content

    @pytest.mark.asyncio
    async def test_entity_index_budget_enforcement(self):
        """AC3: Total Entity Index stays under 8000 chars."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir)
            (ws / ".context").mkdir(parents=True)

            # Create many projects with many headings to force pruning
            for i in range(20):
                proj_dir = ws / "Projects" / f"Project{i:02d}"
                proj_dir.mkdir(parents=True)
                # Each project has 10 unique headings → 200 total
                headings = "\n\n".join(
                    f"## LongHeadingName{i}_{j} Extra Words Here\n\nContent."
                    for j in range(10)
                )
                (proj_dir / "TECH.md").write_text(
                    f"# Project{i:02d} Tech\n\n{headings}\n"
                )

            mgr = SwarmWorkspaceManager()
            await mgr.refresh_projects_index(str(ws))

            content = (ws / ".context" / "PROJECTS.md").read_text()
            # Extract just the Entity Index section
            if "## Cross-Project Knowledge Index" in content:
                start = content.index("## Cross-Project Knowledge Index")
                # Find the separator line after Entity Index
                end = content.index("---", start)
                entity_section = content[start:end]
                assert len(entity_section) <= 8000


class TestAssetNeutralScaffold:
    """DDD provisioning templates must be asset-neutral (paradigm decision 2026-07-19).

    A DDD is a universal brain governing 0..N assets of an open kind; the CREATE-time
    scaffold does NOT know the asset set (bindings are added at BIND), so the AGENTS.md
    and REFRESHER.md templates must state the paradigm asset-neutrally and must NOT
    hardcode "the physical repo" (that re-breaks data-agent and pure-knowledge brains).
    Regression guard for the swarm_workspace_manager.py SECTION_SCAFFOLD templates.
    """

    def test_agents_template_not_repo_presupposing(self):
        from core.swarm_workspace_manager import SECTION_SCAFFOLD
        agents = SECTION_SCAFFOLD["AGENTS.md"].lower()
        # NEGATIVE: no repo-presupposition, in ANY phrasing (not just the exact old string).
        # "the physical repo" is the paradigm-forbidden presupposition — assert it is gone
        # wholesale, so a differently-worded repo assumption can't slip past (Gate-2 MED).
        assert "physical repo" not in agents, (
            "AGENTS.md template presupposes a repo — breaks data-agent/pure-knowledge brains"
        )
        # POSITIVE: states the 0..N open-kind asset paradigm (single, non-redundant assertion)
        assert "0..n" in agents, "AGENTS.md template must state the 0..N governed-assets model"
        assert "governed asset" in agents

    def test_refresher_template_not_repo_presupposing(self):
        from core.swarm_workspace_manager import SECTION_SCAFFOLD
        refresher = SECTION_SCAFFOLD["REFRESHER.md"].lower()
        # NEGATIVE: no repo-presupposition in ANY phrasing (Gate-2 MED — the old test only
        # caught the verbatim string; a reworded repo assumption would have passed).
        assert "physical repo" not in refresher, (
            "REFRESHER.md template presupposes a repo — must be asset-kind-neutral"
        )
        # POSITIVE: shape-follows-kind + zero-asset no-op semantics (not merely the word 'asset')
        assert "kind" in refresher, "REFRESHER.md must say the refresher shape follows the asset kind"
        assert "no-op" in refresher, "REFRESHER.md must state the 0-asset no-op case"
