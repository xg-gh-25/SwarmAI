"""Tests for ArtifactManager - hybrid storage with DB metadata + filesystem content.

Tests cover:
- CRUD operations
- Hybrid storage (DB metadata + filesystem content)
- Versioning logic: {filename}_v{NNN}.{ext}
- Tagging support via artifact_tags table
- Type-to-folder mapping

Requirements: 27.1-27.11
"""
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.artifact_manager import ArtifactManager, TYPE_FOLDER_MAP
from schemas.artifact import (
    ArtifactCreate,
    ArtifactResponse,
    ArtifactType,
    ArtifactUpdate,
)
from database import db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def workspace_with_tmp(tmp_path):
    """Create a workspace record pointing to a real tmp directory."""
    now = datetime.now(timezone.utc).isoformat()
    ws_path = str(tmp_path / "TestWS")
    os.makedirs(ws_path, exist_ok=True)

    workspace = await db.swarm_workspaces.put({
        "id": "ws-test-artifacts",
        "name": "TestWS",
        "file_path": ws_path,
        "context": "Test workspace for artifact tests",
        "icon": "📁",
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    })
    return workspace


@pytest.fixture
def manager(workspace_with_tmp):
    """Create an ArtifactManager with a mock workspace_manager that returns paths as-is."""
    class FakeWSManager:
        def expand_path(self, file_path: str):
            return file_path  # paths are already absolute in tests

    return ArtifactManager(workspace_manager=FakeWSManager())


@pytest.fixture
def sample_create_data(workspace_with_tmp):
    """Sample ArtifactCreate data."""
    return ArtifactCreate(
        workspace_id=workspace_with_tmp["id"],
        title="Project Plan",
        artifact_type=ArtifactType.PLAN,
        file_path="Artifacts/Plans/project-plan_v001.md",
        created_by="user-1",
        tags=["important", "q1"],
    )


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------

class TestArtifactManagerCreate:
    """Tests for artifact creation with hybrid storage."""

    @pytest.mark.asyncio
    async def test_create_stores_metadata_in_db(self, manager, sample_create_data):
        """Requirement 27.2: metadata stored in database."""
        result = await manager.create(sample_create_data, content="# Plan\nContent here")

        assert isinstance(result, ArtifactResponse)
        assert result.title == "Project Plan"
        assert result.artifact_type == ArtifactType.PLAN
        assert result.version == 1
        assert result.created_by == "user-1"
        assert result.workspace_id == sample_create_data.workspace_id

    @pytest.mark.asyncio
    async def test_create_writes_content_to_filesystem(self, manager, sample_create_data, workspace_with_tmp):
        """Requirement 27.1: content stored as files in workspace filesystem."""
        content = "# Project Plan\n\nThis is the plan."
        result = await manager.create(sample_create_data, content=content)

        # Verify file exists on disk
        ws_path = workspace_with_tmp["file_path"]
        file_path = Path(ws_path) / result.file_path
        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == content

    @pytest.mark.asyncio
    async def test_create_uses_correct_type_folder(self, manager, workspace_with_tmp):
        """Requirement 27.1: type subfolders (Plans/, Reports/, Docs/, Decisions/)."""
        for art_type, expected_folder in [
            (ArtifactType.PLAN, "Plans"),
            (ArtifactType.REPORT, "Reports"),
            (ArtifactType.DOC, "Docs"),
            (ArtifactType.DECISION, "Decisions"),
            (ArtifactType.OTHER, "Docs"),
        ]:
            data = ArtifactCreate(
                workspace_id=workspace_with_tmp["id"],
                title=f"Test {art_type.value}",
                artifact_type=art_type,
                file_path=f"Artifacts/{expected_folder}/test.md",
                created_by="user-1",
            )
            result = await manager.create(data, content="test")
            assert f"Artifacts/{expected_folder}/" in result.file_path

    @pytest.mark.asyncio
    async def test_create_with_tags(self, manager, sample_create_data):
        """Requirement 27.7: artifact tagging support."""
        result = await manager.create(sample_create_data, content="content")

        assert result.tags is not None
        assert set(result.tags) == {"important", "q1"}

    @pytest.mark.asyncio
    async def test_create_versioned_filename(self, manager, sample_create_data):
        """Requirement 27.4: versioning format {filename}_v{NNN}.{ext}."""
        result = await manager.create(sample_create_data, content="v1 content")

        assert "_v001.md" in result.file_path

    @pytest.mark.asyncio
    async def test_create_with_empty_content(self, manager, sample_create_data, workspace_with_tmp):
        """Creating an artifact with empty content should still create the file."""
        result = await manager.create(sample_create_data, content="")

        ws_path = workspace_with_tmp["file_path"]
        file_path = Path(ws_path) / result.file_path
        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == ""


