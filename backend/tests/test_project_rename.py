"""Unit and property-based tests for project rename preserving identity.

Tests that ``SwarmWorkspaceManager.update_project()`` with a name change
correctly renames the project directory on the filesystem while preserving
immutable identity fields (``id``, ``created_at``), incrementing the version
counter, updating ``updated_at``, and appending a ``renamed`` history entry
with before/after name values.

Key test areas:

- ``TestRenameFilesystem``       — Directory renamed, ``.project.json`` updated
- ``TestRenameIdentity``         — UUID and created_at preserved across rename
- ``TestRenameVersioning``       — Version incremented, updated_at refreshed
- ``TestRenameHistory``          — History entry with action=renamed, correct changes
- ``TestRenameErrorCases``       — Duplicate name (ValueError) and invalid name (ValueError)
- ``TestPropertyRenamePreservesIdentity`` — Property 3: Hypothesis-driven rename identity preservation

**Requirements: 4.7, 18.5**
"""

import json
import shutil
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

from core.swarm_workspace_manager import SwarmWorkspaceManager


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

valid_project_names = st.from_regex(
    r"[a-zA-Z0-9][a-zA-Z0-9 _.\-]{0,49}", fullmatch=True
).filter(lambda n: n.strip() == n and not n.endswith("."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_project(
    manager: SwarmWorkspaceManager,
    workspace_path: str,
    name: str = "original-project",
) -> dict:
    """Create a project and return its metadata."""
    projects_dir = Path(workspace_path) / "Projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return await manager.create_project(
        project_name=name,
        workspace_path=workspace_path,
    )


# ---------------------------------------------------------------------------
# Tests: Rename updates filesystem
# ---------------------------------------------------------------------------


class TestRenameFilesystem:
    """Verify rename updates directory name and .project.json on disk."""

    @pytest.mark.asyncio
    async def test_rename_updates_directory_name(self, tmp_path: Path):
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_project(manager, ws)
        project_id = created["id"]

        await manager.update_project(
            project_id, {"name": "renamed-project"}, workspace_path=ws
        )

        old_dir = tmp_path / "Projects" / "original-project"
        new_dir = tmp_path / "Projects" / "renamed-project"
        assert not old_dir.exists(), "Old directory should no longer exist"
        assert new_dir.exists(), "New directory should exist"
        assert new_dir.is_dir()

    @pytest.mark.asyncio
    async def test_rename_updates_name_in_project_json(self, tmp_path: Path):
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_project(manager, ws)
        project_id = created["id"]

        await manager.update_project(
            project_id, {"name": "renamed-project"}, workspace_path=ws
        )

        project_json_path = tmp_path / "Projects" / "renamed-project" / ".project.json"
        assert project_json_path.exists()
        data = json.loads(project_json_path.read_text())
        assert data["name"] == "renamed-project"


# ---------------------------------------------------------------------------
# Tests: Rename preserves identity
# ---------------------------------------------------------------------------


class TestRenameIdentity:
    """Verify rename preserves id (UUID) and created_at unchanged."""

    @pytest.mark.asyncio
    async def test_rename_preserves_uuid(self, tmp_path: Path):
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id, {"name": "new-name"}, workspace_path=ws
        )

        assert updated["id"] == project_id

    @pytest.mark.asyncio
    async def test_rename_preserves_created_at(self, tmp_path: Path):
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_project(manager, ws)
        project_id = created["id"]
        original_created_at = created["created_at"]

        updated = await manager.update_project(
            project_id, {"name": "new-name"}, workspace_path=ws
        )

        assert updated["created_at"] == original_created_at


# ---------------------------------------------------------------------------
# Tests: Rename versioning
# ---------------------------------------------------------------------------


class TestRenameVersioning:
    """Verify rename increments version and updates updated_at."""

    @pytest.mark.asyncio
    async def test_rename_increments_version(self, tmp_path: Path):
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_project(manager, ws)
        project_id = created["id"]
        original_version = created["version"]

        updated = await manager.update_project(
            project_id, {"name": "new-name"}, workspace_path=ws
        )

        assert updated["version"] == original_version + 1

    @pytest.mark.asyncio
    async def test_rename_updates_updated_at(self, tmp_path: Path):
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_project(manager, ws)
        project_id = created["id"]
        original_updated_at = created["updated_at"]

        updated = await manager.update_project(
            project_id, {"name": "new-name"}, workspace_path=ws
        )

        assert updated["updated_at"] >= original_updated_at
        assert updated["updated_at"] != original_updated_at


