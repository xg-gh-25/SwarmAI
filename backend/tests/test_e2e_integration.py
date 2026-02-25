"""End-to-end integration tests for the workspace refactor (Task 28).

These tests exercise full lifecycle flows through the HTTP API layer,
verifying that the backend components work together correctly.

Requirements: 4.7, 4.8, 16.5, 17.5, 36.1-36.11, 37.1-37.12, 38.1-38.12
"""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from database import db
from tests.helpers import (
    now_iso,
    create_workspace,
    create_custom_workspace,
    ensure_default_workspace,
    seed_todo,
)


# ---------------------------------------------------------------------------
# Shared seed helpers
# ---------------------------------------------------------------------------

async def _seed_skill(name: str, is_privileged: bool = False) -> str:
    """Insert a skill row and return its ID."""
    now = now_iso()
    sid = str(uuid4())
    await db.skills.put({
        "id": sid,
        "name": name,
        "description": f"Desc {name}",
        "version": "1.0.0",
        "is_system": False,
        "is_privileged": 1 if is_privileged else 0,
        "created_at": now,
        "updated_at": now,
    })
    return sid


async def _enable_skill(workspace_id: str, skill_id: str, enabled: bool = True) -> None:
    """Create a workspace_skills junction row."""
    now = now_iso()
    await db.workspace_skills.put({
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "skill_id": skill_id,
        "enabled": 1 if enabled else 0,
        "created_at": now,
        "updated_at": now,
    })


async def _seed_communication(workspace_id: str, title: str, status: str = "pending_reply") -> str:
    """Insert a communication row and return its ID."""
    now = now_iso()
    cid = str(uuid4())
    await db.communications.put({
        "id": cid,
        "workspace_id": workspace_id,
        "title": title,
        "description": f"Desc for {title}",
        "recipient": "team@example.com",
        "channel_type": "email",
        "status": status,
        "priority": "medium",
        "due_date": None,
        "ai_draft_content": None,
        "source_task_id": None,
        "source_todo_id": None,
        "sent_at": None,
        "created_at": now,
        "updated_at": now,
    })
    return cid


async def _seed_plan_item(workspace_id: str, title: str, focus_type: str = "today") -> str:
    """Insert a plan_item row and return its ID."""
    now = now_iso()
    pid = str(uuid4())
    await db.plan_items.put({
        "id": pid,
        "workspace_id": workspace_id,
        "title": title,
        "description": f"Desc for {title}",
        "source_todo_id": None,
        "source_task_id": None,
        "status": "active",
        "priority": "medium",
        "scheduled_date": None,
        "focus_type": focus_type,
        "sort_order": 0,
        "created_at": now,
        "updated_at": now,
    })
    return pid



# =========================================================================
# 28.1 — ToDo lifecycle: Create → Edit → Convert to Task → Verify linkage
# Requirements: 4.7, 4.8
# =========================================================================


class TestToDoLifecycle:
    """End-to-end test for the full ToDo lifecycle via HTTP API."""

    async def test_create_edit_convert_verify_linkage(self, client: TestClient):
        """Create ToDo → Edit → Convert to Task → Verify linkage.

        After conversion:
        - todo.task_id must be set to the new task's ID
        - task.source_todo_id must be set to the original todo's ID
        - todo.status must be 'handled'
        """
        ws_id = await ensure_default_workspace()

        # Step 1: Create a ToDo
        create_resp = client.post("/api/todos", json={
            "workspace_id": ws_id,
            "title": "Review PR #42",
            "description": "Code review for auth module",
            "source_type": "manual",
            "priority": "high",
        })
        assert create_resp.status_code == 201, create_resp.text
        todo = create_resp.json()
        todo_id = todo["id"]
        assert todo["title"] == "Review PR #42"
        assert todo["status"] == "pending"
        assert todo["priority"] == "high"

        # Step 2: Edit the ToDo
        edit_resp = client.put(f"/api/todos/{todo_id}", json={
            "title": "Review PR #42 — urgent",
            "description": "Code review for auth module (blocking release)",
        })
        assert edit_resp.status_code == 200, edit_resp.text
        updated_todo = edit_resp.json()
        assert updated_todo["title"] == "Review PR #42 — urgent"
        assert updated_todo["description"] == "Code review for auth module (blocking release)"

        # Step 3: Convert ToDo to Task
        convert_resp = client.post(f"/api/todos/{todo_id}/convert-to-task", json={
            "agent_id": "default",
        })
        assert convert_resp.status_code == 200, convert_resp.text
        task = convert_resp.json()
        task_id = task["id"]

        # Step 4: Verify linkage
        # 4a: task.source_todo_id == todo_id
        assert task["source_todo_id"] == todo_id

        # 4b: Re-fetch the ToDo and verify todo.task_id == task_id
        get_resp = client.get(f"/api/todos/{todo_id}")
        assert get_resp.status_code == 200
        final_todo = get_resp.json()
        assert final_todo["task_id"] == task_id

        # 4c: todo.status == 'handled'
        assert final_todo["status"] == "handled"

        # 4d: Task inherits the ToDo's workspace_id
        assert task["workspace_id"] == ws_id

    async def test_convert_preserves_title_and_priority(self, client: TestClient):
        """Converted task inherits title and priority from the ToDo."""
        ws_id = await ensure_default_workspace()

        create_resp = client.post("/api/todos", json={
            "workspace_id": ws_id,
            "title": "Deploy hotfix",
            "priority": "high",
        })
        todo = create_resp.json()

        convert_resp = client.post(f"/api/todos/{todo['id']}/convert-to-task", json={
            "agent_id": "default",
        })
        task = convert_resp.json()
        assert task["title"] == "Deploy hotfix"
        assert task["priority"] == "high"
        assert task["status"] == "draft"



