"""Unit tests for workspace configuration API router endpoints.

Tests CRUD operations for Skills, MCPs, Knowledgebases, Context management,
audit log retrieval, and privileged capability confirmation for the
/api/workspaces/{id}/... config endpoints.

Requirements: 19.6-19.9
"""
import json
import tempfile

import pytest
from fastapi.testclient import TestClient

from database import db
from tests.helpers import now_iso


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_id(client: TestClient) -> str:
    """Create a workspace backed by a real temp directory."""
    temp_path = tempfile.mkdtemp()
    resp = client.post("/api/swarm-workspaces", json={
        "name": "ConfigTestWS",
        "file_path": temp_path,
        "context": "Workspace for config router tests",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture
def second_workspace_id(client: TestClient) -> str:
    """Create a second workspace for isolation tests."""
    temp_path = tempfile.mkdtemp()
    resp = client.post("/api/swarm-workspaces", json={
        "name": "ConfigTestWS2",
        "file_path": temp_path,
        "context": "Second workspace for config tests",
    })
    assert resp.status_code == 201
    return resp.json()["id"]


async def _ensure_default_workspace() -> str:
    """Ensure a default SwarmWS workspace exists and return its ID."""
    existing = await db.swarm_workspaces.get_default()
    if existing:
        return existing["id"]
    from uuid import uuid4
    now = now_iso()
    ws_id = str(uuid4())
    await db.swarm_workspaces.put({
        "id": ws_id,
        "name": "SwarmWS",
        "file_path": f"/tmp/test-swarmws-{ws_id[:8]}",
        "context": "Default workspace",
        "icon": "🏠",
        "is_default": True,
        "is_archived": 0,
        "archived_at": None,
        "created_at": now,
        "updated_at": now,
    })
    return ws_id


async def _seed_skill(name: str = "TestSkill", is_privileged: bool = False) -> str:
    """Insert a skill directly into the DB and return its ID."""
    from uuid import uuid4
    now = now_iso()
    skill_id = str(uuid4())
    await db.skills.put({
        "id": skill_id,
        "name": name,
        "description": f"Test skill: {name}",
        "folder_name": f"skill-{skill_id[:8]}",
        "local_path": f"/tmp/skills/{skill_id[:8]}",
        "version": "1.0.0",
        "is_system": 0,
        "is_privileged": 1 if is_privileged else 0,
        "current_version": 0,
        "has_draft": 0,
        "created_at": now,
        "updated_at": now,
    })
    return skill_id


async def _seed_mcp(name: str = "TestMCP", is_privileged: bool = False) -> str:
    """Insert an MCP server directly into the DB and return its ID."""
    from uuid import uuid4
    now = now_iso()
    mcp_id = str(uuid4())
    await db.mcp_servers.put({
        "id": mcp_id,
        "name": name,
        "description": f"Test MCP: {name}",
        "connection_type": "stdio",
        "config": json.dumps({"command": "echo", "args": ["test"]}),
        "allowed_tools": "[]",
        "rejected_tools": "[]",
        "is_active": 1,
        "is_system": 0,
        "is_privileged": 1 if is_privileged else 0,
        "created_at": now,
        "updated_at": now,
    })
    return mcp_id


async def _seed_workspace_skill(workspace_id: str, skill_id: str, enabled: bool = True) -> None:
    """Insert a workspace_skills junction row."""
    from uuid import uuid4
    now = now_iso()
    await db.workspace_skills.put({
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "skill_id": skill_id,
        "enabled": 1 if enabled else 0,
        "created_at": now,
        "updated_at": now,
    })


async def _seed_workspace_mcp(workspace_id: str, mcp_id: str, enabled: bool = True) -> None:
    """Insert a workspace_mcps junction row."""
    from uuid import uuid4
    now = now_iso()
    await db.workspace_mcps.put({
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "mcp_server_id": mcp_id,
        "enabled": 1 if enabled else 0,
        "created_at": now,
        "updated_at": now,
    })


async def _enable_skill_in_both(swarmws_id: str, workspace_id: str, skill_id: str) -> None:
    """Enable a skill in both SwarmWS and a custom workspace (intersection model)."""
    await _seed_workspace_skill(swarmws_id, skill_id, enabled=True)
    await _seed_workspace_skill(workspace_id, skill_id, enabled=True)


async def _enable_mcp_in_both(swarmws_id: str, workspace_id: str, mcp_id: str) -> None:
    """Enable an MCP in both SwarmWS and a custom workspace (intersection model)."""
    await _seed_workspace_mcp(swarmws_id, mcp_id, enabled=True)
    await _seed_workspace_mcp(workspace_id, mcp_id, enabled=True)


# ============================================================================
# Skills Endpoints (Intersection Model)
# Validates: Requirement 19.6
# ============================================================================


class TestGetSkills:
    """Tests for GET /api/workspaces/{id}/skills."""

    def test_get_skills_empty(self, client: TestClient, workspace_id: str):
        """No skills configured returns empty list."""
        resp = client.get(f"/api/workspaces/{workspace_id}/skills")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.anyio
    async def test_get_skills_returns_configured(self, client: TestClient, workspace_id: str):
        """Skills enabled in both SwarmWS and workspace are returned (intersection)."""
        swarmws_id = await _ensure_default_workspace()
        skill_id = await _seed_skill("MySkill")
        await _enable_skill_in_both(swarmws_id, workspace_id, skill_id)

        resp = client.get(f"/api/workspaces/{workspace_id}/skills")
        assert resp.status_code == 200
        data = resp.json()
        skill_ids = [s["skill_id"] for s in data]
        assert skill_id in skill_ids

    @pytest.mark.anyio
    async def test_get_skills_includes_privileged_flag(self, client: TestClient, workspace_id: str):
        """Privileged skills have is_privileged=True in response."""
        swarmws_id = await _ensure_default_workspace()
        skill_id = await _seed_skill("PrivSkill", is_privileged=True)
        await _enable_skill_in_both(swarmws_id, workspace_id, skill_id)

        resp = client.get(f"/api/workspaces/{workspace_id}/skills")
        assert resp.status_code == 200
        priv_skills = [s for s in resp.json() if s["skill_id"] == skill_id]
        assert len(priv_skills) == 1
        assert priv_skills[0]["is_privileged"] is True

    @pytest.mark.anyio
    async def test_get_skills_intersection_excludes_swarmws_only(self, client: TestClient, workspace_id: str):
        """Skill enabled only in SwarmWS (not workspace) is NOT in effective set."""
        swarmws_id = await _ensure_default_workspace()
        skill_id = await _seed_skill("SwarmOnlySkill")
        await _seed_workspace_skill(swarmws_id, skill_id, enabled=True)
        # NOT enabled in custom workspace

        resp = client.get(f"/api/workspaces/{workspace_id}/skills")
        assert resp.status_code == 200
        skill_ids = [s["skill_id"] for s in resp.json()]
        assert skill_id not in skill_ids


class TestUpdateSkills:
    """Tests for PUT /api/workspaces/{id}/skills."""

    @pytest.mark.anyio
    async def test_update_skill_enable(self, client: TestClient, workspace_id: str):
        """Enable a skill via PUT."""
        swarmws_id = await _ensure_default_workspace()
        skill_id = await _seed_skill("EnableMe")
        await _seed_workspace_skill(swarmws_id, skill_id, enabled=True)
        await _seed_workspace_skill(workspace_id, skill_id, enabled=False)

        resp = client.put(f"/api/workspaces/{workspace_id}/skills", json={
            "configs": [{
                "skill_id": skill_id,
                "skill_name": "EnableMe",
                "enabled": True,
                "is_privileged": False,
            }]
        })
        assert resp.status_code == 200
        updated = [s for s in resp.json() if s["skill_id"] == skill_id]
        assert len(updated) == 1
        assert updated[0]["enabled"] is True

    @pytest.mark.anyio
    async def test_update_skill_disable(self, client: TestClient, workspace_id: str):
        """Disable a skill via PUT."""
        swarmws_id = await _ensure_default_workspace()
        skill_id = await _seed_skill("DisableMe")
        await _enable_skill_in_both(swarmws_id, workspace_id, skill_id)

        resp = client.put(f"/api/workspaces/{workspace_id}/skills", json={
            "configs": [{
                "skill_id": skill_id,
                "skill_name": "DisableMe",
                "enabled": False,
                "is_privileged": False,
            }]
        })
        assert resp.status_code == 200
        # After disabling, skill should not appear in effective set
        skill_ids = [s["skill_id"] for s in resp.json()]
        assert skill_id not in skill_ids

    @pytest.mark.anyio
    async def test_update_skill_creates_audit_entry(self, client: TestClient, workspace_id: str):
        """Updating a skill config creates an audit log entry."""
        swarmws_id = await _ensure_default_workspace()
        skill_id = await _seed_skill("AuditSkill")
        await _enable_skill_in_both(swarmws_id, workspace_id, skill_id)

        client.put(f"/api/workspaces/{workspace_id}/skills", json={
            "configs": [{
                "skill_id": skill_id,
                "skill_name": "AuditSkill",
                "enabled": False,
                "is_privileged": False,
            }]
        })

        audit_resp = client.get(f"/api/workspaces/{workspace_id}/audit-log")
        assert audit_resp.status_code == 200
        entries = audit_resp.json()["entries"]
        skill_entries = [e for e in entries if e["entity_id"] == skill_id]
        assert len(skill_entries) >= 1
        assert skill_entries[0]["entity_type"] == "skill"
        assert skill_entries[0]["change_type"] == "disabled"


# ============================================================================
# MCPs Endpoints (Intersection Model)
# Validates: Requirement 19.7
# ============================================================================


class TestGetMcps:
    """Tests for GET /api/workspaces/{id}/mcps."""

    def test_get_mcps_empty(self, client: TestClient, workspace_id: str):
        resp = client.get(f"/api/workspaces/{workspace_id}/mcps")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.anyio
    async def test_get_mcps_returns_configured(self, client: TestClient, workspace_id: str):
        swarmws_id = await _ensure_default_workspace()
        mcp_id = await _seed_mcp("MyMCP")
        await _enable_mcp_in_both(swarmws_id, workspace_id, mcp_id)

        resp = client.get(f"/api/workspaces/{workspace_id}/mcps")
        assert resp.status_code == 200
        mcp_ids = [m["mcp_server_id"] for m in resp.json()]
        assert mcp_id in mcp_ids

    @pytest.mark.anyio
    async def test_get_mcps_includes_privileged_flag(self, client: TestClient, workspace_id: str):
        swarmws_id = await _ensure_default_workspace()
        mcp_id = await _seed_mcp("PrivMCP", is_privileged=True)
        await _enable_mcp_in_both(swarmws_id, workspace_id, mcp_id)

        resp = client.get(f"/api/workspaces/{workspace_id}/mcps")
        assert resp.status_code == 200
        priv_mcps = [m for m in resp.json() if m["mcp_server_id"] == mcp_id]
        assert len(priv_mcps) == 1
        assert priv_mcps[0]["is_privileged"] is True

    @pytest.mark.anyio
    async def test_get_mcps_intersection_excludes_swarmws_only(self, client: TestClient, workspace_id: str):
        """MCP enabled only in SwarmWS is NOT in effective set for custom workspace."""
        swarmws_id = await _ensure_default_workspace()
        mcp_id = await _seed_mcp("SwarmOnlyMCP")
        await _seed_workspace_mcp(swarmws_id, mcp_id, enabled=True)

        resp = client.get(f"/api/workspaces/{workspace_id}/mcps")
        assert resp.status_code == 200
        mcp_ids = [m["mcp_server_id"] for m in resp.json()]
        assert mcp_id not in mcp_ids


class TestUpdateMcps:
    """Tests for PUT /api/workspaces/{id}/mcps."""

    @pytest.mark.anyio
    async def test_update_mcp_enable(self, client: TestClient, workspace_id: str):
        swarmws_id = await _ensure_default_workspace()
        mcp_id = await _seed_mcp("EnableMCP")
        await _seed_workspace_mcp(swarmws_id, mcp_id, enabled=True)
        await _seed_workspace_mcp(workspace_id, mcp_id, enabled=False)

        resp = client.put(f"/api/workspaces/{workspace_id}/mcps", json={
            "configs": [{
                "mcp_server_id": mcp_id,
                "mcp_server_name": "EnableMCP",
                "enabled": True,
                "is_privileged": False,
            }]
        })
        assert resp.status_code == 200
        updated = [m for m in resp.json() if m["mcp_server_id"] == mcp_id]
        assert len(updated) == 1
        assert updated[0]["enabled"] is True

    @pytest.mark.anyio
    async def test_update_mcp_disable(self, client: TestClient, workspace_id: str):
        swarmws_id = await _ensure_default_workspace()
        mcp_id = await _seed_mcp("DisableMCP")
        await _enable_mcp_in_both(swarmws_id, workspace_id, mcp_id)

        resp = client.put(f"/api/workspaces/{workspace_id}/mcps", json={
            "configs": [{
                "mcp_server_id": mcp_id,
                "mcp_server_name": "DisableMCP",
                "enabled": False,
                "is_privileged": False,
            }]
        })
        assert resp.status_code == 200
        mcp_ids = [m["mcp_server_id"] for m in resp.json()]
        assert mcp_id not in mcp_ids

    @pytest.mark.anyio
    async def test_update_mcp_creates_audit_entry(self, client: TestClient, workspace_id: str):
        swarmws_id = await _ensure_default_workspace()
        mcp_id = await _seed_mcp("AuditMCP")
        await _enable_mcp_in_both(swarmws_id, workspace_id, mcp_id)

        client.put(f"/api/workspaces/{workspace_id}/mcps", json={
            "configs": [{
                "mcp_server_id": mcp_id,
                "mcp_server_name": "AuditMCP",
                "enabled": False,
                "is_privileged": False,
            }]
        })

        audit_resp = client.get(f"/api/workspaces/{workspace_id}/audit-log")
        assert audit_resp.status_code == 200
        entries = audit_resp.json()["entries"]
        mcp_entries = [e for e in entries if e["entity_id"] == mcp_id]
        assert len(mcp_entries) >= 1
        assert mcp_entries[0]["entity_type"] == "mcp"
        assert mcp_entries[0]["change_type"] == "disabled"


# ============================================================================
# Knowledgebases Endpoints (Union Model with Exclusions)
# Validates: Requirement 19.8
# ============================================================================


class TestGetKnowledgebases:
    """Tests for GET /api/workspaces/{id}/knowledgebases."""

    def test_get_knowledgebases_empty(self, client: TestClient, workspace_id: str):
        resp = client.get(f"/api/workspaces/{workspace_id}/knowledgebases")
        assert resp.status_code == 200
        assert resp.json() == []


class TestAddKnowledgebase:
    """Tests for POST /api/workspaces/{id}/knowledgebases."""

    def test_add_knowledgebase_success(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
            "source_type": "local_file",
            "source_path": "/data/docs/guide.md",
            "display_name": "Project Guide",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["display_name"] == "Project Guide"
        assert data["source_type"] == "local_file"
        assert data["source_path"] == "/data/docs/guide.md"
        assert "id" in data

    def test_add_knowledgebase_with_metadata(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
            "source_type": "url",
            "source_path": "https://example.com/docs",
            "display_name": "External Docs",
            "metadata": {"category": "reference", "priority": 1},
        })
        assert resp.status_code == 201
        assert resp.json()["metadata"]["category"] == "reference"

    def test_add_knowledgebase_with_exclusions(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
            "source_type": "context_file",
            "source_path": "/ctx/notes.md",
            "display_name": "Notes",
            "excluded_sources": [1, 2, 3],
        })
        assert resp.status_code == 201
        assert resp.json()["excluded_sources"] == [1, 2, 3]

    def test_add_knowledgebase_all_source_types(self, client: TestClient, workspace_id: str):
        """All valid source types should be accepted."""
        for src_type in ["local_file", "url", "indexed_document", "context_file", "vector_index"]:
            resp = client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
                "source_type": src_type,
                "source_path": f"/path/{src_type}",
                "display_name": f"KB {src_type}",
            })
            assert resp.status_code == 201, f"Failed for source_type={src_type}"

    def test_add_knowledgebase_appears_in_list(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
            "source_type": "local_file",
            "source_path": "/data/listed.md",
            "display_name": "Listed KB",
        })
        kb_id = resp.json()["id"]

        list_resp = client.get(f"/api/workspaces/{workspace_id}/knowledgebases")
        assert list_resp.status_code == 200
        kb_ids = [k["id"] for k in list_resp.json()]
        assert kb_id in kb_ids

    def test_add_knowledgebase_creates_audit_entry(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
            "source_type": "local_file",
            "source_path": "/data/audit-test.md",
            "display_name": "Audit Test KB",
        })
        assert resp.status_code == 201
        kb_id = resp.json()["id"]

        audit_resp = client.get(f"/api/workspaces/{workspace_id}/audit-log")
        assert audit_resp.status_code == 200
        entries = audit_resp.json()["entries"]
        kb_entries = [e for e in entries if e["entity_id"] == kb_id]
        assert len(kb_entries) >= 1
        assert kb_entries[0]["entity_type"] == "knowledgebase"
        assert kb_entries[0]["change_type"] == "added"