class TestArtifactManagerGet:
    """Tests for artifact retrieval."""

    @pytest.mark.asyncio
    async def test_get_existing(self, manager, sample_create_data):
        """Requirement 27.8: retrieve a specific artifact."""
        created = await manager.create(sample_create_data, content="content")
        result = await manager.get(created.id)

        assert result is not None
        assert result.id == created.id
        assert result.title == "Project Plan"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, manager):
        """Returns None for nonexistent artifact."""
        result = await manager.get("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_content(self, manager, sample_create_data):
        """Test reading file content back."""
        content = "# My Plan\nDetailed content here."
        created = await manager.create(sample_create_data, content=content)

        read_content = await manager.get_content(created.id)
        assert read_content == content

    @pytest.mark.asyncio
    async def test_get_content_nonexistent(self, manager):
        """Returns None for nonexistent artifact content."""
        result = await manager.get_content("nonexistent-id")
        assert result is None


class TestArtifactManagerList:
    """Tests for artifact listing."""

    @pytest.mark.asyncio
    async def test_list_by_workspace(self, manager, workspace_with_tmp):
        """Requirement 27.8: list artifacts for a workspace."""
        ws_id = workspace_with_tmp["id"]
        for i in range(3):
            data = ArtifactCreate(
                workspace_id=ws_id,
                title=f"Artifact {i}",
                artifact_type=ArtifactType.DOC,
                file_path=f"Artifacts/Docs/artifact-{i}.md",
                created_by="user-1",
            )
            await manager.create(data, content=f"content {i}")

        results = await manager.list(workspace_id=ws_id)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_list_by_type(self, manager, workspace_with_tmp):
        """Requirement 27.9: list artifacts grouped by type."""
        ws_id = workspace_with_tmp["id"]
        for art_type in [ArtifactType.PLAN, ArtifactType.REPORT, ArtifactType.PLAN]:
            data = ArtifactCreate(
                workspace_id=ws_id,
                title=f"Test {art_type.value}",
                artifact_type=art_type,
                file_path=f"Artifacts/test.md",
                created_by="user-1",
            )
            await manager.create(data, content="content")

        plans = await manager.list(workspace_id=ws_id, artifact_type=ArtifactType.PLAN)
        assert len(plans) == 2

        reports = await manager.list(workspace_id=ws_id, artifact_type=ArtifactType.REPORT)
        assert len(reports) == 1

    @pytest.mark.asyncio
    async def test_list_pagination(self, manager, workspace_with_tmp):
        """Test pagination with limit and offset."""
        ws_id = workspace_with_tmp["id"]
        for i in range(5):
            data = ArtifactCreate(
                workspace_id=ws_id,
                title=f"Artifact {i}",
                artifact_type=ArtifactType.DOC,
                file_path=f"Artifacts/Docs/artifact-{i}.md",
                created_by="user-1",
            )
            await manager.create(data, content=f"content {i}")

        page1 = await manager.list(workspace_id=ws_id, limit=2, offset=0)
        assert len(page1) == 2

        page2 = await manager.list(workspace_id=ws_id, limit=2, offset=2)
        assert len(page2) == 2

        page3 = await manager.list(workspace_id=ws_id, limit=2, offset=4)
        assert len(page3) == 1


class TestArtifactManagerUpdate:
    """Tests for artifact updates and versioning."""

    @pytest.mark.asyncio
    async def test_update_metadata_only(self, manager, sample_create_data):
        """Update metadata without creating a new version."""
        created = await manager.create(sample_create_data, content="original")

        update_data = ArtifactUpdate(title="Updated Plan Title")
        result = await manager.update(created.id, update_data)

        assert result is not None
        assert result.title == "Updated Plan Title"
        assert result.version == 1  # No version bump

    @pytest.mark.asyncio
    async def test_update_with_new_content_creates_version(self, manager, sample_create_data, workspace_with_tmp):
        """Requirement 27.4, 27.5: new version created, previous preserved."""
        created = await manager.create(sample_create_data, content="v1 content")

        update_data = ArtifactUpdate()
        result = await manager.update(created.id, update_data, new_content="v2 content")

        assert result is not None
        assert result.version == 2
        assert "_v002.md" in result.file_path

        # Verify v1 file still exists
        ws_path = workspace_with_tmp["file_path"]
        v1_path = Path(ws_path) / created.file_path
        assert v1_path.exists()
        assert v1_path.read_text(encoding="utf-8") == "v1 content"

        # Verify v2 file exists
        v2_path = Path(ws_path) / result.file_path
        assert v2_path.exists()
        assert v2_path.read_text(encoding="utf-8") == "v2 content"

    @pytest.mark.asyncio
    async def test_update_tags(self, manager, sample_create_data):
        """Requirement 27.7: update tags."""
        created = await manager.create(sample_create_data, content="content")
        assert set(created.tags) == {"important", "q1"}

        update_data = ArtifactUpdate(tags=["updated", "new-tag"])
        result = await manager.update(created.id, update_data)

        assert result is not None
        assert set(result.tags) == {"updated", "new-tag"}

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, manager):
        """Returns None for nonexistent artifact."""
        update_data = ArtifactUpdate(title="New Title")
        result = await manager.update("nonexistent-id", update_data)
        assert result is None

    @pytest.mark.asyncio
    async def test_multiple_version_increments(self, manager, sample_create_data, workspace_with_tmp):
        """Test creating multiple versions sequentially."""
        created = await manager.create(sample_create_data, content="v1")

        v2 = await manager.update(created.id, ArtifactUpdate(), new_content="v2")
        assert v2.version == 2

        v3 = await manager.update(created.id, ArtifactUpdate(), new_content="v3")
        assert v3.version == 3
        assert "_v003.md" in v3.file_path

        # All three files should exist
        ws_path = workspace_with_tmp["file_path"]
        assert (Path(ws_path) / created.file_path).exists()
        assert (Path(ws_path) / v2.file_path).exists()
        assert (Path(ws_path) / v3.file_path).exists()


