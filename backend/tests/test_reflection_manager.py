"""Tests for ReflectionManager - hybrid storage with DB metadata + filesystem content.

Tests cover:
- CRUD operations
- Hybrid storage (DB metadata + filesystem content)
- File naming convention: {reflection_type}_{date}.md
- Daily recap generation (aggregate completed tasks, handled signals)
- Weekly summary generation (aggregate daily recaps)

Requirements: 28.1-28.11
"""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from core.reflection_manager import ReflectionManager, REFLECTIONS_SUBFOLDER
from schemas.reflection import (
    GeneratedBy,
    ReflectionCreate,
    ReflectionResponse,
    ReflectionType,
    ReflectionUpdate,
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
        "id": "ws-test-reflections",
        "name": "TestWS",
        "file_path": ws_path,
        "context": "Test workspace for reflection tests",
        "icon": "📁",
        "is_default": False,
        "created_at": now,
        "updated_at": now,
    })
    return workspace


@pytest.fixture
def manager(workspace_with_tmp):
    """Create a ReflectionManager with a mock workspace_manager that returns paths as-is."""
    class FakeWSManager:
        def expand_path(self, file_path: str):
            return file_path  # paths are already absolute in tests

    return ReflectionManager(workspace_manager=FakeWSManager())


@pytest.fixture
def sample_create_data(workspace_with_tmp):
    """Sample ReflectionCreate data for a daily recap."""
    return ReflectionCreate(
        workspace_id=workspace_with_tmp["id"],
        reflection_type=ReflectionType.DAILY_RECAP,
        title="Daily Recap - 2025-02-21",
        file_path="Artifacts/Reports/daily_recap_2025-02-21.md",
        period_start=datetime(2025, 2, 21, 0, 0, 0, tzinfo=timezone.utc),
        period_end=datetime(2025, 2, 21, 23, 59, 59, tzinfo=timezone.utc),
        generated_by=GeneratedBy.SYSTEM,
    )


# ---------------------------------------------------------------------------
# CRUD Tests
# ---------------------------------------------------------------------------

class TestReflectionManagerCreate:
    """Tests for ReflectionManager.create()."""

    @pytest.mark.asyncio
    async def test_create_stores_metadata_in_db(self, manager, sample_create_data):
        """Requirement 28.2: metadata stored in database."""
        result = await manager.create(sample_create_data, content="# Daily Recap")

        assert isinstance(result, ReflectionResponse)
        assert result.workspace_id == sample_create_data.workspace_id
        assert result.reflection_type == ReflectionType.DAILY_RECAP
        assert result.title == "Daily Recap - 2025-02-21"
        assert result.generated_by == GeneratedBy.SYSTEM

    @pytest.mark.asyncio
    async def test_create_writes_content_to_filesystem(self, manager, sample_create_data, workspace_with_tmp):
        """Requirement 28.1: content stored as markdown files in Artifacts/Reports/."""
        content = "# Daily Recap\n\nSome content here."
        result = await manager.create(sample_create_data, content=content)

        ws_path = workspace_with_tmp["file_path"]
        file_path = Path(ws_path) / result.file_path
        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == content

    @pytest.mark.asyncio
    async def test_create_uses_correct_naming(self, manager, sample_create_data):
        """Requirement 28.1: naming convention {reflection_type}_{date}.md."""
        result = await manager.create(sample_create_data, content="test")

        assert "daily_recap_2025-02-21.md" in result.file_path
        assert result.file_path.startswith("Artifacts/Reports/")

    @pytest.mark.asyncio
    async def test_create_with_empty_content(self, manager, sample_create_data, workspace_with_tmp):
        """Creating a reflection with empty content should still create the file."""
        result = await manager.create(sample_create_data, content="")

        ws_path = workspace_with_tmp["file_path"]
        file_path = Path(ws_path) / result.file_path
        assert file_path.exists()
        assert file_path.read_text(encoding="utf-8") == ""

    @pytest.mark.asyncio
    async def test_create_weekly_summary(self, manager, workspace_with_tmp):
        """Requirement 28.3: support weekly_summary type."""
        data = ReflectionCreate(
            workspace_id=workspace_with_tmp["id"],
            reflection_type=ReflectionType.WEEKLY_SUMMARY,
            title="Weekly Summary - 2025-02-17",
            file_path="pending",
            period_start=datetime(2025, 2, 17, tzinfo=timezone.utc),
            period_end=datetime(2025, 2, 23, tzinfo=timezone.utc),
            generated_by=GeneratedBy.SYSTEM,
        )
        result = await manager.create(data, content="# Weekly Summary")

        assert result.reflection_type == ReflectionType.WEEKLY_SUMMARY
        assert "weekly_summary_2025-02-17.md" in result.file_path

    @pytest.mark.asyncio
    async def test_create_lessons_learned(self, manager, workspace_with_tmp):
        """Requirement 28.3: support lessons_learned type."""
        data = ReflectionCreate(
            workspace_id=workspace_with_tmp["id"],
            reflection_type=ReflectionType.LESSONS_LEARNED,
            title="Lessons Learned",
            file_path="pending",
            period_start=datetime(2025, 2, 1, tzinfo=timezone.utc),
            period_end=datetime(2025, 2, 28, tzinfo=timezone.utc),
            generated_by=GeneratedBy.USER,
        )
        result = await manager.create(data, content="# Lessons")

        assert result.reflection_type == ReflectionType.LESSONS_LEARNED
        assert result.generated_by == GeneratedBy.USER


