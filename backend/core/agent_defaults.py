"""Default agent bootstrap: creation and MCP server registration.

This module handles the lifecycle of the default SwarmAgent — the system agent
that is automatically created on first launch and updated on subsequent startups.

Bootstrap flow:
1. ``ensure_default_agent`` is called by ``initialization_manager`` at startup.
2. Default (system) MCP servers are registered in DB from
   ``desktop/resources/default-mcp-servers.json``. These are the only MCPs
   stored in DB and bound to the agent's ``mcp_ids``.
3. User-local MCP servers (``~/.swarm-ai/user-mcp-servers.json``) are loaded
   directly from file at session start by ``AgentManager._build_mcp_config()``.
   They bypass the DB entirely — the file IS the source of truth.

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
    
    # --- Register system MCP servers in DB (the only MCPs that use DB) ---
    system_mcp_ids: list[str] = []
    if not skip_registration:
        mcp_config_path = resources_dir / "default-mcp-servers.json"
        if mcp_config_path.exists():
            system_mcp_ids = await _register_default_mcp_servers(mcp_config_path)
            if system_mcp_ids:
                logger.info(f"Ensured {len(system_mcp_ids)} system MCP servers are registered")
        else:
            logger.warning(f"Default MCP servers config not found: {mcp_config_path}")

    # NOTE: User-local MCPs (user-mcp-servers.json) are NOT registered here.
    # They are loaded directly from file by AgentManager._build_mcp_config()
    # at session start. The file is the source of truth — no DB indirection.

    # Check if default agent already exists
    existing = await db.agents.get(DEFAULT_AGENT_ID)
    if existing:
        existing_mcp_ids = set(existing.get("mcp_ids", []))
        updated_mcp_ids = list(existing_mcp_ids | set(system_mcp_ids))

        # Remove deactivated system MCPs
        for mcp_id in list(updated_mcp_ids):
            record = await db.mcp_servers.get(mcp_id)
            if record and not record.get("is_active", True):
                updated_mcp_ids.remove(mcp_id)
                logger.info(f"Removed deactivated MCP from agent: {mcp_id}")

        needs_update = (
            set(updated_mcp_ids) != existing_mcp_ids or
            existing.get("name") != SWARM_AGENT_NAME or
            not existing.get("is_system_agent")
        )

        if needs_update:
            await db.agents.update(DEFAULT_AGENT_ID, {
                "mcp_ids": updated_mcp_ids,
                "name": SWARM_AGENT_NAME,
                "is_system_agent": True,
            })
            logger.info(f"Updated default agent: mcps={len(updated_mcp_ids)}")
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
        "mcp_ids": system_mcp_ids,  # Only system MCPs; user-local loaded from file
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
            
        # Cleanup: deactivate system MCPs that are no longer in the JSON config.
        # This handles the case where a default MCP is removed between versions
        # (e.g. Filesystem MCP removed because built-in tools cover all its features).
        # Only affects is_system=True records — user-added MCPs are never touched.
        all_system_mcps = await db.mcp_servers.list_by_system()
        active_ids = set(mcp_ids)
        for mcp in all_system_mcps:
            if mcp["id"] not in active_ids:
                await db.mcp_servers.update(mcp["id"], {
                    "is_system": False,
                    "is_active": False,
                })
                logger.info(
                    f"Deactivated stale system MCP: {mcp['id']} "
                    f"(no longer in default-mcp-servers.json)"
                )

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