class TestUpdateKnowledgebase:
    """Tests for PUT /api/workspaces/{id}/knowledgebases/{kb_id}."""

    def _create_kb(self, client: TestClient, workspace_id: str) -> dict:
        resp = client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
            "source_type": "local_file",
            "source_path": "/data/original.md",
            "display_name": "Original KB",
        })
        assert resp.status_code == 201
        return resp.json()

    def test_update_display_name(self, client: TestClient, workspace_id: str):
        kb = self._create_kb(client, workspace_id)
        resp = client.put(
            f"/api/workspaces/{workspace_id}/knowledgebases/{kb['id']}",
            json={"display_name": "Updated KB"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Updated KB"

    def test_update_source_path(self, client: TestClient, workspace_id: str):
        kb = self._create_kb(client, workspace_id)
        resp = client.put(
            f"/api/workspaces/{workspace_id}/knowledgebases/{kb['id']}",
            json={"source_path": "/data/new-path.md"},
        )
        assert resp.status_code == 200
        assert resp.json()["source_path"] == "/data/new-path.md"

    def test_update_source_type(self, client: TestClient, workspace_id: str):
        kb = self._create_kb(client, workspace_id)
        resp = client.put(
            f"/api/workspaces/{workspace_id}/knowledgebases/{kb['id']}",
            json={"source_type": "url"},
        )
        assert resp.status_code == 200
        assert resp.json()["source_type"] == "url"

    def test_update_excluded_sources(self, client: TestClient, workspace_id: str):
        kb = self._create_kb(client, workspace_id)
        resp = client.put(
            f"/api/workspaces/{workspace_id}/knowledgebases/{kb['id']}",
            json={"excluded_sources": [10, 20]},
        )
        assert resp.status_code == 200
        assert resp.json()["excluded_sources"] == [10, 20]

    def test_update_not_found(self, client: TestClient, workspace_id: str):
        resp = client.put(
            f"/api/workspaces/{workspace_id}/knowledgebases/nonexistent-kb-id",
            json={"display_name": "Nope"},
        )
        assert resp.status_code == 404

    def test_update_creates_audit_entry(self, client: TestClient, workspace_id: str):
        kb = self._create_kb(client, workspace_id)
        client.put(
            f"/api/workspaces/{workspace_id}/knowledgebases/{kb['id']}",
            json={"display_name": "Audited Update"},
        )
        audit_resp = client.get(f"/api/workspaces/{workspace_id}/audit-log")
        assert audit_resp.status_code == 200
        entries = audit_resp.json()["entries"]
        update_entries = [
            e for e in entries
            if e["entity_id"] == kb["id"] and e["change_type"] == "updated"
        ]
        assert len(update_entries) >= 1


class TestDeleteKnowledgebase:
    """Tests for DELETE /api/workspaces/{id}/knowledgebases/{kb_id}."""

    def _create_kb(self, client: TestClient, workspace_id: str) -> dict:
        resp = client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
            "source_type": "local_file",
            "source_path": "/data/delete-me.md",
            "display_name": "Delete Me KB",
        })
        assert resp.status_code == 201
        return resp.json()

    def test_delete_success(self, client: TestClient, workspace_id: str):
        kb = self._create_kb(client, workspace_id)
        resp = client.delete(f"/api/workspaces/{workspace_id}/knowledgebases/{kb['id']}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_removes_from_list(self, client: TestClient, workspace_id: str):
        kb = self._create_kb(client, workspace_id)
        client.delete(f"/api/workspaces/{workspace_id}/knowledgebases/{kb['id']}")

        list_resp = client.get(f"/api/workspaces/{workspace_id}/knowledgebases")
        assert list_resp.status_code == 200
        kb_ids = [k["id"] for k in list_resp.json()]
        assert kb["id"] not in kb_ids

    def test_delete_not_found(self, client: TestClient, workspace_id: str):
        resp = client.delete(f"/api/workspaces/{workspace_id}/knowledgebases/nonexistent-kb-id")
        assert resp.status_code == 404

    def test_delete_creates_audit_entry(self, client: TestClient, workspace_id: str):
        kb = self._create_kb(client, workspace_id)
        client.delete(f"/api/workspaces/{workspace_id}/knowledgebases/{kb['id']}")

        audit_resp = client.get(f"/api/workspaces/{workspace_id}/audit-log")
        assert audit_resp.status_code == 200
        entries = audit_resp.json()["entries"]
        remove_entries = [
            e for e in entries
            if e["entity_id"] == kb["id"] and e["change_type"] == "removed"
        ]
        assert len(remove_entries) >= 1


# ============================================================================
# Context Endpoints
# Validates: Requirements 29.9, 29.10
# ============================================================================


class TestGetContext:
    """Tests for GET /api/workspaces/{id}/context."""

    def test_get_context_returns_content_key(self, client: TestClient, workspace_id: str):
        resp = client.get(f"/api/workspaces/{workspace_id}/context")
        assert resp.status_code == 200
        assert "content" in resp.json()

    def test_get_context_nonexistent_workspace(self, client: TestClient):
        resp = client.get("/api/workspaces/nonexistent-ws-id/context")
        assert resp.status_code == 404


class TestUpdateContext:
    """Tests for PUT /api/workspaces/{id}/context."""

    def test_update_context_success(self, client: TestClient, workspace_id: str):
        resp = client.put(f"/api/workspaces/{workspace_id}/context", json={
            "content": "# My Workspace\n\nThis is the project context.",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_update_then_get_context(self, client: TestClient, workspace_id: str):
        """Updated context should be retrievable."""
        content = "# Updated Context\n\nNew content here."
        client.put(f"/api/workspaces/{workspace_id}/context", json={"content": content})

        resp = client.get(f"/api/workspaces/{workspace_id}/context")
        assert resp.status_code == 200
        assert resp.json()["content"] == content

    def test_update_context_nonexistent_workspace(self, client: TestClient):
        resp = client.put("/api/workspaces/nonexistent-ws-id/context", json={
            "content": "nope",
        })
        assert resp.status_code == 404


class TestCompressContext:
    """Tests for POST /api/workspaces/{id}/context/compress."""

    def test_compress_context_success(self, client: TestClient, workspace_id: str):
        client.put(f"/api/workspaces/{workspace_id}/context", json={
            "content": "# Project\n\nSome context to compress.",
        })

        resp = client.post(f"/api/workspaces/{workspace_id}/context/compress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "compressed"
        assert "content" in data

    def test_compress_empty_context(self, client: TestClient, workspace_id: str):
        resp = client.post(f"/api/workspaces/{workspace_id}/context/compress")
        assert resp.status_code == 200

    def test_compress_nonexistent_workspace(self, client: TestClient):
        resp = client.post("/api/workspaces/nonexistent-ws-id/context/compress")
        assert resp.status_code == 404


# ============================================================================
# Audit Log Endpoints
# Validates: Requirement 25.5
# ============================================================================


class TestGetAuditLog:
    """Tests for GET /api/workspaces/{id}/audit-log."""

    def test_get_audit_log_empty(self, client: TestClient, workspace_id: str):
        resp = client.get(f"/api/workspaces/{workspace_id}/audit-log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["entries"] == []
        assert data["total"] == 0
        assert data["has_more"] is False

    def test_get_audit_log_after_kb_add(self, client: TestClient, workspace_id: str):
        """Adding a knowledgebase should produce an audit entry."""
        client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
            "source_type": "url",
            "source_path": "https://example.com",
            "display_name": "Example",
        })

        resp = client.get(f"/api/workspaces/{workspace_id}/audit-log")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["entries"]) >= 1

    def test_audit_log_entry_fields(self, client: TestClient, workspace_id: str):
        """Audit entries should have all required fields."""
        client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
            "source_type": "local_file",
            "source_path": "/data/fields-test.md",
            "display_name": "Fields Test",
        })

        resp = client.get(f"/api/workspaces/{workspace_id}/audit-log")
        assert resp.status_code == 200
        entry = resp.json()["entries"][0]
        assert "id" in entry
        assert "workspace_id" in entry
        assert "change_type" in entry
        assert "entity_type" in entry
        assert "entity_id" in entry
        assert "changed_by" in entry
        assert "changed_at" in entry

    def test_audit_log_pagination_limit(self, client: TestClient, workspace_id: str):
        for i in range(5):
            client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
                "source_type": "local_file",
                "source_path": f"/data/page-{i}.md",
                "display_name": f"Page KB {i}",
            })

        resp = client.get(f"/api/workspaces/{workspace_id}/audit-log?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 2
        assert data["total"] >= 5
        assert data["has_more"] is True

    def test_audit_log_pagination_offset(self, client: TestClient, workspace_id: str):
        for i in range(4):
            client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
                "source_type": "local_file",
                "source_path": f"/data/off-{i}.md",
                "display_name": f"Offset KB {i}",
            })

        all_resp = client.get(f"/api/workspaces/{workspace_id}/audit-log?limit=100")
        total = all_resp.json()["total"]

        offset_resp = client.get(f"/api/workspaces/{workspace_id}/audit-log?offset=2&limit=100")
        assert offset_resp.status_code == 200
        assert len(offset_resp.json()["entries"]) == total - 2

    def test_audit_log_scoped_to_workspace(
        self, client: TestClient, workspace_id: str, second_workspace_id: str
    ):
        """Audit log only returns entries for the requested workspace."""
        client.post(f"/api/workspaces/{workspace_id}/knowledgebases", json={
            "source_type": "local_file",
            "source_path": "/data/ws1.md",
            "display_name": "WS1 KB",
        })
        client.post(f"/api/workspaces/{second_workspace_id}/knowledgebases", json={
            "source_type": "local_file",
            "source_path": "/data/ws2.md",
            "display_name": "WS2 KB",
        })

        resp = client.get(f"/api/workspaces/{workspace_id}/audit-log")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        assert all(e["workspace_id"] == workspace_id for e in entries)


