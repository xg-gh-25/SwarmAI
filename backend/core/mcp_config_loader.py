"""MCP file-based configuration loader.

Replaces the DB-backed ``build_mcp_config()`` and
``merge_user_local_mcp_servers()`` in ``mcp_config_builder.py`` with a
deterministic two-layer JSON file system.  Two files in
``.claude/mcps/`` become the single source of truth:

- ``mcp-catalog.json`` — Product-seeded catalog entries (git-tracked,
  team-shared).  Users toggle ``enabled`` and set ``env`` values.
  Entries default to ``enabled: false``.
- ``mcp-dev.json`` — User-owned personal/dev MCPs and plugin-installed
  MCPs (git-ignored).  Full CRUD.  Entries default to ``enabled: true``.

Key public symbols:

- ``get_mcp_file_paths``        — Return ``(catalog_path, dev_path)`` for a
  workspace.
- ``read_layer``                — Read and parse a single layer file.
- ``merge_layers``              — Merge two layers; dev overrides catalog by
  ``id``, filters out ``enabled=False``.
- ``add_mcp_server_to_dict``    — Add a single MCP entry with dedup
  (moved from ``mcp_config_builder.py``, unchanged logic).
- ``inject_channel_mcp``        — Add channel-specific MCP server
  (moved from ``mcp_config_builder.py``, unchanged logic).
- ``load_mcp_config``           — Synchronous entry point called by
  ``PromptBuilder._build_mcp_config()``.  Reads both layers, merges,
  converts via ``add_mcp_server_to_dict()``, returns
  ``(mcp_servers, disallowed_tools)``.

All functions are synchronous — only file I/O, no DB access.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from utils.bundle_paths import get_python_executable
from utils.mcp_validation import validate_config_entry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_MCP_DIR = ".claude/mcps"
_CATALOG_FILENAME = "mcp-catalog.json"
_DEV_FILENAME = "mcp-dev.json"


def get_mcp_file_paths(workspace_path: Path) -> tuple[Path, Path]:
    """Return ``(catalog_path, dev_path)`` for the workspace.

    Files live at ``.claude/mcps/mcp-catalog.json`` and
    ``.claude/mcps/mcp-dev.json`` relative to *workspace_path*.
    """
    mcps_dir = workspace_path / _MCP_DIR
    return mcps_dir / _CATALOG_FILENAME, mcps_dir / _DEV_FILENAME


# ---------------------------------------------------------------------------
# Layer reading
# ---------------------------------------------------------------------------


def read_layer(path: Path, default_enabled: bool) -> list[dict]:
    """Read and parse a single JSON layer file.

    Returns a list of Config_Entry dicts.  Each entry that lacks an
    explicit ``enabled`` key gets *default_enabled* applied:

    - Catalog layer passes ``default_enabled=False`` (opt-in).
    - Dev layer passes ``default_enabled=True`` (opt-out).

    Returns ``[]`` on missing file or invalid JSON (logs a warning).
    """
    if not path.exists():
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Cannot read MCP layer file %s: %s", path, exc)
        return []

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Invalid JSON in MCP layer file %s: %s", path, exc)
        return []

    if not isinstance(data, list):
        logger.warning(
            "MCP layer file %s: expected a JSON array, got %s",
            path,
            type(data).__name__,
        )
        return []

    for entry in data:
        if isinstance(entry, dict) and "enabled" not in entry:
            entry["enabled"] = default_enabled

    return data


# ---------------------------------------------------------------------------
# Layer merging
# ---------------------------------------------------------------------------


def merge_layers(
    catalog_entries: list[dict],
    dev_entries: list[dict],
) -> list[dict]:
    """Merge two layers.  Dev entries override catalog entries by ``id``.

    1. Build an ordered dict from catalog entries keyed by ``id``.
    2. Overlay dev entries — same ``id`` replaces the catalog entry.
    3. Filter out any entry where ``enabled`` is explicitly ``False``.

    Returns the final list of enabled Config_Entry dicts.
    """
    merged: dict[str, dict] = {}

    for entry in catalog_entries:
        entry_id = entry.get("id")
        if entry_id:
            merged[entry_id] = entry

    for entry in dev_entries:
        entry_id = entry.get("id")
        if entry_id:
            merged[entry_id] = entry

    return [
        entry for entry in merged.values()
        if entry.get("enabled") is not False
    ]


# ---------------------------------------------------------------------------
# Catalog template merge (called once at app startup)
# ---------------------------------------------------------------------------

def merge_catalog_template(
    workspace_path: Path,
    template_path: Path,
) -> None:
    """Merge bundled catalog template into the user's ``mcp-catalog.json``.

    Called **once** at app startup (from ``initialization_manager``), NOT
    per-session.

    Behaviour:
    - Appends new entries (by ``id``) with ``enabled: false``.
    - Updates entries where ``template._version > existing._version``,
      preserving the user's ``enabled`` and ``config.env`` values.
    - Writes only when changes are detected (new entries added or
      version-bumped entries updated).
    - Skips silently if *template_path* does not exist.
    - If ``mcp-catalog.json`` does not exist yet, seeds it from the
      template (all entries with ``enabled: false``).
    - Creates ``.claude/mcps/`` directory if needed.
    """
    # ------------------------------------------------------------------
    # 1. Read the bundled template
    # ------------------------------------------------------------------
    if not template_path.exists():
        return

    try:
        template_text = template_path.read_text(encoding="utf-8")
        template_entries: list[dict] = json.loads(template_text)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Cannot read catalog template %s: %s", template_path, exc,
        )
        return

    if not isinstance(template_entries, list):
        logger.warning(
            "Catalog template %s: expected a JSON array, got %s",
            template_path,
            type(template_entries).__name__,
        )
        return

    # ------------------------------------------------------------------
    # 2. Read the user's existing catalog (or start empty)
    # ------------------------------------------------------------------
    catalog_path, _ = get_mcp_file_paths(workspace_path)

    existing_entries: list[dict] = []
    if catalog_path.exists():
        try:
            catalog_text = catalog_path.read_text(encoding="utf-8")
            existing_entries = json.loads(catalog_text)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Cannot read catalog file %s: %s", catalog_path, exc,
            )
            existing_entries = []

        if not isinstance(existing_entries, list):
            logger.warning(
                "Catalog file %s: expected a JSON array, got %s",
                catalog_path,
                type(existing_entries).__name__,
            )
            existing_entries = []

    # Build a lookup of existing entries by id for O(1) access.
    existing_by_id: dict[str, dict] = {}
    for entry in existing_entries:
        eid = entry.get("id")
        if eid:
            existing_by_id[eid] = entry

    # ------------------------------------------------------------------
    # 3. Merge: append new, update version-bumped
    # ------------------------------------------------------------------
    changed = False

    for tpl_entry in template_entries:
        tpl_id = tpl_entry.get("id")
        if not tpl_id:
            continue

        if tpl_id not in existing_by_id:
            # New entry — seed with enabled: false
            new_entry = dict(tpl_entry)
            new_entry["enabled"] = False
            existing_entries.append(new_entry)
            existing_by_id[tpl_id] = new_entry
            changed = True
        else:
            # Existing entry — check version bump
            existing = existing_by_id[tpl_id]
            tpl_version = tpl_entry.get("_version", 0)
            existing_version = existing.get("_version", 0)

            if tpl_version > existing_version:
                # Preserve user's enabled and config.env
                user_enabled = existing.get("enabled")
                user_env = (
                    existing.get("config", {}).get("env")
                    if isinstance(existing.get("config"), dict)
                    else None
                )

                # Update all fields from template
                idx = existing_entries.index(existing)
                updated = dict(tpl_entry)

                # Restore user customizations
                if user_enabled is not None:
                    updated["enabled"] = user_enabled
                if user_env is not None:
                    if "config" not in updated or not isinstance(updated.get("config"), dict):
                        updated["config"] = {}
                    updated["config"]["env"] = user_env

                existing_entries[idx] = updated
                existing_by_id[tpl_id] = updated
                changed = True

    # ------------------------------------------------------------------
    # 4. Write back only when changes detected (atomic write)
    # ------------------------------------------------------------------
    if not changed:
        return

    # Ensure directory exists
    catalog_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = catalog_path.with_suffix(catalog_path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(existing_entries, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(catalog_path))
    except OSError as exc:
        logger.error(
            "Failed to write catalog file %s: %s", catalog_path, exc,
        )
        # Clean up tmp file if it exists
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Moved from mcp_config_builder.py — unchanged logic
# ---------------------------------------------------------------------------


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
        expanded_args = []
        for arg in raw_args:
            expanded = os.path.expandvars(arg)
            # Warn if env var expansion left a $VAR placeholder unexpanded
            if "$" in expanded and expanded != arg:
                # Partial expansion — some vars resolved, some didn't
                pass
            elif "$" in expanded and expanded == arg:
                # No expansion happened — likely undefined env var
                logger.warning(
                    "MCP server '%s': arg '%s' contains unexpanded env var",
                    server_name, arg,
                )
            expanded_args.append(expanded)

        command = config.get("command")
        if not command:
            logger.warning("MCP server '%s': missing 'command' in config, skipping", server_name)
            return

        mcp_servers[server_name] = {
            "type": "stdio",
            "command": command,
            "args": expanded_args,
        }
        env = config.get("env")
        if env and isinstance(env, dict):
            # Expand env var references in env values too
            expanded_env = {}
            for k, v in env.items():
                if isinstance(v, str):
                    expanded_v = os.path.expandvars(v)
                    if "$" in v and expanded_v == v:
                        logger.warning(
                            "MCP server '%s': env var '%s' value '%s' contains unexpanded reference",
                            server_name, k, v,
                        )
                    expanded_env[k] = expanded_v
                else:
                    expanded_env[k] = v
            mcp_servers[server_name]["env"] = expanded_env
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

    if channel_type == "slack":
        env_vars.update({
            "SLACK_BOT_TOKEN": channel_context.get("bot_token", ""),
            "SLACK_CHANNEL_ID": channel_context.get("chat_id", ""),
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def load_mcp_config(
    workspace_path: Path,
    enable_mcp: bool,
) -> tuple[dict, list[str]]:
    """Load MCP server configuration from the two-layer file system.

    Synchronous entry point called by
    ``PromptBuilder._build_mcp_config()``.  No DB access — only reads
    two JSON files from ``.claude/mcps/``.

    Steps:
      1. If *enable_mcp* is ``False``, return empty results immediately.
      2. Read catalog layer (``mcp-catalog.json``, default enabled=False).
      3. Read dev layer (``mcp-dev.json``, default enabled=True).
      4. Merge layers (dev overrides catalog by ``id``, filter disabled).
      5. Convert each enabled entry via ``add_mcp_server_to_dict()``.

    Returns:
        Tuple of ``(mcp_servers, disallowed_tools)`` in the format
        expected by ``ClaudeAgentOptions``.
    """
    mcp_servers: dict = {}
    disallowed_tools: list[str] = []

    if not enable_mcp:
        return mcp_servers, disallowed_tools

    catalog_path, dev_path = get_mcp_file_paths(workspace_path)

    catalog_entries = read_layer(catalog_path, default_enabled=False)
    dev_entries = read_layer(dev_path, default_enabled=True)

    enabled_entries = merge_layers(catalog_entries, dev_entries)

    used_names: set = set()
    for entry in enabled_entries:
        errors = validate_config_entry(entry)
        if errors:
            entry_id = entry.get("id", "<unknown>")
            logger.warning(
                "Skipping invalid MCP entry '%s': %s",
                entry_id,
                "; ".join(errors),
            )
            continue
        add_mcp_server_to_dict(
            entry, mcp_servers, disallowed_tools, used_names,
        )

    return mcp_servers, disallowed_tools


# ---------------------------------------------------------------------------
# Plugin MCP helpers
# ---------------------------------------------------------------------------


def write_plugin_mcps(
    workspace_path: Path,
    mcp_data: dict,
    plugin_id: str,
) -> list[str]:
    """Convert ``.mcp.json`` mcpServers entries to Config_Entry objects.

    Appends to ``mcp-dev.json`` with ``source: "plugin"`` and
    ``plugin_id``.  Skips entries whose ``id`` already exists from a
    different source (logs warning).

    Format conversion::

        {"mcpServers": {"name": {"command": "x", "args": [...], "env": {...}}}}

    becomes::

        {"id": "name", "name": "name", "connection_type": "stdio",
         "config": {"command": "x", "args": [...], "env": {...}},
         "source": "plugin", "plugin_id": "my-plugin", "enabled": true}

    Returns list of written server names.
    """
    _, dev_path = get_mcp_file_paths(workspace_path)
    existing = read_layer(dev_path, default_enabled=True)
    existing_ids = {
        e.get("id"): e.get("source", "user") for e in existing
    }

    written: list[str] = []
    for server_name, server_cfg in mcp_data.get("mcpServers", {}).items():
        if server_name in existing_ids:
            if existing_ids[server_name] != "plugin":
                logger.warning(
                    "Plugin '%s': MCP '%s' already exists (source=%s), skipping",
                    plugin_id, server_name, existing_ids[server_name],
                )
            continue

        entry = {
            "id": server_name,
            "name": server_name,
            "connection_type": "stdio",
            "config": dict(server_cfg),
            "enabled": True,
            "source": "plugin",
            "plugin_id": plugin_id,
        }
        existing.append(entry)
        written.append(server_name)

    if written:
        dev_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = dev_path.with_suffix(dev_path.suffix + ".tmp")
        try:
            tmp.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(str(tmp), str(dev_path))
        except OSError:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
        logger.info(
            "Plugin '%s': wrote %d MCP servers to mcp-dev.json",
            plugin_id, len(written),
        )

    return written


def remove_plugin_mcps(workspace_path: Path, plugin_id: str) -> int:
    """Remove all entries from ``mcp-dev.json`` where ``plugin_id`` matches.

    Called by ``PluginManager.uninstall_plugin()``.
    Returns count of removed entries.
    """
    _, dev_path = get_mcp_file_paths(workspace_path)
    if not dev_path.exists():
        return 0

    existing = read_layer(dev_path, default_enabled=True)
    before = len(existing)
    filtered = [
        e for e in existing
        if e.get("plugin_id") != plugin_id
    ]
    removed = before - len(filtered)

    if removed > 0:
        tmp = dev_path.with_suffix(dev_path.suffix + ".tmp")
        try:
            tmp.write_text(
                json.dumps(filtered, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(str(tmp), str(dev_path))
        except OSError:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise
        logger.info(
            "Plugin '%s': removed %d MCP servers from mcp-dev.json",
            plugin_id, removed,
        )

    return removed
