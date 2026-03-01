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


class AppConfigResponse(BaseModel):
    """Response model for app configuration. No secrets."""

    use_bedrock: bool = False
    aws_region: str = "us-east-1"
    anthropic_base_url: Optional[str] = None
    available_models: list[str] = Field(default_factory=list)
    default_model: str = "claude-sonnet-4-5-20250929"
    claude_code_disable_experimental_betas: bool = True
    # Credential status (read-only, derived at GET time)
    aws_credentials_configured: bool = False
    anthropic_api_key_configured: bool = False
