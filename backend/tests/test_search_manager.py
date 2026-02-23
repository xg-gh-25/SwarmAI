"""Unit tests for SearchManager.

Tests global search across entity types (ToDos, Tasks, PlanItems,
Communications, Artifacts, Reflections) and thread search via
ThreadSummary (NOT raw ChatMessages).

Requirements: 31.1-31.7, 38.1-38.12
"""
import pytest
from uuid import uuid4

from database import db
from core.search_manager import search_manager, SEARCHABLE_ENTITY_TYPES
from tests.helpers import now_iso, create_workspace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_agent(agent_id: str = None) -> dict:
    now = now_iso()
    agent = {
        "id": agent_id or str(uuid4()),
        "name": "Test Agent",
        "description": "Agent for search tests",
        "model": "claude-sonnet-4-20250514",
        "permission_mode": "default",
        "created_at": now,
        "updated_at": now,
    }
    await db.agents.put(agent)
    return agent


async def _create_todo(workspace_id: str, title: str = "Test ToDo", description: str = "A test todo") -> dict:
    now = now_iso()
    todo = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "title": title,
        "description": description,
        "source_type": "manual",
        "status": "pending",
        "priority": "none",
        "created_at": now,
        "updated_at": now,
    }
    await db.todos.put(todo)
    return todo


async def _create_task(workspace_id: str, agent_id: str, title: str = "Test Task", description: str = "A test task") -> dict:
    now = now_iso()
    task = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "title": title,
        "description": description,
        "status": "draft",
        "priority": "none",
        "created_at": now,
        "updated_at": now,
    }
    await db.tasks.put(task)
    return task


async def _create_plan_item(workspace_id: str, title: str = "Test Plan", description: str = "A plan item") -> dict:
    now = now_iso()
    item = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "title": title,
        "description": description,
        "status": "active",
        "priority": "none",
        "focus_type": "today",
        "sort_order": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db.plan_items.put(item)
    return item


async def _create_communication(workspace_id: str, title: str = "Test Comm", description: str = "A communication") -> dict:
    now = now_iso()
    comm = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "title": title,
        "description": description,
        "recipient": "user@example.com",
        "channel_type": "email",
        "status": "pending_reply",
        "priority": "none",
        "created_at": now,
        "updated_at": now,
    }
    await db.communications.put(comm)
    return comm


async def _create_artifact(workspace_id: str, title: str = "Test Artifact") -> dict:
    now = now_iso()
    artifact = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "artifact_type": "doc",
        "title": title,
        "file_path": f"/tmp/artifacts/{title}.md",
        "version": 1,
        "created_by": "user",
        "created_at": now,
        "updated_at": now,
    }
    await db.artifacts.put(artifact)
    return artifact


async def _create_reflection(workspace_id: str, title: str = "Test Reflection") -> dict:
    now = now_iso()
    reflection = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "reflection_type": "daily_recap",
        "title": title,
        "file_path": f"/tmp/reflections/{title}.md",
        "period_start": now,
        "period_end": now,
        "generated_by": "user",
        "created_at": now,
        "updated_at": now,
    }
    await db.reflections.put(reflection)
    return reflection


async def _create_thread_with_summary(
    workspace_id: str,
    agent_id: str,
    thread_title: str = "Test Thread",
    summary_text: str = "Summary of the thread",
    key_decisions: str = None,
) -> tuple[dict, dict]:
    """Create a chat thread and its summary. Returns (thread, summary)."""
    now = now_iso()
    thread = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "agent_id": agent_id,
        "mode": "explore",
        "title": thread_title,
        "created_at": now,
        "updated_at": now,
    }
    await db.chat_threads.put(thread)

    summary = {
        "id": str(uuid4()),
        "thread_id": thread["id"],
        "summary_type": "rolling",
        "summary_text": summary_text,
        "key_decisions": key_decisions,
        "updated_at": now,
    }
    await db.thread_summaries.put(summary)
    return thread, summary


# ---------------------------------------------------------------------------
# Tests: Basic search
# ---------------------------------------------------------------------------

