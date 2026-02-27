"""Unit tests for the SwarmWS initialization flow.

Tests the ``InitializationManager`` and ``SwarmWorkspaceManager`` integration
that runs on every application startup.  The focus is on:

- First-launch behaviour: full folder structure + sample data creation
- Subsequent-launch behaviour: integrity verification without overwriting
- Missing system-managed item recreation (including Knowledge/Memory/)
- Sample data preservation across re-initialization
- Knowledge/Memory/ directory creation and integrity checking

Testing methodology: unit tests with a real ``SQLiteDatabase`` backed by a
temporary file and a temporary workspace directory for full filesystem
isolation.

Validates: Requirements 2.4, 3.2, 29.1, 30.1, 31.1, 31.2, 31.3, 31.4, 31.5
"""

import os
import shutil
import tempfile

import pytest

from core.swarm_workspace_manager import (
    FOLDER_STRUCTURE,
    PROJECT_SYSTEM_FILES,
    PROJECT_SYSTEM_FOLDERS,
    SYSTEM_MANAGED_ROOT_FILES,
    SYSTEM_MANAGED_SECTION_FILES,
    SwarmWorkspaceManager,
)
from database.sqlite import SQLiteDatabase


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_workspace(tmp_path):
    """Return a fresh temporary directory to use as the SwarmWS root."""
    ws = tmp_path / "SwarmWS"
    return str(ws)


@pytest.fixture
async def real_db(tmp_path):
    """Create a real SQLiteDatabase backed by a temp file."""
    db_path = tmp_path / "test.db"
    database = SQLiteDatabase(db_path=str(db_path))
    await database.initialize()
    return database