# =========================================================================
# 28.2 — Workspace config inheritance: intersection model for skills
# Requirements: 16.5, 17.5
# =========================================================================


class TestWorkspaceConfigInheritance:
    """End-to-end test for workspace configuration inheritance."""

    async def test_skills_intersection_model(self, client: TestClient):
        """Configure workspace skills → Verify all enabled skills returned.

        In the single-workspace model, the endpoint returns all skills
        enabled for the workspace directly (no intersection model).
        """
        swarmws_id = await ensure_default_workspace()
        custom_ws_id = await create_custom_workspace(name="ProjectAlpha")

        # Seed three skills
        skill_a = await _seed_skill("SkillA")
        skill_b = await _seed_skill("SkillB")
        skill_c = await _seed_skill("SkillC")

        # Enable A and C in custom workspace
        await _enable_skill(custom_ws_id, skill_a, enabled=True)
        await _enable_skill(custom_ws_id, skill_c, enabled=True)

        # Fetch effective skills for custom workspace
        resp = client.get(f"/api/workspaces/{custom_ws_id}/skills")
        assert resp.status_code == 200, resp.text
        effective = resp.json()
        effective_ids = {s["skill_id"] for s in effective if s["enabled"]}

        # Both A and C are enabled in the workspace → both should appear
        assert skill_a in effective_ids
        assert skill_c in effective_ids
        # B is not enabled in the workspace → should not appear
        assert skill_b not in effective_ids

    async def test_swarmws_sees_own_enabled_skills(self, client: TestClient):
        """SwarmWS effective skills = its own enabled skills (no intersection)."""
        swarmws_id = await ensure_default_workspace()

        skill_x = await _seed_skill("SkillX")
        await _enable_skill(swarmws_id, skill_x, enabled=True)

        resp = client.get(f"/api/workspaces/{swarmws_id}/skills")
        assert resp.status_code == 200
        effective = resp.json()
        effective_ids = {s["skill_id"] for s in effective if s["enabled"]}
        assert skill_x in effective_ids

    async def test_privileged_skill_not_auto_enabled(self, client: TestClient):
        """Privileged skills require explicit enablement."""
        swarmws_id = await ensure_default_workspace()

        priv_skill = await _seed_skill("DangerousSkill", is_privileged=True)

        # Not explicitly enabled → should not appear as effective
        resp = client.get(f"/api/workspaces/{swarmws_id}/skills")
        assert resp.status_code == 200
        effective_ids = {s["skill_id"] for s in resp.json() if s["enabled"]}
        assert priv_skill not in effective_ids



# =========================================================================
# 28.3 — Archive workflow: REMOVED (single-workspace model)
# Archive/unarchive lifecycle tests are obsolete — SwarmWS cannot be archived.
# =========================================================================


# =========================================================================
# 28.4 — Global View aggregation: Multi-workspace → workspace_id="all"
# Requirements: 37.1-37.12
# =========================================================================


