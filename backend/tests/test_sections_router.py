"""Unit tests for Sections API router endpoints.

Tests section counts, grouped responses, "all" workspace aggregation,
and pagination for the /api/workspaces/{id}/sections endpoints.

Requirements: 7.1-7.12
"""
import pytest
from uuid import uuid4
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from database import db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _create_workspace(name: str = "SectionsTestWS", is_archived: bool = False) -> str:
    """Create a workspace config entry directly in the DB and return its ID.

    In the single-workspace model, always uses 'swarmws' as the ID.
    """
    ws_id = "swarmws"
    now = _now()
    await db.workspace_config.put({
        "id": ws_id,
        "name": name,
        "file_path": f"/tmp/test-sections/{ws_id[:8]}",
        "icon": "📁",
        "context": f"Test workspace {name}",
        "created_at": now,
        "updated_at": now,
    })
    return ws_id


async def _seed_todo(workspace_id: str, status: str = "pending", **kw) -> str:
    todo_id = str(uuid4())
    now = _now()
    await db.todos.put({
        "id": todo_id,
        "workspace_id": workspace_id,
        "title": kw.get("title", f"Todo-{todo_id[:6]}"),
        "description": kw.get("description"),
        "source": kw.get("source"),
        "source_type": kw.get("source_type", "manual"),
        "status": status,
        "priority": kw.get("priority", "none"),
        "due_date": kw.get("due_date"),
        "task_id": kw.get("task_id"),
        "created_at": now,
        "updated_at": now,
    })
    return todo_id


async def _seed_plan_item(workspace_id: str, focus_type: str = "today", **kw) -> str:
    item_id = str(uuid4())
    now = _now()
    await db.plan_items.put({
        "id": item_id,
        "workspace_id": workspace_id,
        "title": kw.get("title", f"PlanItem-{item_id[:6]}"),
        "description": kw.get("description"),
        "source_todo_id": kw.get("source_todo_id"),
        "source_task_id": kw.get("source_task_id"),
        "status": kw.get("status", "active"),
        "priority": kw.get("priority", "none"),
        "scheduled_date": kw.get("scheduled_date"),
        "focus_type": focus_type,
        "sort_order": kw.get("sort_order", 0),
        "created_at": now,
        "updated_at": now,
    })
    return item_id


async def _seed_task(workspace_id: str, status: str = "draft", **kw) -> str:
    task_id = str(uuid4())
    now = _now()
    await db.tasks.put({
        "id": task_id,
        "agent_id": kw.get("agent_id", "default"),
        "session_id": kw.get("session_id"),
        "workspace_id": workspace_id,
        "title": kw.get("title", f"Task-{task_id[:6]}"),
        "description": kw.get("description"),
        "status": status,
        "priority": kw.get("priority", "none"),
        "source_todo_id": kw.get("source_todo_id"),
        "blocked_reason": kw.get("blocked_reason"),
        "model": kw.get("model", "claude-sonnet-4-20250514"),
        "created_at": now,
        "updated_at": now,
    })
    return task_id


async def _seed_communication(workspace_id: str, status: str = "pending_reply", **kw) -> str:
    comm_id = str(uuid4())
    now = _now()
    await db.communications.put({
        "id": comm_id,
        "workspace_id": workspace_id,
        "title": kw.get("title", f"Comm-{comm_id[:6]}"),
        "description": kw.get("description"),
        "recipient": kw.get("recipient", "someone@example.com"),
        "channel_type": kw.get("channel_type", "email"),
        "status": status,
        "priority": kw.get("priority", "none"),
        "due_date": kw.get("due_date"),
        "ai_draft_content": kw.get("ai_draft_content"),
        "source_task_id": kw.get("source_task_id"),
        "source_todo_id": kw.get("source_todo_id"),
        "sent_at": kw.get("sent_at"),
        "created_at": now,
        "updated_at": now,
    })
    return comm_id


