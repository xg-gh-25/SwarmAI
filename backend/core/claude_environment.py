"""Claude SDK environment configuration and client wrapper.

This module isolates environment setup concerns.  It is responsible for:

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
- ``_env_lock``                        — Module-level ``asyncio.Lock`` that
                                         serializes ``_configure_claude_environment``
                                         + client creation to prevent concurrent
                                         sessions from racing on ``os.environ``.
"""
from __future__ import annotations

import asyncio
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

# Module-level lock that serializes _configure_claude_environment + client
# creation.  The Claude Agent SDK reads os.environ at subprocess spawn time,
# so we must hold this lock from env configuration through client.__aenter__()
# to prevent concurrent sessions from seeing each other's env mutations.
_env_lock = asyncio.Lock()


class _ClaudeClientWrapper:
    """Wrapper to handle anyio cleanup errors when ClaudeSDKClient is used with asyncio tasks.

    When receive_response() is called from a different asyncio task than the one that
    created the client, anyio's cancel scope can get confused during cleanup.
    This wrapper suppresses that specific error.
    """

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options: ClaudeAgentOptions = options
        self.client: ClaudeSDKClient | None = None
        self.pid: int | None = None  # Captured immediately after subprocess spawn

    async def __aenter__(self) -> ClaudeSDKClient:
        self.client = ClaudeSDKClient(options=self.options)
        result = await self.client.__aenter__()  # type: ignore[union-attr]
        # Capture PID immediately — this is the most reliable time to extract it.
        # The subprocess is freshly spawned, all internal references are intact.
        self.pid = self._extract_pid()
        if self.pid:
            logger.info("Claude CLI subprocess spawned with pid=%d", self.pid)
        else:
            logger.warning("Could not extract PID from Claude CLI subprocess")
        return result

    def _extract_pid(self) -> int | None:
        """Best-effort PID extraction from the SDK client internal chain.

        Tries multiple paths through the SDK internals since the internal
        structure may vary across SDK versions:
        1. client -> _transport -> _process -> pid (v0.2.x primary path)
        2. client -> _query -> transport -> _process -> pid (v0.2.x query.transport is public)
        3. client -> _query -> _transport -> _process -> pid (v0.1.x legacy path)
        4. client -> _process -> pid (direct, future-proofing)
        5. Walk all attributes (last resort)

        Returns None if all paths fail.
        """
        try:
            if self.client is None:
                return None

            # Path 1: client -> _transport -> _process -> pid
            # Primary path for SDK v0.2.x: client stores transport directly
            transport = getattr(self.client, "_transport", None)
            if transport is not None:
                process = getattr(transport, "_process", None)
                if process is not None:
                    pid = getattr(process, "pid", None)
                    if pid is not None:
                        return pid

            # Path 2: client -> _query -> transport -> _process -> pid
            # SDK v0.2.x: Query uses public 'transport' attribute (not '_transport')
            query = getattr(self.client, "_query", None)
            if query is not None:
                transport = getattr(query, "transport", None)
                if transport is not None:
                    process = getattr(transport, "_process", None)
                    if process is not None:
                        pid = getattr(process, "pid", None)
                        if pid is not None:
                            return pid

            # Path 3: client -> _query -> _transport -> _process -> pid
            # Legacy path for SDK v0.1.x (Query used private _transport)
            if query is not None:
                transport = getattr(query, "_transport", None)
                if transport is not None:
                    process = getattr(transport, "_process", None)
                    if process is not None:
                        pid = getattr(process, "pid", None)
                        if pid is not None:
                            return pid

            # Path 4: client -> _process -> pid (direct)
            process = getattr(self.client, "_process", None)
            if process is not None:
                pid = getattr(process, "pid", None)
                if pid is not None:
                    return pid

            # Path 5: Walk all attributes looking for a process-like object with pid
            for attr_name in dir(self.client):
                if attr_name.startswith("__"):
                    continue
                try:
                    attr = getattr(self.client, attr_name, None)
                    if attr is None:
                        continue
                    # Check if it has a _process or process attribute with pid
                    for proc_attr in ("_process", "process"):
                        proc = getattr(attr, proc_attr, None)
                        if proc is not None:
                            pid = getattr(proc, "pid", None)
                            if isinstance(pid, int) and pid > 0:
                                logger.info(
                                    "PID extracted via fallback path: client.%s.%s.pid = %d",
                                    attr_name, proc_attr, pid,
                                )
                                return pid
                except Exception:
                    continue

            return None
        except Exception:
            return None

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
    - ``CLAUDE_CODE_DISABLE_AUTO_MEMORY`` — always ``"1"``; SwarmAI owns its
      memory pipeline (DailyActivity → distillation → MEMORY.md)

    **Env vars NOT set** (delegated to AWS credential chain):

    - ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``, ``AWS_SESSION_TOKEN``
    - ``AWS_BEARER_TOKEN_BEDROCK``

    Raises:
        AuthenticationNotConfiguredError: If no ``ANTHROPIC_API_KEY`` env var
            is set and Bedrock is disabled.
    """
    # 1. Bedrock toggle + region (from cached config, zero IO)
    use_bedrock = config.get("use_bedrock", True)

    if use_bedrock:
        os.environ["CLAUDE_CODE_USE_BEDROCK"] = "true"
        region = config.get("aws_region", "us-east-1") or "us-east-1"
        os.environ["AWS_REGION"] = region
        os.environ["AWS_DEFAULT_REGION"] = region
        # Clear a stale key that WE injected in a prior API-key-mode spawn — the
        # daemon is long-lived, so a leftover ANTHROPIC_API_KEY from a previous
        # config would ride into this Bedrock session and confuse SDK routing.
        # Only pop the one we injected (persisted key); never clobber a user's
        # explicit shell export (which would === the persisted value only if they
        # match — safest is to pop only when it equals the persisted key).
        _persisted = config.get("anthropic_api_key")
        if _persisted and os.environ.get("ANTHROPIC_API_KEY") == _persisted:
            os.environ.pop("ANTHROPIC_API_KEY", None)
    else:
        os.environ.pop("CLAUDE_CODE_USE_BEDROCK", None)

    # 1b. Anthropic direct (API-key) mode — inject the persisted key into the
    # environment so the SDK subprocess uses it, WITHOUT requiring the user to
    # export ANTHROPIC_API_KEY in their shell (a Finder-launched launchd daemon
    # does not inherit shell env) and WITHOUT a daemon relaunch (this runs on
    # every spawn). The key lives in the durable 0o600 secret store, never in
    # config.json. Bedrock mode must NOT set it. An env var already set by the
    # user takes precedence (don't clobber an explicit export).
    if not use_bedrock:
        persisted_key = config.get("anthropic_api_key")
        if persisted_key and not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = persisted_key

    # 2. Base URL (optional custom endpoint)
    base_url = config.get("anthropic_base_url")
    if base_url:
        os.environ["ANTHROPIC_BASE_URL"] = base_url
    else:
        os.environ.pop("ANTHROPIC_BASE_URL", None)

    # 3. Disable CLI auto-memory — SwarmAI owns its own memory pipeline
    # (DailyActivity → distillation → MEMORY.md). CLI auto-memory would
    # create conflicting writes and duplicate context injection.
    os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"

    # 4. Ownership tag — all child processes inherit this so the orphan
    # reaper can identify which backend instance spawned them.  This is
    # the foundation of the ownership-based orphan detection model:
    # only processes with this tag AND a dead owner are killed.
    os.environ["SWARMAI_OWNER_PID"] = str(os.getpid())
    logger.info("Ownership tag set: SWARMAI_OWNER_PID=%d", os.getpid())

    # 5. SDK initialize timeout — controls how long the SDK waits for the
    # CLI subprocess to complete its `initialize` control handshake (MCP
    # server startup, plugin sync, etc.).  Default is 60s which is too
    # tight for cross-region Bedrock (Beijing → us-east-1) + 5 MCP servers.
    # The SDK reads this env var in milliseconds, floors at 60s:
    #   initialize_timeout = max(CLAUDE_CODE_STREAM_CLOSE_TIMEOUT / 1000, 60)
    # Set to 180s to match our session_unit.INIT_TIMEOUT.
    os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "180000")

    # 6. MCP connection mode — SDK v0.2.82+ connects MCP servers in the
    # background by default (non-blocking). This means MCPs report
    # status="pending" until ready. Set to "0" to restore blocking behavior
    # where the session waits up to 5s for each MCP before the first query.
    # Safety net: ensures all MCPs are ready when the first message arrives.
    # TODO(2026-06): Remove after validating background MCP init works
    # reliably with our get_mcp_status() health checks.
    os.environ.setdefault("MCP_CONNECTION_NONBLOCKING", "0")

    # 6b. Bash tool timeout — the CLI already defaults BASH_DEFAULT_TIMEOUT_MS
    # to 120s (max 600s/10min). We re-assert the 120s default explicitly to
    # document intent; we deliberately DO NOT raise the 10min max (a higher
    # ceiling only allows longer hangs). NOTE: this only bounds FOREGROUND
    # commands — background commands (run_in_background) ignore it entirely
    # (anthropics/claude-code#61568), which is the real runaway hole; that is
    # handled separately by the background-command guard, not here.
    os.environ.setdefault("BASH_DEFAULT_TIMEOUT_MS", "120000")

    # 7. Pre-flight auth validation
    # AWS credentials are NOT checked here — the SDK resolves them via the
    # standard credential chain at query time. Auth errors from expired
    # credentials are caught by session_unit's retry logic.
    has_api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not has_api_key and not use_bedrock:
        raise AuthenticationNotConfiguredError(
            "No API key configured. Please add your Anthropic API key in Settings or enable Bedrock authentication."
        )

    logger.info(f"Claude environment configured - Bedrock: {use_bedrock}, Base URL: {base_url or 'default'}")
