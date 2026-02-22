"""MCP Server-related Pydantic models."""
from pydantic import BaseModel, Field
from typing import Literal, Any
from datetime import datetime


class MCPConfig(BaseModel):
    """MCP server configuration model."""

    id: str | None = None
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    connection_type: Literal["stdio", "sse", "http"]
    config: dict[str, Any] = Field(
        ..., description="Connection-specific configuration"
    )
    allowed_tools: list[str] | None = None
    rejected_tools: list[str] | None = None
    endpoint: str | None = None
    version: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        json_schema_extra = {
            "example": {
                "name": "PostgreSQL MCP",
                "description": "Database query tool",
                "connection_type": "stdio",
                "config": {
                    "command": "uvx",
                    "args": ["mcp-server-postgres", "postgresql://..."],
                },
                "allowed_tools": ["query_database", "list_tables"],
            }
        }


class MCPCreateRequest(BaseModel):
    """Request model for creating an MCP server configuration."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    connection_type: Literal["stdio", "sse", "http"]
    config: dict[str, Any]
    allowed_tools: list[str] | None = None
    rejected_tools: list[str] | None = None


class MCPUpdateRequest(BaseModel):
    """Request model for updating an MCP server configuration."""

    name: str | None = None
    description: str | None = None
    connection_type: Literal["stdio", "sse", "http"] | None = None
    config: dict[str, Any] | None = None
    allowed_tools: list[str] | None = None
    rejected_tools: list[str] | None = None


class MCPResponse(BaseModel):
    """Response model for MCP server."""

    id: str
    name: str
    description: str | None = None
    connection_type: str
    config: dict[str, Any]
    allowed_tools: list[str] | None = None
    rejected_tools: list[str] | None = None
    endpoint: str | None = None
    version: str | None = None
    created_at: str
    updated_at: str