class TestSearchBasic:
    """Basic search functionality tests."""

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        """Empty or whitespace query returns no results."""
        result = await search_manager.search("")
        assert result.total == 0
        assert result.groups == []

        result2 = await search_manager.search("   ")
        assert result2.total == 0

    @pytest.mark.asyncio
    async def test_search_finds_todo_by_title(self):
        """Search matches ToDo title."""
        ws = await create_workspace("SearchWS")
        await _create_todo(ws["id"], title="Deploy microservice alpha")

        result = await search_manager.search("microservice")
        assert result.total >= 1
        todo_group = next((g for g in result.groups if g.entity_type == "todo"), None)
        assert todo_group is not None
        assert any("microservice" in item.title.lower() for item in todo_group.items)

    @pytest.mark.asyncio
    async def test_search_finds_todo_by_description(self):
        """Search matches ToDo description."""
        ws = await create_workspace("SearchWS")
        await _create_todo(ws["id"], title="Generic title", description="Fix the authentication bug")

        result = await search_manager.search("authentication")
        assert result.total >= 1
        todo_group = next((g for g in result.groups if g.entity_type == "todo"), None)
        assert todo_group is not None
        assert len(todo_group.items) >= 1

    @pytest.mark.asyncio
    async def test_search_finds_task(self):
        """Search matches Task title."""
        ws = await create_workspace("SearchWS")
        agent = await _create_agent()
        await _create_task(ws["id"], agent["id"], title="Refactor database layer")

        result = await search_manager.search("database")
        task_group = next((g for g in result.groups if g.entity_type == "task"), None)
        assert task_group is not None
        assert task_group.total >= 1

    @pytest.mark.asyncio
    async def test_search_finds_plan_item(self):
        """Search matches PlanItem title."""
        ws = await create_workspace("SearchWS")
        await _create_plan_item(ws["id"], title="Review quarterly OKRs")

        result = await search_manager.search("quarterly")
        pi_group = next((g for g in result.groups if g.entity_type == "plan_item"), None)
        assert pi_group is not None
        assert pi_group.total >= 1

    @pytest.mark.asyncio
    async def test_search_finds_communication(self):
        """Search matches Communication title."""
        ws = await create_workspace("SearchWS")
        await _create_communication(ws["id"], title="Follow up with design team")

        result = await search_manager.search("design team")
        comm_group = next((g for g in result.groups if g.entity_type == "communication"), None)
        assert comm_group is not None
        assert comm_group.total >= 1

    @pytest.mark.asyncio
    async def test_search_finds_artifact(self):
        """Search matches Artifact title."""
        ws = await create_workspace("SearchWS")
        await _create_artifact(ws["id"], title="Architecture Decision Record")

        result = await search_manager.search("Architecture")
        art_group = next((g for g in result.groups if g.entity_type == "artifact"), None)
        assert art_group is not None
        assert art_group.total >= 1

    @pytest.mark.asyncio
    async def test_search_finds_reflection(self):
        """Search matches Reflection title."""
        ws = await create_workspace("SearchWS")
        await _create_reflection(ws["id"], title="Weekly sprint retrospective")

        result = await search_manager.search("retrospective")
        ref_group = next((g for g in result.groups if g.entity_type == "reflection"), None)
        assert ref_group is not None
        assert ref_group.total >= 1

    @pytest.mark.asyncio
    async def test_search_no_match(self):
        """Search with no matching data returns empty groups."""
        ws = await create_workspace("SearchWS")
        await _create_todo(ws["id"], title="Something unrelated")

        result = await search_manager.search("zzzznonexistentzzzz")
        assert result.total == 0
        assert result.groups == []


# ---------------------------------------------------------------------------
# Tests: Thread search via ThreadSummary (Requirement 31.1)
# ---------------------------------------------------------------------------

