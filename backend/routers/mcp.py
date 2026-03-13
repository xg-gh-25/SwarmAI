"""MCP file-based configuration validation endpoints.

Replaces the DB-backed CRUD router with thin file-based endpoints.
All mutations go through server-side validation before writing to
``.claude/mcps/mcp-catalog.json`` or ``.claude/mcps/mcp-dev.json``.

Endpoints:
- ``GET  /mcp``                — Merged view from both layers
- ``GET  /mcp/catalog``        — Raw catalog layer entries
- ``PATCH /mcp/catalog/{id}``  — Toggle enabled / update env
- ``GET  /mcp/dev``            — Raw dev layer entries
- ``POST /mcp/dev``            — Create dev entry
- ``PUT  /mcp/dev/{id}``       — Update dev entry
- ``DELETE /mcp/dev/{id}``     — Delete non-plugin dev entry
"""

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter

from core.mcp_config_loader import (
    get_mcp_file_paths,
    read_layer,
    merge_layers,
)
from core.exceptions import ValidationException
from schemas.mcp import (
    CatalogUpdateRequest,
    DevCreateRequest,
    DevUpdateRequest,
    ConfigEntryResponse,
)
from utils.mcp_validation import (
    validate_env_no_system_db,
    validate_config_entry,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_workspace_path() -> Path:
    """Resolve the SwarmWS workspace path."""
    from config import get_app_data_dir
    return get_app_data_dir() / "SwarmWS"


def _atomic_write_json(path: Path, data: list[dict]) -> None:
    """Write JSON list to file using atomic tmp+replace pattern."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(path))
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def _entry_to_response(entry: dict, layer: str) -> dict:
    """Convert a raw Config_Entry dict to ConfigEntryResponse fields."""
    return {
        "id": entry.get("id", ""),
        "name": entry.get("name", ""),
        "description": entry.get("description"),
        "connection_type": entry.get("connection_type", "stdio"),
        "config": entry.get("config", {}),
        "enabled": entry.get("enabled", layer == "dev"),
        "rejected_tools": entry.get("rejected_tools"),
        "category": entry.get("category"),
        "source": entry.get("source"),
        "plugin_id": entry.get("plugin_id"),
        "layer": layer,
        "required_env": entry.get("required_env"),
        "optional_env": entry.get("optional_env"),
        "presets": entry.get("presets"),
    }


# ---------------------------------------------------------------------------
# GET endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_merged_mcps() -> list[ConfigEntryResponse]:
    """Return all entries from both layers, merged (dev overrides catalog)."""
    ws = _get_workspace_path()
    cat_path, dev_path = get_mcp_file_paths(ws)
    cat = read_layer(cat_path, default_enabled=False)
    dev = read_layer(dev_path, default_enabled=True)
    merged = merge_layers(cat, dev)

    # Tag each entry with its layer for the response
    dev_ids = {e.get("id") for e in dev}
    result = []
    for entry in merged:
        eid = entry.get("id")
        layer = "dev" if eid in dev_ids else "catalog"
        result.append(_entry_to_response(entry, layer))
    return result


@router.get("/catalog")
async def list_catalog() -> list[ConfigEntryResponse]:
    """Return raw catalog layer entries."""
    ws = _get_workspace_path()
    cat_path, _ = get_mcp_file_paths(ws)
    entries = read_layer(cat_path, default_enabled=False)
    return [_entry_to_response(e, "catalog") for e in entries]


@router.get("/dev")
async def list_dev() -> list[ConfigEntryResponse]:
    """Return raw dev layer entries."""
    ws = _get_workspace_path()
    _, dev_path = get_mcp_file_paths(ws)
    entries = read_layer(dev_path, default_enabled=True)
    return [_entry_to_response(e, "dev") for e in entries]


# ---------------------------------------------------------------------------
# PATCH catalog
# ---------------------------------------------------------------------------

@router.patch("/catalog/{entry_id}")
async def update_catalog_entry(
    entry_id: str, update: CatalogUpdateRequest,
) -> ConfigEntryResponse:
    """Update enabled/env on a catalog entry."""
    ws = _get_workspace_path()
    cat_path, _ = get_mcp_file_paths(ws)
    entries = read_layer(cat_path, default_enabled=False)

    target = None
    for entry in entries:
        if entry.get("id") == entry_id:
            target = entry
            break

    if target is None:
        raise ValidationException(
            message="Not found",
            detail=f"Catalog entry '{entry_id}' not found",
            fields=[{"field": "entry_id", "error": "Not found in catalog"}],
        )

    if update.enabled is not None:
        target["enabled"] = update.enabled

    if update.env is not None:
        validate_env_no_system_db(update.env)
        if "config" not in target or not isinstance(target.get("config"), dict):
            target["config"] = {}
        target["config"]["env"] = update.env

    _atomic_write_json(cat_path, entries)
    return ConfigEntryResponse(**_entry_to_response(target, "catalog"))


# ---------------------------------------------------------------------------
# Dev CRUD
# ---------------------------------------------------------------------------

@router.post("/dev", status_code=201)
async def create_dev_entry(request: DevCreateRequest) -> ConfigEntryResponse:
    """Create a new dev entry."""
    ws = _get_workspace_path()
    _, dev_path = get_mcp_file_paths(ws)

    entry = request.model_dump()
    entry["source"] = "user"

    errors = validate_config_entry(entry)
    if errors:
        raise ValidationException(
            message="Invalid MCP entry",
            detail="; ".join(errors),
            fields=[{"field": "config", "error": e} for e in errors],
        )

    entries = read_layer(dev_path, default_enabled=True)

    # Check for duplicate id
    if any(e.get("id") == entry["id"] for e in entries):
        raise ValidationException(
            message="Duplicate ID",
            detail=f"Dev entry with id '{entry['id']}' already exists",
            fields=[{"field": "id", "error": "Already exists"}],
        )

    entries.append(entry)
    _atomic_write_json(dev_path, entries)
    return ConfigEntryResponse(**_entry_to_response(entry, "dev"))


@router.put("/dev/{entry_id}")
async def update_dev_entry(
    entry_id: str, update: DevUpdateRequest,
) -> ConfigEntryResponse:
    """Update an existing dev entry."""
    ws = _get_workspace_path()
    _, dev_path = get_mcp_file_paths(ws)
    entries = read_layer(dev_path, default_enabled=True)

    target = None
    for entry in entries:
        if entry.get("id") == entry_id:
            target = entry
            break

    if target is None:
        raise ValidationException(
            message="Not found",
            detail=f"Dev entry '{entry_id}' not found",
            fields=[{"field": "entry_id", "error": "Not found"}],
        )

    # Apply partial updates
    updates = update.model_dump(exclude_unset=True)
    for key, value in updates.items():
        target[key] = value

    errors = validate_config_entry(target)
    if errors:
        raise ValidationException(
            message="Invalid MCP entry",
            detail="; ".join(errors),
            fields=[{"field": "config", "error": e} for e in errors],
        )

    _atomic_write_json(dev_path, entries)
    return ConfigEntryResponse(**_entry_to_response(target, "dev"))


@router.delete("/dev/{entry_id}", status_code=204)
async def delete_dev_entry(entry_id: str):
    """Delete a dev entry (non-plugin only)."""
    ws = _get_workspace_path()
    _, dev_path = get_mcp_file_paths(ws)
    entries = read_layer(dev_path, default_enabled=True)

    target = None
    for entry in entries:
        if entry.get("id") == entry_id:
            target = entry
            break

    if target is None:
        raise ValidationException(
            message="Not found",
            detail=f"Dev entry '{entry_id}' not found",
            fields=[{"field": "entry_id", "error": "Not found"}],
        )

    if target.get("source") == "plugin":
        raise ValidationException(
            message="Cannot delete plugin MCP",
            detail="Cannot delete plugin-installed MCP. Uninstall the plugin instead.",
            fields=[{"field": "source", "error": "Plugin entries cannot be deleted directly"}],
        )

    entries.remove(target)
    _atomic_write_json(dev_path, entries)
