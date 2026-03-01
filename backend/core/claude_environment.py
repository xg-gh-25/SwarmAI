"""Claude SDK environment configuration and client wrapper.

This module was extracted from ``agent_manager.py`` to isolate environment
setup concerns.  It is responsible for:

- ``_configure_claude_environment``    — Reads non-secret app settings from
                                         the in-memory ``AppConfigManager``
                                         cache (zero IO) and sets the minimal
                                         process-level environment variables
                                         consumed by the Claude Agent SDK.
                                         Never sets AWS credential env vars.
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
from __future__ import annotations

import os
import logging
from typing import TYPE_CHECKING

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
)

if TYPE_CHECKING:
    from core.app_config_manager import AppConfigManager

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


def _configure_claude_environment(config: AppConfigManager) -> None:
    """Configure env vars for Claude SDK from in-memory config cache.

    Reads non-secret settings from the ``AppConfigManager`` in-memory cache
    (zero IO) and sets the minimal process-level environment variables
    consumed by the Claude Agent SDK.

    **Env vars set by this function:**

    - ``CLAUDE_CODE_USE_BEDROCK`` — ``"true"`` when Bedrock enabled, removed otherwise
    - ``AWS_REGION`` / ``AWS_DEFAULT_REGION`` — from cached config
    - ``ANTHROPIC_BASE_URL`` — optional custom endpoint from cached config
    - ``CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`` — from cached config

    **Env vars NOT set** (delegated to AWS credential chain):

    - ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``, ``AWS_SESSION_TOKEN``
    - ``AWS_BEARER_TOKEN_BEDROCK``

    Raises:
        AuthenticationNotConfiguredError: If no ``ANTHROPIC_API_KEY`` env var
            is set and Bedrock is disabled.
    """
    # 1. Bedrock toggle + region (from cached config, zero IO)
    use_bedrock = config.get("use_bedrock", False)

    if use_bedrock:
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "true"
        region = config.get("aws_region", "us-east-1") or "us-east-1"
        os.environ["AWS_REGION"] = region
        os.environ["AWS_DEFAULT_REGION"] = region
    else:
        os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)

    # 2. Base URL (optional custom endpoint)
    base_url = config.get("anthropic_base_url")
    if base_url:
        os.environ["ANTHROPIC_BASE_URL"] = base_url
    else:
        os.environ.pop("ANTHROPIC_BASE_URL", None)

    # 3. Experimental betas flag
    if config.get("claude_code_disable_experimental_betas", True):
        os.environ["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "true"
    else:
        os.environ.pop("CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", None)

    # 4. Pre-flight auth validation
    # AWS credentials are NOT checked here — the SDK resolves them via the
    # standard credential chain at query time. Auth errors from expired
    # credentials are caught by _run_query_on_client's _AUTH_PATTERNS.
    has_api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not has_api_key and not use_bedrock:
        raise AuthenticationNotConfiguredError(
            "No API key configured. Please add your Anthropic API key in Settings or enable Bedrock authentication."
        )

    logger.info(f"Claude environment configured - Bedrock: {use_bedrock}, Base URL: {base_url or 'default'}")