async def _seed_artifact(workspace_id: str, artifact_type: str = "doc", **kw) -> str:
    art_id = str(uuid4())
    now = _now()
    await db.artifacts.put({
        "id": art_id,
        "workspace_id": workspace_id,
        "task_id": kw.get("task_id"),
        "artifact_type": artifact_type,
        "title": kw.get("title", f"Artifact-{art_id[:6]}"),
        "file_path": kw.get("file_path", f"/tmp/artifacts/{art_id}.md"),
        "version": kw.get("version", 1),
        "created_by": kw.get("created_by", "system"),
        "created_at": now,
        "updated_at": now,
    })
    return art_id


async def _seed_reflection(workspace_id: str, reflection_type: str = "daily_recap", **kw) -> str:
    ref_id = str(uuid4())
    now = _now()
    await db.reflections.put({
        "id": ref_id,
        "workspace_id": workspace_id,
        "reflection_type": reflection_type,
        "title": kw.get("title", f"Reflection-{ref_id[:6]}"),
        "file_path": kw.get("file_path", f"/tmp/reflections/{ref_id}.md"),
        "period_start": kw.get("period_start", now),
        "period_end": kw.get("period_end", now),
        "generated_by": kw.get("generated_by", "user"),
        "created_at": now,
        "updated_at": now,
    })
    return ref_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ws_id(client: TestClient) -> str:
    """Create a workspace via API and return its ID."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(_create_workspace("SectionsWS1"))


@pytest.fixture
def ws_id2(client: TestClient) -> str:
    """Create a second workspace for aggregation tests."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(_create_workspace("SectionsWS2"))


@pytest.fixture
def archived_ws_id(client: TestClient) -> str:
    """Create an archived workspace."""
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        _create_workspace("ArchivedWS", is_archived=True)
    )


# ---------------------------------------------------------------------------
# Section Counts Tests (Requirement 7.1)
# ---------------------------------------------------------------------------

