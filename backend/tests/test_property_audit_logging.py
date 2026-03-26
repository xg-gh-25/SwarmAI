"""Property-based tests for configuration change audit logging.

**Feature: workspace-refactor, Property 16: Configuration change audit logging**

Uses Hypothesis to verify that for any change to workspace Skills, MCPs,
or Knowledgebases configuration, an audit log entry is created with all
required fields: workspace_id, change_type, entity_type, entity_id,
old_value, new_value, changed_by, changed_at.

**Validates: Requirements 25.1-25.8**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone
from uuid import uuid4

from database import db
from core.audit_manager import audit_manager
from schemas.workspace_config import (
    AuditLogEntry,
    AuditLogCreate,
    ChangeType,
    EntityType,
)
from tests.helpers import ensure_default_workspace
from tests.helpers import PROPERTY_SETTINGS





# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

change_type_strategy = st.sampled_from(list(ChangeType))
entity_type_strategy = st.sampled_from(list(EntityType))

entity_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=50,
).filter(lambda x: x.strip())

changed_by_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=50,
).filter(lambda x: x.strip())

old_value_strategy = st.one_of(
    st.none(),
    st.text(min_size=0, max_size=200),
)

new_value_strategy = st.one_of(
    st.none(),
    st.text(min_size=0, max_size=200),
)


# ---------------------------------------------------------------------------
# Helpers — workspace setup imported from conftest
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------

class TestAuditLogEntryCreation:
    """Property 16: Configuration change audit logging.

    *For any* change to workspace Skills, MCPs, or Knowledgebases
    configuration, an audit log entry SHALL be created with:
    workspace_id, change_type, entity_type, entity_id, old_value,
    new_value, changed_by, and changed_at.

    **Validates: Requirements 25.1-25.8**
    """

    @given(
        change_type=change_type_strategy,
        entity_type=entity_type_strategy,
        entity_id=entity_id_strategy,
        old_value=old_value_strategy,
        new_value=new_value_strategy,
        changed_by=changed_by_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_log_change_creates_entry_with_all_required_fields(
        self,
        change_type: ChangeType,
        entity_type: EntityType,
        entity_id: str,
        old_value,
        new_value,
        changed_by: str,
    ):
        """Every log_change call produces an entry with all required fields.

        **Validates: Requirements 25.1, 25.2, 25.3, 25.4, 25.8**
        """
        ws_id = await ensure_default_workspace()

        entry = await audit_manager.log_change(
            workspace_id=ws_id,
            change_type=change_type,
            entity_type=entity_type,
            entity_id=entity_id,
            old_value=old_value,
            new_value=new_value,
            changed_by=changed_by,
        )

        # All required fields must be present and correct
        assert isinstance(entry, AuditLogEntry)
        assert entry.workspace_id == ws_id
        assert entry.change_type == change_type
        assert entry.entity_type == entity_type
        assert entry.entity_id == entity_id
        assert entry.old_value == old_value
        assert entry.new_value == new_value
        assert entry.changed_by == changed_by
        assert isinstance(entry.changed_at, datetime)
        assert entry.id is not None and len(entry.id) > 0

    @given(
        change_type=change_type_strategy,
        entity_type=entity_type_strategy,
        entity_id=entity_id_strategy,
        changed_by=changed_by_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_log_change_persists_to_database(
        self,
        change_type: ChangeType,
        entity_type: EntityType,
        entity_id: str,
        changed_by: str,
    ):
        """Every audit entry is retrievable from the database after creation.

        **Validates: Requirements 25.1, 25.2**
        """
        ws_id = await ensure_default_workspace()

        entry = await audit_manager.log_change(
            workspace_id=ws_id,
            change_type=change_type,
            entity_type=entity_type,
            entity_id=entity_id,
            changed_by=changed_by,
        )

        # Verify the entry exists in the database
        stored = await db.workspace_audit_log.get(entry.id)
        assert stored is not None
        assert stored["workspace_id"] == ws_id
        assert stored["change_type"] == change_type.value
        assert stored["entity_type"] == entity_type.value
        assert stored["entity_id"] == entity_id
        assert stored["changed_by"] == changed_by

    @given(
        num_entries=st.integers(min_value=1, max_value=8),
        change_type=change_type_strategy,
        entity_type=entity_type_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_audit_log_pagination(
        self,
        num_entries: int,
        change_type: ChangeType,
        entity_type: EntityType,
    ):
        """Audit log entries are retrievable with correct pagination.

        **Validates: Requirements 25.5**
        """
        ws_id = await ensure_default_workspace()

        # Create N audit entries
        for i in range(num_entries):
            await audit_manager.log_change(
                workspace_id=ws_id,
                change_type=change_type,
                entity_type=entity_type,
                entity_id=f"entity-{i}",
                changed_by="test-user",
            )

        # Retrieve with pagination
        result = await audit_manager.get_audit_log(
            workspace_id=ws_id,
            limit=num_entries,
            offset=0,
        )

        assert result["total"] >= num_entries
        assert len(result["entries"]) >= num_entries
        assert result["limit"] == num_entries
        assert result["offset"] == 0
        assert isinstance(result["has_more"], bool)

        # All returned entries must be valid AuditLogEntry instances
        for e in result["entries"]:
            assert isinstance(e, AuditLogEntry)
            assert e.workspace_id == ws_id

    @given(
        change_type=change_type_strategy,
        entity_type=entity_type_strategy,
        entity_id=entity_id_strategy,
        changed_by=changed_by_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_log_change_from_model_creates_entry(
        self,
        change_type: ChangeType,
        entity_type: EntityType,
        entity_id: str,
        changed_by: str,
    ):
        """log_change_from_model produces the same result as log_change.

        **Validates: Requirements 25.1, 25.2**
        """
        ws_id = await ensure_default_workspace()

        data = AuditLogCreate(
            workspace_id=ws_id,
            change_type=change_type,
            entity_type=entity_type,
            entity_id=entity_id,
            changed_by=changed_by,
        )

        entry = await audit_manager.log_change_from_model(data)

        assert isinstance(entry, AuditLogEntry)
        assert entry.workspace_id == ws_id
        assert entry.change_type == change_type
        assert entry.entity_type == entity_type
        assert entry.entity_id == entity_id
        assert entry.changed_by == changed_by
        assert isinstance(entry.changed_at, datetime)
