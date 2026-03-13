"""MCP file-config-oriented Pydantic schemas.

Replaces the DB-backed CRUD schemas with models for the two-layer
file-based MCP configuration system.

- ``CatalogUpdateRequest``   — PATCH catalog entry (enabled/env toggle)
- ``DevCreateRequest``       — POST new dev MCP entry
- ``DevUpdateRequest``       — PUT existing dev entry (partial)
- ``ConfigEntryResponse``    — Unified response for any Config_Entry
"""

from pydantic import BaseModel, Field
from typing import Literal, Any


class CatalogUpdateRequest(BaseModel):
    """PATCH /mcp/catalog/{id} — toggle enabled, update env.

    When ``env`` is provided, the handler merges it into
    ``entry["config"]["env"]`` to match ``add_mcp_server_to_dict()``
    expectations.
    """
    enabled: bool | None = None
    env: dict[str, str] | None = None


class DevCreateRequest(BaseModel):
    """POST /mcp/dev — create a new dev MCP entry."""
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    connection_type: Literal["stdio", "sse", "http"]
    config: dict[str, Any]
    description: str | None = None
    enabled: bool = True
    rejected_tools: list[str] | None = None


class DevUpdateRequest(BaseModel):
    """PUT /mcp/dev/{id} — update an existing dev entry."""
    name: str | None = None
    connection_type: Literal["stdio", "sse", "http"] | None = None
    config: dict[str, Any] | None = None
    description: str | None = None
    enabled: bool | None = None
    rejected_tools: list[str] | None = None


class ConfigEntryResponse(BaseModel):
    """Unified response for any Config_Entry from either layer."""
    id: str
    name: str
    description: str | None = None
    connection_type: Literal["stdio", "sse", "http"]
    config: dict[str, Any]
    enabled: bool
    rejected_tools: list[str] | None = None
    category: str | None = None
    source: str | None = None
    plugin_id: str | None = None
    layer: Literal["catalog", "dev"]
    # Catalog-only fields
    required_env: list[dict] | None = None
    optional_env: list[dict] | None = None
    presets: dict | None = None
