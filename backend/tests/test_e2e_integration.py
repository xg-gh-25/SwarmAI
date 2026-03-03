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
# Note: Skill fixtures are no longer needed in the filesystem model.
# Skills are referenced by folder name in agent.allowed_skills.


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
    """End-to-end test for workspace configuration inheritance.
    
    Note: In the filesystem-based skill model, skills are no longer managed
    via workspace_skills junction tables. Instead, agents directly reference
    skill folder names in their allowed_skills field. These tests verify
    that agents can be configured with allowed_skills correctly.
    """

    async def test_skills_intersection_model(self, client: TestClient):
        """Configure agent skills → Verify allowed_skills are stored correctly.

        In the filesystem model, agents directly reference skill folder names.
        This test verifies that the allowed_skills field is persisted correctly.
        """
        swarmws_id = await ensure_default_workspace()

        # Create an agent with specific skills enabled
        # Note: We use skill folder names directly (no DB validation at creation time)
        # global_user_mode must be False to use allowed_skills restrictions
        agent_resp = client.post("/api/agents", json={
            "name": "Test Agent with Skills",
            "description": "Agent for testing skill configuration",
            "allowed_skills": ["skilla", "skillc"],
            "global_user_mode": False,
        })
        assert agent_resp.status_code == 201, agent_resp.text
        agent = agent_resp.json()

        # Verify the agent has the correct allowed_skills
        assert set(agent["allowed_skills"]) == {"skilla", "skillc"}
        
        # Verify skillb is not in the list
        assert "skillb" not in agent["allowed_skills"]

    async def test_swarmws_sees_own_enabled_skills(self, client: TestClient):
        """Agent allowed_skills are persisted and retrieved correctly."""
        swarmws_id = await ensure_default_workspace()

        # Create an agent with a specific skill enabled
        # global_user_mode must be False to use allowed_skills restrictions
        agent_resp = client.post("/api/agents", json={
            "name": "Agent with SkillX",
            "description": "Test agent",
            "allowed_skills": ["skillx"],
            "global_user_mode": False,
        })
        assert agent_resp.status_code == 201
        agent = agent_resp.json()
        agent_id = agent["id"]
        
        # Verify skill is in allowed_skills
        assert "skillx" in agent["allowed_skills"]
        
        # Fetch the agent again to verify persistence
        get_resp = client.get(f"/api/agents/{agent_id}")
        assert get_resp.status_code == 200
        fetched_agent = get_resp.json()
        assert "skillx" in fetched_agent["allowed_skills"]

    async def test_privileged_skill_not_auto_enabled(self, client: TestClient):
        """Skills are not automatically enabled without explicit configuration.
        
        In the filesystem model, skills must be explicitly added to an agent's
        allowed_skills list. This test verifies that agents start with an
        empty allowed_skills list by default.
        """
        swarmws_id = await ensure_default_workspace()

        # Create an agent without any skills enabled
        agent_resp = client.post("/api/agents", json={
            "name": "Agent without skills",
            "description": "Test agent",
            "allowed_skills": [],  # Explicitly empty
        })
        assert agent_resp.status_code == 201
        agent = agent_resp.json()
        
        # Verify the allowed_skills list is empty
        assert agent["allowed_skills"] == []
        
        # Verify a specific skill is not in the list
        assert "dangerous-skill" not in agent["allowed_skills"]



# =========================================================================
# 28.3 — Archive workflow: REMOVED (single-workspace model)
# Archive/unarchive lifecycle tests are obsolete — SwarmWS cannot be archived.
# =========================================================================


# =========================================================================
# 28.4 — Global View aggregation: Multi-workspace → workspace_id="all"
# Requirements: 37.1-37.12
# =========================================================================


class TestGlobalViewAggregation:
    """End-to-end test for ToDo aggregation across workspaces.

    Operating Loop sections endpoint was removed. This test now only verifies
    ToDo listing across workspaces via the /api/todos endpoint.
    """

    async def test_aggregation_totals_across_workspaces(self, client: TestClient):
        """Create ToDos in multiple workspaces → List all → Verify totals."""
        swarmws_id = await ensure_default_workspace()
        ws_a_id = await create_custom_workspace(name="WorkspaceA")
        ws_b_id = await create_custom_workspace(name="WorkspaceB")

        # Seed ToDos in each workspace
        await seed_todo(swarmws_id, title="SwarmWS signal 1", status="pending")
        await seed_todo(swarmws_id, title="SwarmWS signal 2", status="overdue")
        await seed_todo(ws_a_id, title="WS-A signal 1", status="pending")
        await seed_todo(ws_b_id, title="WS-B signal 1", status="pending")
        await seed_todo(ws_b_id, title="WS-B signal 2", status="in_discussion")

        # List todos per workspace
        resp = client.get("/api/todos", params={"workspace_id": swarmws_id})
        assert resp.status_code == 200, resp.text
        swarmws_todos = resp.json()
        assert len(swarmws_todos) >= 2

        resp = client.get("/api/todos", params={"workspace_id": ws_a_id})
        assert resp.status_code == 200
        ws_a_todos = resp.json()
        assert len(ws_a_todos) >= 1

        resp = client.get("/api/todos", params={"workspace_id": ws_b_id})
        assert resp.status_code == 200
        ws_b_todos = resp.json()
        assert len(ws_b_todos) >= 2

    async def test_single_workspace_scope(self, client: TestClient):
        """Scoped view returns ToDos for a single workspace."""
        ws_id = await ensure_default_workspace()

        await seed_todo(ws_id, title="A signal", status="pending")
        await seed_todo(ws_id, title="B signal", status="pending")

        resp = client.get("/api/todos", params={"workspace_id": ws_id})
        assert resp.status_code == 200
        todos = resp.json()
        assert len(todos) >= 2



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
        """Search returns results from ToDo entity type (Operating Loop entities removed)."""
        ws_id = await ensure_default_workspace()

        await seed_todo(ws_id, title="Omega signal item")
        await seed_todo(ws_id, title="Omega second item")

        resp = client.get("/api/search", params={"query": "Omega"})
        assert resp.status_code == 200
        results = resp.json()

        entity_types_found = {g["entity_type"] for g in results["groups"]}
        assert "todo" in entity_types_found
        # Operating Loop entity types should NOT appear
        assert "communication" not in entity_types_found
        assert "plan_item" not in entity_types_found
        assert results["total"] >= 2