class TestGlobalViewAggregation:
    """End-to-end test for Global View aggregation across workspaces."""

    async def test_aggregation_totals_across_workspaces(self, client: TestClient):
        """Create items in multiple workspaces → Get counts with workspace_id='all' → Verify totals."""
        swarmws_id = await ensure_default_workspace()
        ws_a_id = await create_custom_workspace(name="WorkspaceA")
        ws_b_id = await create_custom_workspace(name="WorkspaceB")

        # Seed ToDos (signals) in each workspace
        await seed_todo(swarmws_id, title="SwarmWS signal 1", status="pending")
        await seed_todo(swarmws_id, title="SwarmWS signal 2", status="overdue")
        await seed_todo(ws_a_id, title="WS-A signal 1", status="pending")
        await seed_todo(ws_b_id, title="WS-B signal 1", status="pending")
        await seed_todo(ws_b_id, title="WS-B signal 2", status="in_discussion")

        # Seed plan items
        await _seed_plan_item(swarmws_id, "Plan SwarmWS", focus_type="today")
        await _seed_plan_item(ws_a_id, "Plan WS-A", focus_type="upcoming")

        # Seed communications
        await _seed_communication(swarmws_id, "Comm SwarmWS", status="pending_reply")
        await _seed_communication(ws_b_id, "Comm WS-B", status="ai_draft")

        # Get aggregated section counts
        resp = client.get("/api/workspaces/all/sections")
        assert resp.status_code == 200, resp.text
        counts = resp.json()

        # Signals: 2 pending (SwarmWS + WS-A + WS-B = 1+1+1=3 pending), 1 overdue, 1 in_discussion
        assert counts["signals"]["pending"] == 3
        assert counts["signals"]["overdue"] == 1
        assert counts["signals"]["in_discussion"] == 1
        assert counts["signals"]["total"] == 5

        # Plan: 1 today, 1 upcoming
        assert counts["plan"]["today"] == 1
        assert counts["plan"]["upcoming"] == 1
        assert counts["plan"]["total"] == 2

        # Communicate: 1 pending_reply, 1 ai_draft
        assert counts["communicate"]["pending_reply"] == 1
        assert counts["communicate"]["ai_draft"] == 1
        assert counts["communicate"]["total"] == 2

    async def test_single_workspace_scope(self, client: TestClient):
        """Scoped view returns items for the singleton workspace."""
        ws_id = await ensure_default_workspace()

        await seed_todo(ws_id, title="A signal", status="pending")
        await seed_todo(ws_id, title="B signal", status="pending")

        # Scoped to the singleton workspace
        resp = client.get(f"/api/workspaces/{ws_id}/sections")
        assert resp.status_code == 200
        counts = resp.json()
        assert counts["signals"]["total"] >= 2
        assert counts["signals"]["pending"] >= 2



# =========================================================================
# 28.5 — Search functionality: Create → Search → Scope filtering → Results
# Requirements: 38.1-38.12
# =========================================================================


class TestSearchFunctionality:
    """End-to-end test for search across entity types with scope filtering."""

    async def test_search_finds_todos_by_title(self, client: TestClient):
        """Search returns ToDos matching the query string."""
        ws_id = await ensure_default_workspace()
        await seed_todo(ws_id, title="Kubernetes migration plan")
        await seed_todo(ws_id, title="Update README docs")

        resp = client.get("/api/search", params={"query": "Kubernetes"})
        assert resp.status_code == 200, resp.text
        results = resp.json()
        assert results["total"] >= 1

        # Find the todos group
        todo_group = next(
            (g for g in results["groups"] if g["entity_type"] == "todo"),
            None,
        )
        assert todo_group is not None
        assert any("Kubernetes" in item["title"] for item in todo_group["items"])

    async def test_search_scope_filtering(self, client: TestClient):
        """Search with scope=workspace_id returns items from the singleton workspace."""
        ws_id = await ensure_default_workspace()

        await seed_todo(ws_id, title="Alpha deployment checklist")
        await seed_todo(ws_id, title="Alpha security review")

        # Search scoped to the singleton workspace
        resp = client.get("/api/search", params={
            "query": "Alpha",
            "scope": ws_id,
        })
        assert resp.status_code == 200
        results = resp.json()

        # Should find both items (same workspace)
        all_items = [item for g in results["groups"] for item in g["items"]]
        assert len(all_items) >= 2
        assert all(item["workspace_id"] == ws_id for item in all_items)

    async def test_search_scope_all_returns_all_non_archived(self, client: TestClient):
        """Search with scope='all' returns items from all non-archived workspaces."""
        await ensure_default_workspace()
        ws_a_id = await create_custom_workspace(name="AllSearchA")
        ws_b_id = await create_custom_workspace(name="AllSearchB")

        await seed_todo(ws_a_id, title="Gamma feature flag")
        await seed_todo(ws_b_id, title="Gamma rollback plan")

        resp = client.get("/api/search", params={
            "query": "Gamma",
            "scope": "all",
        })
        assert resp.status_code == 200
        results = resp.json()
        all_items = [item for g in results["groups"] for item in g["items"]]
        assert len(all_items) == 2

    async def test_search_across_entity_types(self, client: TestClient):
        """Search returns results from multiple entity types."""
        ws_id = await ensure_default_workspace()

        await seed_todo(ws_id, title="Omega signal item")
        await _seed_communication(ws_id, title="Omega comm item")
        await _seed_plan_item(ws_id, title="Omega plan item")

        resp = client.get("/api/search", params={"query": "Omega"})
        assert resp.status_code == 200
        results = resp.json()

        entity_types_found = {g["entity_type"] for g in results["groups"]}
        assert "todo" in entity_types_found
        assert "communication" in entity_types_found
        assert "plan_item" in entity_types_found
        assert results["total"] >= 3
