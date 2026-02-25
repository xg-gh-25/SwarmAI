"""Reflection manager for the Reflection section of the Daily Work Operating Loop.

This module provides the ReflectionManager class for managing Reflection entities,
which represent structured review items capturing progress, insights, and lessons learned.
Reflections use hybrid storage: content stored as markdown files in the filesystem
under Artifacts/Reports/, metadata tracked in the database.

File naming convention: {reflection_type}_{date}.md
    e.g., daily_recap_2025-02-21.md, weekly_summary_2025-02-21.md

Requirements: 28.1-28.11
"""
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

import aiosqlite
import anyio

from database import db
from schemas.reflection import (
    GeneratedBy,
    ReflectionCreate,
    ReflectionResponse,
    ReflectionType,
    ReflectionUpdate,
)

logger = logging.getLogger(__name__)

# Reflection content is stored under Artifacts/Reports/
# Requirement 28.1: Store content in Artifacts/Reports/ folder
REFLECTIONS_SUBFOLDER = "Artifacts/Reports"


class ReflectionManager:
    """Manages Reflection entities with hybrid storage (DB metadata + filesystem content).

    Reflections are structured review items capturing progress, insights, and
    lessons learned. Their metadata lives in the database while the actual
    content files live in the workspace filesystem under Artifacts/Reports/.

    Key Features:
    - CRUD operations with hybrid storage
    - File naming: {reflection_type}_{date}.md
    - Daily recap generation (aggregate completed tasks, handled signals)
    - Weekly summary generation (aggregate daily recaps)
    - Default workspace assignment to SwarmWS

    Requirements: 28.1-28.11
    """

    def __init__(self, workspace_manager=None):
        """Initialize ReflectionManager.

        Args:
            workspace_manager: Optional SwarmWorkspaceManager instance for path
                expansion. If None, imports the global instance.
        """
        self._workspace_manager = workspace_manager

    @property
    def workspace_manager(self):
        if self._workspace_manager is None:
            from core.swarm_workspace_manager import swarm_workspace_manager
            self._workspace_manager = swarm_workspace_manager
        return self._workspace_manager

    async def _get_default_workspace_id(self) -> str:
        """Get the default workspace (SwarmWS) ID."""
        workspace = await db.workspace_config.get_config()
        if not workspace:
            raise ValueError("SwarmWS workspace config not found.")
        return workspace["id"]

    async def _resolve_reports_dir(self, workspace_id: str) -> Path:
        """Resolve the filesystem path for storing reflection content.

        Returns the expanded absolute path: {workspace_file_path}/Artifacts/Reports/

        Raises:
            ValueError: If workspace config not found.
        """
        workspace = await db.workspace_config.get_config()
        if not workspace:
            raise ValueError(f"Workspace config not found")

        expanded = self.workspace_manager.expand_path(workspace["file_path"])
        return Path(expanded) / REFLECTIONS_SUBFOLDER

    def _build_filename(self, reflection_type: ReflectionType, date: datetime) -> str:
        """Build a reflection filename from type and date.

        Format: {reflection_type}_{YYYY-MM-DD}.md
        Requirement 28.1: naming convention {reflection_type}_{date}.md

        Args:
            reflection_type: The type of reflection.
            date: The date for the filename (typically period_start).

        Returns:
            Filename string, e.g. "daily_recap_2025-02-21.md"
        """
        date_str = date.strftime("%Y-%m-%d")
        return f"{reflection_type.value}_{date_str}.md"

    async def _ensure_directory(self, dir_path: Path) -> None:
        """Ensure a directory exists, creating it if needed."""
        await anyio.to_thread.run_sync(lambda: dir_path.mkdir(parents=True, exist_ok=True))

    async def _write_file(self, file_path: Path, content: str) -> None:
        """Write content to a file."""
        await anyio.to_thread.run_sync(lambda: file_path.write_text(content, encoding="utf-8"))

    async def _read_file(self, file_path: Path) -> Optional[str]:
        """Read content from a file, returning None if not found."""
        def _read():
            if file_path.exists():
                return file_path.read_text(encoding="utf-8")
            return None
        return await anyio.to_thread.run_sync(_read)

    async def _delete_file(self, file_path: Path) -> bool:
        """Delete a file if it exists. Returns True if deleted."""
        def _del():
            if file_path.exists():
                file_path.unlink()
                return True
            return False
        return await anyio.to_thread.run_sync(_del)

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse a datetime string to datetime object."""
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

    def _dict_to_response(self, data: dict) -> ReflectionResponse:
        """Convert a database dict to ReflectionResponse."""
        return ReflectionResponse(
            id=data["id"],
            workspace_id=data["workspace_id"],
            reflection_type=data["reflection_type"],
            title=data["title"],
            file_path=data["file_path"],
            period_start=self._parse_datetime(data["period_start"]) or datetime.now(timezone.utc),
            period_end=self._parse_datetime(data["period_end"]) or datetime.now(timezone.utc),
            generated_by=data["generated_by"],
            created_at=self._parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
            updated_at=self._parse_datetime(data["updated_at"]) or datetime.now(timezone.utc),
        )

    # -----------------------------------------------------------------------
    # CRUD Methods
    # -----------------------------------------------------------------------

    async def create(
        self,
        data: ReflectionCreate,
        content: str = "",
    ) -> ReflectionResponse:
        """Create a new Reflection with hybrid storage.

        Stores metadata in the database and writes content to the filesystem
        under Artifacts/Reports/ folder. The file_path from data is used as a
        hint; the actual path is always computed from reflection_type and
        period_start to enforce the naming convention.

        Args:
            data: ReflectionCreate schema with reflection details.
            content: The markdown content to write. Defaults to empty string.

        Returns:
            ReflectionResponse with the created reflection metadata.

        Validates: Requirements 28.1, 28.2, 28.3, 28.4
        """
        workspace_id = data.workspace_id
        if not workspace_id:
            workspace_id = await self._get_default_workspace_id()

        # Build filename and resolve directory
        # Always compute the canonical path from type + date
        reports_dir = await self._resolve_reports_dir(workspace_id)
        await self._ensure_directory(reports_dir)

        filename = self._build_filename(data.reflection_type, data.period_start)
        file_path_abs = reports_dir / filename

        # Write content to filesystem
        # Requirement 28.1: content stored as markdown files in Artifacts/Reports/
        await self._write_file(file_path_abs, content)

        # Store relative path from workspace root
        relative_path = f"{REFLECTIONS_SUBFOLDER}/{filename}"

        now = datetime.now(timezone.utc).isoformat()
        reflection_id = str(uuid4())

        reflection_dict = {
            "id": reflection_id,
            "workspace_id": workspace_id,
            "reflection_type": data.reflection_type.value,
            "title": data.title,
            "file_path": relative_path,
            "period_start": data.period_start.isoformat(),
            "period_end": data.period_end.isoformat(),
            "generated_by": data.generated_by.value,
            "created_at": now,
            "updated_at": now,
        }

        await db.reflections.put(reflection_dict)
        logger.info(f"Created Reflection {reflection_id} at {relative_path}")

        return self._dict_to_response(reflection_dict)

    async def get(self, reflection_id: str) -> Optional[ReflectionResponse]:
        """Get a Reflection by ID.

        Args:
            reflection_id: The ID of the Reflection to retrieve.

        Returns:
            ReflectionResponse if found, None otherwise.

        Validates: Requirements 28.9
        """
        result = await db.reflections.get(reflection_id)
        if not result:
            return None
        return self._dict_to_response(result)

    async def get_content(self, reflection_id: str) -> Optional[str]:
        """Get the file content of a Reflection.

        Reads the content from the filesystem using the stored file_path.

        Args:
            reflection_id: The ID of the Reflection.

        Returns:
            File content as string, or None if reflection or file not found.
        """
        result = await db.reflections.get(reflection_id)
        if not result:
            return None

        workspace = await db.workspace_config.get_config()
        if not workspace:
            return None

        expanded = self.workspace_manager.expand_path(workspace["file_path"])
        file_path = Path(expanded) / result["file_path"]
        return await self._read_file(file_path)

    async def list(
        self,
        workspace_id: Optional[str] = None,
        reflection_type: Optional[ReflectionType] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ReflectionResponse]:
        """List Reflections with optional filtering.

        Args:
            workspace_id: Filter by workspace ID.
            reflection_type: Filter by reflection type.
            limit: Maximum number of results (default 50).
            offset: Number of results to skip for pagination.

        Returns:
            List of ReflectionResponse objects.

        Validates: Requirements 28.9, 28.10
        """
        if workspace_id:
            type_value = reflection_type.value if reflection_type else None
            results = await db.reflections.list_by_workspace(workspace_id, type_value)
        else:
            results = await db.reflections.list()
            if reflection_type:
                results = [r for r in results if r.get("reflection_type") == reflection_type.value]

        paginated = results[offset:offset + limit]
        return [self._dict_to_response(r) for r in paginated]

    async def update(
        self,
        reflection_id: str,
        data: ReflectionUpdate,
        new_content: Optional[str] = None,
    ) -> Optional[ReflectionResponse]:
        """Update an existing Reflection.

        When new_content is provided, the content file is overwritten.

        Args:
            reflection_id: The ID of the Reflection to update.
            data: ReflectionUpdate schema with fields to update.
            new_content: If provided, overwrites the content file.

        Returns:
            Updated ReflectionResponse if found, None otherwise.

        Validates: Requirements 28.8, 28.9
        """
        existing = await db.reflections.get(reflection_id)
        if not existing:
            return None

        updates = {}
        if data.reflection_type is not None:
            updates["reflection_type"] = data.reflection_type.value
        if data.title is not None:
            updates["title"] = data.title
        if data.period_start is not None:
            updates["period_start"] = data.period_start.isoformat()
        if data.period_end is not None:
            updates["period_end"] = data.period_end.isoformat()
        if data.generated_by is not None:
            updates["generated_by"] = data.generated_by.value

        # If reflection type or period changed, we may need to rename the file
        if data.reflection_type is not None or data.period_start is not None:
            ref_type = ReflectionType(updates.get("reflection_type", existing["reflection_type"]))
            period_start_str = updates.get("period_start", existing["period_start"])
            period_start = self._parse_datetime(period_start_str) or datetime.now(timezone.utc)

            new_filename = self._build_filename(ref_type, period_start)
            new_relative_path = f"{REFLECTIONS_SUBFOLDER}/{new_filename}"

            if new_relative_path != existing["file_path"]:
                # Rename the file on disk
                workspace = await db.workspace_config.get_config()
                if workspace:
                    expanded = self.workspace_manager.expand_path(workspace["file_path"])
                    old_abs = Path(expanded) / existing["file_path"]
                    new_abs = Path(expanded) / new_relative_path

                    # Read old content, write to new path, delete old
                    old_content = await self._read_file(old_abs)
                    if old_content is not None:
                        await self._ensure_directory(new_abs.parent)
                        await self._write_file(new_abs, new_content if new_content is not None else old_content)
                        if old_abs != new_abs:
                            await self._delete_file(old_abs)
                        new_content = None  # Already written

                updates["file_path"] = new_relative_path

        # Write new content if provided
        if new_content is not None:
            workspace = await db.workspace_config.get_config()
            if workspace:
                expanded = self.workspace_manager.expand_path(workspace["file_path"])
                file_path = updates.get("file_path", existing["file_path"])
                file_path_abs = Path(expanded) / file_path
                await self._ensure_directory(file_path_abs.parent)
                await self._write_file(file_path_abs, new_content)

        if not updates:
            return self._dict_to_response(existing)

        result = await db.reflections.update(reflection_id, updates)
        if not result:
            return None

        logger.info(f"Updated Reflection {reflection_id}")
        return self._dict_to_response(result)

    async def delete(self, reflection_id: str, delete_file: bool = False) -> bool:
        """Delete a Reflection.

        Removes the database record and optionally the filesystem content.

        Args:
            reflection_id: The ID of the Reflection to delete.
            delete_file: If True, also delete the content file from filesystem.

        Returns:
            True if deleted, False if not found.

        Validates: Requirements 28.9
        """
        existing = await db.reflections.get(reflection_id)
        if not existing:
            return False

        # Optionally delete the file
        if delete_file:
            workspace = await db.workspace_config.get_config()
            if workspace:
                expanded = self.workspace_manager.expand_path(workspace["file_path"])
                file_path = Path(expanded) / existing["file_path"]
                await self._delete_file(file_path)

        await db.reflections.delete(reflection_id)
        logger.info(f"Deleted Reflection {reflection_id}")
        return True

    # -----------------------------------------------------------------------
    # Daily Recap & Weekly Summary Generation
    # -----------------------------------------------------------------------

    async def _get_completed_tasks_for_period(
        self, workspace_id: str, start: datetime, end: datetime
    ) -> List[dict]:
        """Get tasks completed within a date range for a workspace.

        Queries the tasks table for tasks with status 'completed' and
        completed_at within the given period.

        Args:
            workspace_id: The workspace to query.
            start: Period start (inclusive).
            end: Period end (inclusive).

        Returns:
            List of task dicts completed in the period.
        """
        all_tasks = await db.tasks.list_all(status="completed", workspace_id=workspace_id)
        start_str = start.isoformat()
        end_str = end.isoformat()

        completed = []
        for task in all_tasks:
            completed_at = task.get("completed_at")
            if completed_at and start_str <= completed_at <= end_str:
                completed.append(task)
        return completed

    async def _get_handled_signals_for_period(
        self, workspace_id: str, start: datetime, end: datetime
    ) -> List[dict]:
        """Get signals (ToDos) handled within a date range for a workspace.

        Queries the todos table for todos with status 'handled' and
        updated_at within the given period.

        Args:
            workspace_id: The workspace to query.
            start: Period start (inclusive).
            end: Period end (inclusive).

        Returns:
            List of todo dicts handled in the period.
        """
        all_todos = await db.todos.list_by_workspace(workspace_id, "handled")
        start_str = start.isoformat()
        end_str = end.isoformat()

        handled = []
        for todo in all_todos:
            updated_at = todo.get("updated_at", "")
            if updated_at and start_str <= updated_at <= end_str:
                handled.append(todo)
        return handled

    def _format_daily_recap_content(
        self,
        date: datetime,
        completed_tasks: List[dict],
        handled_signals: List[dict],
    ) -> str:
        """Format the markdown content for a daily recap.

        Args:
            date: The date of the recap.
            completed_tasks: Tasks completed on this day.
            handled_signals: Signals handled on this day.

        Returns:
            Formatted markdown string.

        Validates: Requirements 28.6
        """
        date_str = date.strftime("%Y-%m-%d")
        lines = [
            f"# Daily Recap - {date_str}",
            "",
        ]

        # Completed Tasks section
        lines.append("## Completed Tasks")
        lines.append("")
        if completed_tasks:
            for task in completed_tasks:
                title = task.get("title", "Untitled Task")
                lines.append(f"- ✅ {title}")
        else:
            lines.append("_No tasks completed today._")
        lines.append("")

        # Handled Signals section
        lines.append("## Handled Signals")
        lines.append("")
        if handled_signals:
            for signal in handled_signals:
                title = signal.get("title", "Untitled Signal")
                lines.append(f"- 📨 {title}")
        else:
            lines.append("_No signals handled today._")
        lines.append("")

        # Summary
        lines.append("## Summary")
        lines.append("")
        lines.append(f"- **Tasks completed**: {len(completed_tasks)}")
        lines.append(f"- **Signals handled**: {len(handled_signals)}")
        lines.append("")

        return "\n".join(lines)

    async def generate_daily_recap(
        self,
        workspace_id: str,
        date: Optional[datetime] = None,
    ) -> ReflectionResponse:
        """Generate a daily recap reflection for a workspace.

        Aggregates completed tasks and handled signals for the given day
        and creates a reflection with the formatted content.

        Args:
            workspace_id: The workspace to generate the recap for.
            date: The date to generate the recap for. Defaults to today (UTC).

        Returns:
            ReflectionResponse for the created daily recap.

        Validates: Requirements 28.6
        """
        if date is None:
            date = datetime.now(timezone.utc)

        # Define the day boundaries
        day_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = date.replace(hour=23, minute=59, second=59, microsecond=999999)

        # Aggregate data
        completed_tasks = await self._get_completed_tasks_for_period(
            workspace_id, day_start, day_end
        )
        handled_signals = await self._get_handled_signals_for_period(
            workspace_id, day_start, day_end
        )

        # Format content
        content = self._format_daily_recap_content(date, completed_tasks, handled_signals)

        date_str = date.strftime("%Y-%m-%d")
        title = f"Daily Recap - {date_str}"

        # Create the reflection
        create_data = ReflectionCreate(
            workspace_id=workspace_id,
            reflection_type=ReflectionType.DAILY_RECAP,
            title=title,
            file_path="pending",  # Overridden by create() with canonical path
            period_start=day_start,
            period_end=day_end,
            generated_by=GeneratedBy.SYSTEM,
        )

        return await self.create(create_data, content=content)

    def _format_weekly_summary_content(
        self,
        week_start: datetime,
        week_end: datetime,
        daily_recaps: List[dict],
        total_tasks: int,
        total_signals: int,
    ) -> str:
        """Format the markdown content for a weekly summary.

        Args:
            week_start: Start of the week.
            week_end: End of the week.
            daily_recaps: Daily recap reflection dicts for the week.
            total_tasks: Total tasks completed across the week.
            total_signals: Total signals handled across the week.

        Returns:
            Formatted markdown string.

        Validates: Requirements 28.7
        """
        start_str = week_start.strftime("%Y-%m-%d")
        end_str = week_end.strftime("%Y-%m-%d")
        lines = [
            f"# Weekly Summary - {start_str} to {end_str}",
            "",
        ]

        # Overview
        lines.append("## Overview")
        lines.append("")
        lines.append(f"- **Total tasks completed**: {total_tasks}")
        lines.append(f"- **Total signals handled**: {total_signals}")
        lines.append(f"- **Daily recaps generated**: {len(daily_recaps)}")
        lines.append("")

        # Daily Breakdown
        lines.append("## Daily Breakdown")
        lines.append("")
        if daily_recaps:
            for recap in daily_recaps:
                recap_title = recap.get("title", "Untitled Recap")
                recap_path = recap.get("file_path", "")
                lines.append(f"- 📅 {recap_title} (`{recap_path}`)")
        else:
            lines.append("_No daily recaps found for this week._")
        lines.append("")

        return "\n".join(lines)

    async def generate_weekly_summary(
        self,
        workspace_id: str,
        week_start: Optional[datetime] = None,
    ) -> ReflectionResponse:
        """Generate a weekly summary reflection for a workspace.

        Aggregates daily recaps for the week and highlights key accomplishments
        and blockers.

        Args:
            workspace_id: The workspace to generate the summary for.
            week_start: The Monday of the week. Defaults to the current week's Monday (UTC).

        Returns:
            ReflectionResponse for the created weekly summary.

        Validates: Requirements 28.7
        """
        if week_start is None:
            now = datetime.now(timezone.utc)
            # Find the most recent Monday
            days_since_monday = now.weekday()
            week_start = (now - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        week_end = (week_start + timedelta(days=6)).replace(
            hour=23, minute=59, second=59, microsecond=999999
        )

        # Get daily recaps for this week
        all_reflections = await db.reflections.list_by_workspace(
            workspace_id, ReflectionType.DAILY_RECAP.value
        )
        week_start_str = week_start.isoformat()
        week_end_str = week_end.isoformat()

        daily_recaps = []
        for r in all_reflections:
            period_start = r.get("period_start", "")
            if period_start and week_start_str <= period_start <= week_end_str:
                daily_recaps.append(r)

        # Aggregate totals from completed tasks and handled signals for the week
        completed_tasks = await self._get_completed_tasks_for_period(
            workspace_id, week_start, week_end
        )
        handled_signals = await self._get_handled_signals_for_period(
            workspace_id, week_start, week_end
        )

        # Format content
        content = self._format_weekly_summary_content(
            week_start, week_end, daily_recaps,
            total_tasks=len(completed_tasks),
            total_signals=len(handled_signals),
        )

        start_str = week_start.strftime("%Y-%m-%d")
        title = f"Weekly Summary - {start_str}"

        # Create the reflection
        create_data = ReflectionCreate(
            workspace_id=workspace_id,
            reflection_type=ReflectionType.WEEKLY_SUMMARY,
            title=title,
            file_path="pending",  # Overridden by create() with canonical path
            period_start=week_start,
            period_end=week_end,
            generated_by=GeneratedBy.SYSTEM,
        )

        return await self.create(create_data, content=content)


# Global instance
reflection_manager = ReflectionManager()
