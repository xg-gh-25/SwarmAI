"""MCP server configuration builder.

Extracted from ``agent_manager.py`` to isolate MCP server configuration
concerns.  Builds the ``mcp_servers`` dict and ``disallowed_tools`` list
for ``ClaudeAgentOptions`` from three sources:

1. DB-registered MCPs (system, plugin) via agent's ``mcp_ids``
2. Source-tree ``user-mcp-servers.json`` (dev convenience)
3. App-data ``~/.swarm-ai/user-mcp-servers.json`` (runtime config)

Key public symbols:

- ``build_mcp_config``          — Async entry point, merges all sources
- ``add_mcp_server_to_dict``    — Add a single MCP entry with dedup
- ``merge_user_local_mcp_servers`` — Load user-local MCP files
- ``inject_channel_mcp``        — Add channel-specific MCP server

All symbols are re-exported by ``agent_manager.py`` as methods on
``AgentManager`` for backward compatibility.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from config import get_app_data_dir
from database import db
from utils.bundle_paths import get_python_executable

logger = logging.getLogger(__name__)


async def build_mcp_config(
    agent_config: dict,
    enable_mcp: bool,
) -> tuple[dict, list[str]]:
    """Build MCP server configuration from DB mcp_ids + user-local file.

    Three sources, merged in order:
    1. Agent's ``mcp_ids`` → looked up in DB (system/plugin MCPs).
    2. ``<repo>/desktop/resources/user-mcp-servers.json`` → source tree.
    3. ``~/.swarm-ai/user-mcp-servers.json`` → runtime user config.
    Source-tree entries win on name collision with app-data entries.

    Per-server ``rejected_tools`` are converted to the SDK's global
    ``disallowed_tools`` format (``mcp__<ServerName>__<tool>``).

    Returns:
        Tuple of (mcp_servers dict, disallowed_tools list).
    """
    mcp_servers: dict = {}
    disallowed_tools: list[str] = []

    if not enable_mcp:
        return mcp_servers, disallowed_tools

    used_names: set = set()

    # --- Source 1: DB-registered MCPs (system, plugin) via mcp_ids ---
    for mcp_id in agent_config.get("mcp_ids", []):
        mcp_config = await db.mcp_servers.get(mcp_id)
        if not mcp_config:
            continue
        if mcp_config.get("disabled"):
            logger.info("Skipping disabled MCP server: %s", mcp_config.get("name", mcp_id))
            continue
        add_mcp_server_to_dict(mcp_config, mcp_servers, disallowed_tools, used_names)

    # --- Source 2+3: User-local MCPs from file (no DB needed) ---
    merge_user_local_mcp_servers(mcp_servers, disallowed_tools, used_names)

    return mcp_servers, disallowed_tools


def add_mcp_server_to_dict(
    mcp_config: dict,
    mcp_servers: dict,
    disallowed_tools: list[str],
    used_names: set,
) -> None:
    """Add a single MCP server entry to the mcp_servers dict.

    Handles name collision, connection type dispatch, env expansion,
    and rejected_tools → disallowed_tools conversion.
    """
    connection_type = mcp_config.get("connection_type", "stdio")
    config = mcp_config.get("config", {})

    server_name = mcp_config.get("name", mcp_config.get("id", "unknown"))
    base_name = server_name
    suffix = 1
    while server_name in used_names:
        server_name = f"{base_name}_{suffix}"
        suffix += 1
    used_names.add(server_name)

    if connection_type == "stdio":
        raw_args = config.get("args", [])
        expanded_args = [os.path.expandvars(a) for a in raw_args]
        mcp_servers[server_name] = {
            "type": "stdio",
            "command": config.get("command"),
            "args": expanded_args,
        }
        env = config.get("env")
        if env and isinstance(env, dict):
            mcp_servers[server_name]["env"] = env
    elif connection_type == "sse":
        mcp_servers[server_name] = {
            "type": "sse",
            "url": config.get("url"),
        }
    elif connection_type == "http":
        mcp_servers[server_name] = {
            "type": "http",
            "url": config.get("url"),
        }

    rejected = mcp_config.get("rejected_tools") or []
    for tool in rejected:
        disallowed_tools.append(f"mcp__{server_name}__{tool}")


def merge_user_local_mcp_servers(
    mcp_servers: dict,
    disallowed_tools: list[str],
    used_names: set,
) -> None:
    """Load user-local MCP servers from config files.

    Two locations checked in order (earlier wins on name collision):
    1. Source tree — ``<repo>/desktop/resources/user-mcp-servers.json``
    2. App data — ``~/.swarm-ai/user-mcp-servers.json``
    """
    _backend_root = Path(__file__).resolve().parent.parent
    _repo_root = _backend_root.parent
    source_tree_path = _repo_root / "desktop" / "resources" / "user-mcp-servers.json"
    app_data_path = get_app_data_dir() / "user-mcp-servers.json"

    for config_path in (source_tree_path, app_data_path):
        if not config_path.exists():
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                mcp_configs = json.load(f)

            if not isinstance(mcp_configs, list):
                continue

            for entry in mcp_configs:
                name = entry.get("name", entry.get("id"))
                if not name:
                    continue
                if entry.get("disabled"):
                    logger.info(
                        "Skipping disabled user-local MCP: %s (from %s)",
                        name, config_path.name,
                    )
                    continue
                if name in used_names:
                    logger.debug(
                        "User-local MCP '%s' already loaded, skipping (from %s)",
                        name, config_path,
                    )
                    continue
                add_mcp_server_to_dict(entry, mcp_servers, disallowed_tools, used_names)
                logger.info("Loaded user-local MCP: %s (from %s)", name, config_path.name)

        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in %s: %s", config_path, e)
        except Exception as e:
            logger.error("Failed to load user-local MCPs from %s: %s", config_path, e)


def inject_channel_mcp(
    mcp_servers: dict,
    channel_context: Optional[dict],
    working_directory: str,
) -> dict:
    """Inject channel-specific MCP servers when running in a channel context.

    When ``channel_context`` is provided, a ``channel-tools`` MCP server
    entry is added so the agent can interact with the originating channel.

    Returns:
        The (possibly updated) mcp_servers dict.
    """
    if not channel_context:
        return mcp_servers

    channel_type = channel_context.get("channel_type", "")
    env_vars = {
        "CHANNEL_TYPE": channel_type,
        "WORKSPACE_DIR": working_directory,
    }

    if channel_type == "feishu":
        env_vars.update({
            "FEISHU_APP_ID": channel_context.get("app_id", ""),
            "FEISHU_APP_SECRET": channel_context.get("app_secret", ""),
            "CHAT_ID": channel_context.get("chat_id", ""),
        })
        reply_to = channel_context.get("reply_to_message_id")
        if reply_to:
            env_vars["REPLY_TO_MESSAGE_ID"] = reply_to

    mcp_script = Path(__file__).resolve().parent.parent / "mcp_servers" / "channel_file_sender.py"
    if mcp_script.exists():
        mcp_servers["channel-tools"] = {
            "type": "stdio",
            "command": get_python_executable(),
            "args": [str(mcp_script)],
            "env": env_vars,
        }
    else:
        logger.warning(f"Channel-tools MCP script not found: {mcp_script}")

    return mcp_servers
