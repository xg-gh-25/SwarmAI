"""Default agent bootstrap and system configuration.

Architecture: The default agent's runtime config is **file-based**, assembled
fresh on every session from two sources:

1. ``default-agent.json``  — Behavioral defaults (permission_mode, tool enables, etc.)
2. ``config.json``         — Runtime settings via AppConfigManager (model, sandbox, etc.)

The SQLite ``agents`` table retains a minimal **marker row** for the default agent,
used only for existence checks (``agent_exists``, ``quick_validation``).  It is
never read as a config source.  Custom (non-default) agents are still fully DB-driven.

Key public symbols:

- ``DEFAULT_AGENT_ID``                    — Constant ID for the default agent
- ``SWARM_AGENT_NAME``                    — Hardcoded system agent display name
- ``build_agent_config``                  — Build fresh runtime config (files for default, DB for custom)
- ``agent_exists``                        — Lightweight existence check (True for default, DB for custom)
- ``ensure_default_agent``                — Idempotent DB marker creation at startup
- ``get_default_agent``                   — [deprecated] Fetch stale DB record
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


def resolve_default_model() -> str | None:
    """Resolve the default model name from config.json.

    Centralised fallback for when ``agent_config.get("model")`` is ``None``
    (e.g. stale DB records for custom agents).  Zero-IO: AppConfigManager
    keeps an in-memory cache.

    Returns:
        Model name string (e.g. ``"claude-opus-4-6"``) or ``None``.
    """
    try:
        from core.app_config_manager import AppConfigManager
        return AppConfigManager.instance().get("default_model")
    except (ImportError, AttributeError, KeyError, TypeError):
        return None


# Cache for default-agent.json — read once, reused across calls.
# Invalidated only on process restart (file changes require restart).
_default_agent_json_cache: dict | None = None


def _load_default_agent_json() -> dict | None:
    """Load and cache default-agent.json from the resources directory.

    Returns the parsed dict, or None if the file is missing/corrupt.
    Caches the result in module-level ``_default_agent_json_cache``.
    """
    global _default_agent_json_cache
    if _default_agent_json_cache is not None:
        return _default_agent_json_cache

    resources_dir = _get_resources_dir()
    config_path = resources_dir / "default-agent.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            _default_agent_json_cache = json.load(f)
        return _default_agent_json_cache
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.error("Failed to read default-agent.json: %s", exc)
        return None


async def get_default_agent() -> dict | None:
    """Get the default agent config (file-based, always fresh).

    .. deprecated:: Prefer ``build_agent_config(DEFAULT_AGENT_ID)`` directly.
        This is a thin wrapper kept for backward compatibility with existing
        callers (e.g. ``routers/system.py``).

    Returns:
        The freshly assembled default agent config, or None if misconfigured.
    """
    return await build_agent_config(DEFAULT_AGENT_ID)


async def agent_exists(agent_id: str) -> bool:
    """Check whether an agent exists (lightweight, no config assembly).

    For the default agent this always returns True (it's built from files,
    the DB record is just a startup marker).  For custom agents it checks
    the database.

    Use this for guard clauses in routers where you only need to reject
    invalid agent IDs — never as a way to fetch config.
    """
    if agent_id == DEFAULT_AGENT_ID:
        return True
    return (await db.agents.get(agent_id)) is not None


async def build_agent_config(agent_id: str) -> dict | None:
    """Build a fresh agent config dict for runtime use.

    For the default agent (``agent_id == "default"``):
      1. Reads ``default-agent.json`` from the resources dir (behavior defaults).
      2. Overlays runtime settings from ``config.json`` via AppConfigManager
         (model, sandbox settings).
      3. Adds standard bookkeeping fields expected by agent_manager consumers.

    For non-default agents: falls back to ``db.agents.get(agent_id)``.

    This replaces the old pattern of ``db.agents.get("default")`` which returned
    a stale DB snapshot that was never re-synced after first creation.

    Returns:
        Agent config dict ready for ``_execute_on_session``, or None if not found.
    """
    if agent_id != DEFAULT_AGENT_ID:
        return await db.agents.get(agent_id)

    # --- Default agent: build from files, not DB ---
    base = _load_default_agent_json()
    if base is None:
        logger.error("default-agent.json unavailable — falling back to DB")
        return await db.agents.get(agent_id)

    # Overlay runtime settings from config.json (zero IO — cached in memory).
    # Import at call-time to avoid circular imports at module load.
    from core.app_config_manager import AppConfigManager
    cfg = AppConfigManager.instance()

    config = {
        # Identity
        "id": DEFAULT_AGENT_ID,
        "name": SWARM_AGENT_NAME,
        "description": base.get("description", "Your AI Team, 24/7"),
        "is_default": True,
        "is_system_agent": True,
        "status": "active",

        # Behavior from default-agent.json
        "permission_mode": base.get("permission_mode", "default"),
        "max_turns": base.get("max_turns", 100),
        "enable_bash_tool": base.get("enable_bash_tool", True),
        "enable_file_tools": base.get("enable_file_tools", True),
        "enable_web_tools": base.get("enable_web_tools", True),
        "global_user_mode": base.get("global_user_mode", True),
        "enable_human_approval": base.get("enable_human_approval", True),
        "sandbox_enabled": base.get("sandbox_enabled", True),
        "allow_all_skills": base.get("allow_all_skills", True),

        # Runtime from config.json
        "model": cfg.get("default_model"),

        # Standard fields — read from default-agent.json with safe defaults
        "system_prompt": base.get("system_prompt", ""),
        "allowed_skills": base.get("allowed_skills", []),
        "mcp_ids": base.get("mcp_ids", []),
        "allowed_tools": base.get("allowed_tools", []),
        "plugin_ids": base.get("plugin_ids", []),
        "enable_tool_logging": base.get("enable_tool_logging", False),
        "context_token_budget": base.get("context_token_budget"),
        "project_id": base.get("project_id"),
        "allowed_directories": base.get("allowed_directories", []),
        "add_dirs": base.get("add_dirs"),
    }

    return config


async def ensure_default_agent(skip_registration: bool = False) -> dict:
    """Ensure the default agent DB marker exists.

    Called during application startup.  The DB record is a **minimal marker**
    used only for existence checks (``agent_exists``, ``quick_validation``).
    All runtime configuration is assembled fresh by ``build_agent_config``
    from ``default-agent.json`` + ``config.json`` — never from the DB record.

    Args:
        skip_registration: Legacy arg, kept for API compat. Ignored.

    Returns:
        The freshly assembled default agent config (from ``build_agent_config``).
    """
    # Ensure the DB marker row exists
    existing = await db.agents.get(DEFAULT_AGENT_ID)
    if existing:
        # Keep name and is_system_agent in sync (cosmetic only)
        needs_update = (
            existing.get("name") != SWARM_AGENT_NAME or
            not existing.get("is_system_agent")
        )
        if needs_update:
            await db.agents.update(DEFAULT_AGENT_ID, {
                "name": SWARM_AGENT_NAME,
                "is_system_agent": True,
            })
            logger.info("Updated default agent DB marker (name/flags)")
        else:
            logger.info("Default agent DB marker up to date")
    else:
        logger.info("Creating default agent DB marker...")
        now = datetime.now().isoformat()
        marker = {
            "id": DEFAULT_AGENT_ID,
            "name": SWARM_AGENT_NAME,
            "description": "SwarmAI — Your AI Team, 24/7",
            "model": None,           # Runtime config comes from config.json
            "permission_mode": "bypassPermissions",
            "system_prompt": "",
            "is_default": True,
            "is_system_agent": True,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        await db.agents.put(marker)
        logger.info("Default agent DB marker created")

    # Return the REAL runtime config (file-based, always fresh)
    config = await build_agent_config(DEFAULT_AGENT_ID)

    # --- Startup consistency check ---
    # Log the resolved config chain so misconfigurations are visible in logs.
    if config:
        from core.app_config_manager import AppConfigManager
        cfg = AppConfigManager.instance()
        _model = cfg.get("default_model")
        _bedrock = cfg.get("use_bedrock", False)
        _perm = config.get("permission_mode")
        _sandbox = config.get("sandbox_enabled")
        logger.info(
            "Agent config chain: model=%s, bedrock=%s, permission=%s, sandbox=%s",
            _model, _bedrock, _perm, _sandbox,
        )

    return config


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
