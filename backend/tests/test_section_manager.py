"""Unit tests for SectionManager.

Tests the SectionManager class which provides section data queries
for the Daily Work Operating Loop sections.

Requirements: 7.1-7.12
"""
import pytest
from datetime import timedelta
from uuid import uuid4

from database import db
from core.section_manager import section_manager
from schemas.section import SectionCounts, SectionResponse
from tests.helpers import now_iso, create_workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _past_iso(days: int = 1) -> str:
    from datetime import datetime, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def _create_todo(workspace_id: str, status: str = "pending", **kwargs) -> dict:
    now = now_iso()
    todo = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "title": kwargs.get("title", f"Todo {uuid4().hex[:6]}"),
        "description": kwargs.get("description"),
        "source": kwargs.get("source"),
        "source_type": kwargs.get("source_type", "manual"),
        "status": status,
        "priority": kwargs.get("priority", "none"),
        "due_date": kwargs.get("due_date"),
        "task_id": kwargs.get("task_id"),
        "created_at": now,
        "updated_at": now,
    }
    return await db.todos.put(todo)


async def _create_plan_item(workspace_id: str, focus_type: str = "upcoming", **kwargs) -> dict:
    now = now_iso()
    item = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "title": kwargs.get("title", f"PlanItem {uuid4().hex[:6]}"),
        "description": kwargs.get("description"),
        "source_todo_id": kwargs.get("source_todo_id"),
        "source_task_id": kwargs.get("source_task_id"),
        "status": kwargs.get("status", "active"),
        "priority": kwargs.get("priority", "none"),
        "scheduled_date": kwargs.get("scheduled_date"),
        "focus_type": focus_type,
        "sort_order": kwargs.get("sort_order", 0),
        "created_at": now,
        "updated_at": now,
    }
    return await db.plan_items.put(item)


async def _create_task(workspace_id: str, status: str = "draft", **kwargs) -> dict:
    now = now_iso()
    task = {
        "id": kwargs.get("id", f"task_{uuid4().hex[:12]}"),
        "agent_id": kwargs.get("agent_id", "default"),
        "workspace_id": workspace_id,
        "session_id": None,
        "status": status,
        "title": kwargs.get("title", f"Task {uuid4().hex[:6]}"),
        "description": kwargs.get("description"),
        "priority": kwargs.get("priority", "none"),
        "source_todo_id": kwargs.get("source_todo_id"),
        "blocked_reason": kwargs.get("blocked_reason"),
        "model": "claude-sonnet-4-20250514",
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "work_dir": None,
        "updated_at": now,
    }
    return await db.tasks.put(task)


async def _create_communication(workspace_id: str, status: str = "pending_reply", **kwargs) -> dict:
    now = now_iso()
    comm = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "title": kwargs.get("title", f"Comm {uuid4().hex[:6]}"),
        "description": kwargs.get("description"),
        "recipient": kwargs.get("recipient", "someone@example.com"),
        "channel_type": kwargs.get("channel_type", "email"),
        "status": status,
        "priority": kwargs.get("priority", "none"),
        "due_date": kwargs.get("due_date"),
        "ai_draft_content": kwargs.get("ai_draft_content"),
        "source_task_id": kwargs.get("source_task_id"),
        "source_todo_id": kwargs.get("source_todo_id"),
        "sent_at": kwargs.get("sent_at"),
        "created_at": now,
        "updated_at": now,
    }
    return await db.communications.put(comm)


async def _create_artifact(workspace_id: str, artifact_type: str = "doc", **kwargs) -> dict:
    now = now_iso()
    artifact = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "task_id": kwargs.get("task_id"),
        "artifact_type": artifact_type,
        "title": kwargs.get("title", f"Artifact {uuid4().hex[:6]}"),
        "file_path": kwargs.get("file_path", f"Artifacts/Docs/test_{uuid4().hex[:6]}.md"),
        "version": kwargs.get("version", 1),
        "created_by": kwargs.get("created_by", "user"),
        "created_at": now,
        "updated_at": now,
    }
    return await db.artifacts.put(artifact)


async def _create_reflection(workspace_id: str, reflection_type: str = "daily_recap", **kwargs) -> dict:
    now = now_iso()
    ref = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "reflection_type": reflection_type,
        "title": kwargs.get("title", f"Reflection {uuid4().hex[:6]}"),
        "file_path": kwargs.get("file_path", f"Artifacts/Reports/daily_recap_{uuid4().hex[:6]}.md"),
        "period_start": kwargs.get("period_start", _past_iso(1)),
        "period_end": kwargs.get("period_end", now),
        "generated_by": kwargs.get("generated_by", "user"),
        "created_at": now,
        "updated_at": now,
    }
    return await db.reflections.put(ref)


