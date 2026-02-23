"""Default agent bootstrap: creation, skill registration, MCP server registration.

This module handles the lifecycle of the default SwarmAgent — the system agent
that is automatically created on first launch and updated on subsequent startups.

Bootstrap flow:
1. ``ensure_default_agent`` is called by ``initialization_manager`` at startup.
2. Default skills are registered from ``desktop/resources/default-skills/``.
3. Default MCP servers are registered from ``desktop/resources/default-mcp-servers.json``.
4. All system skills/MCPs are bound to the default agent record in the database.
5. If the agent already exists, new system resources are merged (user additions preserved).

Helper ``expand_skill_ids_with_plugins`` is used at runtime to combine explicit
skill IDs with skills contributed by selected plugins.
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


def _get_templates_dir() -> Path:
    """Get the templates directory path."""
    return Path(__file__).resolve().parent.parent / "templates"


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
        # Register default skills (only during full initialization)
        skills_dir = resources_dir / "default-skills"
        if skills_dir.exists():
            skill_ids = await _register_default_skills(skills_dir)
            if skill_ids:
                logger.info(f"Ensured {len(skill_ids)} default skills are registered")
        else:
            logger.warning(f"Default skills directory not found: {skills_dir}")
        
        # Register default MCP servers (only during full initialization)
        mcp_config_path = resources_dir / "default-mcp-servers.json"
        if mcp_config_path.exists():
            mcp_ids = await _register_default_mcp_servers(mcp_config_path)
            if mcp_ids:
                logger.info(f"Ensured {len(mcp_ids)} default MCP servers are registered")
        else:
            logger.warning(f"Default MCP servers config not found: {mcp_config_path}")
    
    # Query ALL system resources from database (not just the ones we registered above)
    # This ensures that if a new system skill/MCP is added to the resources folder,
    # it gets bound on restart without requiring code changes
    all_system_skills = await db.skills.list_by_system()
    all_system_mcps = await db.mcp_servers.list_by_system()
    
    # Extract IDs from system resources
    skill_ids = [skill["id"] for skill in all_system_skills]
    mcp_ids = [mcp["id"] for mcp in all_system_mcps]
    
    logger.info(f"Found {len(skill_ids)} system skills and {len(mcp_ids)} system MCPs to bind to SwarmAgent")
    
    # Check if default agent already exists
    existing = await db.agents.get(DEFAULT_AGENT_ID)
    if existing:
        # Update existing agent with any new default skills/MCPs
        existing_skill_ids = set(existing.get("skill_ids", []))
        existing_mcp_ids = set(existing.get("mcp_ids", []))
        new_skill_ids = set(skill_ids)
        new_mcp_ids = set(mcp_ids)
        
        # Merge new defaults into existing (preserve user additions)
        updated_skill_ids = list(existing_skill_ids | new_skill_ids)
        updated_mcp_ids = list(existing_mcp_ids | new_mcp_ids)
        
        # Check if we need to update skills/MCPs or system agent properties
        needs_update = (
            set(updated_skill_ids) != existing_skill_ids or
            set(updated_mcp_ids) != existing_mcp_ids or
            existing.get("name") != SWARM_AGENT_NAME or
            not existing.get("is_system_agent")
        )
        
        if needs_update:
            await db.agents.update(DEFAULT_AGENT_ID, {
                "skill_ids": updated_skill_ids,
                "mcp_ids": updated_mcp_ids,
                "name": SWARM_AGENT_NAME,  # Ensure hardcoded name
                "is_system_agent": True,  # Ensure system agent flag
            })
            logger.info(f"Updated default agent with new skills/MCPs: skills={len(updated_skill_ids)}, mcps={len(updated_mcp_ids)}")
            # Refresh the existing agent data
            existing = await db.agents.get(DEFAULT_AGENT_ID)
        else:
            logger.info(f"Default agent already exists: {existing.get('name')}")
        
        return existing
    
    logger.info("Creating default agent...")
    templates_dir = _get_templates_dir()
    
    # Load default agent configuration
    config_path = resources_dir / "default-agent.json"
    if not config_path.exists():
        logger.error(f"Default agent config not found: {config_path}")
        raise FileNotFoundError(f"Default agent configuration missing: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        agent_config = json.load(f)
    
    # Load system prompt from SWARMAI.md template
    template_path = templates_dir / "SWARMAI.md"
    system_prompt = ""
    if template_path.exists():
        content = template_path.read_text(encoding="utf-8")
        # Skip YAML frontmatter if present
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                system_prompt = parts[2].strip()
            else:
                system_prompt = content
        else:
            system_prompt = content
        logger.info("Loaded SWARMAI.md system prompt template")
    else:
        logger.warning(f"SWARMAI.md template not found: {template_path}")
    
    # Create the default agent (skill_ids and mcp_ids already collected above)
    now = datetime.now().isoformat()
    agent_data = {
        "id": DEFAULT_AGENT_ID,
        "name": SWARM_AGENT_NAME,  # Hardcoded system agent name
        "description": agent_config.get("description", "Your AI Team, 24/7"),
        "model": agent_config.get("model", settings.default_model),
        "permission_mode": agent_config.get("permission_mode", "default"),
        "max_turns": agent_config.get("max_turns", 100),
        "system_prompt": system_prompt,
        "is_default": True,
        "is_system_agent": True,  # Mark as protected system agent
        "skill_ids": skill_ids,
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


async def _register_default_skills(skills_dir: Path) -> list[str]:
    """Register default skills from the skills directory.
    
    Args:
        skills_dir: Path to directory containing SKILL.md files
        
    Returns:
        List of registered skill IDs
    """
    skill_ids = []
    
    for skill_file in skills_dir.glob("*.md"):
        try:
            content = skill_file.read_text(encoding="utf-8")
            
            # Parse YAML frontmatter
            metadata = {}
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    import yaml
                    metadata = yaml.safe_load(parts[1]) or {}
            
            skill_name = metadata.get("name", skill_file.stem)
            skill_id = f"default-{skill_name.lower().replace(' ', '-')}"
            
            # Check if skill already exists
            existing = await db.skills.get(skill_id)
            if existing:
                # Update existing record to ensure is_system=True (Requirement 7.4)
                if not existing.get("is_system"):
                    await db.skills.update(skill_id, {"is_system": True})
                    logger.debug(f"Updated existing skill with is_system=True: {skill_id}")
                skill_ids.append(skill_id)
                continue
            
            # Create skill record
            now = datetime.now().isoformat()
            skill_data = {
                "id": skill_id,
                "name": skill_name,
                "description": metadata.get("description", ""),
                "folder_name": skill_file.stem.lower(),
                "local_path": str(skill_file),
                "source_type": "system",
                "version": metadata.get("version", "1.0.0"),
                "is_system": True,
                "created_at": now,
                "updated_at": now,
            }
            
            await db.skills.put(skill_data)
            skill_ids.append(skill_id)
            logger.debug(f"Registered default skill: {skill_name}")
            
        except Exception as e:
            logger.error(f"Failed to register skill {skill_file}: {e}")
    
    return skill_ids


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
                # Update existing record to ensure is_system=True (Requirement 7.4)
                if not existing.get("is_system"):
                    await db.mcp_servers.update(mcp_id, {"is_system": True})
                    logger.debug(f"Updated existing MCP server with is_system=True: {mcp_id}")
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


async def expand_skill_ids_with_plugins(
    skill_ids: list[str],
    plugin_ids: list[str],
    allow_all_skills: bool = False,
) -> list[str]:
    """Combine explicit skill_ids with skills from selected plugins.

    Returns a deduplicated list preserving order: explicit skills first,
    then plugin skills. Skips expansion when allow_all_skills is True.
    """
    if allow_all_skills or not plugin_ids:
        return list(skill_ids)

    seen = set(skill_ids)
    effective = list(skill_ids)
    for plugin_id in plugin_ids:
        plugin_skills = await db.skills.list_by_source_plugin(plugin_id)
        for skill in plugin_skills:
            sid = skill.get("id")
            if sid and sid not in seen:
                seen.add(sid)
                effective.append(sid)
    return effective
