"""Unit tests for CommunicationManager.

Tests CRUD operations, sent timestamp auto-set, ai_draft_content storage,
workspace isolation, and pagination.

Requirements: 23.1-23.11
"""
import pytest
from datetime import datetime, timezone, timedelta

from core.communication_manager import communication_manager
from schemas.communication import (
    CommunicationCreate,
    CommunicationUpdate,
    CommunicationStatus,
    ChannelType,
)
from schemas.todo import Priority
from tests.helpers import create_workspace


# ---------------------------------------------------------------------------
# Tests: Create
# ---------------------------------------------------------------------------


class TestCommunicationCreate:
    """Tests for CommunicationManager.create()."""

    @pytest.mark.asyncio
    async def test_create_basic(self):
        ws = await create_workspace()
        data = CommunicationCreate(
            workspace_id=ws["id"],
            title="Follow up with Alice",
            recipient="alice@example.com",
        )
        result = await communication_manager.create(data)

        assert result.id is not None
        assert result.workspace_id == ws["id"]
        assert result.title == "Follow up with Alice"
        assert result.recipient == "alice@example.com"
        assert result.status == CommunicationStatus.PENDING_REPLY
        assert result.channel_type == ChannelType.OTHER
        assert result.priority == Priority.NONE
        assert result.sent_at is None
        assert result.created_at is not None
        assert result.updated_at is not None

    @pytest.mark.asyncio
    async def test_create_with_all_fields(self):
        ws = await create_workspace()
        due = datetime.now(timezone.utc) + timedelta(days=3)
        data = CommunicationCreate(
            workspace_id=ws["id"],
            title="Project update email",
            description="Send weekly project status update",
            recipient="team@example.com",
            channel_type=ChannelType.EMAIL,
            status=CommunicationStatus.AI_DRAFT,
            priority=Priority.HIGH,
            due_date=due,
            ai_draft_content="Dear team, here is the weekly update...",
            source_task_id="task-123",
            source_todo_id="todo-456",
        )
        result = await communication_manager.create(data)

        assert result.title == "Project update email"
        assert result.description == "Send weekly project status update"
        assert result.recipient == "team@example.com"
        assert result.channel_type == ChannelType.EMAIL
        assert result.status == CommunicationStatus.AI_DRAFT
        assert result.priority == Priority.HIGH
        assert result.due_date is not None
        assert result.ai_draft_content == "Dear team, here is the weekly update..."
        assert result.source_task_id == "task-123"
        assert result.source_todo_id == "todo-456"
        assert result.sent_at is None

    @pytest.mark.asyncio
    async def test_create_defaults_to_swarmws(self):
        swarm_ws = await create_workspace("SwarmWS", is_default=True)
        data = CommunicationCreate(
            workspace_id="",
            title="Unassigned comm",
            recipient="bob@example.com",
        )
        result = await communication_manager.create(data)
        assert result.workspace_id == swarm_ws["id"]

    @pytest.mark.asyncio
    async def test_create_with_sent_status_sets_sent_at(self):
        ws = await create_workspace()
        data = CommunicationCreate(
            workspace_id=ws["id"],
            title="Already sent message",
            recipient="charlie@example.com",
            status=CommunicationStatus.SENT,
        )
        result = await communication_manager.create(data)

        assert result.status == CommunicationStatus.SENT
        assert result.sent_at is not None

    @pytest.mark.asyncio
    async def test_create_with_ai_draft_content(self):
        ws = await create_workspace()
        draft = "Hi Bob,\n\nHere is the draft for the proposal..."
        data = CommunicationCreate(
            workspace_id=ws["id"],
            title="Proposal draft",
            recipient="bob@example.com",
            status=CommunicationStatus.AI_DRAFT,
            ai_draft_content=draft,
        )
        result = await communication_manager.create(data)

        assert result.ai_draft_content == draft
        assert result.status == CommunicationStatus.AI_DRAFT


# ---------------------------------------------------------------------------
# Tests: Get
# ---------------------------------------------------------------------------


class TestCommunicationGet:
    """Tests for CommunicationManager.get()."""

    @pytest.mark.asyncio
    async def test_get_existing(self):
        ws = await create_workspace()
        data = CommunicationCreate(
            workspace_id=ws["id"],
            title="Test comm",
            recipient="test@example.com",
        )
        created = await communication_manager.create(data)
        fetched = await communication_manager.get(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.title == "Test comm"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self):
        result = await communication_manager.get("nonexistent-id")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: List
# ---------------------------------------------------------------------------


class TestCommunicationList:
    """Tests for CommunicationManager.list()."""

    @pytest.mark.asyncio
    async def test_list_by_workspace(self):
        ws = await create_workspace()
        for i in range(3):
            await communication_manager.create(CommunicationCreate(
                workspace_id=ws["id"],
                title=f"Comm {i}",
                recipient=f"user{i}@example.com",
            ))
        results = await communication_manager.list(workspace_id=ws["id"])
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self):
        ws = await create_workspace()
        await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="Pending",
            recipient="a@example.com",
            status=CommunicationStatus.PENDING_REPLY,
        ))
        await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="Draft",
            recipient="b@example.com",
            status=CommunicationStatus.AI_DRAFT,
        ))

        results = await communication_manager.list(
            workspace_id=ws["id"],
            status=CommunicationStatus.PENDING_REPLY,
        )
        assert len(results) == 1
        assert results[0].title == "Pending"

    @pytest.mark.asyncio
    async def test_list_pagination(self):
        ws = await create_workspace()
        for i in range(5):
            await communication_manager.create(CommunicationCreate(
                workspace_id=ws["id"],
                title=f"Comm {i}",
                recipient=f"user{i}@example.com",
            ))

        page1 = await communication_manager.list(workspace_id=ws["id"], limit=2, offset=0)
        page2 = await communication_manager.list(workspace_id=ws["id"], limit=2, offset=2)
        page3 = await communication_manager.list(workspace_id=ws["id"], limit=2, offset=4)

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1

    @pytest.mark.asyncio
    async def test_list_isolates_workspaces(self):
        ws1 = await create_workspace("WS1")
        ws2 = await create_workspace("WS2")

        await communication_manager.create(CommunicationCreate(
            workspace_id=ws1["id"],
            title="WS1 comm",
            recipient="a@example.com",
        ))
        await communication_manager.create(CommunicationCreate(
            workspace_id=ws2["id"],
            title="WS2 comm",
            recipient="b@example.com",
        ))

        ws1_results = await communication_manager.list(workspace_id=ws1["id"])
        ws2_results = await communication_manager.list(workspace_id=ws2["id"])

        assert len(ws1_results) == 1
        assert ws1_results[0].title == "WS1 comm"
        assert len(ws2_results) == 1
        assert ws2_results[0].title == "WS2 comm"


