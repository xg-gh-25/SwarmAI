"""Default agent bootstrap: creation and MCP server registration.

This module handles the lifecycle of the default SwarmAgent — the system agent
that is automatically created on first launch and updated on subsequent startups.

Bootstrap flow:
1. ``ensure_default_agent`` is called by ``initialization_manager`` at startup.
2. Default MCP servers are registered from ``desktop/resources/default-mcp-servers.json``.
3. All system MCPs are bound to the default agent record in the database.
4. If the agent already exists, new MCP resources are merged (user additions preserved).

Skills are now filesystem-based (see ``skill_manager.py``). Built-in skills live
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
from config import settings
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
    
    if not skip_registration:
        # Register default MCP servers (only during full initialization)
        mcp_config_path = resources_dir / "default-mcp-servers.json"
        if mcp_config_path.exists():
            mcp_ids = await _register_default_mcp_servers(mcp_config_path)
            if mcp_ids:
                logger.info(f"Ensured {len(mcp_ids)} default MCP servers are registered")
        else:
            logger.warning(f"Default MCP servers config not found: {mcp_config_path}")
    
    # Query system MCP servers from database
    all_system_mcps = await db.mcp_servers.list_by_system()
    mcp_ids = [mcp["id"] for mcp in all_system_mcps]
    
    logger.info(f"Found {len(mcp_ids)} system MCPs to bind to SwarmAgent")
    
    # Check if default agent already exists
    existing = await db.agents.get(DEFAULT_AGENT_ID)
    if existing:
        # Update existing agent with any new default MCPs
        existing_mcp_ids = set(existing.get("mcp_ids", []))
        new_mcp_ids = set(mcp_ids)
        
        # Merge new default MCPs into existing (preserve user additions)
        updated_mcp_ids = list(existing_mcp_ids | new_mcp_ids)
        
        # Check if we need to update MCPs or system agent properties
        needs_update = (
            set(updated_mcp_ids) != existing_mcp_ids or
            existing.get("name") != SWARM_AGENT_NAME or
            not existing.get("is_system_agent")
        )
        
        if needs_update:
            await db.agents.update(DEFAULT_AGENT_ID, {
                "mcp_ids": updated_mcp_ids,
                "name": SWARM_AGENT_NAME,  # Ensure hardcoded name
                "is_system_agent": True,  # Ensure system agent flag
            })
            logger.info(f"Updated default agent with new MCPs: mcps={len(updated_mcp_ids)}")
            # Refresh the existing agent data
            existing = await db.agents.get(DEFAULT_AGENT_ID)
        else:
            logger.info(f"Default agent already exists: {existing.get('name')}")
        
        return existing
    
    logger.info("Creating default agent...")
    
    # Load default agent configuration
    config_path = resources_dir / "default-agent.json"
    if not config_path.exists():
        logger.error(f"Default agent config not found: {config_path}")
        raise FileNotFoundError(f"Default agent configuration missing: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        agent_config = json.load(f)
    
    # System prompt is now loaded from ~/.swarm-ai/.context/SWARMAI.md
    # at session start by ContextDirectoryLoader — not stored in DB.
    
    # Create the default agent (mcp_ids already collected above)
    now = datetime.now().isoformat()
    agent_data = {
        "id": DEFAULT_AGENT_ID,
        "name": SWARM_AGENT_NAME,  # Hardcoded system agent name
        "description": agent_config.get("description", "Your AI Team, 24/7"),
        "model": None,  # Model resolved at runtime from config.json, not stored in DB
        "permission_mode": agent_config.get("permission_mode", "default"),
        "max_turns": agent_config.get("max_turns", 100),
        "system_prompt": "",  # Loaded from .context/SWARMAI.md at session start
        "is_default": True,
        "is_system_agent": True,  # Mark as protected system agent
        "allowed_skills": [],  # Built-in skills always available without explicit listing
        "allow_all_skills": agent_config.get("allow_all_skills", True),
        "mcp_ids": mcp_ids,
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
    
    # Save to database
    await db.agents.put(agent_data)
    logger.info(f"Default agent created: {agent_data['name']}")
    
    return agent_data


async def _register_default_mcp_servers(config_path: Path) -> list[str]:
    """Register default MCP servers from configuration file.
    
    Args:
        config_path: Path to default-mcp-servers.json
        
    Returns:
        List of registered MCP server IDs
    """
    mcp_ids = []
    
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            mcp_configs = json.load(f)
        
        for mcp_config in mcp_configs:
            mcp_id = mcp_config.get("id", f"default-{mcp_config.get('name', 'mcp').lower()}")
            
            # Check if MCP server already exists
            existing = await db.mcp_servers.get(mcp_id)
            if existing:
                # Sync system MCP fields from config on every startup.
                # This ensures returning users pick up new defaults
                # (e.g. rejected_tools, config changes like $HOME path).
                updates: dict = {}
                if not existing.get("is_system"):
                    updates["is_system"] = True
                new_rejected = mcp_config.get("rejected_tools", [])
                if existing.get("rejected_tools") != new_rejected:
                    updates["rejected_tools"] = new_rejected
                new_cfg = mcp_config.get("config", {})
                if existing.get("config") != new_cfg:
                    updates["config"] = new_cfg
                if updates:
                    await db.mcp_servers.update(mcp_id, updates)
                    logger.debug(f"Synced system MCP server fields: {mcp_id} → {list(updates.keys())}")
                mcp_ids.append(mcp_id)
                continue
            
            # Create MCP server record
            now = datetime.now().isoformat()
            mcp_data = {
                "id": mcp_id,
                "name": mcp_config.get("name", "MCP Server"),
                "description": mcp_config.get("description", ""),
                "connection_type": mcp_config.get("connection_type", "stdio"),
                "config": mcp_config.get("config", {}),
                "rejected_tools": mcp_config.get("rejected_tools", []),
                "source_type": "system",
                "is_system": True,
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            }
            
            await db.mcp_servers.put(mcp_data)
            mcp_ids.append(mcp_id)
            logger.debug(f"Registered default MCP server: {mcp_data['name']}")
            
    except Exception as e:
        logger.error(f"Failed to register MCP servers: {e}")
    
    return mcp_ids


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
