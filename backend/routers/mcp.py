"""MCP Server CRUD API endpoints."""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from schemas.mcp import MCPCreateRequest, MCPUpdateRequest, MCPResponse
from config import get_app_data_dir
from database import db
from core.exceptions import (
    MCPServerNotFoundException,
    ValidationException,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Protected-path guard — prevent MCP env vars from targeting system DB
# ---------------------------------------------------------------------------

def _validate_env_no_system_db(env: dict[str, str]) -> None:
    """Reject env vars whose values resolve to SwarmAI's internal database.

    Checks every value that looks like a filesystem path (contains ``/``
    or ``\\``).  Resolves via ``Path.expanduser().resolve()`` and blocks:

    1. Exact match to ``~/.swarm-ai/data.db`` (or WAL/SHM companions).
    2. Any ``.db`` file anywhere inside ``~/.swarm-ai/``.

    This prevents users from accidentally pointing the SQLite MCP (or any
    future MCP) at the live system database, which would cause write-lock
    contention and allow unrestricted SQL (including DROP TABLE).
    """
    if not env:
        return

    app_dir = get_app_data_dir().resolve()
    protected_files = {
        (app_dir / "data.db"),
        (app_dir / "data.db-wal"),
        (app_dir / "data.db-shm"),
    }

    for key, value in env.items():
        # Skip values that don't look like paths
        if "/" not in value and "\\" not in value:
            continue
        try:
            resolved = Path(value).expanduser().resolve()
        except (OSError, ValueError):
            continue

        # Block exact match to system DB files
        if resolved in protected_files:
            raise ValidationException(
                message="Protected path",
                detail=(
                    f"'{key}' points to SwarmAI's system database. "
                    f"Use a separate database file instead."
                ),
                fields=[{
                    "field": f"env.{key}",
                    "error": "Cannot target SwarmAI system database",
                }],
            )

        # Block any .db inside ~/.swarm-ai/
        if resolved.suffix == ".db":
            try:
                is_inside = resolved.is_relative_to(app_dir)
            except (ValueError, TypeError):
                is_inside = False
            if is_inside:
                raise ValidationException(
                    message="Protected path",
                    detail=(
                        f"'{key}' points to a database inside SwarmAI's data "
                        f"directory ({app_dir}). Use a path outside this directory."
                    ),
                    fields=[{
                        "field": f"env.{key}",
                        "error": "Cannot use databases inside SwarmAI data directory",
                    }],
                )


# ---------------------------------------------------------------------------
# Optional MCP catalog (loaded once from bundled resource)
# ---------------------------------------------------------------------------

def _load_optional_catalog() -> list[dict]:
    """Load the optional-mcp-servers.json catalog from resources."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)
        catalog_path = base / "optional-mcp-servers.json"
    else:
        # Dev mode: backend/ is sibling of desktop/
        catalog_path = (
            Path(__file__).resolve().parent.parent.parent
            / "desktop" / "resources" / "optional-mcp-servers.json"
        )
    if not catalog_path.exists():
        logger.debug("Optional MCP catalog not found at %s", catalog_path)
        return []
    try:
        with open(catalog_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to load optional MCP catalog: %s", exc)
        return []


@router.get("/catalog")
async def list_optional_mcp_catalog():
    """Return the catalog of optional MCP servers users can enable.

    Each entry includes required_env, optional_env, presets, and
    setup hints so the frontend can render a guided setup flow.
    Entries already registered in the DB are annotated with
    ``installed: true``.
    """
    catalog = _load_optional_catalog()
    installed_ids = {s["id"] for s in await db.mcp_servers.list()}
    for entry in catalog:
        entry["installed"] = entry["id"] in installed_ids
    return catalog


class MCPInstallRequest(BaseModel):
    """Request to install an optional MCP from the catalog."""
    catalog_id: str
    env: dict[str, str] = {}


@router.post("/catalog/install", response_model=MCPResponse, status_code=201)
async def install_optional_mcp(request: MCPInstallRequest):
    """Install an optional MCP server from the catalog.

    Looks up the catalog entry by ``catalog_id``, merges user-provided
    ``env`` vars into the config, and registers it in the database.
    """
    catalog = _load_optional_catalog()
    entry = next((e for e in catalog if e["id"] == request.catalog_id), None)
    if not entry:
        raise ValidationException(
            message="Unknown catalog entry",
            detail=f"No optional MCP with id '{request.catalog_id}' in catalog",
            fields=[{"field": "catalog_id", "error": "Not found in catalog"}],
        )

    # Check not already installed
    existing = await db.mcp_servers.get(entry["id"])
    if existing:
        raise ValidationException(
            message="Already installed",
            detail=f"MCP server '{entry['name']}' is already installed",
            fields=[{"field": "catalog_id", "error": "Already installed"}],
        )

    # Validate env vars don't target system DB
    _validate_env_no_system_db(request.env)

    # Build config with env vars
    config = dict(entry.get("config", {}))
    if request.env:
        config["env"] = request.env

    now = datetime.now().isoformat()
    server_data = {
        "id": entry["id"],
        "name": entry["name"],
        "description": entry.get("description", ""),
        "connection_type": entry.get("connection_type", "stdio"),
        "config": config,
        "source_type": "marketplace",
        "is_system": False,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    server = await db.mcp_servers.put(server_data)
    logger.info("Installed optional MCP: %s", entry["name"])
    return server


@router.get("", response_model=list[MCPResponse])
async def list_mcp_servers():
    """List all MCP servers."""
    return await db.mcp_servers.list()


@router.get("/{mcp_id}", response_model=MCPResponse)
async def get_mcp_server(mcp_id: str):
    """Get a specific MCP server by ID."""
    server = await db.mcp_servers.get(mcp_id)
    if not server:
        raise MCPServerNotFoundException(
            detail=f"MCP server with ID '{mcp_id}' does not exist",
            suggested_action="Please check the MCP server ID and try again"
        )
    return server


@router.post("", response_model=MCPResponse, status_code=201)
async def create_mcp_server(request: MCPCreateRequest):
    """Create a new MCP server configuration."""
    # Validate env vars don't target system DB
    if request.config.get("env"):
        _validate_env_no_system_db(request.config["env"])

    # Validate config based on connection type
    if request.connection_type == "stdio":
        if not request.config.get("command"):
            raise ValidationException(
                message="Invalid MCP server configuration",
                detail="stdio connection type requires 'command' in config",
                fields=[{"field": "config.command", "error": "This field is required for stdio connection"}]
            )
    elif request.connection_type in ("sse", "http"):
        if not request.config.get("url"):
            raise ValidationException(
                message="Invalid MCP server configuration",
                detail=f"{request.connection_type} connection type requires 'url' in config",
                fields=[{"field": "config.url", "error": f"This field is required for {request.connection_type} connection"}]
            )

    # Generate endpoint based on connection type
    endpoint = ""
    if request.connection_type == "stdio":
        command = request.config.get("command", "")
        args = request.config.get("args", [])
        endpoint = f"{command} {' '.join(args)}"
    else:
        url = request.config.get("url", "")
        endpoint = url.replace("http://", "").replace("https://", "")

    server_data = {
        "name": request.name,
        "description": request.description,
        "connection_type": request.connection_type,
        "config": request.config,
        "allowed_tools": request.allowed_tools,
        "rejected_tools": request.rejected_tools,
        "endpoint": endpoint,
        "version": "v1.0.0",
        "is_active": True,
    }
    server = await db.mcp_servers.put(server_data)
    return server


@router.put("/{mcp_id}", response_model=MCPResponse)
async def update_mcp_server(mcp_id: str, request: MCPUpdateRequest):
    """Update an existing MCP server configuration."""
    existing = await db.mcp_servers.get(mcp_id)
    if not existing:
        raise MCPServerNotFoundException(
            detail=f"MCP server with ID '{mcp_id}' does not exist",
            suggested_action="Please check the MCP server ID and try again"
        )

    updates = request.model_dump(exclude_unset=True)

    # Validate env vars don't target system DB
    if "config" in updates and updates["config"].get("env"):
        _validate_env_no_system_db(updates["config"]["env"])

    # Validate config if being updated
    if "config" in updates:
        connection_type = updates.get("connection_type") or existing.get("connection_type")
        if connection_type == "stdio":
            if "command" in updates["config"] and not updates["config"]["command"]:
                raise ValidationException(
                    message="Invalid MCP server configuration",
                    detail="stdio connection type requires 'command' in config",
                    fields=[{"field": "config.command", "error": "This field cannot be empty for stdio connection"}]
                )
        elif connection_type in ("sse", "http"):
            if "url" in updates["config"] and not updates["config"]["url"]:
                raise ValidationException(
                    message="Invalid MCP server configuration",
                    detail=f"{connection_type} connection type requires 'url' in config",
                    fields=[{"field": "config.url", "error": f"This field cannot be empty for {connection_type} connection"}]
                )

    # Update endpoint if config changed
    if "config" in updates:
        connection_type = updates.get("connection_type") or existing.get("connection_type")
        if connection_type == "stdio":
            command = updates["config"].get("command", "")
            args = updates["config"].get("args", [])
            updates["endpoint"] = f"{command} {' '.join(args)}"
        else:
            url = updates["config"].get("url", "")
            updates["endpoint"] = url.replace("http://", "").replace("https://", "")

    server = await db.mcp_servers.update(mcp_id, updates)
    return server


@router.delete("/{mcp_id}", status_code=204)
async def delete_mcp_server(mcp_id: str):
    """Delete an MCP server configuration."""
    deleted = await db.mcp_servers.delete(mcp_id)
    if not deleted:
        raise MCPServerNotFoundException(
            detail=f"MCP server with ID '{mcp_id}' does not exist",
            suggested_action="Please check the MCP server ID and try again"
        )
