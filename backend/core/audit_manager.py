"""Audit manager for tracking workspace configuration changes.

This module provides the AuditManager class for logging and retrieving
audit trail entries when workspace configurations (Skills, MCPs,
Knowledgebases, workspace settings) are modified.

Requirements: 25.1-25.8
"""
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from database import db
from schemas.workspace_config import (
    AuditLogEntry,
    AuditLogCreate,
    ChangeType,
    EntityType,
)

logger = logging.getLogger(__name__)


class AuditManager:
    """Manages audit log entries for workspace configuration changes.

    All workspace configuration changes (Skills, MCPs, Knowledgebases,
    workspace settings) are logged with before/after values for
    governance and traceability.

    Requirements: 25.1-25.8
    """

    async def log_change(
        self,
        workspace_id: str,
        change_type: ChangeType,
        entity_type: EntityType,
        entity_id: str,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        changed_by: str = "system",
    ) -> AuditLogEntry:
        """Log a workspace configuration change.

        Args:
            workspace_id: ID of the workspace where the change occurred.
            change_type: Type of change (enabled, disabled, added, removed, updated).
            entity_type: Type of entity changed (skill, mcp, knowledgebase, workspace_setting).
            entity_id: ID of the entity that was changed.
            old_value: Previous value before the change (JSON serialized).
            new_value: New value after the change (JSON serialized).
            changed_by: User identifier who made the change (defaults to "system").

        Returns:
            AuditLogEntry: The created audit log entry.

        Validates: Requirements 25.1, 25.2, 25.3, 25.4, 25.8
        """
        now = datetime.now(timezone.utc).isoformat()
        entry_id = str(uuid4())

        entry_dict = {
            "id": entry_id,
            "workspace_id": workspace_id,
            "change_type": change_type.value if isinstance(change_type, ChangeType) else change_type,
            "entity_type": entity_type.value if isinstance(entity_type, EntityType) else entity_type,
            "entity_id": entity_id,
            "old_value": old_value,
            "new_value": new_value,
            "changed_by": changed_by,
            "changed_at": now,
        }

        await db.workspace_audit_log.put(entry_dict)
        logger.info(
            f"Audit log: {change_type} {entity_type} '{entity_id}' "
            f"in workspace {workspace_id} by {changed_by}"
        )

        return self._dict_to_entry(entry_dict)

    async def log_change_from_model(self, data: AuditLogCreate) -> AuditLogEntry:
        """Log a workspace configuration change from a Pydantic model.

        Args:
            data: AuditLogCreate schema with audit log details.

        Returns:
            AuditLogEntry: The created audit log entry.

        Validates: Requirements 25.1, 25.2
        """
        return await self.log_change(
            workspace_id=data.workspace_id,
            change_type=data.change_type,
            entity_type=data.entity_type,
            entity_id=data.entity_id,
            old_value=data.old_value,
            new_value=data.new_value,
            changed_by=data.changed_by,
        )

    async def get_audit_log(
        self,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Get audit log entries for a workspace with pagination.

        Args:
            workspace_id: ID of the workspace to get audit log for.
            limit: Maximum number of entries to return (default 50).
            offset: Number of entries to skip for pagination (default 0).

        Returns:
            dict with keys:
                - entries: List of AuditLogEntry objects.
                - total: Total number of entries for this workspace.
                - limit: The limit used.
                - offset: The offset used.
                - has_more: Whether there are more entries beyond this page.

        Validates: Requirements 25.5
        """
        total = await db.workspace_audit_log.count_by_workspace(workspace_id)

        paginated = await db.workspace_audit_log.list_by_workspace_paginated(
            workspace_id, limit=limit, offset=offset
        )
        entries = [self._dict_to_entry(entry) for entry in paginated]

        return {
            "entries": entries,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": (offset + limit) < total,
        }

    async def get_audit_log_by_entity(
        self,
        entity_type: EntityType,
        entity_id: str,
    ) -> list[AuditLogEntry]:
        """Get audit log entries for a specific entity.

        Args:
            entity_type: Type of entity (skill, mcp, knowledgebase, workspace_setting).
            entity_id: ID of the entity.

        Returns:
            List of AuditLogEntry objects ordered by most recent first.

        Validates: Requirements 25.1
        """
        entity_type_value = entity_type.value if isinstance(entity_type, EntityType) else entity_type
        results = await db.workspace_audit_log.list_by_entity(
            entity_type_value, entity_id
        )
        return [self._dict_to_entry(entry) for entry in results]

    @staticmethod
    def _ensure_str(value) -> str | None:
        """Ensure a value is a JSON string (re-serialize if auto-parsed by DB layer)."""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        # _row_to_dict auto-parses JSON strings into dicts/lists;
        # re-serialize them so the Pydantic model (Optional[str]) is happy.
        import json as _json
        return _json.dumps(value)

    def _dict_to_entry(self, data: dict) -> AuditLogEntry:
        """Convert a database dict to AuditLogEntry.

        Args:
            data: Database row dict.

        Returns:
            AuditLogEntry Pydantic model.
        """
        changed_at = data.get("changed_at", "")
        if isinstance(changed_at, str):
            try:
                if changed_at.endswith("Z"):
                    changed_at = changed_at[:-1] + "+00:00"
                changed_at = datetime.fromisoformat(changed_at)
                if changed_at.tzinfo is None:
                    changed_at = changed_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                changed_at = datetime.now(timezone.utc)

        return AuditLogEntry(
            id=data["id"],
            workspace_id=data["workspace_id"],
            change_type=data["change_type"],
            entity_type=data["entity_type"],
            entity_id=data["entity_id"],
            old_value=self._ensure_str(data.get("old_value")),
            new_value=self._ensure_str(data.get("new_value")),
            changed_by=data["changed_by"],
            changed_at=changed_at,
        )


# Global instance
audit_manager = AuditManager()