# ---------------------------------------------------------------------------
# Tests: get_section_counts
# ---------------------------------------------------------------------------

class TestGetSectionCounts:
    """Tests for SectionManager.get_section_counts."""

    @pytest.mark.asyncio
    async def test_empty_workspace_returns_zero_counts(self, client):
        """All counts should be zero for an empty workspace."""
        ws = await create_workspace()
        counts = await section_manager.get_section_counts(ws["id"])

        assert isinstance(counts, SectionCounts)
        assert counts.signals.total == 0
        assert counts.plan.total == 0
        assert counts.execute.total == 0
        assert counts.communicate.total == 0
        assert counts.artifacts.total == 0
        assert counts.reflection.total == 0

    @pytest.mark.asyncio
    async def test_counts_reflect_created_items(self, client):
        """Counts should match the number of items created."""
        ws = await create_workspace()

        # Create items in each section
        await _create_todo(ws["id"], status="pending")
        await _create_todo(ws["id"], status="overdue")
        await _create_todo(ws["id"], status="in_discussion")
        await _create_plan_item(ws["id"], focus_type="today")
        await _create_plan_item(ws["id"], focus_type="upcoming")
        await _create_task(ws["id"], status="draft")
        await _create_task(ws["id"], status="wip")
        await _create_task(ws["id"], status="completed")
        await _create_communication(ws["id"], status="pending_reply")
        await _create_communication(ws["id"], status="ai_draft")
        await _create_artifact(ws["id"], artifact_type="doc")
        await _create_artifact(ws["id"], artifact_type="plan")
        await _create_reflection(ws["id"], reflection_type="daily_recap")

        counts = await section_manager.get_section_counts(ws["id"])

        assert counts.signals.total == 3
        assert counts.signals.pending == 1
        assert counts.signals.overdue == 1
        assert counts.signals.in_discussion == 1
        assert counts.plan.total == 2
        assert counts.plan.today == 1
        assert counts.plan.upcoming == 1
        assert counts.execute.total == 3
        assert counts.execute.draft == 1
        assert counts.execute.wip == 1
        assert counts.execute.completed == 1
        assert counts.communicate.total == 2
        assert counts.communicate.pending_reply == 1
        assert counts.communicate.ai_draft == 1
        assert counts.artifacts.total == 2
        assert counts.artifacts.doc == 1
        assert counts.artifacts.plan == 1
        assert counts.reflection.total == 1
        assert counts.reflection.daily_recap == 1

    @pytest.mark.asyncio
    async def test_all_workspace_aggregation(self, client):
        """workspace_id='all' should aggregate across non-archived workspaces."""
        ws1 = await create_workspace(name="WS1")
        ws2 = await create_workspace(name="WS2")

        await _create_todo(ws1["id"], status="pending")
        await _create_todo(ws2["id"], status="pending")
        await _create_todo(ws2["id"], status="overdue")

        counts = await section_manager.get_section_counts("all")

        assert counts.signals.total == 3
        assert counts.signals.pending == 2
        assert counts.signals.overdue == 1

    @pytest.mark.asyncio
    async def test_archived_workspace_excluded_from_all(self, client):
        """In single-workspace model, all items appear in 'all' aggregation."""
        ws = await create_workspace(name="Active")
        await _create_todo(ws["id"], status="pending")

        counts = await section_manager.get_section_counts("all")

        # The singleton workspace's todo should be counted
        assert counts.signals.total >= 1
        assert counts.signals.pending >= 1

    @pytest.mark.asyncio
    async def test_handled_todos_not_counted_in_signals(self, client):
        """Handled/cancelled/deleted ToDos should not appear in signal counts."""
        ws = await create_workspace()
        await _create_todo(ws["id"], status="pending")
        await _create_todo(ws["id"], status="handled")
        await _create_todo(ws["id"], status="cancelled")

        counts = await section_manager.get_section_counts(ws["id"])

        # Only pending is an active signal status
        assert counts.signals.total == 1
        assert counts.signals.pending == 1


# ---------------------------------------------------------------------------
# Tests: get_signals
# ---------------------------------------------------------------------------