# ---------------------------------------------------------------------------
# Tests: Update
# ---------------------------------------------------------------------------


class TestCommunicationUpdate:
    """Tests for CommunicationManager.update()."""

    @pytest.mark.asyncio
    async def test_update_title(self):
        ws = await create_workspace()
        created = await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="Original",
            recipient="a@example.com",
        ))
        updated = await communication_manager.update(
            created.id,
            CommunicationUpdate(title="Updated title"),
        )
        assert updated is not None
        assert updated.title == "Updated title"

    @pytest.mark.asyncio
    async def test_update_status_to_sent_sets_sent_at(self):
        """Requirement 23.6: sent_at is set when status changes to sent."""
        ws = await create_workspace()
        created = await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="To be sent",
            recipient="a@example.com",
            status=CommunicationStatus.PENDING_REPLY,
        ))
        assert created.sent_at is None

        updated = await communication_manager.update(
            created.id,
            CommunicationUpdate(status=CommunicationStatus.SENT),
        )
        assert updated is not None
        assert updated.status == CommunicationStatus.SENT
        assert updated.sent_at is not None

    @pytest.mark.asyncio
    async def test_update_status_to_sent_preserves_existing_sent_at(self):
        """If sent_at is already set, don't overwrite it."""
        ws = await create_workspace()
        created = await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="Already sent",
            recipient="a@example.com",
            status=CommunicationStatus.SENT,
        ))
        original_sent_at = created.sent_at

        # Update something else while keeping sent status
        updated = await communication_manager.update(
            created.id,
            CommunicationUpdate(title="Updated title", status=CommunicationStatus.SENT),
        )
        assert updated is not None
        assert updated.sent_at == original_sent_at

    @pytest.mark.asyncio
    async def test_update_ai_draft_content(self):
        ws = await create_workspace()
        created = await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="Draft comm",
            recipient="a@example.com",
            status=CommunicationStatus.AI_DRAFT,
        ))
        assert created.ai_draft_content is None

        updated = await communication_manager.update(
            created.id,
            CommunicationUpdate(ai_draft_content="AI generated draft content here"),
        )
        assert updated is not None
        assert updated.ai_draft_content == "AI generated draft content here"

    @pytest.mark.asyncio
    async def test_update_nonexistent(self):
        result = await communication_manager.update(
            "nonexistent-id",
            CommunicationUpdate(title="Nope"),
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_update_no_changes(self):
        ws = await create_workspace()
        created = await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="No change",
            recipient="a@example.com",
        ))
        updated = await communication_manager.update(
            created.id,
            CommunicationUpdate(),
        )
        assert updated is not None
        assert updated.title == "No change"

    @pytest.mark.asyncio
    async def test_update_channel_type(self):
        ws = await create_workspace()
        created = await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="Channel test",
            recipient="a@example.com",
            channel_type=ChannelType.OTHER,
        ))
        updated = await communication_manager.update(
            created.id,
            CommunicationUpdate(channel_type=ChannelType.SLACK),
        )
        assert updated is not None
        assert updated.channel_type == ChannelType.SLACK


# ---------------------------------------------------------------------------
# Tests: Delete
# ---------------------------------------------------------------------------


class TestCommunicationDelete:
    """Tests for CommunicationManager.delete()."""

    @pytest.mark.asyncio
    async def test_delete_existing(self):
        ws = await create_workspace()
        created = await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="To delete",
            recipient="a@example.com",
        ))
        deleted = await communication_manager.delete(created.id)
        assert deleted is True

        fetched = await communication_manager.get(created.id)
        assert fetched is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self):
        result = await communication_manager.delete("nonexistent-id")
        assert result is False


# ---------------------------------------------------------------------------
# Tests: Count by status
# ---------------------------------------------------------------------------


class TestCommunicationCountByStatus:
    """Tests for CommunicationManager.count_by_status()."""

    @pytest.mark.asyncio
    async def test_count_by_status(self):
        ws = await create_workspace()
        await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="Pending 1",
            recipient="a@example.com",
            status=CommunicationStatus.PENDING_REPLY,
        ))
        await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="Pending 2",
            recipient="b@example.com",
            status=CommunicationStatus.PENDING_REPLY,
        ))
        await communication_manager.create(CommunicationCreate(
            workspace_id=ws["id"],
            title="Sent",
            recipient="c@example.com",
            status=CommunicationStatus.SENT,
        ))

        pending_count = await communication_manager.count_by_status(
            ws["id"], CommunicationStatus.PENDING_REPLY
        )
        sent_count = await communication_manager.count_by_status(
            ws["id"], CommunicationStatus.SENT
        )
        assert pending_count == 2
        assert sent_count == 1
