"""Marketplace and Plugin schemas."""
from typing import Optional, Literal
from pydantic import BaseModel, Field
from datetime import datetime


# ============== Marketplace Schemas ==============

class MarketplaceCreate(BaseModel):
    """Schema for creating a new marketplace."""
    name: str = Field(..., description="Display name for the marketplace")
    description: Optional[str] = Field(None, description="Description of the marketplace")
    type: Literal["git", "http", "local"] = Field(..., description="Type of marketplace source")
    url: str = Field(..., description="URL or path to the marketplace")
    branch: Optional[str] = Field("main", description="Git branch (for git type)")


class MarketplaceUpdate(BaseModel):
    """Schema for updating a marketplace."""
    name: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    branch: Optional[str] = None


class AvailablePluginInfo(BaseModel):
    """Basic info for a plugin available in a marketplace."""
    name: str
    version: str
    description: Optional[str] = None
    author: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)


class MarketplaceResponse(BaseModel):
    """Schema for marketplace response."""
    id: str
    name: str
    description: Optional[str] = None
    type: Literal["git", "http", "local"]
    url: str
    branch: Optional[str] = "main"
    is_active: bool = True
    last_synced_at: Optional[str] = None
    cached_plugins: list[AvailablePluginInfo] = Field(default_factory=list)
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


# ============== Plugin Schemas ==============

class PluginManifest(BaseModel):
    """Schema for plugin.json manifest file."""
    name: str = Field(..., description="Plugin name")
    version: str = Field(..., description="Plugin version (semver)")
    description: Optional[str] = Field(None, description="Plugin description")
    author: Optional[str] = Field(None, description="Plugin author")
    license: Optional[str] = Field(None, description="License type")
    homepage: Optional[str] = Field(None, description="Homepage URL")
    repository: Optional[str] = Field(None, description="Repository URL")
    keywords: list[str] = Field(default_factory=list, description="Search keywords")

    # Content declarations
    skills: list[str] = Field(default_factory=list, description="List of skill folder names")
    commands: list[str] = Field(default_factory=list, description="List of command names")
    agents: list[str] = Field(default_factory=list, description="List of agent names")
    hooks: list[str] = Field(default_factory=list, description="List of hook config files")
    mcp_servers: list[str] = Field(default_factory=list, description="List of MCP server names")

    # Requirements
    min_claude_code_version: Optional[str] = Field(None, description="Minimum Claude Code version")
    dependencies: list[str] = Field(default_factory=list, description="Other plugin dependencies")


class PluginInstallRequest(BaseModel):
    """Schema for installing a plugin."""
    plugin_name: str = Field(..., description="Name of the plugin to install")
    marketplace_id: str = Field(..., description="ID of the marketplace to install from")
    version: Optional[str] = Field(None, description="Specific version to install (latest if not specified)")


class PluginResponse(BaseModel):
    """Schema for plugin response."""
    id: str
    name: str
    description: Optional[str] = None
    version: str
    marketplace_id: str
    marketplace_name: Optional[str] = None
    author: Optional[str] = None
    license: Optional[str] = None

    # Installed content
    installed_skills: list[str] = Field(default_factory=list)
    installed_commands: list[str] = Field(default_factory=list)
    installed_agents: list[str] = Field(default_factory=list)
    installed_hooks: list[str] = Field(default_factory=list)
    installed_mcp_servers: list[str] = Field(default_factory=list)

    # Status
    status: Literal["installed", "disabled", "error"] = "installed"
    install_path: Optional[str] = None
    installed_at: str
    updated_at: str

    class Config:
        from_attributes = True


class AvailablePlugin(BaseModel):
    """Schema for a plugin available in a marketplace (not yet installed)."""
    name: str
    description: Optional[str] = None
    version: str
    author: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    skills_count: int = 0
    commands_count: int = 0
    agents_count: int = 0
    is_installed: bool = False
    installed_version: Optional[str] = None


class MarketplacePluginsResponse(BaseModel):
    """Response for listing plugins from a marketplace."""
    marketplace_id: str
    marketplace_name: str
    plugins: list[AvailablePlugin]
    last_synced_at: Optional[str] = None
