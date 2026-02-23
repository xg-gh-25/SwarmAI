"""Claude SDK environment configuration and client wrapper.

This module was extracted from ``agent_manager.py`` to isolate environment
setup concerns.  It is responsible for:

- ``_configure_claude_environment``    — Reads API settings from the database
                                         (Settings page) and configures the
                                         process-level environment variables
                                         consumed by the Claude Agent SDK
                                         (API key, Bedrock credentials, region).
- ``_ClaudeClientWrapper``             — Async context-manager wrapper around
                                         ``ClaudeSDKClient`` that suppresses
                                         anyio cancel-scope cleanup errors when
                                         the client is used across asyncio tasks.
- ``AuthenticationNotConfiguredError`` — Raised by pre-flight validation when
                                         neither an Anthropic API key nor
                                         Bedrock authentication is configured.

All public symbols are re-exported by ``agent_manager.py`` for backward
compatibility, so existing callers require zero import changes.
"""
import os
import logging

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
)

from config import settings

logger = logging.getLogger(__name__)


class _ClaudeClientWrapper:
    """Wrapper to handle anyio cleanup errors when ClaudeSDKClient is used with asyncio tasks.

    When receive_response() is called from a different asyncio task than the one that
    created the client, anyio's cancel scope can get confused during cleanup.
    This wrapper suppresses that specific error.
    """

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options: ClaudeAgentOptions = options
        self.client: ClaudeSDKClient | None = None

    async def __aenter__(self) -> ClaudeSDKClient:
        self.client = ClaudeSDKClient(options=self.options)
        return await self.client.__aenter__()  # type: ignore[union-attr]

    async def __aexit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: object) -> bool:
        if self.client is None:
            return False
        try:
            return await self.client.__aexit__(exc_type, exc_val, exc_tb)
        except RuntimeError as e:
            if "cancel scope" in str(e).lower():
                logger.warning(f"Suppressed anyio cleanup error (expected when using asyncio tasks): {e}")
                return False  # Don't suppress the original exception if any
            raise


class AuthenticationNotConfiguredError(Exception):
    """Raised when no authentication method (API key or Bedrock) is configured."""
    pass


async def _configure_claude_environment() -> None:
    """Configure environment variables for Claude Code CLI.

    Reads API configuration from database settings (Settings page in UI).
    Falls back to environment variables from config.py if no database settings exist.
    """
    # Import here to avoid circular imports
    from routers.settings import get_api_settings

    # Get API settings from database
    api_settings = await get_api_settings()

    # Set ANTHROPIC_API_KEY - prefer database setting, fall back to env var
    api_key = api_settings.get("anthropic_api_key") or settings.anthropic_api_key
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key

    # Set ANTHROPIC_BASE_URL if configured (for custom endpoints)
    base_url = api_settings.get("anthropic_base_url") or settings.anthropic_base_url
    if base_url:
        os.environ["ANTHROPIC_BASE_URL"] = base_url
    elif "ANTHROPIC_BASE_URL" in os.environ:
        # Clear it if not configured but exists in environment
        del os.environ["ANTHROPIC_BASE_URL"]

    # Set CLAUDE_CODE_USE_BEDROCK if enabled - prefer database setting
    use_bedrock = api_settings.get("use_bedrock", False) or settings.claude_code_use_bedrock
    bedrock_auth_type = api_settings.get("bedrock_auth_type", "credentials")

    if use_bedrock:
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "true"

        # Get region (common for both auth types)
        aws_region = api_settings.get("aws_region", "us-east-1")
        if aws_region:
            os.environ["AWS_REGION"] = aws_region
            os.environ["AWS_DEFAULT_REGION"] = aws_region

        if bedrock_auth_type == "bearer_token":
            # Use Bearer Token authentication
            aws_bearer_token = api_settings.get("aws_bearer_token")
            if aws_bearer_token:
                os.environ["AWS_BEARER_TOKEN_BEDROCK"] = aws_bearer_token
            # Clear AK/SK credentials when using bearer token
            os.environ.pop("AWS_ACCESS_KEY_ID", None)
            os.environ.pop("AWS_SECRET_ACCESS_KEY", None)
            os.environ.pop("AWS_SESSION_TOKEN", None)
        else:
            # Use AK/SK credentials authentication
            aws_access_key = api_settings.get("aws_access_key_id")
            aws_secret_key = api_settings.get("aws_secret_access_key")
            aws_session_token = api_settings.get("aws_session_token")

            if aws_access_key:
                os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key
            if aws_secret_key:
                os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_key
            if aws_session_token:
                os.environ["AWS_SESSION_TOKEN"] = aws_session_token
            else:
                # Clear session token if not provided
                os.environ.pop("AWS_SESSION_TOKEN", None)
            # Clear bearer token when using AK/SK
            os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
    else:
        # Clear Bedrock-related env vars when not using Bedrock
        os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)
        os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)

    # Set CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS if enabled (from env only)
    if settings.claude_code_disable_experimental_betas:
        os.environ["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "true"
    elif "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS" in os.environ:
        del os.environ["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"]

    # Pre-flight auth validation: ensure at least one auth method is configured
    has_api_key = api_settings.get("anthropic_api_key") or settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not has_api_key and not use_bedrock:
        raise AuthenticationNotConfiguredError(
            "No API key configured. Please add your Anthropic API key in Settings or enable Bedrock authentication."
        )

    logger.info(f"Claude environment configured - Bedrock: {use_bedrock}, Auth: {bedrock_auth_type if use_bedrock else 'N/A'}, Base URL: {base_url or 'default'}")