class TestArtifactManagerDelete:
    """Tests for artifact deletion."""

    @pytest.mark.asyncio
    async def test_delete_metadata_only(self, manager, sample_create_data, workspace_with_tmp):
        """Delete DB record but keep file."""
        created = await manager.create(sample_create_data, content="content")

        result = await manager.delete(created.id, delete_file=False)
        assert result is True

        # DB record gone
        assert await manager.get(created.id) is None

        # File still exists
        ws_path = workspace_with_tmp["file_path"]
        assert (Path(ws_path) / created.file_path).exists()

    @pytest.mark.asyncio
    async def test_delete_with_file(self, manager, sample_create_data, workspace_with_tmp):
        """Delete both DB record and file."""
        created = await manager.create(sample_create_data, content="content")

        result = await manager.delete(created.id, delete_file=True)
        assert result is True

        # DB record gone
        assert await manager.get(created.id) is None

        # File also gone
        ws_path = workspace_with_tmp["file_path"]
        assert not (Path(ws_path) / created.file_path).exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, manager):
        """Returns False for nonexistent artifact."""
        result = await manager.delete("nonexistent-id")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_removes_tags(self, manager, sample_create_data):
        """Deleting an artifact also removes its tags."""
        created = await manager.create(sample_create_data, content="content")
        assert len(created.tags) == 2

        await manager.delete(created.id)

        # Tags should be gone
        tags = await db.artifact_tags.list_by_artifact(created.id)
        assert len(tags) == 0