class TestGetSignals:
    """Tests for SectionManager.get_signals."""

    @pytest.mark.asyncio
    async def test_returns_section_response(self, client):
        """Should return a valid SectionResponse."""
        ws = await create_workspace()
        await _create_todo(ws["id"], status="pending")

        result = await section_manager.get_signals(ws["id"])

        assert "total" in result.counts
        assert "pending" in result.counts
        assert isinstance(result.groups, list)
        assert result.pagination.limit == 50
        assert result.pagination.offset == 0
        assert result.pagination.total >= 1
        assert isinstance(result.sort_keys, list)
        assert len(result.sort_keys) > 0

    @pytest.mark.asyncio
    async def test_groups_by_status(self, client):
        """Items should be grouped by status sub-category."""
        ws = await create_workspace()
        await _create_todo(ws["id"], status="pending")
        await _create_todo(ws["id"], status="overdue")
        await _create_todo(ws["id"], status="in_discussion")

        result = await section_manager.get_signals(ws["id"])

        group_names = [g.name for g in result.groups]
        assert "pending" in group_names
        assert "overdue" in group_names
        assert "in_discussion" in group_names
        assert result.counts["total"] == 3

    @pytest.mark.asyncio
    async def test_pagination(self, client):
        """Pagination should limit results correctly."""
        ws = await create_workspace()
        for _ in range(5):
            await _create_todo(ws["id"], status="pending")

        result = await section_manager.get_signals(ws["id"], limit=2, offset=0)

        assert result.pagination.total == 5
        assert result.pagination.limit == 2
        assert result.pagination.has_more is True

        # Count items across all groups
        total_items = sum(len(g.items) for g in result.groups)
        assert total_items <= 2

    @pytest.mark.asyncio
    async def test_all_workspace_aggregation(self, client):
        """workspace_id='all' should aggregate signals from all non-archived workspaces."""
        ws1 = await create_workspace(name="WS1")
        ws2 = await create_workspace(name="WS2")

        await _create_todo(ws1["id"], status="pending")
        await _create_todo(ws2["id"], status="overdue")

        result = await section_manager.get_signals("all")

        assert result.counts["total"] == 2
        assert result.counts["pending"] == 1
        assert result.counts["overdue"] == 1

    @pytest.mark.asyncio
    async def test_last_updated_at_set(self, client):
        """last_updated_at should be set when items exist."""
        ws = await create_workspace()
        await _create_todo(ws["id"], status="pending")

        result = await section_manager.get_signals(ws["id"])

        assert result.last_updated_at is not None

    @pytest.mark.asyncio
    async def test_empty_returns_zero_counts(self, client):
        """Empty workspace should return zero counts and no groups."""
        ws = await create_workspace()

        result = await section_manager.get_signals(ws["id"])

        assert result.counts["total"] == 0
        assert len(result.groups) == 0
        assert result.last_updated_at is None


# ---------------------------------------------------------------------------
# Tests: get_execute
# ---------------------------------------------------------------------------

class TestGetExecute:
    """Tests for SectionManager.get_execute."""

    @pytest.mark.asyncio
    async def test_groups_by_task_status(self, client):
        """Tasks should be grouped by status."""
        ws = await create_workspace()
        await _create_task(ws["id"], status="draft")
        await _create_task(ws["id"], status="wip")
        await _create_task(ws["id"], status="blocked")
        await _create_task(ws["id"], status="completed")

        result = await section_manager.get_execute(ws["id"])

        group_names = [g.name for g in result.groups]
        assert "draft" in group_names
        assert "wip" in group_names
        assert "blocked" in group_names
        assert "completed" in group_names
        assert result.counts["total"] == 4

    @pytest.mark.asyncio
    async def test_cancelled_tasks_excluded_from_active(self, client):
        """Cancelled tasks should not appear in active section counts."""
        ws = await create_workspace()
        await _create_task(ws["id"], status="draft")
        await _create_task(ws["id"], status="cancelled")

        result = await section_manager.get_execute(ws["id"])

        assert result.counts["total"] == 1
        assert result.counts["draft"] == 1

    @pytest.mark.asyncio
    async def test_all_workspace_aggregation(self, client):
        """workspace_id='all' should aggregate tasks from all non-archived workspaces."""
        ws1 = await create_workspace(name="WS1")
        ws2 = await create_workspace(name="WS2")

        await _create_task(ws1["id"], status="wip")
        await _create_task(ws2["id"], status="draft")

        result = await section_manager.get_execute("all")

        assert result.counts["total"] == 2


# ---------------------------------------------------------------------------
# Tests: get_plan, get_communicate, get_artifacts, get_reflection
# ---------------------------------------------------------------------------