@pytest.fixture
def manager():
    """Return a fresh SwarmWorkspaceManager instance."""
    return SwarmWorkspaceManager()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_default_config(monkeypatch, workspace_path: str):
    """Patch DEFAULT_WORKSPACE_CONFIG to point at *workspace_path*."""
    import core.swarm_workspace_manager as swm_mod
    monkeypatch.setitem(swm_mod.DEFAULT_WORKSPACE_CONFIG, "file_path", workspace_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFirstLaunchCreatesFullStructure:
    """First launch on an empty directory creates all system items + sample data.

    Validates: Requirements 31.1, 31.5
    """

    @pytest.mark.asyncio
    async def test_first_launch_creates_full_structure(
        self, manager, real_db, tmp_workspace, monkeypatch
    ):
        """Fresh DB + empty dir → all system folders, root files, section
        context files, and sample data are present."""
        _patch_default_config(monkeypatch, tmp_workspace)

        result = await manager.ensure_default_workspace(real_db)

        # Workspace config returned correctly
        assert result["id"] == "swarmws"
        assert result["name"] == "SwarmWS"

        root = tmp_workspace

        # All section folders exist
        for folder in FOLDER_STRUCTURE:
            assert os.path.isdir(os.path.join(root, folder)), (
                f"Folder {folder} should exist"
            )

        # Root-level system files exist
        for filename in SYSTEM_MANAGED_ROOT_FILES:
            assert os.path.isfile(os.path.join(root, filename)), (
                f"Root file {filename} should exist"
            )

        # Section-level context files exist
        for rel_path in SYSTEM_MANAGED_SECTION_FILES:
            assert os.path.isfile(os.path.join(root, rel_path)), (
                f"Section file {rel_path} should exist"
            )

        # Sample data: Knowledge Base asset and Notes file
        assert os.path.isfile(os.path.join(
            root, "Knowledge", "Knowledge Base", "sample-knowledge-asset.md"
        ))
        assert os.path.isfile(os.path.join(
            root, "Knowledge", "Notes", "sample-note.md"
        ))

        # Sample data: Knowledge/Memory/ directory and sample memory item
        memory_dir = os.path.join(root, "Knowledge", "Memory")
        assert os.path.isdir(memory_dir), (
            "Knowledge/Memory/ directory should exist after first launch"
        )
        sample_memory = os.path.join(memory_dir, "communication-style.md")
        assert os.path.isfile(sample_memory), (
            "Sample memory file communication-style.md should exist"
        )

        # Sample data: sample project with full scaffold
        project_dir = os.path.join(root, "Projects", "Website Redesign")
        assert os.path.isdir(project_dir), "Sample project directory should exist"
        assert os.path.isfile(os.path.join(project_dir, ".project.json"))
        assert os.path.isfile(os.path.join(project_dir, "instructions.md"))
        for sys_folder in PROJECT_SYSTEM_FOLDERS:
            assert os.path.isdir(os.path.join(project_dir, sys_folder)), (
                f"Project system folder {sys_folder} should exist"
            )


class TestSubsequentLaunchPreservesContent:
    """Re-initialization preserves user-modified content.

    Validates: Requirements 31.2, 31.4
    """

    @pytest.mark.asyncio
    async def test_subsequent_launch_preserves_content(
        self, manager, real_db, tmp_workspace, monkeypatch
    ):
        """Modify a system file, re-init → content preserved (not overwritten)."""
        _patch_default_config(monkeypatch, tmp_workspace)

        # First init
        await manager.ensure_default_workspace(real_db)

        # User edits a system file
        ctx_l0 = os.path.join(tmp_workspace, "context-L0.md")
        custom_content = "# My Custom Context\nUser-edited content.\n"
        with open(ctx_l0, "w", encoding="utf-8") as f:
            f.write(custom_content)

        # Second init (subsequent launch)
        await manager.ensure_default_workspace(real_db)

        # Content must be preserved
        with open(ctx_l0, "r", encoding="utf-8") as f:
            assert f.read() == custom_content


class TestMissingSystemItemsRecreated:
    """Deleted system-managed items are recreated on re-initialization.

    Validates: Requirements 29.1, 31.1, 31.2, 31.3
    """

    @pytest.mark.asyncio
    async def test_missing_system_items_recreated(
        self, manager, real_db, tmp_workspace, monkeypatch
    ):
        """Delete some system files and folders, re-init → they come back."""
        _patch_default_config(monkeypatch, tmp_workspace)

        # First init
        await manager.ensure_default_workspace(real_db)

        # Delete a root system file
        os.remove(os.path.join(tmp_workspace, "system-prompts.md"))

        # Delete a section context file
        os.remove(os.path.join(tmp_workspace, "Knowledge", "context-L0.md"))

        # Delete a section folder entirely
        shutil.rmtree(os.path.join(tmp_workspace, "Knowledge", "Notes"))

        # Re-init
        await manager.ensure_default_workspace(real_db)

        # All deleted items should be recreated
        assert os.path.isfile(os.path.join(tmp_workspace, "system-prompts.md")), (
            "system-prompts.md should be recreated"
        )
        assert os.path.isfile(
            os.path.join(tmp_workspace, "Knowledge", "context-L0.md")
        ), "Knowledge/context-L0.md should be recreated"
        assert os.path.isdir(os.path.join(tmp_workspace, "Knowledge", "Notes")), (
            "Knowledge/Notes/ folder should be recreated"
        )

    @pytest.mark.asyncio
    async def test_missing_knowledge_memory_recreated(
        self, manager, real_db, tmp_workspace, monkeypatch
    ):
        """Delete Knowledge/Memory/, re-init → it is recreated.

        Validates: Requirements 2.4, 3.2, 29.1
        """
        _patch_default_config(monkeypatch, tmp_workspace)

        # First init
        await manager.ensure_default_workspace(real_db)

        memory_dir = os.path.join(tmp_workspace, "Knowledge", "Memory")
        assert os.path.isdir(memory_dir)

        # Delete Knowledge/Memory/
        shutil.rmtree(memory_dir)
        assert not os.path.exists(memory_dir)

        # Re-init
        await manager.ensure_default_workspace(real_db)

        # Knowledge/Memory/ should be recreated
        assert os.path.isdir(memory_dir), (
            "Knowledge/Memory/ should be recreated on re-initialization"
        )


class TestSampleDataNotOverwritten:
    """Sample data modifications survive re-initialization.

    Validates: Requirements 31.2, 25.7
    """

    @pytest.mark.asyncio
    async def test_sample_data_not_overwritten(
        self, manager, real_db, tmp_workspace, monkeypatch
    ):
        """First init creates sample data, modify it, re-init → modifications
        preserved."""
        _patch_default_config(monkeypatch, tmp_workspace)

        # First init
        await manager.ensure_default_workspace(real_db)

        # Modify sample knowledge base asset
        artifact_path = os.path.join(
            tmp_workspace, "Knowledge", "Knowledge Base", "sample-knowledge-asset.md"
        )
        custom_artifact = "# My Edited Knowledge Asset\nCustom content.\n"
        with open(artifact_path, "w", encoding="utf-8") as f:
            f.write(custom_artifact)

        # Modify sample note
        notebook_path = os.path.join(
            tmp_workspace, "Knowledge", "Notes", "sample-note.md"
        )
        custom_notebook = "# My Edited Note\nCustom notes.\n"
        with open(notebook_path, "w", encoding="utf-8") as f:
            f.write(custom_notebook)

        # Re-init
        await manager.ensure_default_workspace(real_db)

        # Modifications must be preserved
        with open(artifact_path, "r", encoding="utf-8") as f:
            assert f.read() == custom_artifact

        with open(notebook_path, "r", encoding="utf-8") as f:
            assert f.read() == custom_notebook
