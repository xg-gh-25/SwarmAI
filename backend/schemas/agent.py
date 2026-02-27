"""Agent-related Pydantic models."""
from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime


class SandboxNetworkConfig(BaseModel):
    """Network configuration for sandbox."""

    allow_local_binding: bool = Field(default=False, description="Allow binding to localhost ports (macOS only)")
    allow_unix_sockets: list[str] = Field(default_factory=list, description="Unix socket paths accessible in sandbox")
    allow_all_unix_sockets: bool = Field(default=False, description="Allow all Unix sockets")


class SandboxConfig(BaseModel):
    """Built-in SDK sandbox configuration for bash command isolation.

    This uses Claude Agent SDK's native sandbox feature which isolates
    bash command execution on macOS/Linux.
    """

    enabled: bool = Field(default=False, description="Enable bash sandbox (macOS/Linux only)")
    auto_allow_bash_if_sandboxed: bool = Field(
        default=True,
        description="Auto-approve bash commands when sandbox is enabled"
    )
    excluded_commands: list[str] = Field(
        default_factory=list,
        description="Commands that bypass sandbox (e.g., ['git', 'docker'])"
    )
    allow_unsandboxed_commands: bool = Field(
        default=False,
        description="Allow model to request running commands outside sandbox"
    )
    network: SandboxNetworkConfig = Field(default_factory=SandboxNetworkConfig)


class AgentConfig(BaseModel):
    """Agent configuration model."""

    id: str | None = None
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    model: str | None = Field(
        default=None, description="Claude model to use (defaults to Claude Code default)"
    )
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] = "default"
    max_turns: int | None = Field(default=None, ge=1, le=100)
    system_prompt: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    plugin_ids: list[str] = Field(default_factory=list, description="List of installed plugin IDs to enable for this agent")
    skill_ids: list[str] = Field(default_factory=list)
    allow_all_skills: bool = Field(default=False, description="If True, agent can access all available skills regardless of skill_ids")
    mcp_ids: list[str] = Field(default_factory=list)
    working_directory: str | None = Field(default=None, description="Working directory for the agent (defaults to settings.agent_workspace_dir)")
    enable_bash_tool: bool = True
    enable_file_tools: bool = True
    enable_web_tools: bool = False
    enable_tool_logging: bool = True
    enable_safety_checks: bool = True
    enable_file_access_control: bool = Field(default=True, description="Restrict file access to working_directory and allowed_directories")
    allowed_directories: list[str] = Field(default_factory=list, description="Additional directories the agent can access (beyond working_directory)")
    global_user_mode: bool = Field(default=True, description="If True, uses home directory and full file access instead of isolated workspace")
    enable_human_approval: bool = Field(default=True, description="If True, dangerous commands require user approval instead of auto-blocking")
    sandbox_enabled: bool = Field(default=True, description="Enable sandbox for bash command isolation")
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig, description="Sandbox configuration for bash isolation")
    status: Literal["active", "inactive"] = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Data Analyst Agent",
                "description": "Specialized in data analysis and visualization",
                "model": "sonnet",
                "permission_mode": "acceptEdits",
                "max_turns": 20,
                "skill_ids": ["xlsx-skill", "docx-skill"],
                "mcp_ids": ["postgres-mcp"],
                "enable_web_tools": True,
            }
        }


class SandboxNetworkConfigRequest(BaseModel):
    """Request model for sandbox network configuration."""

    allow_local_binding: bool | None = None
    allow_unix_sockets: list[str] | None = None
    allow_all_unix_sockets: bool | None = None


class SandboxConfigRequest(BaseModel):
    """Request model for sandbox configuration."""

    enabled: bool | None = None
    auto_allow_bash_if_sandboxed: bool | None = None
    excluded_commands: list[str] | None = None
    allow_unsandboxed_commands: bool | None = None
    network: SandboxNetworkConfigRequest | None = None


class AgentCreateRequest(BaseModel):
    """Request model for creating an agent."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    model: str | None = None
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] = "bypassPermissions"
    max_turns: int | None = Field(default=100, ge=1, le=100)
    system_prompt: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    plugin_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    allow_all_skills: bool = False
    mcp_ids: list[str] = Field(default_factory=list)
    enable_bash_tool: bool = True
    enable_file_tools: bool = True
    enable_web_tools: bool = False
    enable_file_access_control: bool = True
    allowed_directories: list[str] = Field(default_factory=list)
    global_user_mode: bool = True
    enable_human_approval: bool = True
    sandbox_enabled: bool = True
    sandbox: SandboxConfigRequest | None = None


class AgentUpdateRequest(BaseModel):
    """Request model for updating an agent."""

    name: str | None = None
    description: str | None = None
    model: str | None = None
    permission_mode: Literal["default", "acceptEdits", "plan", "bypassPermissions"] | None = None
    max_turns: int | None = None
    system_prompt: str | None = None
    allowed_tools: list[str] | None = None
    plugin_ids: list[str] | None = None
    skill_ids: list[str] | None = None
    allow_all_skills: bool | None = None
    mcp_ids: list[str] | None = None
    enable_bash_tool: bool | None = None
    enable_file_tools: bool | None = None
    enable_web_tools: bool | None = None
    enable_tool_logging: bool | None = None
    enable_safety_checks: bool | None = None
    enable_file_access_control: bool | None = None
    allowed_directories: list[str] | None = None
    global_user_mode: bool | None = None
    enable_human_approval: bool | None = None
    sandbox_enabled: bool | None = None
    sandbox: SandboxConfigRequest | None = None
    status: Literal["active", "inactive"] | None = None


class SandboxNetworkConfigResponse(BaseModel):
    """Response model for sandbox network configuration."""

    allow_local_binding: bool = False
    allow_unix_sockets: list[str] = Field(default_factory=list)
    allow_all_unix_sockets: bool = False


class SandboxConfigResponse(BaseModel):
    """Response model for sandbox configuration."""

    enabled: bool = False
    auto_allow_bash_if_sandboxed: bool = True
    excluded_commands: list[str] = Field(default_factory=list)
    allow_unsandboxed_commands: bool = False
    network: SandboxNetworkConfigResponse = Field(default_factory=SandboxNetworkConfigResponse)


class AgentResponse(BaseModel):
    """Response model for agent."""

    id: str
    name: str
    description: str | None = None
    model: str | None = None
    permission_mode: str = "default"
    max_turns: int | None = None
    system_prompt: str | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    plugin_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    allow_all_skills: bool = False
    mcp_ids: list[str] = Field(default_factory=list)
    working_directory: str | None = None
    enable_bash_tool: bool = True
    enable_file_tools: bool = True
    enable_web_tools: bool = False
    enable_tool_logging: bool = True
    enable_safety_checks: bool = True
    enable_file_access_control: bool = True
    allowed_directories: list[str] = Field(default_factory=list)
    global_user_mode: bool = True
    enable_human_approval: bool = True
    sandbox_enabled: bool = True
    sandbox: SandboxConfigResponse = Field(default_factory=SandboxConfigResponse)
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
