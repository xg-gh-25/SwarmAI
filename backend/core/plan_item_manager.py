"""PlanItem manager for the Plan section of the Daily Work Operating Loop.

This module provides the PlanItemManager class for managing PlanItem entities,
which represent prioritized work items in the Plan section. PlanItems can be
workspace-scoped (local) or SwarmWS-scoped (global/cross-domain).

Requirements: 22.1-22.12
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from database import db
from schemas.plan_item import (
    PlanItemCreate,
    PlanItemUpdate,
    PlanItemResponse,
    PlanItemStatus,
    FocusType,
)
from schemas.todo import Priority

logger = logging.getLogger(__name__)


class PlanItemManager:
    """Manages PlanItem entities in the Plan section of the Daily Work Operating Loop.

    PlanItems represent prioritized work items that can be linked to source
    ToDos or Tasks. They support reordering within focus_type categories
    and automatic completion when linked Tasks complete.

    Key Features:
    - CRUD operations for PlanItems
    - Default workspace assignment to SwarmWS
    - Linked task completion cascade
    - Reordering within focus_type category via sort_order

    Requirements: 22.1-22.12
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

    async def create(self, data: PlanItemCreate) -> PlanItemResponse:
        """Create a new PlanItem.

        Args:
            data: PlanItemCreate schema with PlanItem details.
                  If workspace_id is not provided, defaults to SwarmWS.

        Returns:
            PlanItemResponse: The created PlanItem.

        Validates: Requirements 22.1, 22.5, 22.10
        """
        workspace_id = data.workspace_id
        if not workspace_id:
            workspace_id = await self._get_default_workspace_id()
            logger.debug(f"Defaulting workspace_id to SwarmWS: {workspace_id}")

        now = datetime.now(timezone.utc).isoformat()
        plan_item_id = str(uuid4())

        plan_item_dict = {
            "id": plan_item_id,
            "workspace_id": workspace_id,
            "title": data.title,
            "description": data.description,
            "source_todo_id": data.source_todo_id,
            "source_task_id": data.source_task_id,
            "status": data.status.value,
            "priority": data.priority.value,
            "scheduled_date": data.scheduled_date.isoformat() if data.scheduled_date else None,
            "focus_type": data.focus_type.value,
            "sort_order": data.sort_order,
            "created_at": now,
            "updated_at": now,
        }

        result = await db.plan_items.put(plan_item_dict)
        logger.info(f"Created PlanItem {plan_item_id} in workspace {workspace_id}")

        return self._dict_to_response(result)

    async def get(self, plan_item_id: str) -> Optional[PlanItemResponse]:
        """Get a PlanItem by ID.

        Args:
            plan_item_id: The ID of the PlanItem to retrieve.

        Returns:
            PlanItemResponse if found, None otherwise.

        Validates: Requirements 22.8
        """
        result = await db.plan_items.get(plan_item_id)
        if not result:
            return None
        return self._dict_to_response(result)

    async def list(
        self,
        workspace_id: Optional[str] = None,
        focus_type: Optional[FocusType] = None,
        status: Optional[PlanItemStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[PlanItemResponse]:
        """List PlanItems with optional filtering.

        Args:
            workspace_id: Filter by workspace ID. If None, returns all PlanItems.
            focus_type: Filter by focus_type category.
            status: Filter by status (applied in-memory after DB query).
            limit: Maximum number of results (default 50).
            offset: Number of results to skip for pagination.

        Returns:
            List of PlanItemResponse objects.

        Validates: Requirements 22.8, 22.9, 22.10
        """
        if workspace_id:
            focus_value = focus_type.value if focus_type else None
            results = await db.plan_items.list_by_workspace(workspace_id, focus_value)
        else:
            results = await db.plan_items.list()

        # Apply status filter in-memory if provided
        if status:
            results = [r for r in results if r.get("status") == status.value]

        # Apply pagination
        paginated = results[offset:offset + limit]

        return [self._dict_to_response(r) for r in paginated]

    async def update(self, plan_item_id: str, data: PlanItemUpdate) -> Optional[PlanItemResponse]:
        """Update an existing PlanItem.

        Args:
            plan_item_id: The ID of the PlanItem to update.
            data: PlanItemUpdate schema with fields to update.

        Returns:
            Updated PlanItemResponse if found, None otherwise.

        Validates: Requirements 22.8
        """
        existing = await db.plan_items.get(plan_item_id)
        if not existing:
            return None

        updates = {}
        if data.title is not None:
            updates["title"] = data.title
        if data.description is not None:
            updates["description"] = data.description
        if data.source_todo_id is not None:
            updates["source_todo_id"] = data.source_todo_id
        if data.source_task_id is not None:
            updates["source_task_id"] = data.source_task_id
        if data.status is not None:
            updates["status"] = data.status.value
        if data.priority is not None:
            updates["priority"] = data.priority.value
        if data.scheduled_date is not None:
            updates["scheduled_date"] = data.scheduled_date.isoformat()
        if data.focus_type is not None:
            updates["focus_type"] = data.focus_type.value
        if data.sort_order is not None:
            updates["sort_order"] = data.sort_order

        if not updates:
            return self._dict_to_response(existing)

        result = await db.plan_items.update(plan_item_id, updates)
        if not result:
            return None

        logger.info(f"Updated PlanItem {plan_item_id}")
        return self._dict_to_response(result)

    async def delete(self, plan_item_id: str) -> bool:
        """Delete a PlanItem.

        Args:
            plan_item_id: The ID of the PlanItem to delete.

        Returns:
            True if deleted, False if not found.

        Validates: Requirements 22.8
        """
        existing = await db.plan_items.get(plan_item_id)
        if not existing:
            return False

        await db.plan_items.delete(plan_item_id)
        logger.info(f"Deleted PlanItem {plan_item_id}")
        return True

    async def cascade_task_completion(self, task_id: str) -> int:
        """Cascade task completion to linked PlanItems.

        When a Task's status changes to "completed", any PlanItem with
        source_task_id matching that task should also be set to "completed".

        Args:
            task_id: The ID of the completed Task.

        Returns:
            Number of PlanItems updated to completed status.

        Validates: Requirements 22.7
        """
        # Get all plan items and filter for those linked to this task
        all_items = await db.plan_items.list()
        linked_items = [
            item for item in all_items
            if item.get("source_task_id") == task_id
            and item.get("status") != PlanItemStatus.COMPLETED.value
        ]

        updated_count = 0
        for item in linked_items:
            await db.plan_items.update(item["id"], {
                "status": PlanItemStatus.COMPLETED.value,
            })
            updated_count += 1
            logger.debug(f"Cascaded completion to PlanItem {item['id']} from Task {task_id}")

        if updated_count > 0:
            logger.info(f"Task {task_id} completion cascaded to {updated_count} PlanItem(s)")

        return updated_count

    async def reorder(
        self,
        workspace_id: str,
        focus_type: FocusType,
        plan_item_ids: List[str],
    ) -> List[PlanItemResponse]:
        """Reorder PlanItems within a focus_type category.

        Updates sort_order for each PlanItem based on its position in the
        provided list of IDs.

        Args:
            workspace_id: The workspace ID to scope the reorder.
            focus_type: The focus_type category to reorder within.
            plan_item_ids: List of PlanItem IDs in desired order.

        Returns:
            List of updated PlanItemResponse objects in new order.

        Raises:
            ValueError: If any plan_item_id doesn't belong to the workspace/focus_type.

        Validates: Requirements 22.6
        """
        # Validate all IDs belong to the workspace and focus_type
        items_in_category = await db.plan_items.list_by_workspace(
            workspace_id, focus_type.value
        )
        valid_ids = {item["id"] for item in items_in_category}

        for pid in plan_item_ids:
            if pid not in valid_ids:
                raise ValueError(
                    f"PlanItem {pid} not found in workspace {workspace_id} "
                    f"with focus_type {focus_type.value}"
                )

        # Update sort_order based on position in the list
        updated_items = []
        for index, pid in enumerate(plan_item_ids):
            result = await db.plan_items.update(pid, {"sort_order": index})
            if result:
                updated_items.append(self._dict_to_response(result))

        logger.info(
            f"Reordered {len(plan_item_ids)} PlanItems in workspace {workspace_id}, "
            f"focus_type={focus_type.value}"
        )
        return updated_items

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

    def _dict_to_response(self, data: dict) -> PlanItemResponse:
        """Convert a database dict to PlanItemResponse.

        Args:
            data: Database row dict.

        Returns:
            PlanItemResponse Pydantic model.
        """
        return PlanItemResponse(
            id=data["id"],
            workspace_id=data["workspace_id"],
            title=data["title"],
            description=data.get("description"),
            source_todo_id=data.get("source_todo_id"),
            source_task_id=data.get("source_task_id"),
            status=data["status"],
            priority=data["priority"],
            scheduled_date=self._parse_datetime(data.get("scheduled_date")),
            focus_type=data["focus_type"],
            sort_order=data.get("sort_order", 0),
            created_at=self._parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
            updated_at=self._parse_datetime(data["updated_at"]) or datetime.now(timezone.utc),
        )


# Global instance
plan_item_manager = PlanItemManager()