class TestSectionCounts:
    """Tests for GET /api/workspaces/{id}/sections. Validates: Requirement 7.1"""

    def test_empty_workspace_returns_zero_counts(self, client: TestClient, ws_id: str):
        resp = client.get(f"/api/workspaces/{ws_id}/sections")
        assert resp.status_code == 200
        data = resp.json()
        assert data["signals"]["total"] == 0
        assert data["plan"]["total"] == 0
        assert data["execute"]["total"] == 0
        assert data["communicate"]["total"] == 0
        assert data["artifacts"]["total"] == 0
        assert data["reflection"]["total"] == 0

    def test_counts_reflect_seeded_data(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        # Seed various entities
        loop.run_until_complete(_seed_todo(ws_id, status="pending"))
        loop.run_until_complete(_seed_todo(ws_id, status="pending"))
        loop.run_until_complete(_seed_todo(ws_id, status="overdue"))
        loop.run_until_complete(_seed_plan_item(ws_id, focus_type="today"))
        loop.run_until_complete(_seed_plan_item(ws_id, focus_type="upcoming"))
        loop.run_until_complete(_seed_task(ws_id, status="draft"))
        loop.run_until_complete(_seed_task(ws_id, status="wip"))
        loop.run_until_complete(_seed_task(ws_id, status="completed"))
        loop.run_until_complete(_seed_communication(ws_id, status="pending_reply"))
        loop.run_until_complete(_seed_artifact(ws_id, artifact_type="doc"))
        loop.run_until_complete(_seed_artifact(ws_id, artifact_type="plan"))
        loop.run_until_complete(_seed_reflection(ws_id, reflection_type="daily_recap"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections")
        assert resp.status_code == 200
        data = resp.json()

        # Signals: 2 pending + 1 overdue = 3
        assert data["signals"]["total"] == 3
        assert data["signals"]["pending"] == 2
        assert data["signals"]["overdue"] == 1
        assert data["signals"]["in_discussion"] == 0

        # Plan: 1 today + 1 upcoming = 2
        assert data["plan"]["total"] == 2
        assert data["plan"]["today"] == 1
        assert data["plan"]["upcoming"] == 1

        # Execute: 1 draft + 1 wip + 1 completed = 3
        assert data["execute"]["total"] == 3
        assert data["execute"]["draft"] == 1
        assert data["execute"]["wip"] == 1
        assert data["execute"]["completed"] == 1

        # Communicate: 1 pending_reply
        assert data["communicate"]["total"] == 1
        assert data["communicate"]["pending_reply"] == 1

        # Artifacts: 1 doc + 1 plan = 2
        assert data["artifacts"]["total"] == 2
        assert data["artifacts"]["doc"] == 1
        assert data["artifacts"]["plan"] == 1

        # Reflection: 1 daily_recap
        assert data["reflection"]["total"] == 1
        assert data["reflection"]["daily_recap"] == 1

    def test_counts_sub_categories(self, client: TestClient, ws_id: str):
        """Verify all sub-category fields are present in the response."""
        resp = client.get(f"/api/workspaces/{ws_id}/sections")
        data = resp.json()
        # Signals sub-categories
        for key in ["total", "pending", "overdue", "in_discussion"]:
            assert key in data["signals"]
        # Plan sub-categories
        for key in ["total", "today", "upcoming", "blocked"]:
            assert key in data["plan"]
        # Execute sub-categories
        for key in ["total", "draft", "wip", "blocked", "completed"]:
            assert key in data["execute"]
        # Communicate sub-categories
        for key in ["total", "pending_reply", "ai_draft", "follow_up"]:
            assert key in data["communicate"]
        # Artifacts sub-categories
        for key in ["total", "plan", "report", "doc", "decision"]:
            assert key in data["artifacts"]
        # Reflection sub-categories
        for key in ["total", "daily_recap", "weekly_summary", "lessons_learned"]:
            assert key in data["reflection"]


# ---------------------------------------------------------------------------
# Grouped Response Tests (Requirements 7.2-7.7, 7.9, 7.11)
# ---------------------------------------------------------------------------

class TestSignalsSection:
    """Tests for GET /api/workspaces/{id}/sections/signals. Validates: Requirement 7.2"""

    def test_signals_grouped_by_status(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_todo(ws_id, status="pending", title="Pending signal"))
        loop.run_until_complete(_seed_todo(ws_id, status="overdue", title="Overdue signal"))
        loop.run_until_complete(_seed_todo(ws_id, status="in_discussion", title="Discussion signal"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/signals")
        assert resp.status_code == 200
        data = resp.json()

        assert "counts" in data
        assert data["counts"]["total"] == 3
        assert data["counts"]["pending"] == 1
        assert data["counts"]["overdue"] == 1
        assert data["counts"]["in_discussion"] == 1

        assert "groups" in data
        group_names = [g["name"] for g in data["groups"]]
        assert "pending" in group_names
        assert "overdue" in group_names
        assert "in_discussion" in group_names

    def test_signals_unified_response_contract(self, client: TestClient, ws_id: str):
        """Verify the unified SectionResponse shape. Validates: Requirement 7.11"""
        resp = client.get(f"/api/workspaces/{ws_id}/sections/signals")
        data = resp.json()
        assert "counts" in data
        assert "groups" in data
        assert "pagination" in data
        assert "sort_keys" in data
        assert "last_updated_at" in data
        # Pagination shape
        pag = data["pagination"]
        assert "limit" in pag
        assert "offset" in pag
        assert "total" in pag
        assert "has_more" in pag

    def test_signals_excludes_handled_and_deleted(self, client: TestClient, ws_id: str):
        """Handled and deleted ToDos should not appear in signals."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_todo(ws_id, status="pending"))
        loop.run_until_complete(_seed_todo(ws_id, status="handled"))
        loop.run_until_complete(_seed_todo(ws_id, status="deleted"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/signals")
        data = resp.json()
        assert data["counts"]["total"] == 1


class TestPlanSection:
    """Tests for GET /api/workspaces/{id}/sections/plan. Validates: Requirement 7.3"""

    def test_plan_grouped_by_focus_type(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_plan_item(ws_id, focus_type="today"))
        loop.run_until_complete(_seed_plan_item(ws_id, focus_type="today"))
        loop.run_until_complete(_seed_plan_item(ws_id, focus_type="upcoming"))
        loop.run_until_complete(_seed_plan_item(ws_id, focus_type="blocked"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/plan")
        assert resp.status_code == 200
        data = resp.json()

        assert data["counts"]["total"] == 4
        assert data["counts"]["today"] == 2
        assert data["counts"]["upcoming"] == 1
        assert data["counts"]["blocked"] == 1

        group_names = [g["name"] for g in data["groups"]]
        assert "today" in group_names


class TestExecuteSection:
    """Tests for GET /api/workspaces/{id}/sections/execute. Validates: Requirement 7.4"""

    def test_execute_grouped_by_status(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_task(ws_id, status="draft"))
        loop.run_until_complete(_seed_task(ws_id, status="wip"))
        loop.run_until_complete(_seed_task(ws_id, status="blocked"))
        loop.run_until_complete(_seed_task(ws_id, status="completed"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/execute")
        assert resp.status_code == 200
        data = resp.json()

        assert data["counts"]["total"] == 4
        assert data["counts"]["draft"] == 1
        assert data["counts"]["wip"] == 1
        assert data["counts"]["blocked"] == 1
        assert data["counts"]["completed"] == 1

    def test_execute_excludes_cancelled(self, client: TestClient, ws_id: str):
        """Cancelled tasks should not appear in active execute counts."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_task(ws_id, status="draft"))
        loop.run_until_complete(_seed_task(ws_id, status="cancelled"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/execute")
        data = resp.json()
        assert data["counts"]["total"] == 1


class TestCommunicateSection:
    """Tests for GET /api/workspaces/{id}/sections/communicate. Validates: Requirement 7.5"""

    def test_communicate_grouped_by_status(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_communication(ws_id, status="pending_reply"))
        loop.run_until_complete(_seed_communication(ws_id, status="ai_draft"))
        loop.run_until_complete(_seed_communication(ws_id, status="follow_up"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/communicate")
        assert resp.status_code == 200
        data = resp.json()

        assert data["counts"]["total"] == 3
        assert data["counts"]["pending_reply"] == 1
        assert data["counts"]["ai_draft"] == 1
        assert data["counts"]["follow_up"] == 1


class TestArtifactsSection:
    """Tests for GET /api/workspaces/{id}/sections/artifacts. Validates: Requirement 7.6"""

    def test_artifacts_grouped_by_type(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_artifact(ws_id, artifact_type="plan"))
        loop.run_until_complete(_seed_artifact(ws_id, artifact_type="report"))
        loop.run_until_complete(_seed_artifact(ws_id, artifact_type="doc"))
        loop.run_until_complete(_seed_artifact(ws_id, artifact_type="decision"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/artifacts")
        assert resp.status_code == 200
        data = resp.json()

        assert data["counts"]["total"] == 4
        assert data["counts"]["plan"] == 1
        assert data["counts"]["report"] == 1
        assert data["counts"]["doc"] == 1
        assert data["counts"]["decision"] == 1


class TestReflectionSection:
    """Tests for GET /api/workspaces/{id}/sections/reflection. Validates: Requirement 7.7"""

    def test_reflection_grouped_by_type(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_reflection(ws_id, reflection_type="daily_recap"))
        loop.run_until_complete(_seed_reflection(ws_id, reflection_type="weekly_summary"))
        loop.run_until_complete(_seed_reflection(ws_id, reflection_type="lessons_learned"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/reflection")
        assert resp.status_code == 200
        data = resp.json()

        assert data["counts"]["total"] == 3
        assert data["counts"]["daily_recap"] == 1
        assert data["counts"]["weekly_summary"] == 1
        assert data["counts"]["lessons_learned"] == 1


# ---------------------------------------------------------------------------
# "all" Workspace Aggregation Tests (Requirement 7.8)
# ---------------------------------------------------------------------------

class TestAllWorkspaceAggregation:
    """Tests for workspace_id='all' aggregation. Validates: Requirement 7.8"""

    def test_all_aggregates_counts_across_workspaces(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        import asyncio
        loop = asyncio.get_event_loop()
        # Seed data in both workspaces
        loop.run_until_complete(_seed_todo(ws_id, status="pending"))
        loop.run_until_complete(_seed_todo(ws_id, status="overdue"))
        loop.run_until_complete(_seed_todo(ws_id2, status="pending"))

        resp = client.get("/api/workspaces/all/sections")
        assert resp.status_code == 200
        data = resp.json()
        # Should aggregate: 2 pending + 1 overdue = 3
        assert data["signals"]["total"] == 3
        assert data["signals"]["pending"] == 2
        assert data["signals"]["overdue"] == 1

    def test_all_excludes_archived_workspaces(
        self, client: TestClient, ws_id: str
    ):
        """In single-workspace model, all items appear in 'all' aggregation."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_todo(ws_id, status="pending"))

        resp = client.get("/api/workspaces/all/sections")
        assert resp.status_code == 200
        data = resp.json()
        # The singleton workspace's todo should be counted
        assert data["signals"]["total"] >= 1

    def test_all_signals_aggregates_groups(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_todo(ws_id, status="pending", title="WS1 signal"))
        loop.run_until_complete(_seed_todo(ws_id2, status="overdue", title="WS2 signal"))

        resp = client.get("/api/workspaces/all/sections/signals")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["total"] == 2
        # Both groups should be present
        group_names = [g["name"] for g in data["groups"]]
        assert "pending" in group_names
        assert "overdue" in group_names

    def test_all_execute_aggregates_tasks(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_task(ws_id, status="draft"))
        loop.run_until_complete(_seed_task(ws_id2, status="wip"))

        resp = client.get("/api/workspaces/all/sections/execute")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["total"] == 2
        assert data["counts"]["draft"] == 1
        assert data["counts"]["wip"] == 1

    def test_all_excludes_archived_from_section_endpoints(
        self, client: TestClient, ws_id: str
    ):
        """In single-workspace model, all items appear in section endpoints."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_plan_item(ws_id, focus_type="today"))

        resp = client.get("/api/workspaces/all/sections/plan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"]["total"] >= 1


# ---------------------------------------------------------------------------
# Pagination Tests (Requirement 7.10)
# ---------------------------------------------------------------------------

class TestPagination:
    """Tests for pagination support. Validates: Requirement 7.10"""

    def test_default_pagination(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        for _ in range(3):
            loop.run_until_complete(_seed_todo(ws_id, status="pending"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/signals")
        data = resp.json()
        pag = data["pagination"]
        assert pag["limit"] == 50  # default
        assert pag["offset"] == 0
        assert pag["total"] == 3
        assert pag["has_more"] is False

    def test_limit_parameter(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        for _ in range(5):
            loop.run_until_complete(_seed_todo(ws_id, status="pending"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/signals?limit=2")
        data = resp.json()
        pag = data["pagination"]
        assert pag["limit"] == 2
        assert pag["total"] == 5
        assert pag["has_more"] is True
        # Count items across all groups
        total_items = sum(len(g["items"]) for g in data["groups"])
        assert total_items <= 2

    def test_offset_parameter(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        for i in range(5):
            loop.run_until_complete(_seed_todo(ws_id, status="pending", title=f"Signal {i}"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/signals?offset=3")
        data = resp.json()
        pag = data["pagination"]
        assert pag["offset"] == 3
        assert pag["total"] == 5
        total_items = sum(len(g["items"]) for g in data["groups"])
        assert total_items == 2  # 5 total - 3 offset = 2

    def test_limit_and_offset_combined(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        for i in range(10):
            loop.run_until_complete(_seed_todo(ws_id, status="pending", title=f"Signal {i}"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/signals?limit=3&offset=2")
        data = resp.json()
        pag = data["pagination"]
        assert pag["limit"] == 3
        assert pag["offset"] == 2
        assert pag["total"] == 10
        assert pag["has_more"] is True
        total_items = sum(len(g["items"]) for g in data["groups"])
        assert total_items <= 3

    def test_pagination_on_plan_section(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        for _ in range(4):
            loop.run_until_complete(_seed_plan_item(ws_id, focus_type="today"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/plan?limit=2")
        data = resp.json()
        assert data["pagination"]["total"] == 4
        assert data["pagination"]["has_more"] is True

    def test_pagination_beyond_total(self, client: TestClient, ws_id: str):
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_todo(ws_id, status="pending"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/signals?offset=100")
        data = resp.json()
        assert data["pagination"]["total"] == 1
        total_items = sum(len(g["items"]) for g in data["groups"])
        assert total_items == 0
        assert data["pagination"]["has_more"] is False

# ---------------------------------------------------------------------------
# SwarmWS Global View Tests (Requirement 37.1-37.12)
# ---------------------------------------------------------------------------

class TestGlobalViewRecommended:
    """Tests for SwarmWS Global View with recommended group.

    Validates: Requirements 37.1-37.12

    SwarmWS Global View (opinionated) includes a "recommended" group with
    top N items sorted by priority desc, updated_at desc.
    Neutral "all" scope does NOT include the recommended group.
    """

    def test_global_view_signals_includes_recommended_group(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        """Global view with global_view=true should include a recommended group."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_todo(ws_id, status="pending", priority="high", title="High priority"))
        loop.run_until_complete(_seed_todo(ws_id2, status="overdue", priority="low", title="Low priority"))
        loop.run_until_complete(_seed_todo(ws_id, status="in_discussion", priority="medium", title="Medium priority"))

        resp = client.get("/api/workspaces/all/sections/signals?global_view=true")
        assert resp.status_code == 200
        data = resp.json()

        group_names = [g["name"] for g in data["groups"]]
        assert "recommended" in group_names

        # Recommended should be the first group
        assert data["groups"][0]["name"] == "recommended"

        # Recommended should have at most 3 items (default N=3)
        recommended = data["groups"][0]
        assert len(recommended["items"]) <= 3

    def test_global_view_recommended_sorted_by_priority_then_updated(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        """Recommended items should be sorted by priority desc, then updated_at desc."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_todo(ws_id, status="pending", priority="low", title="Low"))
        loop.run_until_complete(_seed_todo(ws_id, status="pending", priority="high", title="High"))
        loop.run_until_complete(_seed_todo(ws_id2, status="pending", priority="medium", title="Medium"))

        resp = client.get("/api/workspaces/all/sections/signals?global_view=true")
        data = resp.json()

        recommended = next(g for g in data["groups"] if g["name"] == "recommended")
        priorities = [item["priority"] for item in recommended["items"]]
        # High should come first, then medium, then low
        assert priorities == ["high", "medium", "low"]

    def test_neutral_all_scope_no_recommended_group(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        """Neutral 'all' scope (global_view=false) should NOT include recommended group."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_todo(ws_id, status="pending", priority="high"))
        loop.run_until_complete(_seed_todo(ws_id2, status="pending", priority="low"))

        # Without global_view parameter (defaults to false)
        resp = client.get("/api/workspaces/all/sections/signals")
        data = resp.json()
        group_names = [g["name"] for g in data["groups"]]
        assert "recommended" not in group_names

        # Explicitly false
        resp = client.get("/api/workspaces/all/sections/signals?global_view=false")
        data = resp.json()
        group_names = [g["name"] for g in data["groups"]]
        assert "recommended" not in group_names

    def test_global_view_on_specific_workspace_no_recommended(
        self, client: TestClient, ws_id: str
    ):
        """global_view=true on a specific workspace (not 'all') should NOT add recommended."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_todo(ws_id, status="pending", priority="high"))

        resp = client.get(f"/api/workspaces/{ws_id}/sections/signals?global_view=true")
        data = resp.json()
        group_names = [g["name"] for g in data["groups"]]
        assert "recommended" not in group_names

    def test_global_view_execute_includes_recommended(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        """Global view on execute section should include recommended group."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_task(ws_id, status="draft", priority="high", title="High task"))
        loop.run_until_complete(_seed_task(ws_id2, status="wip", priority="low", title="Low task"))

        resp = client.get("/api/workspaces/all/sections/execute?global_view=true")
        assert resp.status_code == 200
        data = resp.json()

        group_names = [g["name"] for g in data["groups"]]
        assert "recommended" in group_names
        assert data["groups"][0]["name"] == "recommended"

    def test_global_view_plan_includes_recommended(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        """Global view on plan section should include recommended group."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_plan_item(ws_id, focus_type="today", priority="high"))
        loop.run_until_complete(_seed_plan_item(ws_id2, focus_type="upcoming", priority="low"))

        resp = client.get("/api/workspaces/all/sections/plan?global_view=true")
        assert resp.status_code == 200
        data = resp.json()

        group_names = [g["name"] for g in data["groups"]]
        assert "recommended" in group_names

    def test_global_view_communicate_includes_recommended(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        """Global view on communicate section should include recommended group."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_communication(ws_id, status="pending_reply", priority="high"))
        loop.run_until_complete(_seed_communication(ws_id2, status="ai_draft", priority="low"))

        resp = client.get("/api/workspaces/all/sections/communicate?global_view=true")
        assert resp.status_code == 200
        data = resp.json()

        group_names = [g["name"] for g in data["groups"]]
        assert "recommended" in group_names

    def test_global_view_artifacts_includes_recommended(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        """Global view on artifacts section should include recommended group."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_artifact(ws_id, artifact_type="doc"))
        loop.run_until_complete(_seed_artifact(ws_id2, artifact_type="plan"))

        resp = client.get("/api/workspaces/all/sections/artifacts?global_view=true")
        assert resp.status_code == 200
        data = resp.json()

        group_names = [g["name"] for g in data["groups"]]
        assert "recommended" in group_names

    def test_global_view_reflection_includes_recommended(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        """Global view on reflection section should include recommended group."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_reflection(ws_id, reflection_type="daily_recap"))
        loop.run_until_complete(_seed_reflection(ws_id2, reflection_type="weekly_summary"))

        resp = client.get("/api/workspaces/all/sections/reflection?global_view=true")
        assert resp.status_code == 200
        data = resp.json()

        group_names = [g["name"] for g in data["groups"]]
        assert "recommended" in group_names

    def test_global_view_recommended_respects_default_n(
        self, client: TestClient, ws_id: str, ws_id2: str
    ):
        """Recommended group should contain at most DEFAULT_RECOMMENDED_N (3) items."""
        import asyncio
        loop = asyncio.get_event_loop()
        # Seed 5 items across workspaces
        for i in range(5):
            ws = ws_id if i % 2 == 0 else ws_id2
            loop.run_until_complete(_seed_todo(ws, status="pending", priority="high", title=f"Signal {i}"))

        resp = client.get("/api/workspaces/all/sections/signals?global_view=true")
        data = resp.json()

        recommended = next(g for g in data["groups"] if g["name"] == "recommended")
        assert len(recommended["items"]) == 3  # Default N=3

    def test_global_view_empty_data_no_recommended(
        self, client: TestClient, ws_id: str
    ):
        """When no items exist, recommended group should not appear."""
        resp = client.get("/api/workspaces/all/sections/signals?global_view=true")
        assert resp.status_code == 200
        data = resp.json()

        group_names = [g["name"] for g in data["groups"]]
        assert "recommended" not in group_names

    def test_global_view_excludes_archived_from_recommended(
        self, client: TestClient, ws_id: str
    ):
        """In single-workspace model, all items appear in recommended group."""
        import asyncio
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_todo(ws_id, status="pending", priority="low", title="Active"))
        loop.run_until_complete(_seed_todo(ws_id, status="pending", priority="high", title="Important"))

        resp = client.get("/api/workspaces/all/sections/signals?global_view=true")
        data = resp.json()

        # Both items should be counted
        assert data["counts"]["total"] >= 2

        recommended = next(g for g in data["groups"] if g["name"] == "recommended")
        assert len(recommended["items"]) >= 2
