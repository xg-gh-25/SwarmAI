"""Settings-related Pydantic models.

This module defines the request and response schemas for the Settings API
(``/api/settings``).  The models deliberately exclude all credential fields
— AWS credentials are resolved via the standard AWS credential chain and
are never stored in ``config.json`` or exposed through the API.

Public symbols:

- ``AppConfigRequest``          — Partial-update request (PUT body).
- ``AppConfigResponse``         — Full config response (GET result).
"""

from pydantic import BaseModel, Field
from typing import Optional


class AppConfigRequest(BaseModel):
    """Request model for updating app configuration. No credential fields."""

    use_bedrock: Optional[bool] = None
    aws_region: Optional[str] = None
    anthropic_base_url: Optional[str] = None
    available_models: Optional[list[str]] = None
    default_model: Optional[str] = None
    claude_code_disable_experimental_betas: Optional[bool] = None
    sandbox_additional_write_paths: Optional[str] = None
    sandbox_enabled_default: Optional[bool] = None
    sandbox_auto_allow_bash: Optional[bool] = None
    sandbox_excluded_commands: Optional[str] = None
    sandbox_allow_unsandboxed: Optional[bool] = None
    sandbox_allowed_hosts: Optional[str] = None


class AppConfigResponse(BaseModel):
    """Response model for app configuration. No secrets."""

    use_bedrock: bool = False
    aws_region: str = "us-east-1"
    anthropic_base_url: Optional[str] = None
    available_models: list[str] = Field(default_factory=list)
    default_model: str = "claude-sonnet-4-5-20250929"
    claude_code_disable_experimental_betas: bool = True
    sandbox_additional_write_paths: str = ""
    sandbox_enabled_default: bool = True
    sandbox_auto_allow_bash: bool = True
    sandbox_excluded_commands: str = "docker"
    sandbox_allow_unsandboxed: bool = False
    sandbox_allowed_hosts: str = "*"
    # Credential status (read-only, derived at GET time)
    aws_credentials_configured: bool = False
    anthropic_api_key_configured: bool = False