class TestReflectionManagerGet:
    """Tests for ReflectionManager.get() and get_content()."""

    @pytest.mark.asyncio
    async def test_get_existing(self, manager, sample_create_data):
        """Requirement 28.9: GET endpoint retrieves a reflection."""
        created = await manager.create(sample_create_data, content="test content")
        result = await manager.get(created.id)

        assert result is not None
        assert result.id == created.id
        assert result.title == created.title

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, manager):
        """Getting a nonexistent reflection returns None."""
        result = await manager.get("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_content(self, manager, sample_create_data):
        """Reading content from filesystem via get_content."""
        content = "# My Reflection\n\nDetailed content."
        created = await manager.create(sample_create_data, content=content)
        result = await manager.get_content(created.id)

        assert result == content

    @pytest.mark.asyncio
    async def test_get_content_nonexistent(self, manager):
        """get_content for nonexistent reflection returns None."""
        result = await manager.get_content("nonexistent-id")
        assert result is None


class TestReflectionManagerList:
    """Tests for ReflectionManager.list()."""

    @pytest.mark.asyncio
    async def test_list_by_workspace(self, manager, workspace_with_tmp):
        """Requirement 28.10: list reflections grouped by type."""
        ws_id = workspace_with_tmp["id"]
        for i in range(3):
            data = ReflectionCreate(
                workspace_id=ws_id,
                reflection_type=ReflectionType.DAILY_RECAP,
                title=f"Recap {i}",
                file_path="pending",
                period_start=datetime(2025, 2, 20 + i, tzinfo=timezone.utc),
                period_end=datetime(2025, 2, 20 + i, 23, 59, 59, tzinfo=timezone.utc),
                generated_by=GeneratedBy.SYSTEM,
            )
            await manager.create(data, content=f"Content {i}")

        results = await manager.list(workspace_id=ws_id)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_list_by_type(self, manager, workspace_with_tmp):
        """Filter by reflection_type."""
        ws_id = workspace_with_tmp["id"]

        await manager.create(ReflectionCreate(
            workspace_id=ws_id, reflection_type=ReflectionType.DAILY_RECAP,
            title="Recap", file_path="pending",
            period_start=datetime(2025, 2, 21, tzinfo=timezone.utc),
            period_end=datetime(2025, 2, 21, tzinfo=timezone.utc),
            generated_by=GeneratedBy.SYSTEM,
        ), content="recap")

        await manager.create(ReflectionCreate(
            workspace_id=ws_id, reflection_type=ReflectionType.LESSONS_LEARNED,
            title="Lesson", file_path="pending",
            period_start=datetime(2025, 2, 21, tzinfo=timezone.utc),
            period_end=datetime(2025, 2, 21, tzinfo=timezone.utc),
            generated_by=GeneratedBy.USER,
        ), content="lesson")

        recaps = await manager.list(workspace_id=ws_id, reflection_type=ReflectionType.DAILY_RECAP)
        assert len(recaps) == 1
        assert recaps[0].reflection_type == ReflectionType.DAILY_RECAP

    @pytest.mark.asyncio
    async def test_list_pagination(self, manager, workspace_with_tmp):
        """Pagination with limit and offset."""
        ws_id = workspace_with_tmp["id"]
        for i in range(5):
            data = ReflectionCreate(
                workspace_id=ws_id, reflection_type=ReflectionType.DAILY_RECAP,
                title=f"Recap {i}", file_path="pending",
                period_start=datetime(2025, 2, 20 + i, tzinfo=timezone.utc),
                period_end=datetime(2025, 2, 20 + i, tzinfo=timezone.utc),
                generated_by=GeneratedBy.SYSTEM,
            )
            await manager.create(data, content=f"Content {i}")

        page1 = await manager.list(workspace_id=ws_id, limit=2, offset=0)
        page2 = await manager.list(workspace_id=ws_id, limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2


class TestReflectionManagerUpdate:
    """Tests for ReflectionManager.update()."""

    @pytest.mark.asyncio
    async def test_update_metadata_only(self, manager, sample_create_data):
        """Requirement 28.8: allow editing reflections."""
        created = await manager.create(sample_create_data, content="original")
        updated = await manager.update(
            created.id,
            ReflectionUpdate(title="Updated Title"),
        )

        assert updated is not None
        assert updated.title == "Updated Title"
        assert updated.id == created.id

    @pytest.mark.asyncio
    async def test_update_with_new_content(self, manager, sample_create_data, workspace_with_tmp):
        """Requirement 28.8: allow editing AI-generated reflections."""
        created = await manager.create(sample_create_data, content="original content")
        updated = await manager.update(
            created.id,
            ReflectionUpdate(),
            new_content="updated content",
        )

        assert updated is not None
        content = await manager.get_content(updated.id)
        assert content == "updated content"

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, manager):
        """Updating a nonexistent reflection returns None."""
        result = await manager.update("nonexistent-id", ReflectionUpdate(title="X"))
        assert result is None

    @pytest.mark.asyncio
    async def test_update_no_changes(self, manager, sample_create_data):
        """Updating with no changes returns the existing reflection."""
        created = await manager.create(sample_create_data, content="test")
        result = await manager.update(created.id, ReflectionUpdate())

        assert result is not None
        assert result.id == created.id


class TestReflectionManagerDelete:
    """Tests for ReflectionManager.delete()."""

    @pytest.mark.asyncio
    async def test_delete_metadata_only(self, manager, sample_create_data, workspace_with_tmp):
        """Requirement 28.9: DELETE endpoint removes metadata."""
        created = await manager.create(sample_create_data, content="test")
        ws_path = workspace_with_tmp["file_path"]
        file_path = Path(ws_path) / created.file_path

        result = await manager.delete(created.id, delete_file=False)
        assert result is True

        # DB record gone
        assert await manager.get(created.id) is None
        # File still exists
        assert file_path.exists()

    @pytest.mark.asyncio
    async def test_delete_with_file(self, manager, sample_create_data, workspace_with_tmp):
        """Delete both metadata and filesystem content."""
        created = await manager.create(sample_create_data, content="test")
        ws_path = workspace_with_tmp["file_path"]
        file_path = Path(ws_path) / created.file_path

        result = await manager.delete(created.id, delete_file=True)
        assert result is True

        assert await manager.get(created.id) is None
        assert not file_path.exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, manager):
        """Deleting a nonexistent reflection returns False."""
        result = await manager.delete("nonexistent-id")
        assert result is False


