"""Property-based tests for Communication sent timestamp.

**Feature: workspace-refactor, Property 30: Communication sent timestamp**

Uses Hypothesis to verify that when a Communication's status changes to
"sent", the sent_at field is automatically set to the current timestamp,
and when status is not "sent", sent_at remains None.

**Validates: Requirements 23.6**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone
from uuid import uuid4

from database import db
from core.communication_manager import communication_manager
from schemas.communication import (
    CommunicationCreate,
    CommunicationUpdate,
    CommunicationStatus,
    ChannelType,
)
from schemas.todo import Priority
from tests.helpers import ensure_default_workspace


PROPERTY_SETTINGS = settings(
    max_examples=2,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# --- Strategies ---

title_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
).filter(lambda x: x.strip())

description_strategy = st.one_of(
    st.none(),
    st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
        min_size=0,
        max_size=200,
    ),
)

recipient_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
    min_size=1,
    max_size=100,
).filter(lambda x: x.strip())

channel_type_strategy = st.sampled_from(list(ChannelType))
priority_strategy = st.sampled_from(list(Priority))

# Statuses that are NOT "sent" — used to create initial communications
non_sent_status_strategy = st.sampled_from([
    CommunicationStatus.PENDING_REPLY,
    CommunicationStatus.AI_DRAFT,
    CommunicationStatus.FOLLOW_UP,
    CommunicationStatus.CANCELLED,
])


class TestCommunicationSentTimestamp:
    """Property 30: Communication sent timestamp.

    *For any* Communication where status changes to "sent", the sent_at
    field SHALL be set to the current timestamp.

    **Validates: Requirements 23.6**
    """

    @given(
        title=title_strategy,
        description=description_strategy,
        recipient=recipient_strategy,
        channel_type=channel_type_strategy,
        priority=priority_strategy,
        initial_status=non_sent_status_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_sent_at_set_when_status_changes_to_sent(
        self,
        title: str,
        description,
        recipient: str,
        channel_type: ChannelType,
        priority: Priority,
        initial_status: CommunicationStatus,
    ):
        """Updating status to 'sent' auto-sets sent_at timestamp.

        **Validates: Requirements 23.6**
        """
        ws_id = await ensure_default_workspace()
        before_update = datetime.now(timezone.utc)

        # 1. Create a Communication with a non-sent status
        comm = await communication_manager.create(CommunicationCreate(
            workspace_id=ws_id,
            title=title,
            description=description,
            recipient=recipient,
            channel_type=channel_type,
            status=initial_status,
            priority=priority,
        ))

        # Verify sent_at is None initially
        assert comm.sent_at is None, (
            f"Expected sent_at=None for status={initial_status.value}, "
            f"got {comm.sent_at}"
        )

        # 2. Update status to "sent"
        updated = await communication_manager.update(
            comm.id,
            CommunicationUpdate(status=CommunicationStatus.SENT),
        )

        after_update = datetime.now(timezone.utc)

        # Property: sent_at must be set
        assert updated is not None
        assert updated.sent_at is not None, (
            "Expected sent_at to be set when status changed to 'sent', "
            "but it was None"
        )

        # Property: sent_at should be a reasonable timestamp (between before and after)
        assert before_update <= updated.sent_at <= after_update, (
            f"sent_at={updated.sent_at} not in expected range "
            f"[{before_update}, {after_update}]"
        )

        # Verify persisted in DB
        stored = await db.communications.get(comm.id)
        assert stored is not None
        assert stored["sent_at"] is not None
        assert stored["status"] == CommunicationStatus.SENT.value

    @given(
        title=title_strategy,
        recipient=recipient_strategy,
        channel_type=channel_type_strategy,
        priority=priority_strategy,
        non_sent_status=non_sent_status_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_sent_at_remains_none_for_non_sent_status(
        self,
        title: str,
        recipient: str,
        channel_type: ChannelType,
        priority: Priority,
        non_sent_status: CommunicationStatus,
    ):
        """Updating to a non-sent status does not set sent_at.

        **Validates: Requirements 23.6**
        """
        ws_id = await ensure_default_workspace()

        # Create with pending_reply status
        comm = await communication_manager.create(CommunicationCreate(
            workspace_id=ws_id,
            title=title,
            recipient=recipient,
            channel_type=channel_type,
            status=CommunicationStatus.PENDING_REPLY,
            priority=priority,
        ))

        assert comm.sent_at is None

        # Update to another non-sent status
        updated = await communication_manager.update(
            comm.id,
            CommunicationUpdate(status=non_sent_status),
        )

        # Property: sent_at must remain None
        assert updated is not None
        assert updated.sent_at is None, (
            f"Expected sent_at=None for status={non_sent_status.value}, "
            f"got {updated.sent_at}"
        )

        # Verify in DB
        stored = await db.communications.get(comm.id)
        assert stored["sent_at"] is None

    @given(
        title=title_strategy,
        recipient=recipient_strategy,
        channel_type=channel_type_strategy,
        priority=priority_strategy,
    )
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_sent_at_not_overwritten_on_repeated_sent_update(
        self,
        title: str,
        recipient: str,
        channel_type: ChannelType,
        priority: Priority,
    ):
        """Once sent_at is set, updating status to 'sent' again does not overwrite it.

        **Validates: Requirements 23.6**
        """
        ws_id = await ensure_default_workspace()

        # Create with non-sent status
        comm = await communication_manager.create(CommunicationCreate(
            workspace_id=ws_id,
            title=title,
            recipient=recipient,
            channel_type=channel_type,
            status=CommunicationStatus.PENDING_REPLY,
            priority=priority,
        ))

        # First update to sent — sets sent_at
        first_update = await communication_manager.update(
            comm.id,
            CommunicationUpdate(status=CommunicationStatus.SENT),
        )
        assert first_update is not None
        assert first_update.sent_at is not None
        original_sent_at = first_update.sent_at

        # Second update to sent — should NOT overwrite sent_at
        second_update = await communication_manager.update(
            comm.id,
            CommunicationUpdate(status=CommunicationStatus.SENT),
        )
        assert second_update is not None
        assert second_update.sent_at == original_sent_at, (
            f"Expected sent_at to remain {original_sent_at}, "
            f"got {second_update.sent_at}"
        )