# ---------------------------------------------------------------------------
# Tests: Rename history entry
# ---------------------------------------------------------------------------


class TestRenameHistory:
    """Verify rename appends history entry with action=renamed and correct changes."""

    @pytest.mark.asyncio
    async def test_rename_appends_renamed_history_entry(self, tmp_path: Path):
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_project(manager, ws)
        project_id = created["id"]

        updated = await manager.update_project(
            project_id, {"name": "new-name"}, workspace_path=ws
        )

        # Should have 2 entries: initial "created" + "renamed"
        assert len(updated["update_history"]) == 2
        entry = updated["update_history"][-1]
        assert entry["action"] == "renamed"
        assert entry["version"] == updated["version"]
        assert "name" in entry["changes"]
        assert entry["changes"]["name"]["from"] == "original-project"
        assert entry["changes"]["name"]["to"] == "new-name"
        assert entry["source"] == "user"


# ---------------------------------------------------------------------------
# Tests: Rename error cases
# ---------------------------------------------------------------------------


class TestRenameErrorCases:
    """Verify rename to duplicate or invalid name raises ValueError."""

    @pytest.mark.asyncio
    async def test_rename_to_duplicate_name_raises(self, tmp_path: Path):
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        await _create_project(manager, ws, name="project-a")
        created_b = await _create_project(manager, ws, name="project-b")

        with pytest.raises(ValueError, match="already exists"):
            await manager.update_project(
                created_b["id"], {"name": "project-a"}, workspace_path=ws
            )

    @pytest.mark.asyncio
    async def test_rename_to_invalid_name_raises(self, tmp_path: Path):
        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()
        created = await _create_project(manager, ws)

        with pytest.raises(ValueError):
            await manager.update_project(
                created["id"], {"name": "invalid/name!"}, workspace_path=ws
            )



# ---------------------------------------------------------------------------
# Property Tests: Rename Preserves Identity
# ---------------------------------------------------------------------------


class TestPropertyRenamePreservesIdentity:
    """Property 3: Project Rename Preserves Identity.

    For any project and any valid new name, renaming via update_project()
    preserves the UUID, renames the directory, increments version, and
    appends a 'renamed' history entry with correct from/to values.

    # Feature: swarmws-projects, Property 3: Project Rename Preserves Identity
    **Validates: Requirements 4.7, 18.5**
    """

    @pytest.mark.asyncio
    @settings(deadline=10000, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        original_name=valid_project_names,
        new_name=valid_project_names,
    )
    async def test_rename_preserves_identity(
        self, tmp_path: Path, original_name: str, new_name: str
    ):
        # Skip if names are identical (no rename occurs)
        if original_name == new_name:
            return

        ws = str(tmp_path)
        manager = SwarmWorkspaceManager()

        # Clean up any leftover projects from previous hypothesis examples
        projects_dir = tmp_path / "Projects"
        if projects_dir.exists():
            shutil.rmtree(projects_dir)
        projects_dir.mkdir(parents=True, exist_ok=True)

        # Clear the UUID index for fresh state
        manager._uuid_index = {}

        created = await manager.create_project(
            project_name=original_name, workspace_path=ws
        )
        original_id = created["id"]
        original_created_at = created["created_at"]
        original_version = created["version"]

        # Check for name collision before attempting rename
        new_dir = projects_dir / new_name
        if new_dir.exists():
            return  # Skip — collision with existing dir

        updated = await manager.update_project(
            original_id, {"name": new_name}, workspace_path=ws
        )

        # UUID preserved
        assert updated["id"] == original_id

        # created_at preserved
        assert updated["created_at"] == original_created_at

        # Version incremented by exactly 1
        assert updated["version"] == original_version + 1

        # Directory renamed
        assert not (projects_dir / original_name).exists()
        assert (projects_dir / new_name).exists()

        # History entry correct
        last_entry = updated["update_history"][-1]
        assert last_entry["action"] == "renamed"
        assert last_entry["changes"]["name"]["from"] == original_name
        assert last_entry["changes"]["name"]["to"] == new_name
