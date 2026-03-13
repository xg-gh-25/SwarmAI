"""Default agent bootstrap and system configuration.

This module handles the lifecycle of the default SwarmAgent — the system agent
that is automatically created on first launch and updated on subsequent startups.

Bootstrap flow:
1. ``ensure_default_agent`` is called by ``initialization_manager`` at startup.
2. MCP servers are now file-based (``.claude/mcps/mcp-catalog.json`` and
   ``mcp-dev.json``).  No DB registration.  Migration from legacy files
   happens in ``mcp_migration.py``.  Catalog merge happens in
   ``mcp_config_loader.merge_catalog_template()``.

Skills are filesystem-based (see ``skill_manager.py``). Built-in skills live
in ``backend/skills/`` and are always available without explicit listing. The
``allowed_skills`` field on agent records is a list of folder names (not DB UUIDs).

Key public symbols:

- ``DEFAULT_AGENT_ID``                    — Constant ID for the default agent
- ``SWARM_AGENT_NAME``                    — Hardcoded system agent display name
- ``ensure_default_agent``                — Idempotent agent creation / update
- ``get_default_agent``                   — Fetch default agent from DB
- ``expand_allowed_skills_with_plugins``  — Combine allowed_skills with plugin folder names
"""

from datetime import datetime
from pathlib import Path
import json
import logging

from database import db
from utils.bundle_paths import get_resources_dir

logger = logging.getLogger(__name__)

# Default agent ID constant
DEFAULT_AGENT_ID: str = "default"

# SwarmAgent name constant - hardcoded, cannot be changed by users
SWARM_AGENT_NAME: str = "SwarmAgent"


def _get_resources_dir() -> Path:
    """Get the resources directory path.

    See utils.bundle_paths for Tauri bundle structure documentation.
    """
    backend_dir = Path(__file__).resolve().parent.parent
    project_root = backend_dir.parent
    dev_resources = project_root / "desktop" / "resources"
    return get_resources_dir(dev_resources)


async def get_default_agent() -> dict | None:
    """Get the default agent from the database.
    
    Returns:
        The default agent dict or None if not found
    """
    agent = await db.agents.get(DEFAULT_AGENT_ID)
    return agent


async def ensure_default_agent(skip_registration: bool = False) -> dict:
    """Ensure the default agent exists, creating it if necessary.
    
    Called during application startup. Loads configuration from
    desktop/resources/default-agent.json and creates the agent
    with associated skills and MCP servers.
    
    Args:
        skip_registration: If True, skip skill/MCP registration (used during
            quick validation when we know resources already exist).
    
    Returns:
        The default agent configuration dict
    """
    resources_dir = _get_resources_dir()

    # NOTE: MCP servers are now file-based (.claude/mcps/). No DB registration needed.
    # Migration and catalog merge happen in initialization_manager.

    # Check if default agent already exists
    existing = await db.agents.get(DEFAULT_AGENT_ID)
    if existing:
        needs_update = (
            existing.get("name") != SWARM_AGENT_NAME or
            not existing.get("is_system_agent")
        )

        if needs_update:
            await db.agents.update(DEFAULT_AGENT_ID, {
                "name": SWARM_AGENT_NAME,
                "is_system_agent": True,
            })
            logger.info(f"Updated default agent name/flags")
            existing = await db.agents.get(DEFAULT_AGENT_ID)
        else:
            logger.info(f"Default agent up to date: {existing.get('name')}")

        return existing

    logger.info("Creating default agent...")

    # Load default agent configuration
    config_path = resources_dir / "default-agent.json"
    if not config_path.exists():
        logger.error(f"Default agent config not found: {config_path}")
        raise FileNotFoundError(f"Default agent configuration missing: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        agent_config = json.load(f)

    # System prompt loaded from .context/SWARMAI.md at session start — not stored in DB.
    now = datetime.now().isoformat()
    agent_data = {
        "id": DEFAULT_AGENT_ID,
        "name": SWARM_AGENT_NAME,
        "description": agent_config.get("description", "Your AI Team, 24/7"),
        "model": None,  # Resolved at runtime from config.json
        "permission_mode": agent_config.get("permission_mode", "default"),
        "max_turns": agent_config.get("max_turns", 100),
        "system_prompt": "",
        "is_default": True,
        "is_system_agent": True,
        "allowed_skills": [],
        "allow_all_skills": agent_config.get("allow_all_skills", True),
        "mcp_ids": [],  # MCP servers now file-based, not DB-bound
        "enable_bash_tool": agent_config.get("enable_bash_tool", True),
        "enable_file_tools": agent_config.get("enable_file_tools", True),
        "enable_web_tools": agent_config.get("enable_web_tools", True),
        "global_user_mode": agent_config.get("global_user_mode", True),
        "enable_human_approval": agent_config.get("enable_human_approval", True),
        "sandbox_enabled": agent_config.get("sandbox_enabled", True),
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }

    await db.agents.put(agent_data)
    logger.info(f"Default agent created: {agent_data['name']}")

    return agent_data


# _register_default_mcp_servers() — REMOVED.
# MCP servers are now file-based (.claude/mcps/). See mcp_config_loader.py.


async def expand_allowed_skills_with_plugins(
    allowed_skills: list[str],
    plugin_ids: list[str],
    allow_all_skills: bool = False,
) -> list[str]:
    """Combine explicit allowed_skills with plugin skill folder names.

    Plugin skills are discovered from ~/.swarm-ai/plugin-skills/ via
    the SkillManager. No database lookups.

    Args:
        allowed_skills: Explicit list of skill folder names from agent config.
        plugin_ids: List of plugin IDs whose skills should be included.
        allow_all_skills: If True, skip expansion (all skills already allowed).

    Returns:
        A deduplicated list: explicit skills first, then plugin skills.
    """
    if allow_all_skills or not plugin_ids:
        return list(allowed_skills)

    seen = set(allowed_skills)
    effective = list(allowed_skills)

    # Import here to avoid circular imports
    from core.skill_manager import skill_manager
    cache = await skill_manager.get_cache()

    for folder_name, info in cache.items():
        if info.source_tier == "plugin" and folder_name not in seen:
            seen.add(folder_name)
            effective.append(folder_name)

    return effective
