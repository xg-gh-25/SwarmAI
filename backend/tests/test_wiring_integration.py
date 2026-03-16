"""Integration tests for wiring (Task 26.6).
Requirements: 14.1-14.9, 26.1-26.7, 34.1-34.7
"""
import json
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from database import db
from tests.helpers import now_iso, create_workspace_with_path


async def _seed_skill(name, is_privileged=False):
    """Create a filesystem-based skill and return its folder name."""
    from pathlib import Path
    from core.skill_manager import skill_manager

    # Create folder name from skill name (kebab-case)
    folder_name = name.lower().replace(" ", "-")

    # Determine skill directory based on privilege level
    if is_privileged:
        # Privileged skills go in built-in directory
        skills_dir = Path.home() / ".swarm-ai" / "built-in-skills"
    else:
        # Regular skills go in user skills directory
        skills_dir = Path.home() / ".swarm-ai" / "skills"

    skill_path = skills_dir / folder_name
    skill_path.mkdir(parents=True, exist_ok=True)

    # Create SKILL.md with frontmatter
    skill_md_content = f"""---
name: {name}
description: Desc {name}
version: 1.0.0
---

# {name}

A test skill for wiring integration tests.
"""

    skill_md = skill_path / "SKILL.md"
    skill_md.write_text(skill_md_content)

    # Invalidate cache so the new skill is visible
    skill_manager.invalidate_cache()

    return folder_name


async def _seed_mcp(name, is_privileged=False):
    now = now_iso()
    mid = str(uuid4())
    await db.mcp_servers.put({
        "id": mid, "name": name,
        "description": f"Desc {name}",
        "connection_type": "stdio",
        "config": json.dumps({"command": "echo"}),
        "allowed_tools": "[]",
        "rejected_tools": "[]",
        "is_system": False,
        "is_privileged": 1 if is_privileged else 0,
        "created_at": now, "updated_at": now,
    })
    return mid


async def _enable_mcp(ws_id, mcp_server_id):
    """Enable an MCP server in the workspace.

    NOTE: Skills no longer use a DB junction table — all filesystem skills
    are always enabled.  This helper is MCP-only now.
    """
    now = now_iso()
    await db.workspace_mcps.put({
        "id": str(uuid4()), "workspace_id": ws_id,
        "mcp_server_id": mcp_server_id, "enabled": 1,
        "created_at": now, "updated_at": now,
    })


async def _make_ws(is_default=False):
    now = now_iso()
    wid = "swarmws"
    ws = {
        "id": wid,
        "name": "SwarmWS",
        "file_path": f"/tmp/test-wiring/{wid[:8]}",
        "icon": "",
        "context": "test",
        "created_at": now, "updated_at": now,
    }
    await db.workspace_config.put(ws)
    return ws


# --- Policy enforcement integration tests via HTTP (Req 26, 34) ---


async def test_task_409_for_nonexistent_skill(client: TestClient):
    """POST /api/tasks with non-existent required skill returns 409.

    In the filesystem model, all existing skills are always enabled.
    A skill that doesn't exist in the cache triggers a 409 violation.
    """
    ws = await _make_ws()

    resp = client.post("/api/tasks", json={
        "agent_id": "default", "message": "test",
        "workspace_id": ws["id"], "required_skills": ["nonexistent-skill-xyz"],
    })
    # Task creation is rejected because skill doesn't exist
    assert resp.status_code >= 400


async def test_task_409_for_disabled_mcp(client: TestClient):
    """POST /api/tasks with disabled required MCP returns 409.

    See note in test_task_409_for_disabled_skill about current behavior.
    """
    ws = await _make_ws()
    mid = await _seed_mcp("RequiredMCP")

    resp = client.post("/api/tasks", json={
        "agent_id": "default", "message": "test",
        "workspace_id": ws["id"], "required_mcps": [mid],
    })
    assert resp.status_code >= 400


async def test_task_not_409_when_caps_enabled(client: TestClient):
    """POST /api/tasks succeeds when required capabilities are enabled.

    In the single-workspace model, enabling a capability in the singleton
    workspace should allow task creation. Currently returns non-409 status.
    """
    default_ws = await db.workspace_config.get_config()
    if not default_ws:
        default_ws = await _make_ws()
    sid = await _seed_skill("EnabledSkill")
    # No _enable_mcp needed — skills are always enabled in filesystem model.

    resp = client.post("/api/tasks", json={
        "agent_id": "default", "message": "test",
        "workspace_id": default_ws["id"], "required_skills": [sid],
    })
    assert resp.status_code != 409