class TestThreadSearch:
    """Thread search must use ThreadSummary, NOT raw ChatMessages."""

    @pytest.mark.asyncio
    async def test_search_finds_thread_by_summary_text(self):
        """Search matches ThreadSummary.summary_text.

        Validates: Requirement 31.1, 31.5
        """
        ws = await create_workspace("ThreadWS")
        agent = await _create_agent()
        await _create_thread_with_summary(
            ws["id"], agent["id"],
            thread_title="Debug session",
            summary_text="Discussed memory leak in the worker pool",
        )

        result = await search_manager.search("memory leak")
        thread_group = next((g for g in result.groups if g.entity_type == "thread"), None)
        assert thread_group is not None
        assert thread_group.total >= 1
        assert "memory leak" in thread_group.items[0].description.lower()

    @pytest.mark.asyncio
    async def test_search_finds_thread_by_key_decisions(self):
        """Search matches ThreadSummary.key_decisions.

        Validates: Requirement 31.5
        """
        ws = await create_workspace("ThreadWS")
        agent = await _create_agent()
        await _create_thread_with_summary(
            ws["id"], agent["id"],
            thread_title="Planning session",
            summary_text="General planning discussion",
            key_decisions='["Adopt serverless architecture", "Use DynamoDB"]',
        )

        result = await search_manager.search("serverless")
        thread_group = next((g for g in result.groups if g.entity_type == "thread"), None)
        assert thread_group is not None
        assert thread_group.total >= 1

    @pytest.mark.asyncio
    async def test_thread_search_does_not_query_raw_messages(self):
        """Raw ChatMessages content is NOT searched.

        Validates: Requirement 31.1, 31.2
        """
        ws = await create_workspace("ThreadWS")
        agent = await _create_agent()
        thread, _ = await _create_thread_with_summary(
            ws["id"], agent["id"],
            thread_title="Chat thread",
            summary_text="Summary about deployment pipeline",
        )

        # Add a raw message with unique content NOT in the summary
        now = now_iso()
        await db.chat_messages.put({
            "id": str(uuid4()),
            "thread_id": thread["id"],
            "role": "user",
            "content": "xyzUniqueMessageContentxyz should not be searchable",
            "created_at": now,
        })

        result = await search_manager.search("xyzUniqueMessageContentxyz")
        thread_group = next((g for g in result.groups if g.entity_type == "thread"), None)
        # Should NOT find the raw message content
        assert thread_group is None or thread_group.total == 0

    @pytest.mark.asyncio
    async def test_search_threads_dedicated_endpoint(self):
        """search_threads() returns only thread results.

        Validates: Requirement 31.7
        """
        ws = await create_workspace("ThreadWS")
        agent = await _create_agent()
        await _create_thread_with_summary(
            ws["id"], agent["id"],
            summary_text="Reviewed API rate limiting strategy",
        )
        # Also create a ToDo that matches
        await _create_todo(ws["id"], title="Rate limiting todo")

        result = await search_manager.search_threads("rate limiting")
        # Should only have thread results
        assert all(g.entity_type == "thread" for g in result.groups)
        assert result.total >= 1


# ---------------------------------------------------------------------------
# Tests: Scope filtering (Requirement 38.3)
# ---------------------------------------------------------------------------

class TestSearchScope:
    """Search respects workspace scope filtering."""

    @pytest.mark.asyncio
    async def test_scope_specific_workspace(self):
        """scope=workspace_id returns only items from that workspace.

        Validates: Requirement 38.3
        """
        ws1 = await create_workspace("WS_Alpha")
        ws2 = await create_workspace("WS_Beta")
        await _create_todo(ws1["id"], title="Alpha unique item")
        await _create_todo(ws2["id"], title="Beta unique item")

        result = await search_manager.search("unique item", scope=ws1["id"])
        for group in result.groups:
            for item in group.items:
                assert item.workspace_id == ws1["id"]

    @pytest.mark.asyncio
    async def test_scope_all_excludes_archived(self):
        """scope='all' excludes archived workspaces.

        Validates: Requirement 38.3
        """
        ws_active = await create_workspace("ActiveWS")
        ws_archived = await create_workspace("ArchivedWS", is_archived=True)
        await _create_todo(ws_active["id"], title="Searchable active item")
        await _create_todo(ws_archived["id"], title="Searchable archived item")

        result = await search_manager.search("Searchable", scope="all")
        all_ws_ids = set()
        for group in result.groups:
            for item in group.items:
                all_ws_ids.add(item.workspace_id)

        assert ws_active["id"] in all_ws_ids
        assert ws_archived["id"] not in all_ws_ids

    @pytest.mark.asyncio
    async def test_scope_specific_archived_workspace_still_searchable(self):
        """Direct scope to archived workspace still returns results.

        Validates: Requirement 38.12
        """
        ws_archived = await create_workspace("ArchivedWS", is_archived=True)
        await _create_todo(ws_archived["id"], title="Archived but findable")

        result = await search_manager.search("findable", scope=ws_archived["id"])
        assert result.total >= 1
        todo_group = next((g for g in result.groups if g.entity_type == "todo"), None)
        assert todo_group is not None
        assert todo_group.items[0].is_archived is True


