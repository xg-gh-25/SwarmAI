"""Unit tests for the SwarmWS initialization flow.

Tests the ``InitializationManager`` and ``SwarmWorkspaceManager`` integration
that runs on every application startup.  The focus is on:

- First-launch behaviour: minimal folder structure (Knowledge/, Projects/) + .gitignore
- Subsequent-launch behaviour: integrity verification without overwriting
- Git repository initialization

Testing methodology: unit tests with a real ``SQLiteDatabase`` backed by a
temporary file and a temporary workspace directory for full filesystem
isolation.

Validates: Requirements 1.1-1.10, 2.1-2.4
"""

import os
import shutil
import tempfile

import pytest

from core.swarm_workspace_manager import (
    FOLDER_STRUCTURE,
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
        """Fresh DB + empty dir → Knowledge/, Projects/, and .gitignore are present."""
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

        # .gitignore exists
        assert os.path.isfile(os.path.join(root, ".gitignore")), (
            ".gitignore should exist"
        )

        # Git repo initialized
        assert os.path.isdir(os.path.join(root, ".git")), (
            ".git/ directory should exist after first launch"
        )

        # Default Knowledge subdirectories exist
        assert os.path.isdir(os.path.join(root, "Knowledge", "Knowledge Base")), (
            "Knowledge Base subdirectory should exist"
        )
        assert os.path.isdir(os.path.join(root, "Knowledge", "Notes")), (
            "Notes subdirectory should exist"
        )


class TestSubsequentLaunchPreservesContent:
    """Re-initialization preserves user-modified content.

    Validates: Requirements 1.7, 1.8
    """

    @pytest.mark.asyncio
    async def test_subsequent_launch_preserves_user_files(
        self, manager, real_db, tmp_workspace, monkeypatch
    ):
        """Create a user file, re-init → content preserved (not overwritten)."""
        _patch_default_config(monkeypatch, tmp_workspace)

        # First init
        await manager.ensure_default_workspace(real_db)

        # User creates a file in Knowledge/
        user_file = os.path.join(tmp_workspace, "Knowledge", "my-notes.md")
        custom_content = "# My Notes\nUser-created content.\n"
        with open(user_file, "w", encoding="utf-8") as f:
            f.write(custom_content)

        # Second init (subsequent launch)
        await manager.ensure_default_workspace(real_db)

        # Content must be preserved
        with open(user_file, "r", encoding="utf-8") as f:
            assert f.read() == custom_content


class TestMissingFoldersRecreated:
    """Deleted system folders are recreated on re-initialization.

    Validates: Requirements 1.7, 1.8
    """

    @pytest.mark.asyncio
    async def test_missing_folders_recreated(
        self, manager, real_db, tmp_workspace, monkeypatch
    ):
        """Delete Knowledge/ folder, re-init → it comes back."""
        _patch_default_config(monkeypatch, tmp_workspace)

        # First init
        await manager.ensure_default_workspace(real_db)

        # Delete Knowledge/ folder
        shutil.rmtree(os.path.join(tmp_workspace, "Knowledge"))

        # Re-init
        await manager.ensure_default_workspace(real_db)

        # Knowledge/ should be recreated
        assert os.path.isdir(os.path.join(tmp_workspace, "Knowledge")), (
            "Knowledge/ folder should be recreated"
        )
        assert os.path.isdir(os.path.join(tmp_workspace, "Projects")), (
            "Projects/ folder should still exist"
        )


