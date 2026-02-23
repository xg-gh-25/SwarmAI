"""Communication manager for the Communicate section of the Daily Work Operating Loop.

This module provides the CommunicationManager class for managing Communication entities,
which represent stakeholder alignment work items. Communications track interactions
with stakeholders including emails, Slack messages, meetings, and other channels.

Requirements: 23.1-23.11
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from database import db
from schemas.communication import (
    CommunicationCreate,
    CommunicationUpdate,
    CommunicationResponse,
    CommunicationStatus,
    ChannelType,
)
from schemas.todo import Priority

logger = logging.getLogger(__name__)


class CommunicationManager:
    """Manages Communication entities in the Communicate section.

    Communications represent stakeholder alignment work items that can be
    linked to source Tasks or ToDos. They support AI-generated draft content
    and automatic sent_at timestamp when status changes to sent.

    Key Features:
    - CRUD operations for Communications
    - Default workspace assignment to SwarmWS
    - Automatic sent_at timestamp on status change to sent
    - AI draft content storage

    Requirements: 23.1-23.11
    """

    async def _get_default_workspace_id(self) -> str:
        """Get the default workspace (SwarmWS) ID.

        Returns:
            str: The ID of the default workspace (SwarmWS).

        Raises:
            ValueError: If no default workspace exists.

        Validates: Requirements 1.3, 1.4
        """
        default_workspace = await db.swarm_workspaces.get_default()
        if not default_workspace:
            raise ValueError("Default workspace (SwarmWS) not found. Please initialize the application first.")
        return default_workspace["id"]

    async def create(self, data: CommunicationCreate) -> CommunicationResponse:
        """Create a new Communication.

        Args:
            data: CommunicationCreate schema with Communication details.
                  If workspace_id is not provided, defaults to SwarmWS.

        Returns:
            CommunicationResponse: The created Communication.

        Validates: Requirements 23.1, 23.5, 23.7, 23.10
        """
        workspace_id = data.workspace_id
        if not workspace_id:
            workspace_id = await self._get_default_workspace_id()
            logger.debug(f"Defaulting workspace_id to SwarmWS: {workspace_id}")

        now = datetime.now(timezone.utc).isoformat()
        comm_id = str(uuid4())

        # If creating with status=sent, auto-set sent_at
        sent_at = None
        if data.status == CommunicationStatus.SENT:
            sent_at = now

        comm_dict = {
            "id": comm_id,
            "workspace_id": workspace_id,
            "title": data.title,
            "description": data.description,
            "recipient": data.recipient,
            "channel_type": data.channel_type.value,
            "status": data.status.value,
            "priority": data.priority.value,
            "due_date": data.due_date.isoformat() if data.due_date else None,
            "ai_draft_content": data.ai_draft_content,
            "source_task_id": data.source_task_id,
            "source_todo_id": data.source_todo_id,
            "sent_at": sent_at,
            "created_at": now,
            "updated_at": now,
        }

        result = await db.communications.put(comm_dict)
        logger.info(f"Created Communication {comm_id} in workspace {workspace_id}")

        return self._dict_to_response(result)

    async def get(self, communication_id: str) -> Optional[CommunicationResponse]:
        """Get a Communication by ID.

        Args:
            communication_id: The ID of the Communication to retrieve.

        Returns:
            CommunicationResponse if found, None otherwise.

        Validates: Requirements 23.8
        """
        result = await db.communications.get(communication_id)
        if not result:
            return None
        return self._dict_to_response(result)

    async def list(
        self,
        workspace_id: Optional[str] = None,
        status: Optional[CommunicationStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[CommunicationResponse]:
        """List Communications with optional filtering.

        Args:
            workspace_id: Filter by workspace ID. If None, returns all Communications.
            status: Filter by status.
            limit: Maximum number of results (default 50).
            offset: Number of results to skip for pagination.

        Returns:
            List of CommunicationResponse objects.

        Validates: Requirements 23.8, 23.9, 23.10
        """
        if workspace_id:
            status_value = status.value if status else None
            results = await db.communications.list_by_workspace(workspace_id, status_value)
        else:
            results = await db.communications.list()

        # Apply status filter in-memory when no workspace_id but status provided
        if status and not workspace_id:
            results = [r for r in results if r.get("status") == status.value]

        # Apply pagination
        paginated = results[offset:offset + limit]

        return [self._dict_to_response(r) for r in paginated]

    async def update(self, communication_id: str, data: CommunicationUpdate) -> Optional[CommunicationResponse]:
        """Update an existing Communication.

        When status changes to 'sent', automatically sets sent_at to current UTC timestamp.

        Args:
            communication_id: The ID of the Communication to update.
            data: CommunicationUpdate schema with fields to update.

        Returns:
            Updated CommunicationResponse if found, None otherwise.

        Validates: Requirements 23.6, 23.8
        """
        existing = await db.communications.get(communication_id)
        if not existing:
            return None

        updates = {}
        if data.title is not None:
            updates["title"] = data.title
        if data.description is not None:
            updates["description"] = data.description
        if data.recipient is not None:
            updates["recipient"] = data.recipient
        if data.channel_type is not None:
            updates["channel_type"] = data.channel_type.value
        if data.priority is not None:
            updates["priority"] = data.priority.value
        if data.due_date is not None:
            updates["due_date"] = data.due_date.isoformat()
        if data.ai_draft_content is not None:
            updates["ai_draft_content"] = data.ai_draft_content
        if data.source_task_id is not None:
            updates["source_task_id"] = data.source_task_id
        if data.source_todo_id is not None:
            updates["source_todo_id"] = data.source_todo_id
        if data.sent_at is not None:
            updates["sent_at"] = data.sent_at.isoformat()

        if data.status is not None:
            updates["status"] = data.status.value
            # Requirement 23.6: auto-set sent_at when status changes to sent
            if data.status == CommunicationStatus.SENT and not existing.get("sent_at"):
                updates["sent_at"] = datetime.now(timezone.utc).isoformat()

        if not updates:
            return self._dict_to_response(existing)

        result = await db.communications.update(communication_id, updates)
        if not result:
            return None

        logger.info(f"Updated Communication {communication_id}")
        return self._dict_to_response(result)

    async def delete(self, communication_id: str) -> bool:
        """Delete a Communication.

        Args:
            communication_id: The ID of the Communication to delete.

        Returns:
            True if deleted, False if not found.

        Validates: Requirements 23.8
        """
        existing = await db.communications.get(communication_id)
        if not existing:
            return False

        await db.communications.delete(communication_id)
        logger.info(f"Deleted Communication {communication_id}")
        return True

    async def count_by_status(self, workspace_id: str, status: CommunicationStatus) -> int:
        """Count Communications by workspace and status.

        Args:
            workspace_id: The workspace ID to scope the count.
            status: The status to count.

        Returns:
            Number of Communications matching the criteria.

        Validates: Requirements 23.9
        """
        return await db.communications.count_by_workspace_and_status(workspace_id, status.value)

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse a datetime string to datetime object.

        Args:
            value: ISO format datetime string or None.

        Returns:
            datetime object or None if parsing fails.
        """
        if not value:
            return None
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None

    def _dict_to_response(self, data: dict) -> CommunicationResponse:
        """Convert a database dict to CommunicationResponse.

        Args:
            data: Database row dict.

        Returns:
            CommunicationResponse Pydantic model.
        """
        return CommunicationResponse(
            id=data["id"],
            workspace_id=data["workspace_id"],
            title=data["title"],
            description=data.get("description"),
            recipient=data["recipient"],
            channel_type=data["channel_type"],
            status=data["status"],
            priority=data["priority"],
            due_date=self._parse_datetime(data.get("due_date")),
            ai_draft_content=data.get("ai_draft_content"),
            source_task_id=data.get("source_task_id"),
            source_todo_id=data.get("source_todo_id"),
            sent_at=self._parse_datetime(data.get("sent_at")),
            created_at=self._parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
            updated_at=self._parse_datetime(data["updated_at"]) or datetime.now(timezone.utc),
        )


# Global instance
communication_manager = CommunicationManager()