# ---------------------------------------------------------------------------
# Daily Recap Generation Tests
# ---------------------------------------------------------------------------

class TestDailyRecapGeneration:
    """Tests for ReflectionManager.generate_daily_recap()."""

    @pytest.mark.asyncio
    async def test_generate_daily_recap_empty(self, manager, workspace_with_tmp):
        """Requirement 28.6: generate daily recap with no tasks or signals."""
        ws_id = workspace_with_tmp["id"]
        date = datetime(2025, 2, 21, 12, 0, 0, tzinfo=timezone.utc)

        result = await manager.generate_daily_recap(ws_id, date=date)

        assert isinstance(result, ReflectionResponse)
        assert result.reflection_type == ReflectionType.DAILY_RECAP
        assert result.generated_by == GeneratedBy.SYSTEM
        assert "2025-02-21" in result.title

        content = await manager.get_content(result.id)
        assert "# Daily Recap - 2025-02-21" in content
        assert "No tasks completed today" in content
        assert "No signals handled today" in content

    @pytest.mark.asyncio
    async def test_generate_daily_recap_with_tasks(self, manager, workspace_with_tmp):
        """Requirement 28.6: aggregate completed tasks."""
        ws_id = workspace_with_tmp["id"]
        date = datetime(2025, 2, 21, 12, 0, 0, tzinfo=timezone.utc)

        # Create a completed task within the day.
        # Use raw SQL insert to preserve the completed_at timestamp,
        # since db.tasks.put() overwrites updated_at with current time.
        import aiosqlite
        now_str = datetime(2025, 2, 21, 15, 0, 0, tzinfo=timezone.utc).isoformat()
        async with aiosqlite.connect(str(db.tasks.db_path)) as conn:
            await conn.execute(
                "INSERT INTO tasks (id, agent_id, workspace_id, title, status, completed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("task-1", "default", ws_id, "Implement feature X", "completed", now_str, now_str, now_str),
            )
            await conn.commit()

        result = await manager.generate_daily_recap(ws_id, date=date)
        content = await manager.get_content(result.id)

        assert "Implement feature X" in content
        assert "Tasks completed**: 1" in content

    @pytest.mark.asyncio
    async def test_generate_daily_recap_with_signals(self, manager, workspace_with_tmp):
        """Requirement 28.6: aggregate handled signals."""
        ws_id = workspace_with_tmp["id"]
        date = datetime(2025, 2, 21, 12, 0, 0, tzinfo=timezone.utc)

        # Create a handled signal within the day.
        # Use raw SQL insert to preserve the updated_at timestamp,
        # since db.todos.put() overwrites updated_at with current time.
        import aiosqlite
        now_str = datetime(2025, 2, 21, 10, 0, 0, tzinfo=timezone.utc).isoformat()
        async with aiosqlite.connect(str(db.todos.db_path)) as conn:
            await conn.execute(
                "INSERT INTO todos (id, workspace_id, title, status, source_type, priority, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("todo-1", ws_id, "Review PR #42", "handled", "manual", "medium", now_str, now_str),
            )
            await conn.commit()

        result = await manager.generate_daily_recap(ws_id, date=date)
        content = await manager.get_content(result.id)

        assert "Review PR #42" in content
        assert "Signals handled**: 1" in content


