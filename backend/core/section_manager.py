"""Section manager for the Daily Work Operating Loop sections.

This module provides the SectionManager class for querying section data
across the six phases: Signals → Plan → Execute → Communicate → Artifacts → Reflection.

Each section method returns a unified SectionResponse with counts, groups,
pagination, sort_keys, and last_updated_at. Supports workspace_id="all"
for aggregation across all non-archived workspaces.

Requirements: 7.1-7.12
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from database import db
from schemas.section import (
    SectionCounts,
    SectionGroup,
    SectionResponse,
    Pagination,
    SignalsCounts,
    PlanCounts,
    ExecuteCounts,
    CommunicateCounts,
    ArtifactsCounts,
    ReflectionCounts,
)
from schemas.todo import ToDoResponse, ToDoStatus
from schemas.plan_item import PlanItemResponse, FocusType
from schemas.communication import CommunicationResponse, CommunicationStatus
from schemas.artifact import ArtifactResponse, ArtifactType
from schemas.reflection import ReflectionResponse, ReflectionType

logger = logging.getLogger(__name__)


class SectionManager:
    """Manages section data queries for the Daily Work Operating Loop.

    Provides methods to retrieve section counts and grouped items for each
    of the six sections. Supports workspace-scoped queries and "all"
    workspace aggregation (excluding archived workspaces).

    When ``global_view=True`` and ``workspace_id="all"``, section methods
    include an additional "recommended" group containing the top N items
    sorted by priority desc then updated_at desc.  This is the opinionated
    SwarmWS Global View.  When ``global_view=False`` (or workspace_id is
    not "all"), no recommendation ranking is applied — this is the neutral
    "All Workspaces" scope.

    Requirements: 7.1-7.12, 37.1-37.12
    """

    # Priority ordering for recommendation ranking (higher = more important)
    _PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}
    DEFAULT_RECOMMENDED_N = 3

    # -- helpers ----------------------------------------------------------

    async def _get_workspace_ids(self, workspace_id: str) -> list[str]:
        """Resolve workspace_id to a list of workspace IDs to query.

        In the single-workspace model, always returns ['swarmws'].
        The "all" parameter is kept for backward compatibility.
        """
        if workspace_id == "all":
            # Single workspace model — always return the singleton
            ws = await db.workspace_config.get_config()
            if ws:
                return [ws["id"]]
            return []
        # Validate workspace config exists
        ws = await db.workspace_config.get_config()
        if not ws:
            logger.warning(f"Workspace config not found in section query")
            return []
        return [workspace_id]

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse an ISO datetime string into a timezone-aware datetime."""
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

    def _latest_updated(self, items: list[dict]) -> Optional[datetime]:
        """Return the most recent updated_at from a list of row dicts."""
        latest: Optional[datetime] = None
        for item in items:
            dt = self._parse_datetime(item.get("updated_at"))
            if dt and (latest is None or dt > latest):
                latest = dt
        return latest

    def _paginate(self, items: list, limit: int, offset: int) -> tuple[list, Pagination]:
        """Apply pagination to a list and return (page, Pagination)."""
        total = len(items)
        page = items[offset : offset + limit]
        return page, Pagination(
            limit=limit,
            offset=offset,
            total=total,
            has_more=(offset + limit) < total,
        )

    def _is_global_view(self, workspace_id: str, global_view: bool) -> bool:
        """Return True when the request is for the opinionated SwarmWS Global View.

        The recommended group is only added when *both* conditions hold:
        - workspace_id == "all"  (cross-workspace aggregation)
        - global_view == True    (opinionated cockpit mode)

        Requirement 37: SwarmWS Global View includes a "Recommended" group;
        neutral "all" scope does not.
        """
        return workspace_id == "all" and global_view


    def _build_recommended_group(
        self,
        all_items: list[dict],
        converter,
        n: int | None = None,
    ) -> SectionGroup:
        """Build the "recommended" group for SwarmWS Global View.

        Selects the top *n* items from *all_items* sorted by priority desc
        then updated_at desc.  The *converter* callable transforms raw DB
        dicts into Pydantic response models.

        Args:
            all_items: All items collected across workspaces (pre-pagination).
            converter: A method that converts a dict to a response model.
            n: Number of recommended items (defaults to DEFAULT_RECOMMENDED_N).

        Returns:
            A SectionGroup named "recommended".

        Requirement 37: SwarmWS Global View SHALL include a "Recommended"
        group showing top N items (default N=3, configurable) based on
        priority desc, then updated_at desc.
        """
        if n is None:
            n = self.DEFAULT_RECOMMENDED_N

        if not all_items:
            return SectionGroup(name="recommended", items=[])

        # Two-pass stable sort: first by updated_at desc, then by priority asc.
        # Python's sort is stable, so items with the same priority retain
        # their updated_at desc ordering from the first pass.
        presorted = sorted(
            all_items,
            key=lambda x: x.get("updated_at", ""),
            reverse=True,
        )
        sorted_items = sorted(
            presorted,
            key=lambda x: self._PRIORITY_ORDER.get(x.get("priority", "none"), 3),
        )
        top_n = sorted_items[:n]
        return SectionGroup(
            name="recommended",
            items=[converter(item) for item in top_n],
        )



    # -- section counts ---------------------------------------------------

    async def get_section_counts(self, workspace_id: str) -> SectionCounts:
        """Get aggregated counts for all six sections.

        Args:
            workspace_id: Workspace ID or "all" for cross-workspace aggregation.

        Returns:
            SectionCounts with counts for each section and sub-category.

        Validates: Requirements 7.1, 7.8, 7.9
        """
        ws_ids = await self._get_workspace_ids(workspace_id)

        signals = SignalsCounts()
        plan = PlanCounts()
        execute = ExecuteCounts()
        communicate = CommunicateCounts()
        artifacts = ArtifactsCounts()
        reflection = ReflectionCounts()

        # Fetch all tasks once before the loop (avoid N repeated DB calls)
        all_tasks_raw = await db.tasks.list_all()

        for wid in ws_ids:
            # Signals counts
            for status in [ToDoStatus.PENDING, ToDoStatus.OVERDUE, ToDoStatus.IN_DISCUSSION]:
                count = await db.todos.count_by_workspace_and_status(wid, status.value)
                if status == ToDoStatus.PENDING:
                    signals.pending += count
                elif status == ToDoStatus.OVERDUE:
                    signals.overdue += count
                elif status == ToDoStatus.IN_DISCUSSION:
                    signals.in_discussion += count
                signals.total += count

            # Plan counts
            for ft in [FocusType.TODAY, FocusType.UPCOMING, FocusType.BLOCKED]:
                count = await db.plan_items.count_by_workspace_and_focus(wid, ft.value)
                if ft == FocusType.TODAY:
                    plan.today += count
                elif ft == FocusType.UPCOMING:
                    plan.upcoming += count
                elif ft == FocusType.BLOCKED:
                    plan.blocked += count
                plan.total += count

            # Execute counts – filter from pre-fetched tasks
            ws_tasks = [t for t in all_tasks_raw if t.get("workspace_id") == wid]
            for t in ws_tasks:
                status = t.get("status", "")
                if status == "draft":
                    execute.draft += 1
                elif status == "wip":
                    execute.wip += 1
                elif status == "blocked":
                    execute.blocked += 1
                elif status == "completed":
                    execute.completed += 1
                execute.total += 1

            # Communicate counts
            for cs in [CommunicationStatus.PENDING_REPLY, CommunicationStatus.AI_DRAFT, CommunicationStatus.FOLLOW_UP]:
                count = await db.communications.count_by_workspace_and_status(wid, cs.value)
                if cs == CommunicationStatus.PENDING_REPLY:
                    communicate.pending_reply += count
                elif cs == CommunicationStatus.AI_DRAFT:
                    communicate.ai_draft += count
                elif cs == CommunicationStatus.FOLLOW_UP:
                    communicate.follow_up += count
                communicate.total += count

            # Artifacts counts
            for at in [ArtifactType.PLAN, ArtifactType.REPORT, ArtifactType.DOC, ArtifactType.DECISION]:
                count = await db.artifacts.count_by_workspace_and_type(wid, at.value)
                if at == ArtifactType.PLAN:
                    artifacts.plan += count
                elif at == ArtifactType.REPORT:
                    artifacts.report += count
                elif at == ArtifactType.DOC:
                    artifacts.doc += count
                elif at == ArtifactType.DECISION:
                    artifacts.decision += count
                artifacts.total += count

            # Reflection counts
            for rt in [ReflectionType.DAILY_RECAP, ReflectionType.WEEKLY_SUMMARY, ReflectionType.LESSONS_LEARNED]:
                count = await db.reflections.count_by_workspace_and_type(wid, rt.value)
                if rt == ReflectionType.DAILY_RECAP:
                    reflection.daily_recap += count
                elif rt == ReflectionType.WEEKLY_SUMMARY:
                    reflection.weekly_summary += count
                elif rt == ReflectionType.LESSONS_LEARNED:
                    reflection.lessons_learned += count
                reflection.total += count

        return SectionCounts(
            signals=signals,
            plan=plan,
            execute=execute,
            communicate=communicate,
            artifacts=artifacts,
            reflection=reflection,
        )

    # -- Signals section ---------------------------------------------------

    async def get_signals(
        self,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
        global_view: bool = False,
    ) -> SectionResponse:
        """Get Signals (ToDos) grouped by status sub-category.

        Args:
            workspace_id: Workspace ID or "all".
            limit: Max items per page (default 50).
            offset: Items to skip.
            global_view: When True and workspace_id="all", include a
                "recommended" group with top N items by priority/recency.

        Returns:
            SectionResponse with ToDos grouped by status.

        Validates: Requirements 7.2, 7.8, 7.9, 7.10, 7.11, 37.1-37.12
        """
        ws_ids = await self._get_workspace_ids(workspace_id)

        # Collect all active ToDos (pending, overdue, in_discussion)
        active_statuses = [ToDoStatus.PENDING, ToDoStatus.OVERDUE, ToDoStatus.IN_DISCUSSION]
        all_items: list[dict] = []
        for wid in ws_ids:
            for status in active_statuses:
                items = await db.todos.list_by_workspace(wid, status.value)
                all_items.extend(items)

        # Sort by created_at desc
        all_items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

        # Build counts
        counts = {"total": 0, "pending": 0, "overdue": 0, "in_discussion": 0}
        for item in all_items:
            s = item.get("status", "")
            counts["total"] += 1
            if s in counts:
                counts[s] += 1

        # Paginate
        page, pagination = self._paginate(all_items, limit, offset)

        # Group paginated items by status
        groups_map: dict[str, list] = {"pending": [], "overdue": [], "in_discussion": []}
        for item in page:
            s = item.get("status", "")
            if s in groups_map:
                groups_map[s].append(self._todo_to_response(item))

        groups = [
            SectionGroup(name=name, items=items)
            for name, items in groups_map.items()
            if items
        ]

        # Add recommended group for SwarmWS Global View
        if self._is_global_view(workspace_id, global_view) and all_items:
            recommended = self._build_recommended_group(
                all_items, self._todo_to_response,
            )
            if recommended.items:
                groups.insert(0, recommended)

        return SectionResponse(
            counts=counts,
            groups=groups,
            pagination=pagination,
            sort_keys=["created_at", "updated_at", "priority", "due_date"],
            last_updated_at=self._latest_updated(all_items),
        )

    # -- Plan section ------------------------------------------------------

    async def get_plan(
        self,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
        global_view: bool = False,
    ) -> SectionResponse:
        """Get PlanItems grouped by focus_type sub-category.

        Args:
            workspace_id: Workspace ID or "all".
            limit: Max items per page (default 50).
            offset: Items to skip.
            global_view: When True and workspace_id="all", include a
                "recommended" group with top N items by priority/recency.

        Returns:
            SectionResponse with PlanItems grouped by focus_type.

        Validates: Requirements 7.3, 7.8, 7.9, 7.10, 7.11, 37.1-37.12
        """
        ws_ids = await self._get_workspace_ids(workspace_id)

        focus_types = [FocusType.TODAY, FocusType.UPCOMING, FocusType.BLOCKED]
        all_items: list[dict] = []
        for wid in ws_ids:
            for ft in focus_types:
                items = await db.plan_items.list_by_workspace(wid, ft.value)
                all_items.extend(items)

        # Sort by sort_order asc, then updated_at desc
        all_items.sort(key=lambda x: (x.get("sort_order", 0), x.get("updated_at", "")))

        # Build counts
        counts = {"total": 0, "today": 0, "upcoming": 0, "blocked": 0}
        for item in all_items:
            ft = item.get("focus_type", "")
            counts["total"] += 1
            if ft in counts:
                counts[ft] += 1

        page, pagination = self._paginate(all_items, limit, offset)

        groups_map: dict[str, list] = {"today": [], "upcoming": [], "blocked": []}
        for item in page:
            ft = item.get("focus_type", "")
            if ft in groups_map:
                groups_map[ft].append(self._plan_item_to_response(item))

        groups = [
            SectionGroup(name=name, items=items)
            for name, items in groups_map.items()
            if items
        ]

        # Add recommended group for SwarmWS Global View
        if self._is_global_view(workspace_id, global_view) and all_items:
            recommended = self._build_recommended_group(
                all_items, self._plan_item_to_response,
            )
            if recommended.items:
                groups.insert(0, recommended)

        return SectionResponse(
            counts=counts,
            groups=groups,
            pagination=pagination,
            sort_keys=["sort_order", "created_at", "updated_at", "priority", "scheduled_date"],
            last_updated_at=self._latest_updated(all_items),
        )

    # -- Execute section ----------------------------------------------------

    async def get_execute(
        self,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
        global_view: bool = False,
    ) -> SectionResponse:
        """Get Tasks grouped by status sub-category.

        Args:
            workspace_id: Workspace ID or "all".
            limit: Max items per page (default 50).
            offset: Items to skip.
            global_view: When True and workspace_id="all", include a
                "recommended" group with top N items by priority/recency.

        Returns:
            SectionResponse with Tasks grouped by status.

        Validates: Requirements 7.4, 7.8, 7.9, 7.10, 7.11, 37.1-37.12
        """
        ws_ids = await self._get_workspace_ids(workspace_id)
        ws_id_set = set(ws_ids)

        # Tasks table doesn't have workspace-scoped list, so fetch all and filter
        all_tasks_raw = await db.tasks.list_all()
        all_items = [
            t for t in all_tasks_raw
            if t.get("workspace_id") in ws_id_set
        ]

        # Sort by created_at desc
        all_items.sort(key=lambda x: x.get("updated_at") or x.get("created_at", ""), reverse=True)

        # Build counts for active statuses
        active_statuses = ["draft", "wip", "blocked", "completed"]
        counts: dict[str, int] = {"total": 0, "draft": 0, "wip": 0, "blocked": 0, "completed": 0}
        # Only count items with active statuses for the section view
        filtered_items = [t for t in all_items if t.get("status") in active_statuses]
        for item in filtered_items:
            s = item.get("status", "")
            counts["total"] += 1
            if s in counts:
                counts[s] += 1

        page, pagination = self._paginate(filtered_items, limit, offset)

        groups_map: dict[str, list] = {"draft": [], "wip": [], "blocked": [], "completed": []}
        for item in page:
            s = item.get("status", "")
            if s in groups_map:
                groups_map[s].append(item)

        groups = [
            SectionGroup(name=name, items=items)
            for name, items in groups_map.items()
            if items
        ]

        # Add recommended group for SwarmWS Global View
        if self._is_global_view(workspace_id, global_view) and filtered_items:
            recommended = self._build_recommended_group(
                filtered_items, lambda x: x,
            )
            if recommended.items:
                groups.insert(0, recommended)

        return SectionResponse(
            counts=counts,
            groups=groups,
            pagination=pagination,
            sort_keys=["created_at", "updated_at", "priority", "status"],
            last_updated_at=self._latest_updated(filtered_items),
        )

    # -- Communicate section -----------------------------------------------

    async def get_communicate(
        self,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
        global_view: bool = False,
    ) -> SectionResponse:
        """Get Communications grouped by status sub-category.

        Args:
            workspace_id: Workspace ID or "all".
            limit: Max items per page (default 50).
            offset: Items to skip.
            global_view: When True and workspace_id="all", include a
                "recommended" group with top N items by priority/recency.

        Returns:
            SectionResponse with Communications grouped by status.

        Validates: Requirements 7.5, 7.8, 7.9, 7.10, 7.11, 37.1-37.12
        """
        ws_ids = await self._get_workspace_ids(workspace_id)

        active_statuses = [
            CommunicationStatus.PENDING_REPLY,
            CommunicationStatus.AI_DRAFT,
            CommunicationStatus.FOLLOW_UP,
        ]
        all_items: list[dict] = []
        for wid in ws_ids:
            for cs in active_statuses:
                items = await db.communications.list_by_workspace(wid, cs.value)
                all_items.extend(items)

        all_items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

        counts = {"total": 0, "pending_reply": 0, "ai_draft": 0, "follow_up": 0}
        for item in all_items:
            s = item.get("status", "")
            counts["total"] += 1
            if s in counts:
                counts[s] += 1

        page, pagination = self._paginate(all_items, limit, offset)

        groups_map: dict[str, list] = {"pending_reply": [], "ai_draft": [], "follow_up": []}
        for item in page:
            s = item.get("status", "")
            if s in groups_map:
                groups_map[s].append(self._communication_to_response(item))

        groups = [
            SectionGroup(name=name, items=items)
            for name, items in groups_map.items()
            if items
        ]

        # Add recommended group for SwarmWS Global View
        if self._is_global_view(workspace_id, global_view) and all_items:
            recommended = self._build_recommended_group(
                all_items, self._communication_to_response,
            )
            if recommended.items:
                groups.insert(0, recommended)

        return SectionResponse(
            counts=counts,
            groups=groups,
            pagination=pagination,
            sort_keys=["created_at", "updated_at", "priority", "due_date"],
            last_updated_at=self._latest_updated(all_items),
        )

    # -- Artifacts section -------------------------------------------------

    async def get_artifacts(
        self,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
        global_view: bool = False,
    ) -> SectionResponse:
        """Get Artifacts grouped by artifact_type sub-category.

        Args:
            workspace_id: Workspace ID or "all".
            limit: Max items per page (default 50).
            offset: Items to skip.
            global_view: When True and workspace_id="all", include a
                "recommended" group with top N items by priority/recency.

        Returns:
            SectionResponse with Artifacts grouped by artifact_type.

        Validates: Requirements 7.6, 7.8, 7.9, 7.10, 7.11, 37.1-37.12
        """
        ws_ids = await self._get_workspace_ids(workspace_id)

        artifact_types = [
            ArtifactType.PLAN,
            ArtifactType.REPORT,
            ArtifactType.DOC,
            ArtifactType.DECISION,
        ]
        all_items: list[dict] = []
        for wid in ws_ids:
            for at in artifact_types:
                items = await db.artifacts.list_by_workspace(wid, at.value)
                all_items.extend(items)
            # Also include "other" type artifacts
            other_items = await db.artifacts.list_by_workspace(wid, ArtifactType.OTHER.value)
            all_items.extend(other_items)

        all_items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

        counts = {"total": 0, "plan": 0, "report": 0, "doc": 0, "decision": 0}
        for item in all_items:
            at = item.get("artifact_type", "")
            counts["total"] += 1
            if at in counts:
                counts[at] += 1

        page, pagination = self._paginate(all_items, limit, offset)

        groups_map: dict[str, list] = {"plan": [], "report": [], "doc": [], "decision": []}
        for item in page:
            at = item.get("artifact_type", "")
            resp = self._artifact_to_response(item)
            if at in groups_map:
                groups_map[at].append(resp)
            # "other" type items are included in total count but not in named groups

        groups = [
            SectionGroup(name=name, items=items)
            for name, items in groups_map.items()
            if items
        ]

        # Add recommended group for SwarmWS Global View
        # Artifacts don't have a priority field, so recommended is by updated_at desc
        if self._is_global_view(workspace_id, global_view) and all_items:
            recommended = self._build_recommended_group(
                all_items, self._artifact_to_response,
            )
            if recommended.items:
                groups.insert(0, recommended)

        return SectionResponse(
            counts=counts,
            groups=groups,
            pagination=pagination,
            sort_keys=["created_at", "updated_at", "artifact_type", "title"],
            last_updated_at=self._latest_updated(all_items),
        )

    # -- Reflection section ------------------------------------------------

    async def get_reflection(
        self,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
        global_view: bool = False,
    ) -> SectionResponse:
        """Get Reflections grouped by reflection_type sub-category.

        Args:
            workspace_id: Workspace ID or "all".
            limit: Max items per page (default 50).
            offset: Items to skip.
            global_view: When True and workspace_id="all", include a
                "recommended" group with top N items by priority/recency.

        Returns:
            SectionResponse with Reflections grouped by reflection_type.

        Validates: Requirements 7.7, 7.8, 7.9, 7.10, 7.11, 37.1-37.12
        """
        ws_ids = await self._get_workspace_ids(workspace_id)

        reflection_types = [
            ReflectionType.DAILY_RECAP,
            ReflectionType.WEEKLY_SUMMARY,
            ReflectionType.LESSONS_LEARNED,
        ]
        all_items: list[dict] = []
        for wid in ws_ids:
            for rt in reflection_types:
                items = await db.reflections.list_by_workspace(wid, rt.value)
                all_items.extend(items)

        all_items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)

        counts = {"total": 0, "daily_recap": 0, "weekly_summary": 0, "lessons_learned": 0}
        for item in all_items:
            rt = item.get("reflection_type", "")
            counts["total"] += 1
            if rt in counts:
                counts[rt] += 1

        page, pagination = self._paginate(all_items, limit, offset)

        groups_map: dict[str, list] = {
            "daily_recap": [],
            "weekly_summary": [],
            "lessons_learned": [],
        }
        for item in page:
            rt = item.get("reflection_type", "")
            if rt in groups_map:
                groups_map[rt].append(self._reflection_to_response(item))

        groups = [
            SectionGroup(name=name, items=items)
            for name, items in groups_map.items()
            if items
        ]

        # Add recommended group for SwarmWS Global View
        # Reflections don't have a priority field, so recommended is by updated_at desc
        if self._is_global_view(workspace_id, global_view) and all_items:
            recommended = self._build_recommended_group(
                all_items, self._reflection_to_response,
            )
            if recommended.items:
                groups.insert(0, recommended)

        return SectionResponse(
            counts=counts,
            groups=groups,
            pagination=pagination,
            sort_keys=["created_at", "updated_at", "reflection_type", "period_start"],
            last_updated_at=self._latest_updated(all_items),
        )

    # -- response converters -----------------------------------------------

    def _todo_to_response(self, data: dict) -> ToDoResponse:
        """Convert a database dict to ToDoResponse."""
        return ToDoResponse(
            id=data["id"],
            workspace_id=data["workspace_id"],
            title=data["title"],
            description=data.get("description"),
            source=data.get("source"),
            source_type=data["source_type"],
            status=data["status"],
            priority=data["priority"],
            due_date=self._parse_datetime(data.get("due_date")),
            task_id=data.get("task_id"),
            created_at=self._parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
            updated_at=self._parse_datetime(data["updated_at"]) or datetime.now(timezone.utc),
        )

    def _plan_item_to_response(self, data: dict) -> PlanItemResponse:
        """Convert a database dict to PlanItemResponse."""
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

    def _communication_to_response(self, data: dict) -> CommunicationResponse:
        """Convert a database dict to CommunicationResponse."""
        return CommunicationResponse(
            id=data["id"],
            workspace_id=data["workspace_id"],
            title=data["title"],
            description=data.get("description"),
            recipient=data.get("recipient", ""),
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

    def _artifact_to_response(self, data: dict) -> ArtifactResponse:
        """Convert a database dict to ArtifactResponse."""
        return ArtifactResponse(
            id=data["id"],
            workspace_id=data["workspace_id"],
            task_id=data.get("task_id"),
            artifact_type=data["artifact_type"],
            title=data["title"],
            file_path=data["file_path"],
            version=data.get("version", 1),
            created_by=data.get("created_by", "system"),
            tags=None,  # Tags loaded separately if needed
            created_at=self._parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
            updated_at=self._parse_datetime(data["updated_at"]) or datetime.now(timezone.utc),
        )

    def _reflection_to_response(self, data: dict) -> ReflectionResponse:
        """Convert a database dict to ReflectionResponse."""
        return ReflectionResponse(
            id=data["id"],
            workspace_id=data["workspace_id"],
            reflection_type=data["reflection_type"],
            title=data["title"],
            file_path=data["file_path"],
            period_start=self._parse_datetime(data["period_start"]) or datetime.now(timezone.utc),
            period_end=self._parse_datetime(data["period_end"]) or datetime.now(timezone.utc),
            generated_by=data.get("generated_by", "user"),
            created_at=self._parse_datetime(data["created_at"]) or datetime.now(timezone.utc),
            updated_at=self._parse_datetime(data["updated_at"]) or datetime.now(timezone.utc),
        )


# Global instance
section_manager = SectionManager()