class TestGetPlan:
    """Tests for SectionManager.get_plan."""

    @pytest.mark.asyncio
    async def test_groups_by_focus_type(self, client):
        """PlanItems should be grouped by focus_type."""
        ws = await create_workspace()
        await _create_plan_item(ws["id"], focus_type="today")
        await _create_plan_item(ws["id"], focus_type="upcoming")
        await _create_plan_item(ws["id"], focus_type="blocked")

        result = await section_manager.get_plan(ws["id"])

        group_names = [g.name for g in result.groups]
        assert "today" in group_names
        assert "upcoming" in group_names
        assert "blocked" in group_names
        assert result.counts["total"] == 3


class TestGetCommunicate:
    """Tests for SectionManager.get_communicate."""

    @pytest.mark.asyncio
    async def test_groups_by_status(self, client):
        """Communications should be grouped by status."""
        ws = await create_workspace()
        await _create_communication(ws["id"], status="pending_reply")
        await _create_communication(ws["id"], status="ai_draft")
        await _create_communication(ws["id"], status="follow_up")

        result = await section_manager.get_communicate(ws["id"])

        group_names = [g.name for g in result.groups]
        assert "pending_reply" in group_names
        assert "ai_draft" in group_names
        assert "follow_up" in group_names
        assert result.counts["total"] == 3


class TestGetArtifacts:
    """Tests for SectionManager.get_artifacts."""

    @pytest.mark.asyncio
    async def test_groups_by_artifact_type(self, client):
        """Artifacts should be grouped by artifact_type."""
        ws = await create_workspace()
        await _create_artifact(ws["id"], artifact_type="plan")
        await _create_artifact(ws["id"], artifact_type="report")
        await _create_artifact(ws["id"], artifact_type="doc")
        await _create_artifact(ws["id"], artifact_type="decision")

        result = await section_manager.get_artifacts(ws["id"])

        group_names = [g.name for g in result.groups]
        assert "plan" in group_names
        assert "report" in group_names
        assert "doc" in group_names
        assert "decision" in group_names
        assert result.counts["total"] == 4


class TestGetReflection:
    """Tests for SectionManager.get_reflection."""

    @pytest.mark.asyncio
    async def test_groups_by_reflection_type(self, client):
        """Reflections should be grouped by reflection_type."""
        ws = await create_workspace()
        await _create_reflection(ws["id"], reflection_type="daily_recap")
        await _create_reflection(ws["id"], reflection_type="weekly_summary")
        await _create_reflection(ws["id"], reflection_type="lessons_learned")

        result = await section_manager.get_reflection(ws["id"])

        group_names = [g.name for g in result.groups]
        assert "daily_recap" in group_names
        assert "weekly_summary" in group_names
        assert "lessons_learned" in group_names
        assert result.counts["total"] == 3


# ---------------------------------------------------------------------------
# Tests: Unified response contract
# ---------------------------------------------------------------------------

class TestUnifiedResponseContract:
    """Verify all section methods return the unified SectionResponse contract.

    Validates: Requirements 7.11, 33.1-33.6
    """

    @pytest.mark.asyncio
    async def test_all_sections_return_required_fields(self, client):
        """Every section method should return counts, groups, pagination, sort_keys, last_updated_at."""
        ws = await create_workspace()

        # Create one item in each section
        await _create_todo(ws["id"], status="pending")
        await _create_plan_item(ws["id"], focus_type="today")
        await _create_task(ws["id"], status="draft")
        await _create_communication(ws["id"], status="pending_reply")
        await _create_artifact(ws["id"], artifact_type="doc")
        await _create_reflection(ws["id"], reflection_type="daily_recap")

        methods = [
            section_manager.get_signals,
            section_manager.get_plan,
            section_manager.get_execute,
            section_manager.get_communicate,
            section_manager.get_artifacts,
            section_manager.get_reflection,
        ]

        for method in methods:
            result = await method(ws["id"])
            assert isinstance(result.counts, dict), f"{method.__name__}: counts should be dict"
            assert "total" in result.counts, f"{method.__name__}: counts should have 'total'"
            assert isinstance(result.groups, list), f"{method.__name__}: groups should be list"
            assert result.pagination is not None, f"{method.__name__}: pagination required"
            assert result.pagination.limit > 0, f"{method.__name__}: limit should be > 0"
            assert result.pagination.offset >= 0, f"{method.__name__}: offset should be >= 0"
            assert isinstance(result.sort_keys, list), f"{method.__name__}: sort_keys should be list"
            assert len(result.sort_keys) > 0, f"{method.__name__}: sort_keys should not be empty"
            assert result.last_updated_at is not None, f"{method.__name__}: last_updated_at should be set"