# ============================================================================
# Privileged Capability Confirmation
# Validates: Requirements 16.2, 16.11, 17.2, 17.11
# ============================================================================


class TestPrivilegedCapabilities:
    """Tests for privileged skill/MCP handling."""

    @pytest.mark.anyio
    async def test_privileged_skill_flag_in_response(self, client: TestClient, workspace_id: str):
        """Privileged skills are flagged in GET response."""
        swarmws_id = await _ensure_default_workspace()
        safe_id = await _seed_skill("SafeSkill", is_privileged=False)
        priv_id = await _seed_skill("PrivilegedSkill", is_privileged=True)
        await _enable_skill_in_both(swarmws_id, workspace_id, safe_id)
        await _enable_skill_in_both(swarmws_id, workspace_id, priv_id)

        resp = client.get(f"/api/workspaces/{workspace_id}/skills")
        assert resp.status_code == 200
        data = resp.json()

        safe = [s for s in data if s["skill_id"] == safe_id]
        priv = [s for s in data if s["skill_id"] == priv_id]
        assert len(safe) == 1
        assert safe[0]["is_privileged"] is False
        assert len(priv) == 1
        assert priv[0]["is_privileged"] is True

    @pytest.mark.anyio
    async def test_privileged_mcp_flag_in_response(self, client: TestClient, workspace_id: str):
        """Privileged MCPs are flagged in GET response."""
        swarmws_id = await _ensure_default_workspace()
        safe_id = await _seed_mcp("SafeMCP", is_privileged=False)
        priv_id = await _seed_mcp("PrivilegedMCP", is_privileged=True)
        await _enable_mcp_in_both(swarmws_id, workspace_id, safe_id)
        await _enable_mcp_in_both(swarmws_id, workspace_id, priv_id)

        resp = client.get(f"/api/workspaces/{workspace_id}/mcps")
        assert resp.status_code == 200
        data = resp.json()

        safe = [m for m in data if m["mcp_server_id"] == safe_id]
        priv = [m for m in data if m["mcp_server_id"] == priv_id]
        assert len(safe) == 1
        assert safe[0]["is_privileged"] is False
        assert len(priv) == 1
        assert priv[0]["is_privileged"] is True

    @pytest.mark.anyio
    async def test_privileged_skill_can_be_toggled(self, client: TestClient, workspace_id: str):
        """Privileged skills can be enabled/disabled via PUT."""
        swarmws_id = await _ensure_default_workspace()
        priv_id = await _seed_skill("TogglePriv", is_privileged=True)
        await _seed_workspace_skill(swarmws_id, priv_id, enabled=True)
        await _seed_workspace_skill(workspace_id, priv_id, enabled=False)

        # Enable
        resp = client.put(f"/api/workspaces/{workspace_id}/skills", json={
            "configs": [{
                "skill_id": priv_id,
                "skill_name": "TogglePriv",
                "enabled": True,
                "is_privileged": True,
            }]
        })
        assert resp.status_code == 200
        enabled = [s for s in resp.json() if s["skill_id"] == priv_id]
        assert len(enabled) == 1
        assert enabled[0]["enabled"] is True

        # Disable
        resp = client.put(f"/api/workspaces/{workspace_id}/skills", json={
            "configs": [{
                "skill_id": priv_id,
                "skill_name": "TogglePriv",
                "enabled": False,
                "is_privileged": True,
            }]
        })
        assert resp.status_code == 200
        disabled = [s for s in resp.json() if s["skill_id"] == priv_id]
        assert len(disabled) == 0  # Not in effective set after disabling
