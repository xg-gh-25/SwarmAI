"""Tests for AuditManager.

Tests cover:
- log_change: creating audit log entries with all required fields
- log_change_from_model: creating entries from Pydantic model
- get_audit_log: retrieving entries with pagination
- get_audit_log_by_entity: retrieving entries for a specific entity
- change_type and entity_type validation
- changed_by defaults to "system"

Requirements: 25.1-25.8
"""
import json

import pytest

from core.audit_manager import AuditManager, audit_manager
from database import db
from schemas.workspace_config import (
    AuditLogCreate,
    AuditLogEntry,
    ChangeType,
    EntityType,
)
from tests.helpers import create_workspace


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def manager() -> AuditManager:
    return AuditManager()


@pytest.fixture
async def workspace() -> dict:
    return await create_workspace("AuditTestWS")


# ---------------------------------------------------------------------------
# Tests: log_change
# ---------------------------------------------------------------------------

class TestLogChange:
    """Tests for AuditManager.log_change method."""

    @pytest.mark.asyncio
    async def test_log_skill_enabled(self, manager, workspace):
        """Log a skill enablement change with all fields populated."""
        entry = await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.ENABLED,
            entity_type=EntityType.SKILL,
            entity_id="skill-123",
            old_value=json.dumps({"enabled": False}),
            new_value=json.dumps({"enabled": True}),
            changed_by="test-user",
        )

        assert isinstance(entry, AuditLogEntry)
        assert entry.workspace_id == workspace["id"]
        assert entry.change_type == ChangeType.ENABLED
        assert entry.entity_type == EntityType.SKILL
        assert entry.entity_id == "skill-123"
        assert entry.old_value == json.dumps({"enabled": False})
        assert entry.new_value == json.dumps({"enabled": True})
        assert entry.changed_by == "test-user"
        assert entry.changed_at is not None
        assert entry.id is not None

    @pytest.mark.asyncio
    async def test_log_mcp_disabled(self, manager, workspace):
        """Log an MCP disablement change."""
        entry = await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.DISABLED,
            entity_type=EntityType.MCP,
            entity_id="mcp-456",
            changed_by="admin",
        )

        assert entry.change_type == ChangeType.DISABLED
        assert entry.entity_type == EntityType.MCP
        assert entry.entity_id == "mcp-456"
        assert entry.old_value is None
        assert entry.new_value is None

    @pytest.mark.asyncio
    async def test_log_knowledgebase_added(self, manager, workspace):
        """Log a knowledgebase addition."""
        entry = await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.ADDED,
            entity_type=EntityType.KNOWLEDGEBASE,
            entity_id="kb-789",
            new_value=json.dumps({"source_type": "url", "source_path": "https://example.com"}),
            changed_by="user1",
        )

        assert entry.change_type == ChangeType.ADDED
        assert entry.entity_type == EntityType.KNOWLEDGEBASE
        assert entry.new_value is not None
        assert entry.old_value is None

    @pytest.mark.asyncio
    async def test_log_workspace_setting_updated(self, manager, workspace):
        """Log a workspace setting update."""
        entry = await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.UPDATED,
            entity_type=EntityType.WORKSPACE_SETTING,
            entity_id="token_budget",
            old_value="4000",
            new_value="8000",
            changed_by="user2",
        )

        assert entry.change_type == ChangeType.UPDATED
        assert entry.entity_type == EntityType.WORKSPACE_SETTING
        assert entry.old_value == "4000"
        assert entry.new_value == "8000"

    @pytest.mark.asyncio
    async def test_log_removed_change(self, manager, workspace):
        """Log a removal change."""
        entry = await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.REMOVED,
            entity_type=EntityType.KNOWLEDGEBASE,
            entity_id="kb-old",
            old_value=json.dumps({"display_name": "Old KB"}),
            changed_by="user3",
        )

        assert entry.change_type == ChangeType.REMOVED
        assert entry.old_value is not None
        assert entry.new_value is None

    @pytest.mark.asyncio
    async def test_default_changed_by_is_system(self, manager, workspace):
        """changed_by defaults to 'system' when not provided."""
        entry = await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.ENABLED,
            entity_type=EntityType.SKILL,
            entity_id="skill-auto",
        )

        assert entry.changed_by == "system"

    @pytest.mark.asyncio
    async def test_entry_persisted_to_database(self, manager, workspace):
        """Verify the entry is actually stored in the database."""
        entry = await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.ADDED,
            entity_type=EntityType.MCP,
            entity_id="mcp-new",
            changed_by="tester",
        )

        # Retrieve directly from DB
        stored = await db.workspace_audit_log.get(entry.id)
        assert stored is not None
        assert stored["workspace_id"] == workspace["id"]
        assert stored["change_type"] == "added"
        assert stored["entity_type"] == "mcp"
        assert stored["entity_id"] == "mcp-new"
        assert stored["changed_by"] == "tester"


# ---------------------------------------------------------------------------
# Tests: log_change_from_model
# ---------------------------------------------------------------------------

class TestLogChangeFromModel:
    """Tests for AuditManager.log_change_from_model method."""

    @pytest.mark.asyncio
    async def test_create_from_pydantic_model(self, manager, workspace):
        """Create an audit entry from an AuditLogCreate model."""
        data = AuditLogCreate(
            workspace_id=workspace["id"],
            change_type=ChangeType.ENABLED,
            entity_type=EntityType.SKILL,
            entity_id="skill-model",
            old_value=None,
            new_value=json.dumps({"enabled": True}),
            changed_by="model-user",
        )

        entry = await manager.log_change_from_model(data)

        assert isinstance(entry, AuditLogEntry)
        assert entry.entity_id == "skill-model"
        assert entry.changed_by == "model-user"


