"""Settings-related Pydantic models."""
from pydantic import BaseModel, Field
from typing import Optional, Literal


class AWSCredentials(BaseModel):
    """AWS credentials for Bedrock access."""

    access_key_id: str = Field(default="", description="AWS Access Key ID")
    secret_access_key: str = Field(default="", description="AWS Secret Access Key")
    session_token: Optional[str] = Field(default=None, description="AWS Session Token (optional)")
    region: str = Field(default="us-east-1", description="AWS Region")


class APIConfiguration(BaseModel):
    """API configuration settings."""

    # Anthropic API settings
    anthropic_api_key: str = Field(default="", description="Anthropic API Key")
    anthropic_base_url: Optional[str] = Field(
        default=None,
        description="Custom Anthropic API base URL (for proxies)"
    )

    # Bedrock settings
    use_bedrock: bool = Field(
        default=False,
        description="Use AWS Bedrock instead of Anthropic API"
    )
    aws_credentials: AWSCredentials = Field(
        default_factory=AWSCredentials,
        description="AWS credentials for Bedrock"
    )


class APIConfigurationRequest(BaseModel):
    """Request model for updating API configuration."""

    anthropic_api_key: Optional[str] = None
    anthropic_base_url: Optional[str] = None
    use_bedrock: Optional[bool] = None
    # Bedrock auth type: "credentials" for AK/SK, "bearer_token" for Bearer Token
    bedrock_auth_type: Optional[Literal["credentials", "bearer_token"]] = None
    # AK/SK credentials
    aws_access_key_id: Optional[str] = None
    aws_secret_access_key: Optional[str] = None
    aws_session_token: Optional[str] = None
    aws_region: Optional[str] = None
    # Bearer token auth
    aws_bearer_token: Optional[str] = None
    # Model configuration
    available_models: Optional[list[str]] = None
    default_model: Optional[str] = None


class APIConfigurationResponse(BaseModel):
    """Response model for API configuration."""

    # Don't return full API key/secrets, just masked versions
    anthropic_api_key_set: bool = Field(description="Whether Anthropic API key is configured")
    anthropic_base_url: Optional[str] = None
    use_bedrock: bool = False
    # Bedrock auth type: "credentials" or "bearer_token"
    bedrock_auth_type: Literal["credentials", "bearer_token"] = "credentials"
    aws_access_key_id_set: bool = Field(description="Whether AWS Access Key ID is configured")
    aws_bearer_token_set: bool = Field(default=False, description="Whether AWS Bearer Token is configured")
    aws_region: str = "us-east-1"
    # Model configuration
    available_models: list[str] = Field(
        default_factory=list,
        description="Available model IDs for agent configuration"
    )
    default_model: str = Field(
        default="claude-sonnet-4-5-20250929",
        description="Default model for new agents"
    )
