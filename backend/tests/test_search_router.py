"""Unit tests for Search API router endpoints.

Tests global search across entity types, thread-specific search,
scope filtering, entity type filtering, result limits, and response format.

Requirements: 31.7, 38.10
"""
import pytest
import tempfile
from uuid import uuid4
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from database import db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_workspace(client: TestClient, name: str = "SearchTestWS") -> str:
    """Create a workspace via API and return its ID."""
    tmp = tempfile.mkdtemp()
    resp = client.post("/api/swarm-workspaces", json={
        "name": name,
        "file_path": tmp,
        "context": f"Workspace for {name}",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


async def _seed_todo(workspace_id: str, title: str, **kw) -> str:
    now = _now_iso()
    todo_id = str(uuid4())
    await db.todos.put({
        "id": todo_id,
        "workspace_id": workspace_id,
        "title": title,
        "description": kw.get("description", f"Desc for {title}"),
        "source": kw.get("source"),
        "source_type": kw.get("source_type", "manual"),
        "status": kw.get("status", "pending"),
        "priority": kw.get("priority", "none"),
        "due_date": None,
        "task_id": None,
        "created_at": now,
        "updated_at": now,
    })
    return todo_id


async def _seed_task(workspace_id: str, title: str, **kw) -> str:
    now = _now_iso()
    task_id = str(uuid4())
    await db.tasks.put({
        "id": task_id,
        "workspace_id": workspace_id,
        "agent_id": "default",
        "session_id": None,
        "title": title,
        "description": kw.get("description", f"Desc for {title}"),
        "status": kw.get("status", "draft"),
        "priority": kw.get("priority", "none"),
        "source_todo_id": None,
        "blocked_reason": None,
        "model": None,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "updated_at": now,
        "error": None,
        "work_dir": None,
    })
    return task_id


async def _seed_plan_item(workspace_id: str, title: str, **kw) -> str:
    now = _now_iso()
    item_id = str(uuid4())
    await db.plan_items.put({
        "id": item_id,
        "workspace_id": workspace_id,
        "title": title,
        "description": kw.get("description", f"Desc for {title}"),
        "source_todo_id": None,
        "source_task_id": None,
        "status": kw.get("status", "active"),
        "priority": kw.get("priority", "none"),
        "scheduled_date": None,
        "focus_type": kw.get("focus_type", "upcoming"),
        "sort_order": 0,
        "created_at": now,
        "updated_at": now,
    })
    return item_id


async def _seed_communication(workspace_id: str, title: str, **kw) -> str:
    now = _now_iso()
    comm_id = str(uuid4())
    await db.communications.put({
        "id": comm_id,
        "workspace_id": workspace_id,
        "title": title,
        "description": kw.get("description", f"Desc for {title}"),
        "recipient": kw.get("recipient", "someone@example.com"),
        "channel_type": kw.get("channel_type", "email"),
        "status": kw.get("status", "pending_reply"),
        "priority": kw.get("priority", "none"),
        "due_date": None,
        "ai_draft_content": None,
        "source_task_id": None,
        "source_todo_id": None,
        "sent_at": None,
        "created_at": now,
        "updated_at": now,
    })
    return comm_id


async def _seed_artifact(workspace_id: str, title: str, **kw) -> str:
    now = _now_iso()
    art_id = str(uuid4())
    await db.artifacts.put({
        "id": art_id,
        "workspace_id": workspace_id,
        "task_id": None,
        "artifact_type": kw.get("artifact_type", "doc"),
        "title": title,
        "file_path": kw.get("file_path", f"Artifacts/Docs/{title}.md"),
        "version": 1,
        "created_by": "user",
        "created_at": now,
        "updated_at": now,
    })
    return art_id


async def _seed_reflection(workspace_id: str, title: str, **kw) -> str:
    now = _now_iso()
    ref_id = str(uuid4())
    await db.reflections.put({
        "id": ref_id,
        "workspace_id": workspace_id,
        "reflection_type": kw.get("reflection_type", "daily_recap"),
        "title": title,
        "file_path": kw.get("file_path", f"Artifacts/Reports/{title}.md"),
        "period_start": kw.get("period_start", "2025-01-01T00:00:00Z"),
        "period_end": kw.get("period_end", "2025-01-01T23:59:59Z"),
        "generated_by": kw.get("generated_by", "user"),
        "created_at": now,
        "updated_at": now,
    })
    return ref_id


async def _seed_thread_with_summary(
    workspace_id: str, thread_title: str, summary_text: str, **kw
) -> tuple[str, str]:
    """Create a chat_thread + thread_summary and return (thread_id, summary_id)."""
    now = _now_iso()
    thread_id = str(uuid4())
    summary_id = str(uuid4())

    import aiosqlite
    async with aiosqlite.connect(str(db.db_path)) as conn:
        await conn.execute(
            "INSERT INTO chat_threads (id, workspace_id, agent_id, task_id, todo_id, mode, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (thread_id, workspace_id, "default", None, None, "explore", thread_title, now, now),
        )
        await conn.execute(
            "INSERT INTO thread_summaries (id, thread_id, summary_type, summary_text, key_decisions, open_questions, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (summary_id, thread_id, "rolling", summary_text,
             kw.get("key_decisions", ""), kw.get("open_questions", ""), now),
        )
        await conn.commit()

    return thread_id, summary_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace_id(client: TestClient) -> str:
    return _create_workspace(client, "SearchWS1")


@pytest.fixture
def second_workspace_id(client: TestClient) -> str:
    return _create_workspace(client, "SearchWS2")


# ---------------------------------------------------------------------------
# Tests: GET /api/search
# ---------------------------------------------------------------------------

class TestGlobalSearch:
    """Tests for the GET /api/search endpoint."""

    def test_search_requires_query(self, client: TestClient):
        """Query param is required."""
        resp = client.get("/api/search")
        # App error handler converts 422 to 400
        assert resp.status_code in (400, 422)

    def test_search_empty_results(self, client: TestClient, workspace_id: str):
        """Search with no matching data returns empty groups."""
        resp = client.get("/api/search", params={"query": "nonexistent_xyz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query"] == "nonexistent_xyz"
        assert data["scope"] == "all"
        assert data["groups"] == []
        assert data["total"] == 0

    @pytest.mark.anyio
    async def test_search_finds_todos(self, client: TestClient, workspace_id: str):
        """Search matches ToDo titles."""
        await _seed_todo(workspace_id, "Deploy search feature")
        resp = client.get("/api/search", params={"query": "Deploy search"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        todo_group = next((g for g in data["groups"] if g["entity_type"] == "todo"), None)
        assert todo_group is not None
        assert todo_group["total"] >= 1
        assert any("Deploy search" in item["title"] for item in todo_group["items"])

    @pytest.mark.anyio
    async def test_search_finds_tasks(self, client: TestClient, workspace_id: str):
        """Search matches Task titles."""
        await _seed_task(workspace_id, "Implement search indexing")
        resp = client.get("/api/search", params={"query": "search indexing"})
        assert resp.status_code == 200
        data = resp.json()
        task_group = next((g for g in data["groups"] if g["entity_type"] == "task"), None)
        assert task_group is not None
        assert task_group["total"] >= 1

    @pytest.mark.anyio
    async def test_search_finds_across_types(self, client: TestClient, workspace_id: str):
        """Search returns results from multiple entity types."""
        await _seed_todo(workspace_id, "Review quarterly report")
        await _seed_task(workspace_id, "Generate quarterly report")
        await _seed_artifact(workspace_id, "Quarterly report draft")

        resp = client.get("/api/search", params={"query": "quarterly report"})
        assert resp.status_code == 200
        data = resp.json()
        entity_types_found = {g["entity_type"] for g in data["groups"]}
        assert "todo" in entity_types_found
        assert "task" in entity_types_found
        assert "artifact" in entity_types_found

    @pytest.mark.anyio
    async def test_search_matches_description(self, client: TestClient, workspace_id: str):
        """Search matches on description field too."""
        await _seed_todo(workspace_id, "Generic title", description="unique_keyword_alpha in description")
        resp = client.get("/api/search", params={"query": "unique_keyword_alpha"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1


class TestSearchScopeFiltering:
    """Tests for scope parameter filtering."""

    @pytest.mark.anyio
    async def test_scope_specific_workspace(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        """scope=workspace_id returns only items from that workspace."""
        await _seed_todo(workspace_id, "WS1 scoped item")
        await _seed_todo(second_workspace_id, "WS2 scoped item")

        resp = client.get("/api/search", params={
            "query": "scoped item",
            "scope": workspace_id,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == workspace_id
        # All returned items should belong to workspace_id
        for group in data["groups"]:
            for item in group["items"]:
                assert item["workspace_id"] == workspace_id

    @pytest.mark.anyio
    async def test_scope_all_returns_from_multiple_workspaces(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        """scope=all returns items from all non-archived workspaces."""
        await _seed_todo(workspace_id, "Cross workspace search alpha")
        await _seed_todo(second_workspace_id, "Cross workspace search alpha")

        resp = client.get("/api/search", params={
            "query": "Cross workspace search alpha",
            "scope": "all",
        })
        assert resp.status_code == 200
        data = resp.json()
        todo_group = next((g for g in data["groups"] if g["entity_type"] == "todo"), None)
        assert todo_group is not None
        assert todo_group["total"] >= 2

    @pytest.mark.anyio
    async def test_scope_defaults_to_all(self, client: TestClient, workspace_id: str):
        """When scope is omitted, defaults to 'all'."""
        await _seed_todo(workspace_id, "Default scope test item")
        resp = client.get("/api/search", params={"query": "Default scope test"})
        assert resp.status_code == 200
        assert resp.json()["scope"] == "all"


class TestEntityTypeFiltering:
    """Tests for entity_types parameter filtering."""

    @pytest.mark.anyio
    async def test_filter_single_entity_type(self, client: TestClient, workspace_id: str):
        """entity_types=todo returns only todo results."""
        await _seed_todo(workspace_id, "Filterable entity item")
        await _seed_task(workspace_id, "Filterable entity item")

        resp = client.get("/api/search", params={
            "query": "Filterable entity",
            "entity_types": "todo",
        })
        assert resp.status_code == 200
        data = resp.json()
        entity_types_found = {g["entity_type"] for g in data["groups"]}
        assert "todo" in entity_types_found
        assert "task" not in entity_types_found

    @pytest.mark.anyio
    async def test_filter_multiple_entity_types(self, client: TestClient, workspace_id: str):
        """entity_types=todo,task returns only those types."""
        await _seed_todo(workspace_id, "Multi filter test")
        await _seed_task(workspace_id, "Multi filter test")
        await _seed_communication(workspace_id, "Multi filter test")

        resp = client.get("/api/search", params={
            "query": "Multi filter test",
            "entity_types": "todo,task",
        })
        assert resp.status_code == 200
        data = resp.json()
        entity_types_found = {g["entity_type"] for g in data["groups"]}
        assert entity_types_found <= {"todo", "task"}

    @pytest.mark.anyio
    async def test_no_entity_types_searches_all(self, client: TestClient, workspace_id: str):
        """Omitting entity_types searches all entity types."""
        await _seed_todo(workspace_id, "Omit filter test")
        await _seed_plan_item(workspace_id, "Omit filter test")

        resp = client.get("/api/search", params={"query": "Omit filter test"})
        assert resp.status_code == 200
        data = resp.json()
        entity_types_found = {g["entity_type"] for g in data["groups"]}
        assert len(entity_types_found) >= 2


class TestSearchResultLimits:
    """Tests for the 50-result-per-entity-type limit."""

    @pytest.mark.anyio
    async def test_has_more_flag(self, client: TestClient, workspace_id: str):
        """When more than 50 items exist, has_more is True."""
        # Seed 52 todos with matching title
        for i in range(52):
            await _seed_todo(workspace_id, f"Limit test item {i}")

        resp = client.get("/api/search", params={
            "query": "Limit test item",
            "entity_types": "todo",
        })
        assert resp.status_code == 200
        data = resp.json()
        todo_group = next((g for g in data["groups"] if g["entity_type"] == "todo"), None)
        assert todo_group is not None
        assert len(todo_group["items"]) <= 50
        assert todo_group["has_more"] is True
        assert todo_group["total"] == 52


# ---------------------------------------------------------------------------
# Tests: GET /api/search/threads
# ---------------------------------------------------------------------------

class TestThreadSearch:
    """Tests for the GET /api/search/threads endpoint."""

    def test_thread_search_requires_query(self, client: TestClient):
        resp = client.get("/api/search/threads")
        # App error handler converts 422 to 400
        assert resp.status_code in (400, 422)

    def test_thread_search_empty(self, client: TestClient, workspace_id: str):
        resp = client.get("/api/search/threads", params={"query": "nonexistent_thread_xyz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["groups"] == []
        assert data["total"] == 0

    @pytest.mark.anyio
    async def test_thread_search_finds_by_summary_text(self, client: TestClient, workspace_id: str):
        """Thread search matches on ThreadSummary.summary_text (Req 31.1, 31.5)."""
        await _seed_thread_with_summary(
            workspace_id,
            thread_title="Architecture discussion",
            summary_text="Discussed microservice architecture and API gateway patterns",
        )
        resp = client.get("/api/search/threads", params={"query": "microservice architecture"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        thread_group = next((g for g in data["groups"] if g["entity_type"] == "thread"), None)
        assert thread_group is not None
        assert thread_group["total"] >= 1

    @pytest.mark.anyio
    async def test_thread_search_finds_by_key_decisions(self, client: TestClient, workspace_id: str):
        """Thread search matches on ThreadSummary.key_decisions."""
        await _seed_thread_with_summary(
            workspace_id,
            thread_title="Decision thread",
            summary_text="General discussion",
            key_decisions="Decided to use PostgreSQL for production",
        )
        resp = client.get("/api/search/threads", params={"query": "PostgreSQL"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    @pytest.mark.anyio
    async def test_thread_search_respects_scope(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        """Thread search respects workspace scope."""
        await _seed_thread_with_summary(
            workspace_id,
            thread_title="WS1 thread",
            summary_text="Thread scoped search test content",
        )
        await _seed_thread_with_summary(
            second_workspace_id,
            thread_title="WS2 thread",
            summary_text="Thread scoped search test content",
        )

        resp = client.get("/api/search/threads", params={
            "query": "Thread scoped search test",
            "scope": workspace_id,
        })
        assert resp.status_code == 200
        data = resp.json()
        thread_group = next((g for g in data["groups"] if g["entity_type"] == "thread"), None)
        assert thread_group is not None
        for item in thread_group["items"]:
            assert item["workspace_id"] == workspace_id

    @pytest.mark.anyio
    async def test_thread_search_returns_only_threads(self, client: TestClient, workspace_id: str):
        """Thread search endpoint returns only thread entity type."""
        await _seed_todo(workspace_id, "Should not appear in thread search")
        await _seed_thread_with_summary(
            workspace_id,
            thread_title="Real thread",
            summary_text="Should not appear in thread search but this should",
        )
        resp = client.get("/api/search/threads", params={"query": "Should not appear"})
        assert resp.status_code == 200
        data = resp.json()
        for group in data["groups"]:
            assert group["entity_type"] == "thread"


# ---------------------------------------------------------------------------
# Tests: Response format
# ---------------------------------------------------------------------------

class TestResponseFormat:
    """Tests for response structure and snake_case convention."""

    @pytest.mark.anyio
    async def test_response_structure(self, client: TestClient, workspace_id: str):
        """Response has required top-level fields."""
        await _seed_todo(workspace_id, "Structure test item")
        resp = client.get("/api/search", params={"query": "Structure test"})
        assert resp.status_code == 200
        data = resp.json()
        assert "query" in data
        assert "scope" in data
        assert "groups" in data
        assert "total" in data

    @pytest.mark.anyio
    async def test_group_structure(self, client: TestClient, workspace_id: str):
        """Each group has entity_type, items, total, has_more."""
        await _seed_todo(workspace_id, "Group structure test")
        resp = client.get("/api/search", params={"query": "Group structure"})
        assert resp.status_code == 200
        data = resp.json()
        for group in data["groups"]:
            assert "entity_type" in group
            assert "items" in group
            assert "total" in group
            assert "has_more" in group

    @pytest.mark.anyio
    async def test_item_structure(self, client: TestClient, workspace_id: str):
        """Each item has required fields (snake_case)."""
        await _seed_todo(workspace_id, "Item structure test")
        resp = client.get("/api/search", params={"query": "Item structure"})
        assert resp.status_code == 200
        data = resp.json()
        for group in data["groups"]:
            for item in group["items"]:
                assert "id" in item
                assert "entity_type" in item
                assert "title" in item
                assert "workspace_id" in item
                assert "updated_at" in item
                assert "is_archived" in item

    @pytest.mark.anyio
    async def test_uses_snake_case(self, client: TestClient, workspace_id: str):
        """Response uses snake_case field names (backend convention)."""
        await _seed_todo(workspace_id, "Snake case test")
        resp = client.get("/api/search", params={"query": "Snake case"})
        assert resp.status_code == 200
        data = resp.json()
        # Top-level keys should be snake_case
        assert "entity_type" not in data  # not at top level
        for group in data["groups"]:
            assert "entity_type" in group
            assert "has_more" in group
            for item in group["items"]:
                assert "entity_type" in item
                assert "workspace_id" in item
                assert "workspace_name" in item
                assert "is_archived" in item
                assert "updated_at" in item