class TestArtifactManagerTags:
    """Tests for tag operations."""

    @pytest.mark.asyncio
    async def test_add_tag(self, manager, sample_create_data):
        """Requirement 27.7: add a tag."""
        created = await manager.create(sample_create_data, content="content")

        result = await manager.add_tag(created.id, "new-tag")
        assert result is True

        artifact = await manager.get(created.id)
        assert "new-tag" in artifact.tags

    @pytest.mark.asyncio
    async def test_add_duplicate_tag(self, manager, sample_create_data):
        """Adding a duplicate tag is idempotent."""
        created = await manager.create(sample_create_data, content="content")

        await manager.add_tag(created.id, "important")  # already exists
        artifact = await manager.get(created.id)
        assert artifact.tags.count("important") == 1

    @pytest.mark.asyncio
    async def test_remove_tag(self, manager, sample_create_data):
        """Requirement 27.7: remove a tag."""
        created = await manager.create(sample_create_data, content="content")

        result = await manager.remove_tag(created.id, "important")
        assert result is True

        artifact = await manager.get(created.id)
        assert "important" not in artifact.tags
        assert "q1" in artifact.tags

    @pytest.mark.asyncio
    async def test_remove_nonexistent_tag(self, manager, sample_create_data):
        """Removing a tag that doesn't exist returns False."""
        created = await manager.create(sample_create_data, content="content")

        result = await manager.remove_tag(created.id, "nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_list_by_tag(self, manager, workspace_with_tmp):
        """List artifacts by tag."""
        ws_id = workspace_with_tmp["id"]

        d1 = ArtifactCreate(
            workspace_id=ws_id, title="Doc A", artifact_type=ArtifactType.DOC,
            file_path="a.md", created_by="user-1", tags=["shared"],
        )
        d2 = ArtifactCreate(
            workspace_id=ws_id, title="Doc B", artifact_type=ArtifactType.DOC,
            file_path="b.md", created_by="user-1", tags=["shared", "extra"],
        )
        d3 = ArtifactCreate(
            workspace_id=ws_id, title="Doc C", artifact_type=ArtifactType.DOC,
            file_path="c.md", created_by="user-1", tags=["other"],
        )
        await manager.create(d1, content="a")
        await manager.create(d2, content="b")
        await manager.create(d3, content="c")

        results = await manager.list_by_tag("shared")
        assert len(results) == 2
        titles = {r.title for r in results}
        assert titles == {"Doc A", "Doc B"}


class TestArtifactManagerProvenance:
    """Tests for artifact provenance tracking."""

    @pytest.mark.asyncio
    async def test_task_id_tracking(self, manager, workspace_with_tmp):
        """Requirement 27.6: track source task_id."""
        data = ArtifactCreate(
            workspace_id=workspace_with_tmp["id"],
            title="Task Output",
            artifact_type=ArtifactType.REPORT,
            file_path="Artifacts/Reports/output.md",
            created_by="agent-1",
            task_id="task-123",
        )
        result = await manager.create(data, content="report content")

        assert result.task_id == "task-123"
        assert result.created_by == "agent-1"

    @pytest.mark.asyncio
    async def test_created_by_tracking(self, manager, workspace_with_tmp):
        """Requirement 27.6: track created_by (user or agent)."""
        data = ArtifactCreate(
            workspace_id=workspace_with_tmp["id"],
            title="User Doc",
            artifact_type=ArtifactType.DOC,
            file_path="Artifacts/Docs/user-doc.md",
            created_by="user-jane",
        )
        result = await manager.create(data, content="user content")
        assert result.created_by == "user-jane"


class TestBuildVersionedFilename:
    """Tests for the filename sanitization and versioning logic."""

    def test_basic_title(self):
        mgr = ArtifactManager()
        assert mgr._build_versioned_filename("Project Plan", 1) == "project-plan_v001.md"

    def test_special_characters(self):
        mgr = ArtifactManager()
        result = mgr._build_versioned_filename("My Doc! @#$%", 2)
        # Special chars stripped, spaces become dashes
        assert result.endswith("_v002.md")
        assert "!" not in result
        assert "@" not in result

    def test_empty_title(self):
        mgr = ArtifactManager()
        assert mgr._build_versioned_filename("", 1) == "artifact_v001.md"

    def test_high_version(self):
        mgr = ArtifactManager()
        assert mgr._build_versioned_filename("Report", 42) == "report_v042.md"

    def test_custom_extension(self):
        mgr = ArtifactManager()
        assert mgr._build_versioned_filename("Data", 1, ".json") == "data_v001.json"