# ---------------------------------------------------------------------------
# Weekly Summary Generation Tests
# ---------------------------------------------------------------------------

class TestWeeklySummaryGeneration:
    """Tests for ReflectionManager.generate_weekly_summary()."""

    @pytest.mark.asyncio
    async def test_generate_weekly_summary_empty(self, manager, workspace_with_tmp):
        """Requirement 28.7: generate weekly summary with no data."""
        ws_id = workspace_with_tmp["id"]
        # Monday of a specific week
        week_start = datetime(2025, 2, 17, 0, 0, 0, tzinfo=timezone.utc)

        result = await manager.generate_weekly_summary(ws_id, week_start=week_start)

        assert isinstance(result, ReflectionResponse)
        assert result.reflection_type == ReflectionType.WEEKLY_SUMMARY
        assert result.generated_by == GeneratedBy.SYSTEM

        content = await manager.get_content(result.id)
        assert "# Weekly Summary - 2025-02-17 to 2025-02-23" in content
        assert "Total tasks completed**: 0" in content

    @pytest.mark.asyncio
    async def test_generate_weekly_summary_with_recaps(self, manager, workspace_with_tmp):
        """Requirement 28.7: aggregate daily recaps in weekly summary."""
        ws_id = workspace_with_tmp["id"]
        week_start = datetime(2025, 2, 17, 0, 0, 0, tzinfo=timezone.utc)

        # Generate a daily recap for a day in the week
        recap_date = datetime(2025, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
        await manager.generate_daily_recap(ws_id, date=recap_date)

        result = await manager.generate_weekly_summary(ws_id, week_start=week_start)
        content = await manager.get_content(result.id)

        assert "Daily recaps generated**: 1" in content


# ---------------------------------------------------------------------------
# Filename Building Tests
# ---------------------------------------------------------------------------

class TestBuildFilename:
    """Tests for ReflectionManager._build_filename()."""

    def test_daily_recap_filename(self):
        mgr = ReflectionManager()
        date = datetime(2025, 2, 21, tzinfo=timezone.utc)
        result = mgr._build_filename(ReflectionType.DAILY_RECAP, date)
        assert result == "daily_recap_2025-02-21.md"

    def test_weekly_summary_filename(self):
        mgr = ReflectionManager()
        date = datetime(2025, 2, 17, tzinfo=timezone.utc)
        result = mgr._build_filename(ReflectionType.WEEKLY_SUMMARY, date)
        assert result == "weekly_summary_2025-02-17.md"

    def test_lessons_learned_filename(self):
        mgr = ReflectionManager()
        date = datetime(2025, 3, 1, tzinfo=timezone.utc)
        result = mgr._build_filename(ReflectionType.LESSONS_LEARNED, date)
        assert result == "lessons_learned_2025-03-01.md"