# ---------------------------------------------------------------------------
# Tests: get_audit_log (pagination)
# ---------------------------------------------------------------------------

class TestGetAuditLog:
    """Tests for AuditManager.get_audit_log with pagination."""

    @pytest.mark.asyncio
    async def test_empty_audit_log(self, manager, workspace):
        """Empty workspace returns zero entries."""
        result = await manager.get_audit_log(workspace["id"])

        assert result["entries"] == []
        assert result["total"] == 0
        assert result["has_more"] is False

    @pytest.mark.asyncio
    async def test_returns_entries_for_workspace(self, manager, workspace):
        """Returns entries belonging to the singleton workspace."""
        await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.ENABLED,
            entity_type=EntityType.SKILL,
            entity_id="skill-a",
        )
        await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.DISABLED,
            entity_type=EntityType.MCP,
            entity_id="mcp-b",
        )

        result = await manager.get_audit_log(workspace["id"])
        assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_pagination_limit_and_offset(self, manager, workspace):
        """Pagination with limit and offset works correctly."""
        # Create 5 entries
        for i in range(5):
            await manager.log_change(
                workspace_id=workspace["id"],
                change_type=ChangeType.UPDATED,
                entity_type=EntityType.WORKSPACE_SETTING,
                entity_id=f"setting-{i}",
                changed_by="paginator",
            )

        # Page 1: limit=2, offset=0
        page1 = await manager.get_audit_log(workspace["id"], limit=2, offset=0)
        assert len(page1["entries"]) == 2
        assert page1["total"] == 5
        assert page1["has_more"] is True
        assert page1["limit"] == 2
        assert page1["offset"] == 0

        # Page 2: limit=2, offset=2
        page2 = await manager.get_audit_log(workspace["id"], limit=2, offset=2)
        assert len(page2["entries"]) == 2
        assert page2["has_more"] is True

        # Page 3: limit=2, offset=4
        page3 = await manager.get_audit_log(workspace["id"], limit=2, offset=4)
        assert len(page3["entries"]) == 1
        assert page3["has_more"] is False

    @pytest.mark.asyncio
    async def test_entries_ordered_by_most_recent(self, manager, workspace):
        """Entries are returned in reverse chronological order."""
        await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.ADDED,
            entity_type=EntityType.SKILL,
            entity_id="first",
        )
        await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.REMOVED,
            entity_type=EntityType.SKILL,
            entity_id="second",
        )

        result = await manager.get_audit_log(workspace["id"])
        assert len(result["entries"]) == 2
        # Most recent first
        assert result["entries"][0].entity_id == "second"
        assert result["entries"][1].entity_id == "first"


# ---------------------------------------------------------------------------
# Tests: get_audit_log_by_entity
# ---------------------------------------------------------------------------

class TestGetAuditLogByEntity:
    """Tests for AuditManager.get_audit_log_by_entity method."""

    @pytest.mark.asyncio
    async def test_filter_by_entity(self, manager, workspace):
        """Returns only entries matching the entity type and ID."""
        await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.ENABLED,
            entity_type=EntityType.SKILL,
            entity_id="target-skill",
        )
        await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.DISABLED,
            entity_type=EntityType.SKILL,
            entity_id="target-skill",
        )
        await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.ENABLED,
            entity_type=EntityType.MCP,
            entity_id="other-mcp",
        )

        entries = await manager.get_audit_log_by_entity(
            EntityType.SKILL, "target-skill"
        )
        assert len(entries) == 2
        assert all(e.entity_id == "target-skill" for e in entries)
        assert all(e.entity_type == EntityType.SKILL for e in entries)

    @pytest.mark.asyncio
    async def test_empty_result_for_unknown_entity(self, manager, workspace):
        """Returns empty list for non-existent entity."""
        entries = await manager.get_audit_log_by_entity(
            EntityType.SKILL, "nonexistent"
        )
        assert entries == []


# ---------------------------------------------------------------------------
# Tests: all change_type values
# ---------------------------------------------------------------------------

class TestChangeTypes:
    """Verify all five change_type values are supported (Req 25.3)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("change_type", list(ChangeType))
    async def test_all_change_types(self, manager, workspace, change_type):
        entry = await manager.log_change(
            workspace_id=workspace["id"],
            change_type=change_type,
            entity_type=EntityType.SKILL,
            entity_id="test-entity",
        )
        assert entry.change_type == change_type


# ---------------------------------------------------------------------------
# Tests: all entity_type values
# ---------------------------------------------------------------------------

class TestEntityTypes:
    """Verify all four entity_type values are supported (Req 25.4)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("entity_type", list(EntityType))
    async def test_all_entity_types(self, manager, workspace, entity_type):
        entry = await manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.UPDATED,
            entity_type=entity_type,
            entity_id="test-entity",
        )
        assert entry.entity_type == entity_type


# ---------------------------------------------------------------------------
# Tests: global instance
# ---------------------------------------------------------------------------

class TestGlobalInstance:
    """Verify the module-level singleton works."""

    @pytest.mark.asyncio
    async def test_global_audit_manager_works(self, workspace):
        entry = await audit_manager.log_change(
            workspace_id=workspace["id"],
            change_type=ChangeType.ADDED,
            entity_type=EntityType.KNOWLEDGEBASE,
            entity_id="kb-global",
        )
        assert entry.id is not None