# ---------------------------------------------------------------------------
# Tests: Entity type filtering
# ---------------------------------------------------------------------------

class TestEntityTypeFilter:
    """Search supports filtering by entity type."""

    @pytest.mark.asyncio
    async def test_filter_single_entity_type(self):
        """entity_types filter restricts search to specified types."""
        ws = await create_workspace("FilterWS")
        await _create_todo(ws["id"], title="Matching keyword here")
        agent = await _create_agent()
        await _create_task(ws["id"], agent["id"], title="Matching keyword here")

        result = await search_manager.search("keyword", entity_types=["todo"])
        entity_types_found = {g.entity_type for g in result.groups}
        assert "todo" in entity_types_found
        assert "task" not in entity_types_found

    @pytest.mark.asyncio
    async def test_filter_multiple_entity_types(self):
        """entity_types filter with multiple types."""
        ws = await create_workspace("FilterWS")
        await _create_todo(ws["id"], title="Multi filter test")
        await _create_plan_item(ws["id"], title="Multi filter test")
        await _create_artifact(ws["id"], title="Multi filter test")

        result = await search_manager.search("Multi filter", entity_types=["todo", "plan_item"])
        entity_types_found = {g.entity_type for g in result.groups}
        assert "todo" in entity_types_found
        assert "plan_item" in entity_types_found
        assert "artifact" not in entity_types_found


# ---------------------------------------------------------------------------
# Tests: Result limits (Requirement 38.11)
# ---------------------------------------------------------------------------

class TestSearchLimits:
    """Search limits results to 50 per entity type."""

    @pytest.mark.asyncio
    async def test_max_50_per_entity_type(self):
        """Results are capped at 50 per entity type with has_more flag.

        Validates: Requirement 38.11
        """
        ws = await create_workspace("LimitWS")
        # Create 55 todos with matching title
        for i in range(55):
            await _create_todo(ws["id"], title=f"Bulk item number {i}")

        result = await search_manager.search("Bulk item")
        todo_group = next((g for g in result.groups if g.entity_type == "todo"), None)
        assert todo_group is not None
        assert len(todo_group.items) <= 50
        assert todo_group.total == 55
        assert todo_group.has_more is True


# ---------------------------------------------------------------------------
# Tests: Result structure
# ---------------------------------------------------------------------------

class TestSearchResultStructure:
    """Search results have correct structure and metadata."""

    @pytest.mark.asyncio
    async def test_result_includes_workspace_name(self):
        """Results include workspace_name for display.

        Validates: Requirement 38.6
        """
        ws = await create_workspace("NamedWorkspace")
        await _create_todo(ws["id"], title="Item with workspace name")

        result = await search_manager.search("workspace name")
        todo_group = next((g for g in result.groups if g.entity_type == "todo"), None)
        assert todo_group is not None
        assert todo_group.items[0].workspace_name == "NamedWorkspace"

    @pytest.mark.asyncio
    async def test_result_includes_entity_type(self):
        """Each result item has entity_type set correctly."""
        ws = await create_workspace("TypeWS")
        await _create_todo(ws["id"], title="Entity type check")

        result = await search_manager.search("Entity type check")
        for group in result.groups:
            for item in group.items:
                assert item.entity_type == group.entity_type

    @pytest.mark.asyncio
    async def test_search_results_grouped_by_type(self):
        """Results are grouped by entity type.

        Validates: Requirement 38.4
        """
        ws = await create_workspace("GroupWS")
        agent = await _create_agent()
        await _create_todo(ws["id"], title="Grouped search term")
        await _create_task(ws["id"], agent["id"], title="Grouped search term")
        await _create_plan_item(ws["id"], title="Grouped search term")

        result = await search_manager.search("Grouped search term")
        entity_types = [g.entity_type for g in result.groups]
        # Should have multiple groups
        assert len(entity_types) >= 3
        # Each group should be unique
        assert len(entity_types) == len(set(entity_types))
