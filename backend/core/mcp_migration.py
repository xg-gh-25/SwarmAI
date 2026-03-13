"""One-time migration from DB + legacy JSON files to file-based MCP config.

Converts existing MCP server records from the ``mcp_servers`` DB table
and legacy ``user-mcp-servers.json`` files into the new
``.claude/mcps/mcp-dev.json`` format.

Key public symbols:

- ``migrate_if_needed``  — Idempotent entry point; runs migration only
  when ``mcp-dev.json`` does not yet exist.

Called from ``initialization_manager.run_full_initialization()`` at app
startup, after ``ensure_default_workspace()`` and before
``refresh_builtin_defaults()``.
"""

import json
import logging
import os
from pathlib import Path

from config import get_app_data_dir
from core.mcp_config_loader import get_mcp_file_paths

logger = logging.getLogger(__name__)


async def migrate_if_needed(workspace_path: Path) -> None:
    """Run one-time migration if ``mcp-dev.json`` does not exist.

    Sources (checked in order, deduplicated by id then name):
      1. DB ``mcp_servers`` table (``source_type != 'system'``)
      2. ``~/.swarm-ai/user-mcp-servers.json``
      3. ``desktop/resources/user-mcp-servers.json``
    """
    _, dev_path = get_mcp_file_paths(workspace_path)
    if dev_path.exists():
        return

    entries: list[dict] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()


    def _add_entry(entry: dict, source_label: str) -> bool:
        eid = entry.get("id", "")
        ename = entry.get("name", entry.get("id", ""))
        if eid and eid in seen_ids:
            return False
        if ename and ename in seen_names:
            return False
        if eid:
            seen_ids.add(eid)
        if ename:
            seen_names.add(ename)
        entries.append(entry)
        return True

    # Source 1: DB mcp_servers table (non-system)
    db_count = 0
    try:
        from database import db
        all_mcps = await db.mcp_servers.list()
        for record in all_mcps:
            if record.get("source_type") == "system" or record.get("is_system"):
                continue
            migrated = {
                "id": record.get("id", ""),
                "name": record.get("name", ""),
                "connection_type": record.get("connection_type", "stdio"),
                "config": record.get("config", {}),
                "enabled": True,
                "source": "user",
            }
            if record.get("description"):
                migrated["description"] = record["description"]
            if record.get("rejected_tools"):
                migrated["rejected_tools"] = record["rejected_tools"]
            if _add_entry(migrated, "DB"):
                db_count += 1
    except Exception as exc:
        logger.error("Migration: failed to read DB mcp_servers: %s", exc)


    # Source 2+3: Legacy user-mcp-servers.json files
    _backend_root = Path(__file__).resolve().parent.parent
    _repo_root = _backend_root.parent
    legacy_paths = [
        get_app_data_dir() / "user-mcp-servers.json",
        _repo_root / "desktop" / "resources" / "user-mcp-servers.json",
    ]
    file_count = 0
    for legacy_path in legacy_paths:
        if not legacy_path.exists():
            continue
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                legacy_entries = json.load(f)
            if not isinstance(legacy_entries, list):
                continue
            for entry in legacy_entries:
                name = entry.get("name", entry.get("id"))
                if not name:
                    logger.warning("Migration: skipping entry without name/id in %s", legacy_path.name)
                    continue
                migrated = {
                    "id": entry.get("id", name),
                    "name": name,
                    "connection_type": entry.get("connection_type", "stdio"),
                    "config": entry.get("config", {}),
                    "enabled": not entry.get("disabled", False),
                    "source": "user",
                }
                if entry.get("description"):
                    migrated["description"] = entry["description"]
                if entry.get("rejected_tools"):
                    migrated["rejected_tools"] = entry["rejected_tools"]
                if _add_entry(migrated, legacy_path.name):
                    file_count += 1
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Migration: failed to read %s: %s", legacy_path, exc)

    # Write mcp-dev.json (atomic)
    if not entries:
        logger.info("Migration: no MCP entries to migrate")
        return
    dev_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dev_path.with_suffix(dev_path.suffix + ".tmp")
    try:
        tmp_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp_path), str(dev_path))
    except OSError as exc:
        logger.error("Migration: failed to write %s: %s", dev_path, exc)
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise
    logger.info("Migration complete: %d from DB, %d from legacy files → %s", db_count, file_count, dev_path)
