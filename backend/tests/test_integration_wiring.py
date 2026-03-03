"""Integration tests for wiring (Task 26.6).
Requirements: 14.1-14.9, 26.1-26.7, 34.1-34.7
"""
import json
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from database import db
from core.context_manager import ContextManager
from tests.helpers import now_iso, create_workspace_with_path


async def _seed_skill(name, is_privileged=False):
    """Create a filesystem-based skill and return its folder name."""
    from pathlib import Path
    
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

A test skill for integration wiring tests.
"""
    
    skill_md = skill_path / "SKILL.md"
    skill_md.write_text(skill_md_content)
    
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
