"""Agent execution engine: session management, response streaming, and SDK orchestration.

This module contains the ``AgentManager`` class — the core execution engine that
drives agent conversations via the Claude Agent SDK. After refactoring, it delegates
to five focused modules for specific concerns:

- ``security_hooks.py``      — 4-layer defense hook factories and dangerous command detection
- ``permission_manager.py``  — PermissionManager singleton for command approval / HITL decisions
- ``cmd_permission_manager.py`` — CmdPermissionManager for filesystem-backed command approvals
- ``agent_defaults.py``      — Default agent bootstrap, skill/MCP registration
- ``claude_environment.py``  — Claude SDK environment configuration and client wrapper
- ``content_accumulator.py`` — O(1) content block deduplication (pure utility)
- ``context_directory_loader.py`` — Centralized context directory loader

All public symbols from those modules are re-exported here for backward compatibility,
so existing callers require zero import changes.

Key responsibilities retained in this module:
- ``_build_options``          — Orchestrates 6 helpers to assemble ``ClaudeAgentOptions``
- ``_build_system_prompt``    — Assembles system prompt via ContextDirectoryLoader
                                (global context) + SystemPromptBuilder (non-file sections)
- ``_resolve_project_id``     — Resolves project UUID from agent config or channel context
- ``_execute_on_session``     — Shared session setup / query / streaming (used by
                                ``run_conversation`` and ``continue_with_answer``)
- ``_run_query_on_client``    — Message processing loop with SSE event dispatch
- ``_format_message``         — Converts SDK messages to frontend-friendly dicts
- Session caching with 12-hour TTL and background cleanup
"""
from typing import AsyncIterator, Optional, Any
from uuid import uuid4
from datetime import date, datetime, timedelta
from pathlib import Path
import logging
import os
import json
import re
import asyncio
import platform
import subprocess
import sys
import time
import traceback

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
    HookMatcher,
)

from hooks.evolution_trigger_hook import ToolFailureTracker, check_tool_result_for_failure

from database import db
from config import settings, get_bedrock_model_id, get_app_data_dir
from utils.bundle_paths import get_python_executable
from .session_manager import session_manager
from .system_prompt import SystemPromptBuilder
from .context_directory_loader import DEFAULT_TOKEN_BUDGET
from .initialization_manager import initialization_manager

logger = logging.getLogger(__name__)

# Agent defaults extracted to agent_defaults.py — re-exported for consumers
# (routers/agents.py, routers/system.py, initialization_manager.py, tests)
from .agent_defaults import (  # noqa: F401
    DEFAULT_AGENT_ID,
    SWARM_AGENT_NAME,
    ensure_default_agent,
    get_default_agent,
    expand_allowed_skills_with_plugins,
)


# Claude environment extracted to claude_environment.py
from .claude_environment import (
    _ClaudeClientWrapper,
    _configure_claude_environment,
    AuthenticationNotConfiguredError,
    _env_lock,
)


# PermissionManager extracted to permission_manager.py — re-exported for
# consumers (routers/chat.py, security_hooks.py)
from .permission_manager import permission_manager as _pm

approve_command = _pm.approve_command
is_command_approved = _pm.is_command_approved
set_permission_decision = _pm.set_permission_decision
wait_for_permission_decision = _pm.wait_for_permission_decision

# Keep clear_session_approvals and hash_command accessible
clear_session_approvals = _pm.clear_session_approvals
_hash_command = _pm.hash_command

# CmdPermissionManager — filesystem-backed, shared across all sessions.
from .cmd_permission_manager import CmdPermissionManager

# CredentialValidator — pre-flight STS check for Bedrock credentials.
from .credential_validator import CredentialValidator

# AppConfigManager — file-based config with in-memory cache.
from .app_config_manager import AppConfigManager


# ContentBlockAccumulator extracted to content_accumulator.py
from .content_accumulator import ContentBlockAccumulator  # noqa: F401 — used internally




# Security hooks extracted to security_hooks.py — used internally by _build_hooks()
from .security_hooks import (
    DANGEROUS_PATTERNS,
    check_dangerous_command,
    pre_tool_logger,
    dangerous_command_blocker,
    create_human_approval_hook,
    create_file_access_permission_handler,
    create_skill_access_checker,
)

from .tscc_state_manager import TSCCStateManager

_tscc_state_manager = TSCCStateManager()

# Per-session system prompt metadata, keyed by session_id.
# Populated by _build_system_prompt() and read by the TSCC API endpoint.
_system_prompt_metadata: dict[str, dict] = {}

# ── SDK multimodal support flag ────────────────────────────────────
# False = always convert image/document blocks to path hints.
# Claude Code CLI does not currently support image/document content blocks
# via stdin JSON. When SDK support lands, flip this to True.
_SDK_SUPPORTS_MULTIMODAL: bool = False

# ── DailyActivity token cap constants ──────────────────────────────
# Applied ephemerally at prompt-assembly time; disk files are never modified.
TOKEN_CAP_PER_DAILY_FILE = 2000
TRUNCATION_MARKER = "[Truncated: kept newest ~2000 tokens]"


async def _convert_unsupported_blocks_to_path_hints(
    content: list[dict],
    session_id: str | None,
) -> list[dict]:
    """Convert image/document content blocks to path hints when SDK doesn't support multimodal.

    Saves base64 data to the agent's workspace under
    ``Attachments/{date}/{uuid}.{ext}`` so files are visible in the
    Workspace Explorer and persist across sessions.  The user controls
    cleanup — files are NOT auto-deleted.

    Text blocks are passed through unchanged.

    Args:
        content: List of content block dicts (image, document, or text).
        session_id: The effective session ID for directory scoping.

    Returns:
        A new list of content blocks with image/document blocks replaced by
        text path hints pointing to the saved files.
    """
    import base64
    from uuid import uuid4

    converted: list[dict] = []
    for block in content:
        block_type = block.get("type")
        if block_type in ("image", "document"):
            source = block.get("source", {})
            data = source.get("data", "")
            media_type = source.get("media_type", "")

            # Determine file extension from media type
            ext_map = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/gif": ".gif",
                "image/webp": ".webp",
                "application/pdf": ".pdf",
            }
            ext = ext_map.get(media_type, ".bin")

            # Save to SwarmWS/Attachments/{date}/ so files are visible
            # in the Workspace Explorer and persist for the user.
            from datetime import date as _date
            from core.initialization_manager import initialization_manager
            ws_path = initialization_manager.get_cached_workspace_path()
            if ws_path:
                date_str = _date.today().isoformat()
                attach_dir = Path(ws_path) / "Attachments" / date_str
            else:
                # Fallback if workspace path not available yet
                attach_dir = Path.home() / ".swarm-ai" / "SwarmWS" / "Attachments"
            attach_dir.mkdir(parents=True, exist_ok=True)
            # Use original filename if provided by frontend, else UUID.
            # Always include a UUID suffix to avoid TOCTOU races when
            # concurrent requests target the same filename.
            original_name = block.get("_filename", "")
            if original_name:
                # Sanitize: keep only the filename, no path components
                safe_name = Path(original_name).name
                stem = Path(safe_name).stem
                # Preserve the original file extension (not the MIME-derived one)
                orig_ext = Path(safe_name).suffix or ext
                file_path = attach_dir / f"{stem}_{uuid4().hex[:6]}{orig_ext}"
            else:
                file_path = attach_dir / f"{uuid4()}{ext}"

            try:
                decoded = base64.b64decode(data)
                # Use thread pool for sync file write to avoid blocking event loop
                import asyncio
                await asyncio.to_thread(file_path.write_bytes, decoded)
                logger.warning(
                    "SDK multimodal fallback: saved %s block to %s (session %s)",
                    block_type,
                    file_path,
                    session_id or "unknown",
                )
                # Use relative path from workspace root for the hint
                rel_path = file_path.relative_to(ws_path) if ws_path else file_path
                converted.append({
                    "type": "text",
                    "text": (
                        f"[Attached {block_type}: {file_path.name}] "
                        f"saved at {rel_path} - use Read tool to access"
                    ),
                })
            except Exception as e:
                logger.error("Failed to save attachment for fallback: %s", e)
                converted.append({
                    "type": "text",
                    "text": f"[Failed to save {block_type} attachment for fallback delivery]",
                })
        else:
            converted.append(block)
    return converted


def _truncate_daily_content(content: str, cap: int = TOKEN_CAP_PER_DAILY_FILE) -> str:
    """Truncate DailyActivity content to fit within a token budget.

    Uses word-based truncation, keeping the *tail* (newest entries) since
    DailyActivity files are append-only.  The number of words to keep is
    ``cap * 3 / 4`` — the inverse of the 4/3 token-estimation heuristic
    used by ``ContextDirectoryLoader.estimate_tokens``.

    When truncation occurs the ``TRUNCATION_MARKER`` is prepended so the
    agent (and the user, via the TSCC viewer) can see that content was
    trimmed.

    Args:
        content: Raw DailyActivity file content (already stripped).
        cap: Maximum token budget for this file.

    Returns:
        The original *content* unchanged when it fits within *cap*,
        otherwise the truncated tail prefixed with the marker.
    """
    from .context_directory_loader import ContextDirectoryLoader

    token_count = ContextDirectoryLoader.estimate_tokens(content)
    if token_count <= cap:
        return content
    words = content.split()
    words_to_keep = max(1, int(cap * 3 / 4))
    truncated = " ".join(words[-words_to_keep:])
    return f"{TRUNCATION_MARKER}\n\n{truncated}"


def _build_error_event(
    code: str,
    message: str,
    *,
    detail: str | None = None,
    suggested_action: str | None = None,
) -> dict:
    """Build a sanitized SSE error event dict.

    When ``settings.debug`` is True the full *detail* string (typically a
    Python traceback) is included verbatim.  In production mode the detail
    is stripped of tracebacks, file paths with line numbers, and library
    version strings so that internal implementation details are never
    leaked to the frontend.

    Requirements 9.1, 9.2.
    """
    event: dict = {"type": "error", "code": code, "error": message}
    if suggested_action:
        event["suggested_action"] = suggested_action
    if detail:
        if settings.debug:
            event["detail"] = detail
        else:
            # Sanitize: drop lines that expose internal implementation details.
            sanitized_lines: list[str] = []
            for line in detail.splitlines():
                stripped = line.strip()
                # Skip traceback header / footer
                if stripped.startswith("Traceback (most recent call last)"):
                    continue
                # Skip file-path / line-number references
                if stripped.startswith("File \"") and ".py\", line" in stripped:
                    continue
                # Skip caret lines that follow file references (e.g. "    ^^^")
                if stripped and all(c in "^~ " for c in stripped):
                    continue
                sanitized_lines.append(line)
            sanitized = "\n".join(sanitized_lines).strip()
            if sanitized:
                event["detail"] = sanitized
    return event


# Auth error patterns used by _run_query_on_client to classify SDK error messages.
# Expanded to cover AWS-specific auth failures (expired tokens, invalid
# credentials, STS signature mismatches) in addition to the original
# Anthropic API patterns.  Requirements 3.5, 3.6.
_AUTH_PATTERNS = [
    "not logged in", "please run /login", "invalid api key",
    "authentication", "unauthorized", "access denied", "forbidden",
    "expired", "credential", "security token", "signaturedoesnotmatch",
    "invalidclienttokenid", "expiredtokenexception",
]

# Comprehensive credential setup guidance shown when AWS credentials are
# missing, expired, or invalid.  Used by the CREDENTIALS_EXPIRED SSE error
# and the auth-error fallback path.
_CREDENTIAL_SETUP_GUIDE = (
    "**To configure AWS credentials, choose one of these options:**\n\n"
    "**Option 1 — ADA CLI** (Amazon internal):\n"
    "```bash\n"
    "ada credentials update --account=ACCOUNT_ID --role=ROLE_NAME --provider=isengard\n"
    "```\n\n"
    "**Option 2 — AWS CLI:**\n"
    "```bash\n"
    "aws configure\n"
    "```\n\n"
    "**Option 3 — Environment variables:**\n"
    "```bash\n"
    "export AWS_ACCESS_KEY_ID=your-key-id\n"
    "export AWS_SECRET_ACCESS_KEY=your-secret-key\n"
    "export AWS_DEFAULT_REGION=us-east-1\n"
    "```\n\n"
    "**Option 4 — Shared credentials file:**\n"
    "Edit `~/.aws/credentials` with your `[default]` profile\n\n"
    "---\n"
    "After configuring credentials, **retry your message** — no restart needed."
)


# System prompt template for the Skill Creator Agent.
# Used by run_skill_creator_conversation; extracted from inline f-string for clarity.
SKILL_CREATOR_SYSTEM_PROMPT_TEMPLATE = """\
You are a Skill Creator Agent specialized in creating Claude Code skills.

Your task is to help users create high-quality skills that extend Claude's capabilities.

IMPORTANT GUIDELINES:
1. Always use the skill-creator skill (invoke /skill-creator) to get guidance on skill creation best practices
2. Follow the skill creation workflow from the skill-creator skill
3. Create skills in the ~/.swarm-ai/skills/ directory (the user skills directory)
4. Ensure SKILL.md has proper YAML frontmatter with name and description
5. Description MUST follow this schema:
   - First line: one-sentence purpose
   - TRIGGER: quoted phrases the user would say
   - DO NOT USE: when a different skill/approach is better (with alternative)
6. Keep skills concise and focused - only include what Claude needs
7. Test any scripts you create before completing

Current task: Create a skill named "{skill_name}" that {skill_description}"""


class AgentManager:
    """Manages agent lifecycle using Claude Agent SDK.

    Uses ClaudeSDKClient for stateful, multi-turn conversations with Claude.
    Claude Code (underlying SDK) has built-in support for Skills and MCP servers.
    """

    # TTL for idle sessions before automatic cleanup (12 hours)
    SESSION_TTL_SECONDS = 12 * 60 * 60
    # Idle threshold for early DailyActivity extraction (30 minutes).
    # When a session has no messages for this long, extract activity
    # but keep the session alive so the user can resume.
    ACTIVITY_IDLE_SECONDS = 30 * 60

    def __init__(
        self,
        config_manager: AppConfigManager | None = None,
        cmd_permission_manager: CmdPermissionManager | None = None,
        credential_validator: CredentialValidator | None = None,
    ):
        self._clients: dict[str, ClaudeSDKClient] = {}
        # Long-lived client storage for session reuse across HTTP requests.
        # Key: session_id, Value: {"client": ClaudeSDKClient, "wrapper": _ClaudeClientWrapper, "created_at": float, "last_used": float}
        self._active_sessions: dict[str, dict] = {}
        self._cleanup_task: asyncio.Task | None = None
        # Per-session locks — prevents concurrent execution on the same session
        # (e.g. double-click "Send" or frontend retry).  Lazily created, cleaned
        # up in _cleanup_session.
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Injected components (set at startup via main.py)
        self._config: AppConfigManager | None = config_manager
        self._cmd_pm: CmdPermissionManager | None = cmd_permission_manager
        self._credential_validator: CredentialValidator | None = credential_validator
        # Session lifecycle hook manager (set at startup via set_hook_manager)
        self._hook_manager = None  # type: SessionLifecycleHookManager | None
        # Background hook executor — fire-and-forget, never blocks chat path
        self._hook_executor = None  # type: BackgroundHookExecutor | None
        # Per-session user turn counter for context monitoring.
        # Key: effective session_id, Value: cumulative user turns.
        self._user_turn_counts: dict[str, int] = {}

    def configure(
        self,
        config_manager: AppConfigManager,
        cmd_permission_manager: CmdPermissionManager,
        credential_validator: CredentialValidator,
    ) -> None:
        """Wire injected components after construction.

        Called from ``main.py`` lifespan after all components are
        initialized and loaded.  This avoids circular-import issues
        that would arise if the components were required at import time
        (the module-level ``agent_manager`` singleton is created during
        import).
        """
        self._config = config_manager
        self._cmd_pm = cmd_permission_manager
        self._credential_validator = credential_validator

    def set_hook_manager(self, hook_manager) -> None:
        """Inject the session lifecycle hook manager at startup."""
        self._hook_manager = hook_manager

    def set_hook_executor(self, executor) -> None:
        """Inject the background hook executor at startup.

        The executor wraps the hook manager and runs hooks as
        fire-and-forget background tasks, fully decoupled from the
        chat path.
        """
        self._hook_executor = executor

    @property
    def hook_executor(self):
        """Public read access to the background hook executor."""
        return self._hook_executor

    @property
    def hook_manager(self):
        """Public read access to the session lifecycle hook manager."""
        return self._hook_manager

    async def _build_hook_context(self, session_id: str, info: dict):
        """Build a HookContext from active session info.

        Uses ``count_by_session()`` (SELECT COUNT) instead of loading
        all messages, for efficiency.
        """
        from .session_hooks import HookContext
        message_count = await db.messages.count_by_session(session_id)
        session = await session_manager.get_session(session_id)
        return HookContext(
            session_id=session_id,
            agent_id=info.get("agent_id", session.agent_id if session else ""),
            message_count=message_count,
            session_start_time=session.created_at if session else "",
            session_title=session.title if session else "Unknown",
        )

    def has_active_session(self, session_id: str) -> bool:
        """Check if a session is currently active in memory."""
        return session_id in self._active_sessions

    def _start_cleanup_loop(self):
        """Start background task to clean up stale sessions."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_stale_sessions_loop())

    async def _cleanup_stale_sessions_loop(self):
        """Periodically clean up sessions that have been idle too long.

        Two-tier idle detection:
        1. **Activity extraction** (30 min idle): Fire the DailyActivity
           extraction hook only.  Session stays alive so the user can
           resume without losing conversation context.
        2. **Full cleanup** (12 h idle): Tear down the session and fire
           all post-session-close hooks.
        """
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                now = time.time()

                # --- Tier 1: Early DailyActivity extraction (30 min idle) ---
                idle_for_extraction = [
                    (sid, info) for sid, info in self._active_sessions.items()
                    if (now - info.get("last_used", info["created_at"]) > self.ACTIVITY_IDLE_SECONDS
                        and not info.get("activity_extracted"))
                ]
                for sid, info in idle_for_extraction:
                    await self._extract_activity_early(sid, info)

                # --- Tier 2: Full cleanup (12 h TTL) ---
                stale = [
                    sid for sid, info in self._active_sessions.items()
                    if now - info.get("last_used", info["created_at"]) > self.SESSION_TTL_SECONDS
                ]
                for sid in stale:
                    logger.info(f"Cleaning up stale session {sid}")
                    await self._cleanup_session(sid)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in session cleanup loop: {e}")

    async def _extract_activity_early(self, session_id: str, info: dict) -> None:
        """Fire only the DailyActivity extraction hook for an idle session.

        Called when a session has been idle for ``ACTIVITY_IDLE_SECONDS``
        but has not yet reached the full TTL.  The session stays alive so
        the user can resume.  The ``activity_extracted`` flag prevents
        re-extraction on subsequent loop iterations.

        Fires as a background task via ``_hook_executor`` so the cleanup
        loop is never blocked by slow LLM calls or I/O.
        """
        executor = self._hook_executor
        hooks = executor.hooks if executor else (self._hook_manager._hooks if self._hook_manager else [])

        # Find the DailyActivity extraction hook by name
        extraction_hook = None
        for hook in hooks:
            if getattr(hook, "name", "") == "daily_activity_extraction":
                extraction_hook = hook
                break

        if not extraction_hook:
            return

        try:
            # Set flag BEFORE firing to prevent re-entry from the next
            # loop iteration and narrow the race window with _get_active_client
            # (which resets the flag when the user sends a new message).
            info["activity_extracted"] = True
            context = await self._build_hook_context(session_id, info)

            if executor:
                # Fire-and-forget — cleanup loop is not blocked
                executor.fire_single(extraction_hook, context, timeout=30.0)
                logger.info(
                    "Early DailyActivity extraction queued (background) for idle session %s",
                    session_id,
                )
            else:
                # Fallback: inline execution (should not happen in production)
                await asyncio.wait_for(
                    extraction_hook.execute(context),
                    timeout=30.0,
                )
                logger.info(
                    "Early DailyActivity extraction for idle session %s (idle %ds)",
                    session_id,
                    int(time.time() - info.get("last_used", info["created_at"])),
                )
        except Exception as exc:
            info["activity_extracted"] = False  # Allow retry on next cycle
            logger.error("Early activity extraction failed for session %s: %s", session_id, exc)

    async def _cleanup_session(self, session_id: str, skip_hooks: bool = False):
        """Disconnect and remove a stored session client.

        Hooks are fired as **background tasks** via ``_hook_executor``
        so session cleanup (and thus the chat path) is never blocked
        by slow hook execution (LLM calls, git operations, etc.).

        Args:
            session_id: The session to clean up.
            skip_hooks: If True, skip firing lifecycle hooks. Used by
                error-recovery paths and ``disconnect_all()`` (which
                fires hooks in its own outer loop).
        """
        # Build hook context BEFORE popping — hooks need session info
        info = self._active_sessions.get(session_id)
        if info and not skip_hooks:
            try:
                context = await self._build_hook_context(session_id, info)
                skip_list = (
                    ["daily_activity_extraction"]
                    if info.get("activity_extracted")
                    else None
                )

                if self._hook_executor:
                    # Fire-and-forget — cleanup continues immediately
                    self._hook_executor.fire(context, skip_hooks=skip_list)
                elif self._hook_manager:
                    # Fallback: fire as background task so cleanup is never blocked.
                    # Even without BackgroundHookExecutor, hooks must not block chat.
                    asyncio.create_task(
                        self._hook_manager.fire_post_session_close(context),
                        name=f"hooks-fallback-{session_id[:8]}",
                    )
            except Exception as exc:
                logger.error("Hook context build failed for %s: %s", session_id, exc)
        # NOW pop and clean up resources
        info = self._active_sessions.pop(session_id, None)
        if info:
            wrapper = info.get("wrapper")
            try:
                if wrapper:
                    await wrapper.__aexit__(None, None, None)
                    logger.info(f"Disconnected long-lived client for session {session_id}")
            except Exception as e:
                logger.warning(f"Error disconnecting session {session_id}: {e}")
        self._clients.pop(session_id, None)
        # Clean up per-session permission queue and session lock
        _pm.remove_session_queue(session_id)
        self._session_locks.pop(session_id, None)
        # Clean up per-session approved commands to prevent unbounded memory growth
        _pm.clear_session_approvals(session_id)
        # Clean up system prompt metadata to prevent unbounded memory growth
        _system_prompt_metadata.pop(session_id, None)
        # Clean up context monitor turn counter
        self._user_turn_counts.pop(session_id, None)

    def _get_active_client(self, session_id: str) -> ClaudeSDKClient | None:
        """Get an existing long-lived client for a session, if available."""
        info = self._active_sessions.get(session_id)
        if info:
            info["last_used"] = time.time()
            # Reset early-extraction flag so new activity gets captured
            # after the next idle period.
            info["activity_extracted"] = False
            return info["client"]
        return None

    def _resolve_allowed_tools(self, agent_config: dict) -> list[str]:
        """Resolve the list of allowed tool names from agent configuration.

        Uses ``allowed_tools`` from config directly when present. Otherwise
        falls back to the individual enable flags (``enable_bash_tool``,
        ``enable_file_tools``, ``enable_web_tools``) for backwards compatibility.

        Args:
            agent_config: Agent configuration dictionary.

        Returns:
            List of allowed tool name strings.
        """
        # Build allowed tools list - use directly from config if provided
        allowed_tools = list(agent_config.get("allowed_tools", []))

        # If no allowed_tools specified, fall back to enable flags for backwards compatibility
        if not allowed_tools:
            if agent_config.get("enable_bash_tool", True):
                allowed_tools.append("Bash")

            if agent_config.get("enable_file_tools", True):
                for tool_name in ["Read", "Write", "Edit", "Glob", "Grep"]:
                    allowed_tools.append(tool_name)

            if agent_config.get("enable_web_tools", True):
                for tool_name in ["WebFetch", "WebSearch"]:
                    allowed_tools.append(tool_name)

        # Note: Skill tool is now user-controllable via the Advanced Tools section
        # If user wants to use skills, they need to enable the Skill tool explicitly

        # Note: Plugin skills are provided via workspace symlinks (expand_allowed_skills_with_plugins),
        # not via the SDK plugins config. Passing plugin paths to the SDK would cause
        # over-inclusion when a repo contains multiple plugins (e.g., anthropics/skills
        # contains both document-skills and example-skills). The workspace approach gives
        # precise per-plugin skill control.

        return allowed_tools

    async def _build_mcp_config(
        self,
        agent_config: dict,
        enable_mcp: bool,
    ) -> tuple[dict, list[str]]:
        """Build MCP server configuration from DB mcp_ids + user-local file.

        Three sources, merged in order:
        1. Agent's ``mcp_ids`` → looked up in DB (system/plugin MCPs).
        2. ``<repo>/desktop/resources/user-mcp-servers.json`` → source tree
           (convenient for developers, gitignored by default).
        3. ``~/.swarm-ai/user-mcp-servers.json`` → runtime user config.
        Source-tree entries win on name collision with app-data entries.

        Per-server ``rejected_tools`` are converted to the SDK's global
        ``disallowed_tools`` format (``mcp__<ServerName>__<tool>``).

        Args:
            agent_config: Agent configuration dictionary.
            enable_mcp: Whether MCP servers are enabled.

        Returns:
            Tuple of (mcp_servers dict, disallowed_tools list).
        """
        mcp_servers: dict = {}
        disallowed_tools: list[str] = []

        if not enable_mcp:
            return mcp_servers, disallowed_tools

        used_names: set = set()  # Track used names to handle collisions

        # --- Source 1: DB-registered MCPs (system, plugin) via mcp_ids ---
        for mcp_id in agent_config.get("mcp_ids", []):
            mcp_config = await db.mcp_servers.get(mcp_id)
            if not mcp_config:
                continue
            # Skip disabled servers (DB or user-local config)
            if mcp_config.get("disabled"):
                logger.info("Skipping disabled MCP server: %s", mcp_config.get("name", mcp_id))
                continue
            self._add_mcp_server_to_dict(
                mcp_config, mcp_servers, disallowed_tools, used_names,
            )

        # --- Source 2: User-local MCPs from file (no DB needed) ---
        self._merge_user_local_mcp_servers(
            mcp_servers, disallowed_tools, used_names,
        )

        return mcp_servers, disallowed_tools

    def _add_mcp_server_to_dict(
        self,
        mcp_config: dict,
        mcp_servers: dict,
        disallowed_tools: list[str],
        used_names: set,
    ) -> None:
        """Add a single MCP server entry to the mcp_servers dict.

        Handles name collision, connection type dispatch, env expansion,
        and rejected_tools → disallowed_tools conversion.
        """
        connection_type = mcp_config.get("connection_type", "stdio")
        config = mcp_config.get("config", {})

        # Deduplicate server names
        server_name = mcp_config.get("name", mcp_config.get("id", "unknown"))
        base_name = server_name
        suffix = 1
        while server_name in used_names:
            server_name = f"{base_name}_{suffix}"
            suffix += 1
        used_names.add(server_name)

        if connection_type == "stdio":
            raw_args = config.get("args", [])
            expanded_args = [os.path.expandvars(a) for a in raw_args]
            mcp_servers[server_name] = {
                "type": "stdio",
                "command": config.get("command"),
                "args": expanded_args,
            }
            env = config.get("env")
            if env and isinstance(env, dict):
                mcp_servers[server_name]["env"] = env
        elif connection_type == "sse":
            mcp_servers[server_name] = {
                "type": "sse",
                "url": config.get("url"),
            }
        elif connection_type == "http":
            mcp_servers[server_name] = {
                "type": "http",
                "url": config.get("url"),
            }

        # Collect per-server rejected_tools → global disallowed_tools.
        # SDK uses mcp__<ServerName>__<tool_name> format.
        rejected = mcp_config.get("rejected_tools") or []
        for tool in rejected:
            disallowed_tools.append(f"mcp__{server_name}__{tool}")

    def _merge_user_local_mcp_servers(
        self,
        mcp_servers: dict,
        disallowed_tools: list[str],
        used_names: set,
    ) -> None:
        """Load user-local MCP servers from config files.

        Two locations are checked **in order** (earlier wins on name collision):

        1. **Source tree** — ``<repo>/desktop/resources/user-mcp-servers.json``
           Convenient for developers: edit in IDE, version-controlled via
           ``.gitignore`` (private by default).  Resolved relative to the
           backend package root.  Only available in dev mode — in PyInstaller
           bundles the repo layout doesn't exist, so this path is simply
           skipped (file won't exist).
        2. **App data dir** — ``~/.swarm-ai/user-mcp-servers.json``
           Runtime config for all users.  Always available regardless of
           packaging mode.

        Entries from both files are merged.  If the same MCP name appears in
        both files, the source-tree entry takes precedence (loaded first).
        Errors are logged but never block session startup.
        """
        # Resolve source-tree path: backend/ -> ../desktop/resources/
        _backend_root = Path(__file__).resolve().parent.parent  # backend/
        _repo_root = _backend_root.parent                       # swarmai/
        source_tree_path = _repo_root / "desktop" / "resources" / "user-mcp-servers.json"

        app_data_path = get_app_data_dir() / "user-mcp-servers.json"

        for config_path in (source_tree_path, app_data_path):
            if not config_path.exists():
                continue
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    mcp_configs = json.load(f)

                if not isinstance(mcp_configs, list):
                    continue

                for entry in mcp_configs:
                    name = entry.get("name", entry.get("id"))
                    if not name:
                        continue
                    # Skip disabled servers
                    if entry.get("disabled"):
                        logger.info(
                            "Skipping disabled user-local MCP: %s (from %s)",
                            name, config_path.name,
                        )
                        continue
                    # Skip if already added from DB or earlier file
                    if name in used_names:
                        logger.debug(
                            "User-local MCP '%s' already loaded, skipping (from %s)",
                            name, config_path,
                        )
                        continue
                    self._add_mcp_server_to_dict(
                        entry, mcp_servers, disallowed_tools, used_names,
                    )
                    logger.info(
                        "Loaded user-local MCP: %s (from %s)", name, config_path.name,
                    )

            except json.JSONDecodeError as e:
                logger.error("Invalid JSON in %s: %s", config_path, e)
            except Exception as e:
                logger.error("Failed to load user-local MCPs from %s: %s", config_path, e)

    async def _build_hooks(
        self,
        agent_config: dict,
        enable_skills: bool,
        enable_mcp: bool,
        resume_session_id: Optional[str],
        session_context: Optional[dict],
    ) -> tuple[dict, list[str], bool]:
        """Build hook matchers and file access permission handler.

        Composes security_hooks functions with the PermissionManager singleton
        to produce the hooks configuration for ClaudeAgentOptions.

        Args:
            agent_config: Agent configuration dictionary.
            enable_skills: Whether skills are enabled.
            enable_mcp: Whether MCP servers are enabled.
            resume_session_id: Optional session ID for resumed sessions.
            session_context: Optional session context dict for hook tracking.

        Returns:
            A tuple of (hooks_config_dict, effective_allowed_skills, allow_all_skills).
            The effective_allowed_skills list contains folder names (not UUIDs).
        """
        hooks: dict = {}

        if agent_config.get("enable_tool_logging", True):
            hooks["PreToolUse"] = [
                HookMatcher(hooks=[pre_tool_logger])
            ]

        if agent_config.get("enable_safety_checks", True):
            if "PreToolUse" not in hooks:
                hooks["PreToolUse"] = []
            hooks["PreToolUse"].append(
                HookMatcher(matcher="Bash", hooks=[dangerous_command_blocker])
            )

        # Add human approval hook for dangerous commands
        # Use resume_session_id for resumed sessions, or agent_id for new sessions
        # The session_key is used for tracking approved commands - must match what's
        # stored in the permission_request and used in continue_with_cmd_permission
        agent_id = agent_config.get("id", 'default')
        session_key = resume_session_id or agent_id or "unknown"

        # Enable human approval hook if configured
        enable_human_approval = agent_config.get("enable_human_approval", True)
        if enable_human_approval:
            if "PreToolUse" not in hooks:
                hooks["PreToolUse"] = []
            # Use provided session_context or create a temporary one
            hook_session_context = session_context if session_context is not None else {"sdk_session_id": resume_session_id or agent_id}
            human_approval = create_human_approval_hook(hook_session_context, session_key, enable_human_approval, _pm, self._cmd_pm)
            hooks["PreToolUse"].append(
                HookMatcher(matcher="Bash", hooks=[human_approval])
            )
            logger.info(f"Human approval hook added for session_key: {session_key}")

        # Skill access control - get allowed skill names for this agent
        allowed_skills = agent_config.get("allowed_skills", [])
        allow_all_skills = agent_config.get("allow_all_skills", False)
        plugin_ids = agent_config.get("plugin_ids", [])
        global_user_mode = agent_config.get("global_user_mode", True)

        # Global User Mode requires allow_all_skills=True (skill restrictions not supported)
        if global_user_mode:
            allow_all_skills = True
            allowed_skills = []  # Ignore allowed_skills in global mode
            plugin_ids = []  # Not needed when all skills allowed
            logger.info("Global User Mode: forcing allow_all_skills=True, ignoring allowed_skills")

        # Expand allowed_skills with skills from selected plugins
        effective_allowed_skills = await expand_allowed_skills_with_plugins(
            allowed_skills, plugin_ids, allow_all_skills
        )

        # In the filesystem-based architecture, allowed_skills are already folder names
        # so they can be passed directly to the security hook (no UUID resolution needed)
        allowed_skill_names = list(effective_allowed_skills)
        logger.info(f"Agent skill access: allow_all={allow_all_skills}, {len(effective_allowed_skills)} skills ({len(allowed_skills)} explicit + {len(plugin_ids)} plugins)")
        logger.debug(f"Skill details: allowed_skills={allowed_skills}, plugin_ids={plugin_ids}, effective_allowed_skills={effective_allowed_skills}")

        # Add skill access checker hook (double protection with per-agent workspace)
        # Skip adding the hook when allow_all_skills is True (no restrictions needed)
        if enable_skills and not allow_all_skills:
            if "PreToolUse" not in hooks:
                hooks["PreToolUse"] = []

            # Get built-in skill names so the hook always allows them
            from core.skill_manager import skill_manager
            cache = await skill_manager.get_cache()
            builtin_names = [
                name for name, info in cache.items()
                if info.source_tier == "built-in"
            ]

            skill_checker = create_skill_access_checker(
                allowed_skill_names,
                builtin_skill_names=builtin_names,
            )
            hooks["PreToolUse"].append(
                HookMatcher(matcher="Skill", hooks=[skill_checker])
            )
            logger.info(f"Skill access checker hook added for skills: {allowed_skill_names} (built-in: {builtin_names})")

        # PreCompact hook — fires before the SDK compacts the context window.
        # Sets flags on session_context so _run_query_on_client can emit an
        # SSE event to the frontend after compaction completes.
        if session_context is not None:
            async def _pre_compact_hook(hook_input, tool_name, hook_context):
                trigger = getattr(hook_input, 'trigger', 'auto')
                logger.info(f"PreCompact hook fired — trigger={trigger}, session={session_context.get('sdk_session_id')}")
                session_context["_compacted"] = True
                session_context["_compact_trigger"] = trigger
                return {}  # empty dict = continue normally

            hooks.setdefault("PreCompact", [])
            hooks["PreCompact"].append(
                HookMatcher(hooks=[_pre_compact_hook])
            )

        return hooks, effective_allowed_skills, allow_all_skills


    def _build_sandbox_config(self, agent_config: dict) -> Optional[dict]:
        """Build the sandbox configuration dict from agent settings.

        Reads ``sandbox_enabled`` from the agent config (falling back to the
        global default) and constructs the SDK sandbox settings dict.  Returns
        ``None`` when sandboxing is disabled or unsupported (Windows).

        Args:
            agent_config: Agent configuration dictionary.

        Returns:
            Sandbox settings dict or ``None`` if sandboxing is disabled.
        """
        sandbox_enabled = agent_config.get("sandbox_enabled", settings.sandbox_enabled_default)

        # Sandbox only works on macOS/Linux, not Windows
        if sandbox_enabled and platform.system() == "Windows":
            logger.warning("Sandbox is not supported on Windows, disabling")
            sandbox_enabled = False

        if not sandbox_enabled:
            return None

        excluded_commands = []
        if settings.sandbox_excluded_commands:
            excluded_commands = [cmd.strip() for cmd in settings.sandbox_excluded_commands.split(",") if cmd.strip()]

        sandbox_settings = {
            "enabled": True,
            "autoAllowBashIfSandboxed": settings.sandbox_auto_allow_bash,
            "excludedCommands": excluded_commands,
            "allowUnsandboxedCommands": settings.sandbox_allow_unsandboxed,
            "network": {"allowLocalBinding": True}
        }
        logger.info(f"Sandbox enabled: {sandbox_settings}")
        return sandbox_settings

    def _inject_channel_mcp(
        self,
        mcp_servers: dict,
        channel_context: Optional[dict],
        working_directory: str,
    ) -> dict:
        """Inject channel-specific MCP servers when running in a channel context.

        When ``channel_context`` is provided, a ``channel-tools`` MCP server
        entry is added to ``mcp_servers`` so the agent can interact with the
        originating channel (e.g. Feishu).

        Args:
            mcp_servers: Current MCP server configuration dict (mutated in place).
            channel_context: Optional channel context for channel-based execution.
            working_directory: The resolved working directory for the agent.

        Returns:
            The (possibly updated) mcp_servers dict.
        """
        if not channel_context:
            return mcp_servers

        channel_type = channel_context.get("channel_type", "")
        env_vars = {
            "CHANNEL_TYPE": channel_type,
            "WORKSPACE_DIR": working_directory,
        }

        if channel_type == "feishu":
            env_vars.update({
                "FEISHU_APP_ID": channel_context.get("app_id", ""),
                "FEISHU_APP_SECRET": channel_context.get("app_secret", ""),
                "CHAT_ID": channel_context.get("chat_id", ""),
            })
            reply_to = channel_context.get("reply_to_message_id")
            if reply_to:
                env_vars["REPLY_TO_MESSAGE_ID"] = reply_to

        mcp_script = Path(__file__).resolve().parent.parent / "mcp_servers" / "channel_file_sender.py"
        if mcp_script.exists():
            mcp_servers["channel-tools"] = {
                "type": "stdio",
                "command": get_python_executable(),
                "args": [str(mcp_script)],
                "env": env_vars,
            }
        else:
            logger.warning(f"Channel-tools MCP script not found: {mcp_script}")

        return mcp_servers


    def _resolve_model(self, agent_config: dict) -> Optional[str]:
        """Resolve the model identifier from config.json (single source of truth).

        The model is ALWAYS read from ``config.json`` via ``AppConfigManager``,
        never from the agent's DB record.  The agent_config ``model`` field is
        ignored — config.json ``default_model`` is the sole authority.

        When Bedrock is enabled, the Anthropic model ID is translated to a
        Bedrock inference profile ID via ``bedrock_model_map`` (config.json)
        with a hardcoded fallback in ``config.py``.

        Returns:
            The resolved model string, or ``None`` if not configured.
        """
        # Single source of truth: config.json default_model
        model = (
            self._config.get("default_model")
            if self._config is not None
            else agent_config.get("model")  # fallback only if config not wired
        )
        use_bedrock = (
            self._config.get("use_bedrock", False)
            if self._config is not None
            else os.environ.get("CLAUDE_CODE_USE_BEDROCK", "").lower() == "true"
        )
        if model and use_bedrock:
            config_map = (
                self._config.get("bedrock_model_map")
                if self._config is not None
                else None
            )
            model = get_bedrock_model_id(model, config_map=config_map)
            logger.info(f"Using Bedrock model: {model}")
        return model

    def _resolve_project_id(
        self,
        agent_config: dict,
        channel_context: Optional[dict],
    ) -> Optional[str]:
        """Resolve the project UUID from agent config or channel context.

        Checks the following sources in order:

        1. ``agent_config["project_id"]`` — explicit project binding
        2. ``channel_context["project_id"]`` — channel-based project context

        Returns ``None`` if no project association is found, indicating a
        global SwarmWS chat that should fall back to legacy context injection.

        Args:
            agent_config: Agent configuration dictionary.
            channel_context: Optional channel context for channel-based execution.

        Returns:
            Project UUID string, or ``None`` if not associated with a project.
        """
        # 1. Explicit project_id in agent config
        project_id = agent_config.get("project_id")
        if project_id:
            return project_id

        # 2. Channel context project_id
        if channel_context and isinstance(channel_context, dict):
            project_id = channel_context.get("project_id")
            if project_id:
                return project_id

        return None

    # Model context window sizes (tokens) for L0/L1 selection
    _MODEL_CONTEXT_WINDOWS: dict[str, int] = {
        "claude-opus-4-6": 200_000,
        "claude-sonnet-4-6": 200_000,
        "claude-sonnet-4-5-20250929": 200_000,
        "claude-opus-4-5-20251101": 200_000,
    }
    _DEFAULT_CONTEXT_WINDOW: int = 200_000
    _CONTEXT_WARN_PCT: int = 70
    _CONTEXT_CRITICAL_PCT: int = 85

    def _get_model_context_window(self, model: Optional[str]) -> int:
        """Return the context window size for a model ID.

        Strips Bedrock prefix/suffix for lookup.  Defaults to 200K.
        """
        if not model:
            return self._DEFAULT_CONTEXT_WINDOW
        base = model.replace("us.anthropic.", "").rstrip(":0")
        if base.endswith("-v1"):
            base = base[:-3]
        return self._MODEL_CONTEXT_WINDOWS.get(base, self._DEFAULT_CONTEXT_WINDOW)

    @staticmethod
    def _sum_usage_input_tokens(usage: dict) -> int:
        """Sum all input token fields from SDK usage data.

        Combines ``input_tokens``, ``cache_read_input_tokens``, and
        ``cache_creation_input_tokens`` into a single total.  Each field
        may be ``None`` (treated as 0).

        Returns 0 when all fields are ``None`` or absent.
        """
        return (
            (usage.get("input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
        )

    def _build_context_warning(
        self,
        input_tokens: int,
        model: Optional[str],
    ) -> Optional[dict]:
        """Build a context_warning SSE event dict from SDK usage data.

        Returns None if input_tokens is invalid (None, 0, negative).
        Uses named threshold constants ``_CONTEXT_WARN_PCT`` and
        ``_CONTEXT_CRITICAL_PCT`` for level classification.
        """
        if input_tokens is None or input_tokens <= 0:
            return None
        window = self._get_model_context_window(model)
        pct = round((input_tokens / window) * 100) if window > 0 else 0
        level = (
            "critical" if pct >= self._CONTEXT_CRITICAL_PCT
            else "warn" if pct >= self._CONTEXT_WARN_PCT
            else "ok"
        )
        tokens_k = input_tokens // 1000
        window_k = window // 1000

        if pct >= self._CONTEXT_CRITICAL_PCT:
            msg = (
                f"**Context alert**: Session is {pct}% full "
                f"(~{tokens_k}K/{window_k}K tokens). "
                f"Recommend: save context and start a new session."
            )
        elif pct >= self._CONTEXT_WARN_PCT:
            msg = (
                f"Heads up — we've used about {pct}% of this session's "
                f"context window (~{tokens_k}K/{window_k}K tokens). "
                f"Consider saving context soon if more heavy tasks remain."
            )
        else:
            msg = (
                f"Context {pct}% full "
                f"(~{tokens_k}K/{window_k}K tokens). Plenty of room."
            )

        return {
            "type": "context_warning",
            "level": level,
            "pct": pct,
            "tokensEst": input_tokens,
            "message": msg,
        }

    # NOTE: _auto_commit_workspace() was removed — replaced by
    # WorkspaceAutoCommitHook (hooks/auto_commit_hook.py) which runs
    # as a fire-and-forget background task via BackgroundHookExecutor.

    async def _build_system_prompt(
        self,
        agent_config: dict,
        working_directory: str,
        channel_context: Optional[dict],
    ) -> Any:
        """Build the system prompt with centralized context directory.

        Assembly order:
        1. ContextDirectoryLoader — global context from SwarmWS/.context/
        2. SystemPromptBuilder — non-file sections (safety, datetime, runtime)

        After loading context files, metadata (file list, token counts,
        truncation status, full prompt text) is stored on ``agent_config``
        under the ``_system_prompt_metadata`` key.  The metadata is later
        copied to the module-level ``_system_prompt_metadata`` dict keyed
        by session_id once the session is established.

        The entire assembly is wrapped in try/except so agent execution is
        never blocked by context assembly failures.
        """
        # ── 1. Centralized context directory (global context) ──────────
        # Reset system_prompt to avoid duplication when _build_options is
        # called twice with the same agent_config (resume-fallback path).
        agent_config["system_prompt"] = ""
        prompt_metadata: dict = {"files": [], "total_tokens": 0, "full_text": ""}
        try:
            from .context_directory_loader import (
                ContextDirectoryLoader, CONTEXT_FILES, GROUP_CHANNEL_EXCLUDE,
            )
            context_dir = Path(working_directory) / ".context"
            # Reserve headroom for ephemeral injections (DailyActivity, Bootstrap,
            # resume context) that are appended after the token-budgeted assembly.
            # 2 DailyActivity files × 2000 tokens each = 4000 token reservation
            # + 2000 tokens for resume conversation context.
            RESUME_CONTEXT_BUDGET = 2000
            EPHEMERAL_HEADROOM = 2 * TOKEN_CAP_PER_DAILY_FILE + RESUME_CONTEXT_BUDGET
            base_budget = agent_config.get("context_token_budget", DEFAULT_TOKEN_BUDGET)
            loader = ContextDirectoryLoader(
                context_dir=context_dir,
                token_budget=max(base_budget - EPHEMERAL_HEADROOM, base_budget // 2),
                templates_dir=Path(__file__).resolve().parent.parent / "context",
            )
            loader.ensure_directory()

            model = self._resolve_model(agent_config)
            model_context_window = self._get_model_context_window(model)

            # Exclude personal files (MEMORY.md, USER.md) in group channels
            # to prevent leaking private context to other participants.
            exclude_files: set[str] | None = None
            if channel_context and channel_context.get("is_group"):
                exclude_files = set(GROUP_CHANNEL_EXCLUDE)
                logger.info("Group channel detected — excluding %s from context", exclude_files)

            context_text = loader.load_all(
                model_context_window=model_context_window,
                exclude_filenames=exclude_files,
            )

            # ── BOOTSTRAP.md detection (ephemeral, not in L1 cache) ──
            bootstrap_path = context_dir / "BOOTSTRAP.md"
            if bootstrap_path.exists():
                try:
                    bootstrap_content = bootstrap_path.read_text(encoding="utf-8").strip()
                    if bootstrap_content:
                        context_text = f"## Onboarding\n{bootstrap_content}\n\n{context_text}"
                except (OSError, UnicodeDecodeError):
                    pass

            # ── DailyActivity reading — last 2 files by date (ephemeral) ──
            # Scans the directory and takes the 2 most recent files by
            # filename (YYYY-MM-DD.md sort).  Handles date gaps (weekends).
            # Token cap is applied per-file to prevent a busy day's log
            # from squeezing out higher-priority context.  Disk files are
            # never modified — truncation is ephemeral.
            daily_activity_dir = Path(working_directory) / "Knowledge" / "DailyActivity"
            if daily_activity_dir.is_dir():
                # Sort .md files by filename descending, take top 2
                da_files = sorted(
                    [f for f in daily_activity_dir.glob("*.md") if f.stem[:4].isdigit()],
                    key=lambda f: f.stem,
                    reverse=True,
                )[:2]
                for daily_file in da_files:
                    try:
                        daily_content = daily_file.read_text(encoding="utf-8").strip()
                        if daily_content:
                            token_count = ContextDirectoryLoader.estimate_tokens(daily_content)
                            if token_count > TOKEN_CAP_PER_DAILY_FILE:
                                daily_content = _truncate_daily_content(
                                    daily_content, TOKEN_CAP_PER_DAILY_FILE
                                )
                            context_text += f"\n\n## Daily Activity ({daily_file.stem})\n{daily_content}"
                    except (OSError, UnicodeDecodeError):
                        pass

                # ── Distillation flag check ──
                flag_path = daily_activity_dir / ".needs_distillation"
                if flag_path.is_file():
                    context_text += (
                        "\n\n## Memory Maintenance Required\n"
                        "Run the s_memory-distill skill now — there are undistilled "
                        "DailyActivity files that need promotion to MEMORY.md. "
                        "After distillation completes, delete the flag file at "
                        f"`{flag_path}`."
                    )

            # ── Resume context injection (ephemeral, for resumed sessions) ──
            if agent_config.get("needs_context_injection") and agent_config.get("resume_app_session_id"):
                from .context_injector import build_resume_context
                resume_ctx = await build_resume_context(agent_config["resume_app_session_id"])
                if resume_ctx:
                    context_text += f"\n\n{resume_ctx}"
                    from .context_directory_loader import ContextDirectoryLoader
                    logger.info(
                        "Resume context injected: ~%d tokens",
                        ContextDirectoryLoader.estimate_tokens(resume_ctx),
                    )
                else:
                    logger.info("Resume context skipped: no injectable messages")

            if context_text:
                existing = agent_config.get("system_prompt", "") or ""
                agent_config["system_prompt"] = (
                    existing + "\n\n" + context_text if existing else context_text
                )
                logger.info(
                    "Injected centralized context: %d chars, ~%d tokens",
                    len(context_text),
                    ContextDirectoryLoader.estimate_tokens(context_text),
                )

            # ── Collect per-file metadata for TSCC system prompt viewer ──
            # The truncation marker format is "[Truncated: N,NNN → M,MMM tokens]".
            # To detect per-section truncation, find the section header in the
            # assembled text and check for the marker within that section block.
            for spec in CONTEXT_FILES:
                filepath = context_dir / spec.filename
                try:
                    if not filepath.exists():
                        continue
                    file_content = filepath.read_text(encoding="utf-8").strip()
                    if not file_content:
                        continue
                    tokens = ContextDirectoryLoader.estimate_tokens(file_content)

                    # Detect truncation: find this section's block in the
                    # assembled text and check for [Truncated: ... tokens]
                    truncated = False
                    if context_text and spec.section_name:
                        section_header = f"## {spec.section_name}\n"
                        header_pos = context_text.find(section_header)
                        if header_pos != -1:
                            # Find the next section header (or end of text)
                            next_header = context_text.find("\n## ", header_pos + len(section_header))
                            section_block = (
                                context_text[header_pos:next_header]
                                if next_header != -1
                                else context_text[header_pos:]
                            )
                            truncated = "[Truncated:" in section_block and "tokens]" in section_block

                    prompt_metadata["files"].append({
                        "filename": spec.filename,
                        "tokens": tokens,
                        "truncated": truncated,
                        "user_customized": spec.user_customized,
                    })
                except (OSError, UnicodeDecodeError):
                    continue

            total_tokens = sum(f["tokens"] for f in prompt_metadata["files"])
            prompt_metadata["total_tokens"] = total_tokens
            prompt_metadata["effective_token_budget"] = loader.compute_token_budget(model_context_window)
            prompt_metadata["full_text"] = agent_config.get("system_prompt", "") or ""

        except Exception as e:
            logger.warning("ContextDirectoryLoader failed: %s", e)

        # Store metadata on agent_config for later retrieval by session init
        agent_config["_system_prompt_metadata"] = prompt_metadata

        # ── 2. SystemPromptBuilder (non-file sections only) ────────────
        sdk_add_dirs = agent_config.get("add_dirs", [])
        prompt_builder = SystemPromptBuilder(
            working_directory=working_directory,
            agent_config=agent_config,
            channel_context=channel_context,
            add_dirs=sdk_add_dirs,
        )
        builder_text = prompt_builder.build()

        # ── 3. Combine: SystemPromptBuilder framing + context files ───
        # SystemPromptBuilder provides identity/safety/datetime/runtime
        # metadata.  Context files (11 files + DailyActivity) were loaded
        # into agent_config["system_prompt"] by step 1 above.  Both must
        # be returned so ClaudeAgentOptions receives the full prompt.
        context_text = agent_config.get("system_prompt", "") or ""
        if context_text:
            return f"{builder_text}\n\n{context_text}"
        return builder_text

    async def _build_options(
        self,
        agent_config: dict,
        enable_skills: bool,
        enable_mcp: bool,
        resume_session_id: Optional[str] = None,
        session_context: Optional[dict] = None,
        channel_context: Optional[dict] = None,
    ) -> ClaudeAgentOptions:
        """Orchestrate helper methods to assemble ClaudeAgentOptions.

        Delegates each concern to a focused helper and assembles the final
        options object from their results.  Contains no inline business logic
        — only orchestration and final assembly.

        Args:
            agent_config: Agent configuration dictionary.
            enable_skills: Whether to enable skills.
            enable_mcp: Whether to enable MCP servers.
            resume_session_id: Optional session ID to resume (for multi-turn conversations).
            session_context: Optional session context dict for hook tracking.
            channel_context: Optional channel context for channel-based execution.
        """
        logger.debug(f"agent_config:{agent_config}")
        logger.debug(f"settings:{settings}")

        # 1. Resolve allowed tools
        allowed_tools = self._resolve_allowed_tools(agent_config)

        # 2. Build MCP server configuration (no workspace_id)
        mcp_servers, mcp_disallowed_tools = await self._build_mcp_config(agent_config, enable_mcp)

        # 3. Build hooks
        hooks, effective_allowed_skills, allow_all_skills = await self._build_hooks(
            agent_config, enable_skills, enable_mcp,
            resume_session_id, session_context,
        )

        # 4. Resolve working directory and file access (inlined, no _resolve_workspace_mode)
        working_directory = initialization_manager.get_cached_workspace_path()
        
        # setting_sources tells Claude SDK where to discover skills/config.
        # "project" means: look in {cwd}/.claude/ subdirectory for skills.
        # Despite the name, this has NO relation to SwarmAI's Projects/ folder.
        # It's just Claude SDK's naming convention for "project-local config".
        #
        # Skill discovery flow:
        # 1. User creates skill → writes to ~/.swarm-ai/skills/my-skill/
        # 2. ProjectionLayer creates symlink: SwarmWS/.claude/skills/my-skill
        # 3. Claude SDK reads setting_sources=["project"]
        # 4. SDK scans {working_directory}/.claude/skills/
        # 5. SDK discovers my-skill symlink → skill is available
        setting_sources = ["project"]
        global_user_mode = agent_config.get("global_user_mode", True)

        if global_user_mode:
            file_access_handler = None
        else:
            allowed_directories = [working_directory]
            extra_dirs = agent_config.get("allowed_directories", [])
            if extra_dirs:
                allowed_directories.extend(extra_dirs)
            file_access_handler = create_file_access_permission_handler(allowed_directories)

        # 5. Build sandbox configuration
        sandbox_settings = self._build_sandbox_config(agent_config)

        # 6. Inject channel-specific MCP servers
        mcp_servers = self._inject_channel_mcp(mcp_servers, channel_context, working_directory)

        # 7. Resolve model (with Bedrock conversion if needed)
        model = self._resolve_model(agent_config)

        # 8. Build system prompt (reads context files — stays per-session)
        system_prompt_config = await self._build_system_prompt(
            agent_config, working_directory, channel_context,
        )

        # Assemble final options
        permission_mode = agent_config.get("permission_mode", "bypassPermissions")
        max_buffer_size = int(os.environ.get("MAX_BUFFER_SIZE", 10 * 1024 * 1024))

        return ClaudeAgentOptions(
            system_prompt=system_prompt_config,
            allowed_tools=allowed_tools if allowed_tools else None,
            # Per-MCP rejected_tools mapped to SDK disallowed_tools format.
            # Blocks duplicate MCP tools that overlap with built-in SDK tools.
            disallowed_tools=mcp_disallowed_tools if mcp_disallowed_tools else [],
            mcp_servers=mcp_servers if mcp_servers else None,
            plugins=None,
            permission_mode=permission_mode,
            model=model,
            stderr=lambda msg: logger.error(msg),
            cwd=working_directory,
            setting_sources=setting_sources,
            hooks=hooks if hooks else None,
            resume=resume_session_id,
            sandbox=sandbox_settings,
            can_use_tool=file_access_handler,
            max_buffer_size=max_buffer_size,
            add_dirs=None,
        )

    async def _save_message(
        self,
        session_id: str,
        role: str,
        content: list[dict],
        model: Optional[str] = None
    ) -> dict:
        """Save a message to the database.

        Args:
            session_id: The session ID
            role: Message role ('user' or 'assistant')
            content: Message content blocks
            model: Optional model name for assistant messages

        Returns:
            The saved message dict
        """
        message_data = {
            "id": str(uuid4()),
            "session_id": session_id,
            "role": role,
            "content": content,
            "model": model,
            "created_at": datetime.now().isoformat(),
        }
        await db.messages.put(message_data)
        return message_data

    async def get_session_messages(self, session_id: str) -> list[dict]:
        """Get all messages for a session.

        Args:
            session_id: The session ID

        Returns:
            List of message dicts ordered by timestamp
        """
        return await db.messages.list_by_session(session_id)

    async def run_conversation(
        self,
        agent_id: str,
        user_message: Optional[str] = None,
        content: Optional[list[dict]] = None,
        session_id: Optional[str] = None,
        enable_skills: bool = False,
        enable_mcp: bool = False,
        channel_context: Optional[dict] = None,
    ) -> AsyncIterator[dict]:
        """Run conversation with agent and stream responses.

        Uses ClaudeSDKClient for multi-turn conversations with Claude.
        Claude Code has built-in support for Skills via the Skill tool.

        For multi-turn conversations, pass the session_id from the SDK's
        init message to resume the conversation from where it left off.

        The session_id is provided by the SDK in the first SystemMessage
        with subtype='init'. This ID must be captured and used for resumption.

        Args:
            agent_id: The agent ID
            user_message: Simple text message (for backward compatibility)
            content: Multimodal content array with text, images, documents
            session_id: Optional session ID for resuming conversations
            enable_skills: Whether to enable skills
            enable_mcp: Whether to enable MCP servers
            channel_context: Optional channel context for channel-based execution
        """
        # Check if this is a new session or resuming an existing one
        is_resuming = session_id is not None

        # Build the query content - support both simple message and multimodal content
        if content is not None:
            # Use multimodal content directly
            query_content = content
            # Extract display text for session title (first text block or "Attachment")
            display_text = None
            for block in content:
                if block.get("type") == "text" and block.get("text"):
                    display_text = block.get("text")
                    break
            if not display_text:
                display_text = "[Attachment message]"
        elif user_message is not None:
            # Simple text message - wrap in content array
            query_content = user_message
            display_text = user_message
        else:
            yield {
                "type": "error",
                "error": "Either message or content must be provided",
            }
            return

        # --- Inject pending evolution nudge into the query context ---
        # If the ToolFailureTracker emitted a nudge on the previous turn,
        # prepend it to the user message so the agent sees it as context.
        # The nudge is consumed (cleared) after injection.
        if is_resuming and session_id:
            session_info = self._active_sessions.get(session_id, {})
            pending_nudge = session_info.pop("pending_evolution_nudge", None)
            if pending_nudge and isinstance(query_content, str):
                query_content = f"[System context: {pending_nudge}]\n\n{query_content}"

        # Get agent config
        agent_config = await db.agents.get(agent_id)
        if not agent_config:
            yield {
                "type": "error",
                "error": f"Agent {agent_id} not found",
            }
            return
        agent_config['allowed_tools'] = []

        logger.info(f"Running conversation with agent {agent_id}, session {session_id}, is_resuming={is_resuming}")
        logger.debug(f"Agent config: {agent_config}")
        logger.info(f"Content type: {'multimodal' if content else 'text'}")

        # For resumed sessions, defer session_start and user message save
        # until after the SDK client path is determined in _execute_on_session.
        # This prevents duplicate session_start events and duplicate user
        # message saves when the backend restarts and falls back to a fresh
        # SDK session.

        # Build deferred content for resumed sessions
        deferred_user_content = None
        app_session_id = None
        if is_resuming:
            user_content = content if content else [{"type": "text", "text": user_message}]
            deferred_user_content = user_content
            app_session_id = session_id

        # Delegate to shared session execution pattern.
        # Track the effective session ID from the result event so we can
        # key the per-session turn counter after the stream completes.
        effective_sid: str | None = None
        # Capture SDK usage data from the result event for inline context
        # monitoring (local to this generator — safe for multi-tab).
        last_input_tokens: Optional[int] = None
        last_model: Optional[str] = None

        async for event in self._execute_on_session(
            agent_config=agent_config,
            query_content=query_content,
            display_text=display_text,
            session_id=session_id,
            enable_skills=enable_skills,
            enable_mcp=enable_mcp,
            is_resuming=is_resuming,
            content=content,
            user_message=user_message,
            agent_id=agent_id,
            channel_context=channel_context,
            app_session_id=app_session_id,
            deferred_user_content=deferred_user_content,
        ):
            # Capture session_id and usage data from the result event
            if event.get("type") == "result":
                if event.get("session_id"):
                    effective_sid = event["session_id"]
                _usage = event.get("usage")
                if _usage:
                    last_input_tokens = self._sum_usage_input_tokens(_usage)
                    # The SDK reports CUMULATIVE token counts across all
                    # internal agentic turns (tool-use loops).  Dividing by
                    # num_turns gives the approximate per-turn context window
                    # consumption, which is what the ring should display.
                    _n_turns = event.get("num_turns") or 1
                    if _n_turns > 1:
                        last_input_tokens = last_input_tokens // _n_turns
                last_model = agent_config.get("model")
            yield event

        # --- Post-response context monitor ---
        # Compute context usage from the SDK's ResultMessage.usage
        # normalized by num_turns (inline, no filesystem scan).
        # Emits on every turn with valid data.
        if effective_sid:
            try:
                turns = self._user_turn_counts.get(effective_sid, 0) + 1
                self._user_turn_counts[effective_sid] = turns

                warning_event = self._build_context_warning(last_input_tokens, last_model)
                if warning_event:
                    if warning_event["level"] in ("warn", "critical"):
                        logger.info(
                            "Context monitor [%s]: %s (%d%%, ~%dK tokens)",
                            warning_event["level"], effective_sid,
                            warning_event["pct"], warning_event["tokensEst"] // 1000,
                        )
                    else:
                        logger.debug(
                            "Context monitor [ok]: %d%% after %d turns",
                            warning_event["pct"], turns,
                        )
                    yield warning_event
            except Exception:
                # Context monitoring is best-effort; never break the response
                logger.debug("Context monitor check failed", exc_info=True)

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Return (or create) a per-session asyncio.Lock.

        Prevents concurrent execution on the same session (e.g. double-click
        "Send", frontend retry, or overlapping answer/permission continuations).
        Locks are lazily created and cleaned up in ``_cleanup_session()``.
        """
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    async def _execute_on_session(
        self,
        agent_config: dict,
        query_content: Any,
        display_text: str,
        session_id: Optional[str],
        enable_skills: bool,
        enable_mcp: bool,
        is_resuming: bool,
        content: Optional[list[dict]],
        user_message: Optional[str],
        agent_id: str,
        channel_context: Optional[dict] = None,
        app_session_id: Optional[str] = None,
        deferred_user_content: Optional[list[dict]] = None,
    ) -> AsyncIterator[dict]:
        """Shared session setup, query execution, and response streaming.

        Handles:
        - Reusing existing long-lived clients for resumed sessions
        - Creating new clients when no active session exists
        - Falling back to fresh sessions when --resume can't work
        - Storing clients for future reuse
        - Error handling and session cleanup

        Concurrency: A per-session lock prevents two concurrent executions
        on the same session (e.g. double-click "Send" or frontend retry).
        If the lock is already held, the caller gets an immediate error.
        """
        # Per-session concurrency guard — prevents double-send corruption.
        # Use the stable app_session_id (tab session ID) when available,
        # otherwise fall back to the SDK session_id.  For new sessions
        # (both IDs None), generate an ephemeral UUID so parallel new
        # sessions don't collide on the shared agent_id.
        lock_key = app_session_id or session_id or str(uuid4())
        is_ephemeral_lock = (app_session_id is None and session_id is None)
        if is_ephemeral_lock:
            logger.info(f"Using ephemeral lock key {lock_key} for new session (agent={agent_id})")
        else:
            logger.debug(f"Using stable lock key {lock_key} for session")
        session_lock = self._get_session_lock(lock_key)

        if session_lock.locked():
            logger.warning(
                "Session %s is already executing — rejecting concurrent request",
                lock_key,
            )
            yield _build_error_event(
                code="SESSION_BUSY",
                message="This chat session is still processing a previous message. Please wait for it to finish.",
                suggested_action="Wait for the current response to complete, then try again.",
            )
            return

        try:
            async with session_lock:
                async for event in self._execute_on_session_inner(
                    agent_config=agent_config,
                    query_content=query_content,
                    display_text=display_text,
                    session_id=session_id,
                    enable_skills=enable_skills,
                    enable_mcp=enable_mcp,
                    is_resuming=is_resuming,
                    content=content,
                    user_message=user_message,
                    agent_id=agent_id,
                    channel_context=channel_context,
                    app_session_id=app_session_id,
                    deferred_user_content=deferred_user_content,
                ):
                    yield event
        finally:
            # Clean up ephemeral lock keys to prevent unbounded memory growth.
            # Non-ephemeral keys are cleaned up by _cleanup_session().
            if is_ephemeral_lock:
                self._session_locks.pop(lock_key, None)

    async def _execute_on_session_inner(
        self,
        agent_config: dict,
        query_content: Any,
        display_text: str,
        session_id: Optional[str],
        enable_skills: bool,
        enable_mcp: bool,
        is_resuming: bool,
        content: Optional[list[dict]],
        user_message: Optional[str],
        agent_id: str,
        channel_context: Optional[dict] = None,
        app_session_id: Optional[str] = None,
        deferred_user_content: Optional[list[dict]] = None,
    ) -> AsyncIterator[dict]:
        """Inner implementation of ``_execute_on_session`` (runs under session lock)."""
        # Configure Claude environment variables.
        # Acquire _env_lock to prevent concurrent sessions from racing on
        # os.environ.  The lock is held through client creation (PATH A) so
        # the spawned subprocess inherits the correct env vars.
        try:
            async with _env_lock:
                _configure_claude_environment(self._config)
        except AuthenticationNotConfiguredError:
            logger.warning("No authentication configured — neither ANTHROPIC_API_KEY nor Bedrock enabled")
            yield _build_error_event(
                code="AUTH_NOT_CONFIGURED",
                message="No API key configured. Please add your Anthropic API key in Settings or enable Bedrock authentication.",
            )
            return

        # Pre-flight credential validation for Bedrock (Requirements 3.1, 3.2)
        # Catches expired/missing AWS credentials before the SDK call, providing
        # an immediate clear error instead of a cryptic SDK failure.
        if self._config.get("use_bedrock"):
            if not await self._credential_validator.is_valid(self._config.get("aws_region", "us-east-1")):
                yield _build_error_event(
                    code="CREDENTIALS_EXPIRED",
                    message="AWS credentials are missing or expired.",
                    suggested_action=_CREDENTIAL_SETUP_GUIDE,
                )
                return

        # Track the actual SDK session_id (captured from init message)
        # Use a dict so forwarder task can see updates (mutable container)
        # Must be created BEFORE _build_options so hook can capture same object
        session_context = {"sdk_session_id": session_id}
        # Carry the stable app-level session ID for resume-fallback scenarios.
        # When app_session_id is set, it overrides sdk_session_id for all
        # persistence and frontend communication.
        session_context["app_session_id"] = app_session_id

        # Build options - use resume parameter if continuing an existing session
        _t_build_start = time.monotonic()
        options = await self._build_options(
            agent_config, enable_skills, enable_mcp,
            session_id if is_resuming else None,
            session_context, channel_context,
        )
        _t_build_elapsed = time.monotonic() - _t_build_start
        logger.info(
            "Built options in %.1fs - allowed_tools: %s, permission_mode: %s, resume: %s",
            _t_build_elapsed, options.allowed_tools, options.permission_mode,
            session_id if is_resuming else None,
        )
        logger.info(f"MCP servers: {list(options.mcp_servers.keys()) if options.mcp_servers else None}")
        logger.info(f"Working directory: {options.cwd}")

        # Collect assistant response content for saving (with O(1) deduplication)
        assistant_content = ContentBlockAccumulator()

        # Start the stale session cleanup loop if not already running
        self._start_cleanup_loop()

        # Check if we can reuse an existing long-lived client for resume
        reused_client = self._get_active_client(session_id) if is_resuming else None

        # Default: no context injection needed for non-resuming requests
        agent_config["needs_context_injection"] = False

        # _need_fresh_client: set by PATH B on failure to trigger automatic
        # retry via PATH A within the same SSE stream.  The user sees a brief
        # "reconnecting" indicator instead of a scary error.
        _need_fresh_client = False

        try:
            # ── PATH B: Reuse existing long-lived client ──────────────
            if reused_client and session_id:
                agent_config["needs_context_injection"] = False
                client = reused_client
                logger.info(f"Reusing long-lived client for session {session_id}")
                self._clients[session_id] = client

                # Deferred save for resumed conversations (PATH B):
                # Emit session_start and save user message now that we know
                # the client path. This was previously done eagerly in
                # run_conversation before the client path was determined.
                if app_session_id is not None and deferred_user_content is not None:
                    yield {
                        "type": "session_start",
                        "sessionId": app_session_id,
                    }
                    title = display_text[:50] + "..." if len(display_text) > 50 else display_text
                    await session_manager.store_session(app_session_id, agent_id, title)
                    await self._save_message(
                        session_id=app_session_id,
                        role="user",
                        content=deferred_user_content,
                    )
                    # Mark as saved so PATH A auto-retry won't double-save
                    deferred_user_content = None

                async for event in self._run_query_on_client(
                    client=client,
                    query_content=query_content,
                    display_text=display_text,
                    agent_config=agent_config,
                    session_context=session_context,
                    assistant_content=assistant_content,
                    is_resuming=is_resuming,
                    content=content,
                    user_message=user_message,
                    agent_id=agent_id,
                ):
                    yield event

                # PATH B post-run: if the reused client hit an error (e.g.
                # watchdog timeout, SDK crash), evict it and signal auto-retry
                # via PATH A instead of returning an error to the user.
                if session_context.get("had_error") and session_id:
                    logger.info(
                        f"PATH B: reused client for {session_id} had error, "
                        f"evicting and auto-retrying with fresh client"
                    )
                    evicted = self._active_sessions.pop(session_id, None)
                    if evicted and evicted.get("wrapper"):
                        try:
                            await asyncio.wait_for(
                                evicted["wrapper"].__aexit__(None, None, None),
                                timeout=5.0,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"PATH B: wrapper disconnect timed out for "
                                f"{session_id} — orphan subprocess may linger "
                                f"until Tauri kill_process_tree at app close"
                            )
                        except Exception as exc:
                            logger.warning(
                                f"PATH B: wrapper disconnect failed for "
                                f"{session_id}: {exc}"
                            )
                    self._clients.pop(session_id, None)

                    # Signal auto-retry: reset error state, fresh accumulator
                    _need_fresh_client = True
                    session_context["had_error"] = False
                    assistant_content = ContentBlockAccumulator()

                    # Tell the frontend to re-enter streaming state.
                    # The prior `error` event set isStreaming=false; without
                    # this the frontend stays in "idle/error" while PATH A
                    # streams new events on the same SSE connection.
                    yield {"type": "reconnecting"}

                    # Visual indicator in the chat stream — appears as a
                    # natural continuation in the assistant message bubble.
                    yield {
                        "type": "assistant",
                        "content": [{
                            "type": "text",
                            "text": (
                                "\n\n---\n\n"
                                "⚠️ *Connection to AI service was interrupted. "
                                "Reconnecting automatically...*\n\n"
                            ),
                        }],
                    }

            # ── PATH A: Create new client ─────────────────────────────
            # Enters when: no reused client exists, OR auto-retry after
            # PATH B failure (_need_fresh_client=True).
            if _need_fresh_client or not (reused_client and session_id):
                if _need_fresh_client:
                    # Auto-retry from PATH B: force resume-fallback behavior
                    # (fresh SDK client with conversation context injection).
                    logger.info(
                        f"Auto-retry: creating fresh client for session "
                        f"{session_id} after PATH B failure"
                    )
                    is_resuming = True  # Triggers resume-fallback below

                # If resuming but no active client exists (server restart, TTL
                # expiry, or PATH B failure), the long-lived CLI subprocess is
                # gone and --resume cannot work.  Start a fresh session instead.
                if is_resuming:
                    logger.info(f"No active client for session {session_id}, starting fresh session instead of --resume")
                    options = await self._build_options(
                        agent_config, enable_skills, enable_mcp,
                        None, session_context, channel_context,
                    )
                    # Reset to behave as a new session
                    is_resuming = False
                    session_context["sdk_session_id"] = None
                    # Observability: log the resume-fallback path
                    if session_context.get("app_session_id") is not None:
                        logger.info(
                            f"Resume-fallback in _execute_on_session: "
                            f"no active client for app session {session_context['app_session_id']}, "
                            f"creating fresh SDK session"
                        )
                    # Flag for context injection: we lost the SDK client, so
                    # inject previous conversation context into the system prompt.
                    agent_config["needs_context_injection"] = True
                    agent_config["resume_app_session_id"] = app_session_id

                # Deferred save for resumed conversations (PATH A):
                # The resume failed (no active client), so we're creating a
                # fresh SDK session. Emit session_start and save user message
                # under the original app_session_id before the new client is
                # created — the init handler will skip its own save.
                if app_session_id is not None and deferred_user_content is not None:
                    yield {
                        "type": "session_start",
                        "sessionId": app_session_id,
                    }
                    title = display_text[:50] + "..." if len(display_text) > 50 else display_text
                    await session_manager.store_session(app_session_id, agent_id, title)
                    await self._save_message(
                        session_id=app_session_id,
                        role="user",
                        content=deferred_user_content,
                    )
                    # Clear deferred content so it's not saved again
                    deferred_user_content = None

                _t_client_start = time.monotonic()
                logger.info("Creating new ClaudeSDKClient...")
                wrapper = _ClaudeClientWrapper(options=options)
                # Hold _env_lock during client creation so the spawned
                # subprocess inherits the correct os.environ values.
                # After __aenter__ the subprocess has its own env copy.
                async with _env_lock:
                    _configure_claude_environment(self._config)
                    client = await wrapper.__aenter__()
                _t_client_elapsed = time.monotonic() - _t_client_start
                logger.info(
                    "ClaudeSDKClient created in %.1fs, is_resuming=%s",
                    _t_client_elapsed, is_resuming,
                )

                # Early registration: store client in _active_sessions NOW
                # so interrupt_session can find it during streaming.
                # The post-stream code will overwrite this with the final
                # effective_session_id key after the stream completes.
                _early_key = (
                    session_context.get("app_session_id")
                    or agent_id  # fallback for brand-new sessions before init
                )
                self._active_sessions[_early_key] = {
                    "client": client,
                    "wrapper": wrapper,
                    "created_at": time.time(),
                    "last_used": time.time(),
                    "activity_extracted": False,
                    "failure_tracker": ToolFailureTracker(),
                }
                # Track the early key so we can clean it up if the final
                # effective_session_id differs (e.g. new session gets a
                # different SDK session_id).
                session_context["_early_active_key"] = _early_key

                try:
                    async for event in self._run_query_on_client(
                        client=client,
                        query_content=query_content,
                        display_text=display_text,
                        agent_config=agent_config,
                        session_context=session_context,
                        assistant_content=assistant_content,
                        is_resuming=is_resuming,
                        content=content,
                        user_message=user_message,
                        agent_id=agent_id,
                    ):
                        yield event
                except Exception:
                    # On error, disconnect the wrapper instead of keeping alive
                    try:
                        await wrapper.__aexit__(None, None, None)
                    except Exception:
                        pass
                    raise

                # Store client for reuse (keep alive for future resume calls)
                # Skip storage if the session ended with an error (e.g. auth failure)
                final_session_id = session_context["sdk_session_id"]
                # Use the app session ID (if set) for keying so the next resume
                # finds the client under the original tab session ID.
                effective_session_id = (
                    session_context["app_session_id"]
                    if session_context.get("app_session_id") is not None
                    else final_session_id
                )
                if session_context.get("had_error"):
                    # PATH A auto-retry: if this was the first attempt (not
                    # already a retry from PATH B), try once more with a fresh
                    # client. This handles the case where a brand-new session
                    # hits a watchdog timeout — without this, the user sees a
                    # dead 1-message session and must manually resend.
                    if not _need_fresh_client and not session_context.get("_path_a_retried"):
                        logger.info(
                            "PATH A: fresh client had error, auto-retrying once "
                            "with a new client"
                        )
                        session_context["had_error"] = False
                        session_context["_path_a_retried"] = True
                        assistant_content = ContentBlockAccumulator()
                        try:
                            await wrapper.__aexit__(None, None, None)
                        except Exception:
                            pass

                        # Re-enter streaming state on the frontend
                        yield {"type": "reconnecting"}

                        # Visual indicator
                        yield {
                            "type": "assistant",
                            "content": [{
                                "type": "text",
                                "text": (
                                    "\n\n---\n\n"
                                    "⚠️ *AI service was slow to respond. "
                                    "Retrying automatically...*\n\n"
                                ),
                            }],
                        }

                        # Create a fresh client for the retry
                        options = await self._build_options(
                            agent_config, enable_skills, enable_mcp,
                            None, session_context, channel_context,
                        )
                        wrapper = _ClaudeClientWrapper(options=options)
                        async with _env_lock:
                            _configure_claude_environment(self._config)
                            client = await wrapper.__aenter__()
                        logger.info("PATH A retry: fresh client created")

                        try:
                            async for event in self._run_query_on_client(
                                client=client,
                                query_content=query_content,
                                display_text=display_text,
                                agent_config=agent_config,
                                session_context=session_context,
                                assistant_content=assistant_content,
                                is_resuming=False,
                                content=content,
                                user_message=user_message,
                                agent_id=agent_id,
                            ):
                                yield event
                        except Exception:
                            try:
                                await wrapper.__aexit__(None, None, None)
                            except Exception:
                                pass
                            raise

                        # Re-evaluate after retry
                        final_session_id = session_context["sdk_session_id"]
                        effective_session_id = (
                            session_context["app_session_id"]
                            if session_context.get("app_session_id") is not None
                            else final_session_id
                        )

                    if session_context.get("had_error"):
                        logger.info("Session had error (after retry), disconnecting instead of storing")
                        try:
                            await wrapper.__aexit__(None, None, None)
                        except Exception:
                            pass
                        # Clean up early registration
                        _early_key = session_context.get("_early_active_key")
                        if _early_key:
                            self._active_sessions.pop(_early_key, None)
                    elif effective_session_id:
                        self._active_sessions[effective_session_id] = {
                            "client": client,
                            "wrapper": wrapper,
                            "created_at": time.time(),
                            "last_used": time.time(),
                            "activity_extracted": False,
                            "failure_tracker": ToolFailureTracker(),
                        }
                        # Clean up early key if it differs from the final key
                        _early_key = session_context.get("_early_active_key")
                        if _early_key and _early_key != effective_session_id:
                            self._active_sessions.pop(_early_key, None)
                        logger.info(f"Stored long-lived client for session {effective_session_id}")

        except Exception as e:
            error_traceback = traceback.format_exc()
            logger.error(f"Error in conversation: {e}")
            logger.error(f"Full traceback:\n{error_traceback}")
            # Clean up broken session from reuse pool — use effective_session_id
            # so we find the entry even after resume-fallback remapping.
            eff_sid = (
                session_context["app_session_id"]
                if session_context.get("app_session_id") is not None
                else session_context.get("sdk_session_id")
            )
            # Check if this error was caused by a user-initiated interrupt.
            # If so, preserve the session for reuse instead of cleaning up.
            _exc_session_info = self._active_sessions.get(eff_sid) if eff_sid else None
            _was_interrupted = _exc_session_info and _exc_session_info.get("interrupted")
            if _was_interrupted:
                logger.info(f"Exception after interrupt for {eff_sid}, preserving session")
                if _exc_session_info:
                    _exc_session_info.pop("interrupted", None)
            elif eff_sid and eff_sid in self._active_sessions:
                await self._cleanup_session(eff_sid, skip_hooks=True)
            if not _was_interrupted:
                yield _build_error_event(
                    code="CONVERSATION_ERROR",
                    message=str(e),
                    detail=error_traceback,
                )

    async def _run_query_on_client(
        self,
        client: ClaudeSDKClient,
        query_content,
        display_text: str,
        agent_config: dict,
        session_context: dict,
        assistant_content: ContentBlockAccumulator,
        is_resuming: bool,
        content: Optional[list[dict]],
        user_message: Optional[str],
        agent_id: str,
    ) -> AsyncIterator[dict]:
        """Send a query on an existing client and yield SSE events.

        This is the shared message-processing loop used by both new and resumed sessions.
        The client is NOT disconnected after the response completes (caller manages lifecycle).
        """
        # Clear any stale interrupted flag from a previous turn so it doesn't
        # leak into the current turn's error handling. If interrupt_session()
        # is called during THIS turn's streaming, it will re-set the flag
        # after this point, and the error handler will see it correctly.
        _clear_eff_sid = (
            session_context["app_session_id"]
            if session_context.get("app_session_id") is not None
            else session_context.get("sdk_session_id")
        )
        if _clear_eff_sid:
            _clear_info = self._active_sessions.get(_clear_eff_sid)
            if _clear_info:
                _clear_info.pop("interrupted", None)

        # --- TSCC state tracking (best-effort) ---
        thread_id = session_context.get("sdk_session_id") or agent_id

        # Two concurrent tasks feed a single combined_queue: one reads SDK messages,
        # the other forwards permission requests for this session. This fan-in pattern
        # lets the main loop process both streams without polling or nested awaits.
        sdk_reader_task = None
        forwarder_task = None

        try:
            logger.info(f"Sending query: {display_text[:100] if display_text else 'multimodal'}...")

            # The SDK expects an async generator for multimodal (image/file) content,
            # but a plain string for simple text queries.
            if isinstance(query_content, list):
                async def multimodal_message_generator():
                    """Async generator for multimodal content.

                    Image/document blocks are converted to path hints since
                    Claude Code CLI does not support them via stdin JSON.
                    """
                    processed = query_content
                    if not _SDK_SUPPORTS_MULTIMODAL:
                        eff_sid = (
                            session_context.get("app_session_id")
                            or session_context.get("sdk_session_id")
                        )
                        processed = await _convert_unsupported_blocks_to_path_hints(
                            query_content, eff_sid
                        )
                    # Strip internal _filename metadata before sending to SDK
                    cleaned = []
                    for blk in processed:
                        if "_filename" in blk:
                            blk = {k: v for k, v in blk.items() if k != "_filename"}
                        cleaned.append(blk)
                    msg = {
                        "type": "user",
                        "message": {"role": "user", "content": cleaned},
                        "parent_tool_use_id": None,
                    }
                    yield msg

                await client.query(multimodal_message_generator())
            else:
                await client.query(query_content)
            logger.info(f"Query sent, waiting for response...")

            # Fan-in queue: merges two async streams (SDK responses + permission requests)
            # into one consumer loop, avoiding race conditions between the two sources.
            combined_queue: asyncio.Queue = asyncio.Queue()
            message_count = 0

            # Generation counter for stale-result filtering during --resume.
            # Each SDK reader task is assigned a monotonically increasing generation.
            # Queue items are tagged with their generation so the main loop can
            # discard items from old readers without draining the queue.
            _generation = 0
            _reader_tasks: list[asyncio.Task] = []

            async def sdk_message_reader(gen: int):
                """Read SDK messages and put them in the combined queue.

                Drains the SDK response stream into combined_queue. On error, pushes
                an error sentinel so the main loop can break cleanly. Always pushes
                sdk_done as a termination signal regardless of success or failure.
                Each item is tagged with ``gen`` so the main loop can filter stale items.
                """
                try:
                    async for message in client.receive_response():
                        await combined_queue.put({"source": "sdk", "message": message, "gen": gen})
                except Exception as e:
                    error_traceback = traceback.format_exc()
                    logger.error(f"SDK message reader error: {e}")
                    logger.error(f"SDK error traceback:\n{error_traceback}")
                    if hasattr(e, 'stderr'):
                        logger.error(f"SDK stderr: {e.stderr}")  # type: ignore[attr-defined]
                    if hasattr(e, 'stdout'):
                        logger.error(f"SDK stdout: {e.stdout}")  # type: ignore[attr-defined]
                    await combined_queue.put({"source": "error", "error": str(e), "detail": error_traceback, "gen": gen})
                finally:
                    await combined_queue.put({"source": "sdk_done", "gen": gen})
                    logger.debug("SDK message reader (gen %d) finished", gen)

            async def permission_request_forwarder():
                """Consume permission requests from this session's dedicated queue.

                Each session has its own queue in PermissionManager, so this task
                simply awaits new items — no filtering, no re-enqueuing, no busy-loop.
                The security hook writes directly to the correct session queue via
                ``enqueue_permission_request(session_id, ...)``.
                """
                try:
                    while True:
                        # Wait for the session_id to be assigned (init message)
                        current_session_id = session_context.get("sdk_session_id")
                        if not current_session_id:
                            await asyncio.sleep(0.05)
                            continue
                        session_queue = _pm.get_session_queue(current_session_id)
                        request = await session_queue.get()
                        logger.info(
                            "Forwarding permission request %s to combined queue for session %s",
                            request.get("requestId"), current_session_id,
                        )
                        await combined_queue.put({"source": "permission", "request": request})
                except asyncio.CancelledError:
                    logger.debug("Permission request forwarder cancelled")
                    raise

            sdk_reader_task = asyncio.create_task(sdk_message_reader(_generation))
            _reader_tasks.append(sdk_reader_task)
            forwarder_task = asyncio.create_task(permission_request_forwarder())

            # --- Main message loop ---
            # Consumes items from the fan-in queue until sdk_done or an error sentinel.
            # Each item is tagged with a "source" key so we can dispatch accordingly:
            #   "sdk"        → a Claude SDK message (AssistantMessage, ResultMessage, SystemMessage, etc.)
            #   "permission" → a human-approval request forwarded from the permission queue
            #   "sdk_done"   → the SDK stream has ended (normal termination)
            #   "error"      → the SDK reader encountered an exception
            formatted = None
            assistant_model = None
            # Stale-result detection for resumed sessions.  During --resume the
            # SDK may replay old messages and return the *previous* turn's
            # ResultMessage without processing the new query.  We track whether
            # the SDK executed any tool_use blocks in this stream — if it did,
            # the result is fresh.  If ResultMessage arrives during a resume
            # with no tool_use execution, we flag it as stale and re-send the
            # query on the same client (up to _MAX_STALE_RETRIES times).
            _MAX_STALE_RETRIES = 2
            _stale_retry_count = 0
            _saw_tool_use = False
            _saw_new_text_block = False  # True once an AssistantMessage TextBlock arrives

            # Watchdog: detect dead SDK subprocess.
            # If the CLI subprocess dies after init but before sending any
            # response, the stdout stream hangs forever (anyio TextReceiveStream
            # doesn't detect broken pipe reliably). This timeout surfaces an
            # error to the user instead of infinite "Thinking...".
            _WATCHDOG_INITIAL_TIMEOUT = 180   # seconds before first real message
            _WATCHDOG_INTER_MSG_TIMEOUT = 180  # seconds between messages during tool use
            _got_first_real_message = False
            _query_sent_at = time.monotonic()  # Track time from query to first message

            while True:
                watchdog_timeout = (
                    _WATCHDOG_INTER_MSG_TIMEOUT if _got_first_real_message
                    else _WATCHDOG_INITIAL_TIMEOUT
                )
                try:
                    item = await asyncio.wait_for(
                        combined_queue.get(), timeout=watchdog_timeout
                    )
                except asyncio.TimeoutError:
                    _watchdog_elapsed = time.monotonic() - _query_sent_at
                    # Check if the SDK reader task is still alive
                    if sdk_reader_task and sdk_reader_task.done():
                        logger.error(
                            "Watchdog: SDK reader task finished but no sdk_done received "
                            "(subprocess likely crashed). Total elapsed: %.1fs, timeout: %ds.",
                            _watchdog_elapsed, watchdog_timeout,
                        )
                    else:
                        logger.error(
                            "Watchdog: No SDK message received in %ds "
                            "(total elapsed: %.1fs, got_first_msg: %s). "
                            "CLI subprocess may be dead or unresponsive.",
                            watchdog_timeout, _watchdog_elapsed, _got_first_real_message,
                        )
                    session_context["had_error"] = True
                    yield _build_error_event(
                        code="SDK_SUBPROCESS_TIMEOUT",
                        message=(
                            f"The AI service didn't respond within {watchdog_timeout}s. "
                            "This usually means the Claude backend is temporarily "
                            "overloaded or the request was too complex."
                        ),
                        suggested_action=(
                            "Your conversation is saved. "
                            "Send your message again to continue."
                        ),
                    )
                    break

                # Generation filter: discard items from old SDK reader generations.
                # Permission items (no "gen" key) pass through unconditionally.
                if item.get("gen") is not None and item["gen"] < _generation:
                    continue

                if item["source"] == "sdk_done":
                    logger.info("SDK iterator finished, exiting message loop")
                    break

                if item["source"] == "permission":
                    request = item["request"]
                    logger.info(f"Emitting permission request: {request.get('requestId')}")
                    yield {"type": "cmd_permission_request", **request}
                    continue

                if item["source"] == "error":
                    # Check if this error was caused by a user-initiated interrupt.
                    _err_eff_sid = (
                        session_context["app_session_id"]
                        if session_context.get("app_session_id") is not None
                        else session_context.get("sdk_session_id")
                    )
                    _err_session_info = self._active_sessions.get(_err_eff_sid) if _err_eff_sid else None
                    if _err_session_info and _err_session_info.get("interrupted"):
                        logger.info(f"SDK reader error after interrupt for {_err_eff_sid}, treating as user stop")
                        _err_session_info.pop("interrupted", None)
                        # Save partial content before exiting
                        if assistant_content and _err_eff_sid:
                            try:
                                await self._save_message(
                                    session_id=_err_eff_sid,
                                    role="assistant",
                                    content=assistant_content.blocks,
                                    model=assistant_model,
                                )
                            except Exception:
                                logger.warning("Failed to save partial content after interrupt error", exc_info=True)
                        break  # Exit the combined_queue loop cleanly
                    logger.error(f"Error from SDK reader: {item['error']}")
                    session_context["had_error"] = True
                    # Persist any partial assistant content accumulated before
                    # the error — prevents message loss on SDK crash / restart.
                    eff_session = (
                        session_context["app_session_id"]
                        if session_context.get("app_session_id") is not None
                        else session_context.get("sdk_session_id")
                    )
                    if assistant_content and eff_session:
                        try:
                            await self._save_message(
                                session_id=eff_session,
                                role="assistant",
                                content=assistant_content.blocks,
                                model=assistant_model,
                            )
                            logger.info(f"Saved partial assistant content ({len(assistant_content.blocks)} blocks) before error")
                        except Exception:
                            logger.warning("Failed to save partial assistant content on error", exc_info=True)
                    # TSCC: mark lifecycle as failed (best-effort)
                    try:
                        sid = session_context.get("sdk_session_id")
                        if sid:
                            await _tscc_state_manager.set_lifecycle_state(sid, "failed")
                    except Exception:
                        logger.debug("TSCC: failed lifecycle update failed", exc_info=True)
                    yield _build_error_event(
                        code="SDK_STREAM_ERROR",
                        message=str(item["error"]),
                        detail=item.get("detail"),
                    )
                    break

                if item["source"] == "sdk":
                    message = item["message"]
                    message_count += 1
                    logger.info(f"Received message {message_count}: {type(message).__name__}")

                    # --- SDK message dispatch ---
                    # Messages are checked in a specific order:
                    #   1. ResultMessage first — may carry final text AND error subtypes
                    #   2. SystemMessage — session init metadata, never forwarded to the client
                    #   3. All other types — formatted via _format_message and yielded as SSE events
                    # ResultMessage is checked TWICE: once here for errors/final text, and again
                    # after _format_message to handle conversation-complete bookkeeping.

                    if isinstance(message, ResultMessage):
                        logger.info(f"ResultMessage: {message}")

                        if message.subtype == 'error_during_execution':
                            eff_sid = (
                                session_context["app_session_id"]
                                if session_context.get("app_session_id") is not None
                                else session_context.get("sdk_session_id")
                            )
                            session_info = self._active_sessions.get(eff_sid)

                            if session_info and session_info.get("interrupted"):
                                # ── User-initiated interrupt — preserve client, suppress error ──
                                logger.info(f"Session {eff_sid} interrupted by user, preserving client")
                                session_info.pop("interrupted", None)  # Clear for next turn
                                # Save partial assistant content (user may want to see what was generated)
                                if assistant_content and eff_sid:
                                    try:
                                        await self._save_message(
                                            session_id=eff_sid,
                                            role="assistant",
                                            content=assistant_content.blocks,
                                            model=assistant_model,
                                        )
                                        logger.info(f"Saved partial assistant content ({len(assistant_content.blocks)} blocks) after user interrupt")
                                    except Exception:
                                        logger.warning("Failed to save partial content after interrupt", exc_info=True)
                                # Do NOT: set had_error, call _cleanup_session, set TSCC "failed", yield error event
                            else:
                                # ── Genuine error — existing behavior unchanged ──
                                error_text = message.result or "Session failed. This may be a stale session — please start a new conversation."
                                logger.warning(f"SDK error_during_execution: {error_text}")
                                session_context["had_error"] = True
                                # TSCC: mark lifecycle as failed
                                try:
                                    sid = session_context.get("sdk_session_id")
                                    if sid:
                                        await _tscc_state_manager.set_lifecycle_state(sid, "failed")
                                except Exception:
                                    logger.debug("TSCC: failed lifecycle update failed", exc_info=True)
                                # Persist partial assistant content before error
                                if assistant_content and eff_sid:
                                    try:
                                        await self._save_message(
                                            session_id=eff_sid,
                                            role="assistant",
                                            content=assistant_content.blocks,
                                            model=assistant_model,
                                        )
                                        logger.info(f"Saved partial assistant content ({len(assistant_content.blocks)} blocks) before error_during_execution")
                                    except Exception:
                                        logger.warning("Failed to save partial assistant content on error_during_execution", exc_info=True)
                                # Remove broken session from reuse pool
                                if eff_sid and eff_sid in self._active_sessions:
                                    await self._cleanup_session(eff_sid, skip_hooks=True)
                                    logger.info(f"Removed broken session {eff_sid} from active sessions pool")
                                yield _build_error_event(
                                    code="ERROR_DURING_EXECUTION",
                                    message=error_text,
                                )

                        elif message.is_error:
                            # is_error=True but subtype is NOT 'error_during_execution':
                            # This covers auth failures (e.g. "Not logged in · Please run /login")
                            # and other SDK-level errors. Yield as an error event, NOT assistant.
                            raw_error = message.result or "An unknown error occurred."
                            error_lower = raw_error.lower()
                            is_auth_error = any(p in error_lower for p in _AUTH_PATTERNS)

                            if is_auth_error:
                                error_msg = f"Authentication failed.\n\n{_CREDENTIAL_SETUP_GUIDE}"
                                logger.warning(f"Auth error detected from SDK: {raw_error}")
                                # Invalidate credential cache so the next request
                                # re-validates via STS instead of trusting a stale
                                # cached True (Requirement 3.5).
                                if self._credential_validator is not None:
                                    self._credential_validator.invalidate()
                            elif os.environ.get("CLAUDE_CODE_USE_BEDROCK", "").lower() == "true":
                                # Defensive fallback (Requirement 3.6): unclassified
                                # error while Bedrock is active — hint at credentials
                                # since expired AWS tokens are the most common cause.
                                error_msg = (
                                    f"{raw_error}\n\n"
                                    "If this persists, your AWS credentials may have expired.\n\n"
                                    f"{_CREDENTIAL_SETUP_GUIDE}"
                                )
                                logger.warning(f"SDK is_error ResultMessage (Bedrock active, unclassified): {raw_error}")
                            else:
                                error_msg = raw_error
                                logger.warning(f"SDK is_error ResultMessage: {raw_error}")

                            session_context["had_error"] = True
                            # TSCC: mark lifecycle as failed
                            try:
                                sid = session_context.get("sdk_session_id")
                                if sid:
                                    await _tscc_state_manager.set_lifecycle_state(sid, "failed")
                            except Exception:
                                logger.debug("TSCC: failed lifecycle update failed", exc_info=True)
                            # Persist partial assistant content before error
                            eff_sid = (
                                session_context["app_session_id"]
                                if session_context.get("app_session_id") is not None
                                else session_context.get("sdk_session_id")
                            )
                            if assistant_content and eff_sid:
                                try:
                                    await self._save_message(
                                        session_id=eff_sid,
                                        role="assistant",
                                        content=assistant_content.blocks,
                                        model=assistant_model,
                                    )
                                    logger.info(f"Saved partial assistant content ({len(assistant_content.blocks)} blocks) before SDK error")
                                except Exception:
                                    logger.warning("Failed to save partial assistant content on SDK error", exc_info=True)
                            yield _build_error_event(
                                code="SDK_ERROR",
                                message=error_msg,
                            )

                        else:
                            # --- Stale-result detection ---
                            # During --resume the SDK may return the *previous* turn's
                            # cached ResultMessage before it processes the new query.
                            # Heuristic: stale if resuming, no tool_use seen, no new
                            # text blocks from AssistantMessage, and num_turns<=1.
                            # A genuine text-only response will have _saw_new_text_block=True
                            # from the preceding AssistantMessage — only stale replays skip that.
                            # If retries remain, bump generation and re-send query.
                            # If retries exhausted, discard the stale result silently.
                            _num_turns = getattr(message, 'num_turns', 0) or 0
                            _looks_stale = (
                                is_resuming
                                and not _saw_tool_use
                                and not _saw_new_text_block
                                and _num_turns <= 1
                            )

                            if _looks_stale and _stale_retry_count < _MAX_STALE_RETRIES:
                                _stale_retry_count += 1
                                _result_preview = (message.result or "")[:80]
                                logger.warning(
                                    "Stale ResultMessage detected (attempt %d/%d, "
                                    "num_turns=%d, saw_tool_use=%s): %s — bumping generation and re-sending query",
                                    _stale_retry_count, _MAX_STALE_RETRIES,
                                    _num_turns, _saw_tool_use, _result_preview,
                                )

                                # Bump generation — all items from old readers will be filtered
                                _generation += 1

                                # Cancel old reader (SDK doesn't support concurrent receive_response iterators)
                                if sdk_reader_task and not sdk_reader_task.done():
                                    sdk_reader_task.cancel()
                                    try:
                                        await sdk_reader_task
                                    except asyncio.CancelledError:
                                        pass

                                # Re-send the query on the same client
                                if isinstance(query_content, list):
                                    async def _retry_multimodal():
                                        msg = {
                                            "type": "user",
                                            "message": {"role": "user", "content": query_content},
                                            "parent_tool_use_id": None,
                                        }
                                        yield msg
                                    await client.query(_retry_multimodal())
                                else:
                                    await client.query(query_content)
                                logger.info("Re-sent query after stale detection (gen %d), restarting SDK reader", _generation)

                                # Reset tracking for the new generation
                                _saw_tool_use = False
                                _saw_new_text_block = False
                                message_count = 0
                                assistant_content = ContentBlockAccumulator()

                                # Start new reader with current generation
                                sdk_reader_task = asyncio.create_task(sdk_message_reader(_generation))
                                _reader_tasks.append(sdk_reader_task)
                                continue  # Back to the while-True loop

                            if _looks_stale and _stale_retry_count >= _MAX_STALE_RETRIES:
                                # Retry budget exhausted but result still looks stale.
                                # This is likely a false positive: after re-sending
                                # the query, the SDK may return the valid response as
                                # a bare ResultMessage without a preceding
                                # AssistantMessage TextBlock, making it look stale.
                                # Accept the result instead of discarding it — the
                                # re-sent query's answer IS the valid response.
                                _result_preview = (message.result or "")[:80]
                                logger.warning(
                                    "Stale ResultMessage after exhausting retries "
                                    "(retry_count=%d, num_turns=%d, saw_tool_use=%s): %s "
                                    "— accepting as valid response (false-positive guard)",
                                    _stale_retry_count, _num_turns, _saw_tool_use, _result_preview,
                                )
                                # Fall through to normal result handling below
                                # instead of discarding.

                            # --- Normal (non-stale) result handling ---
                            result_text = message.result
                            if result_text:
                                logger.debug(f"ResultMessage result_text: {result_text[:50]}...")
                                # Emit the final text as an SSE assistant event so the frontend
                                # can render it immediately, then accumulate it for DB persistence.
                                yield {
                                    "type": "assistant",
                                    "content": [{"type": "text", "text": result_text}],
                                    "model": agent_config.get("model", "claude-sonnet-4-20250514")
                                }
                                result_block = {"type": "text", "text": result_text}
                                assistant_content.add(result_block)

                    if isinstance(message, SystemMessage):
                        logger.info(f"SystemMessage subtype: {message.subtype}, data: {message.data}")

                        # The 'init' SystemMessage carries the SDK-assigned session ID.
                        # For new sessions we bootstrap persistence (store session + save
                        # the user message). For resumed sessions the session already exists
                        # in the DB, so we only capture the ID for later reference.
                        if message.subtype == 'init':
                            session_context["sdk_session_id"] = message.data.get('session_id')
                            _init_elapsed = time.monotonic() - _query_sent_at
                            logger.info(
                                "SDK init received in %.1fs, session_id: %s",
                                _init_elapsed, session_context['sdk_session_id'],
                            )

                            # Update thread_id now that we have the real session ID
                            try:
                                await _tscc_state_manager.get_or_create_state(
                                    session_context["sdk_session_id"], None, display_text[:50] if display_text else "Chat"
                                )
                                await _tscc_state_manager.set_lifecycle_state(session_context["sdk_session_id"], "active")
                            except Exception:
                                logger.debug("TSCC: lifecycle init failed", exc_info=True)

                            # Store system prompt metadata keyed by session_id
                            # so the /system-prompt endpoint can retrieve it.
                            _meta = agent_config.get("_system_prompt_metadata")
                            if _meta and session_context["sdk_session_id"]:
                                _system_prompt_metadata[session_context["sdk_session_id"]] = _meta

                            if session_context.get("app_session_id") is not None:
                                # Resume-fallback: the app session ID overrides
                                # the SDK's internal ID for client registration
                                # and persistence. session_start + user message
                                # were already emitted by _execute_on_session.
                                self._clients[session_context["app_session_id"]] = client
                                # Observability: log the session ID mapping for debugging
                                if session_context["sdk_session_id"] != session_context["app_session_id"]:
                                    logger.info(
                                        f"Resume-fallback: mapping SDK session "
                                        f"{session_context['sdk_session_id']} → "
                                        f"app session {session_context['app_session_id']}"
                                    )
                            elif not is_resuming:
                                # Register the client for potential reuse by continue_with_answer
                                self._clients[session_context["sdk_session_id"]] = client

                                yield {
                                    "type": "session_start",
                                    "sessionId": session_context["sdk_session_id"],
                                }

                                title = display_text[:50] + "..." if len(display_text) > 50 else display_text
                                await session_manager.store_session(session_context["sdk_session_id"], agent_id, title)

                                user_content = content if content else [{"type": "text", "text": user_message}]
                                await self._save_message(
                                    session_id=session_context["sdk_session_id"],
                                    role="user",
                                    content=user_content
                                )

                        # Forward task_started events so the frontend can show
                        # sub-agent activity (e.g. "Sub-agent: Explore frontend codebase")
                        if message.subtype == 'task_started':
                            yield {
                                "type": "agent_activity",
                                "description": message.data.get('description', 'Running sub-task'),
                                "taskType": message.data.get('task_type', 'unknown'),
                                "taskId": message.data.get('task_id'),
                            }

                        # Other SystemMessages are internal metadata — not forwarded
                        continue

                    # --- Stale-result tracking for AssistantMessage ---
                    # Track whether this stream has seen tool_use or fresh text
                    # blocks, which indicate the SDK is doing real work (not
                    # just replaying the previous turn's cached result).
                    if isinstance(message, AssistantMessage):
                        if not _got_first_real_message:
                            _ttfm = time.monotonic() - _query_sent_at
                            logger.info(
                                "Time to first AssistantMessage: %.1fs", _ttfm
                            )
                        _got_first_real_message = True
                        for _blk in message.content:
                            if isinstance(_blk, ToolUseBlock):
                                _saw_tool_use = True
                            elif isinstance(_blk, TextBlock):
                                _saw_new_text_block = True

                    # --- Format and dispatch non-system messages ---
                    # _format_message converts SDK message types (AssistantMessage, ToolUseMessage,
                    # etc.) into SSE-friendly dicts. Returns None for messages that shouldn't
                    # be forwarded to the frontend.
                    formatted = await self._format_message(message, agent_config, session_context["sdk_session_id"])
                    if formatted:
                        logger.debug(f"Formatted message type: {formatted.get('type')}")

                        # Accumulate assistant content blocks for later DB persistence.
                        # The accumulator deduplicates by block key (text content, tool_use id,
                        # tool_result tool_use_id) so repeated blocks from streaming don't
                        # produce duplicate entries in the saved message.
                        if formatted.get('type') == 'assistant' and formatted.get('content'):
                            assistant_content.extend(formatted['content'])
                            assistant_model = formatted.get('model')

                        yield formatted

                        # --- Evolution trigger: check tool results for repeated failures ---
                        if formatted.get('type') == 'assistant' and formatted.get('content'):
                            eff_sid = (
                                session_context.get("app_session_id")
                                or session_context.get("sdk_session_id")
                            )
                            tracker = (
                                self._active_sessions.get(eff_sid, {}).get("failure_tracker")
                                if eff_sid else None
                            )
                            if tracker:
                                # Build tool_use_id → tool_name map from this message's blocks
                                # and merge into session-level map for cross-message lookups
                                if eff_sid and eff_sid in self._active_sessions:
                                    _session_tool_names = self._active_sessions[eff_sid].setdefault("_tool_name_map", {})
                                else:
                                    _session_tool_names = {}
                                for b in formatted['content']:
                                    if b.get('type') == 'tool_use' and b.get('id') and b.get('name'):
                                        _session_tool_names[b['id']] = b['name']
                                for blk in formatted['content']:
                                    if blk.get('type') == 'tool_result' and blk.get('is_error'):
                                        tool_name = _session_tool_names.get(blk.get('tool_use_id'), 'unknown')
                                        error_text = str(blk.get('content', ''))[:200]
                                        nudge = check_tool_result_for_failure(
                                            tool_name, error_text, True, tracker
                                        )
                                        if nudge:
                                            # Store nudge for injection into next turn's query
                                            if eff_sid and eff_sid in self._active_sessions:
                                                self._active_sessions[eff_sid]["pending_evolution_nudge"] = nudge
                                            yield {
                                                "type": "system_nudge",
                                                "content": nudge,
                                                "nudge_type": "evolution_trigger",
                                            }
                                    elif blk.get('type') == 'tool_result' and not blk.get('is_error'):
                                        tool_name = _session_tool_names.get(blk.get('tool_use_id'), 'unknown')
                                        tracker.reset_tool(tool_name)

                        # Early-return events: ask_user_question and cmd_permission_request both
                        # pause the conversation to wait for external input. We persist any
                        # accumulated assistant content before returning so the partial
                        # response isn't lost if the user takes a long time to respond.
                        if formatted.get('type') == 'ask_user_question':
                            logger.info(f"AskUserQuestion detected, stopping to wait for user input")
                            try:
                                sid = session_context.get("sdk_session_id")
                                if sid:
                                    await _tscc_state_manager.set_lifecycle_state(sid, "paused")
                            except Exception:
                                logger.debug("TSCC: paused lifecycle failed", exc_info=True)
                            sdk_session = session_context.get("sdk_session_id")
                            # Use effective_session_id for persistence so
                            # assistant content is saved under the app session
                            # ID during resume-fallback scenarios.
                            eff_session = (
                                session_context["app_session_id"]
                                if session_context.get("app_session_id") is not None
                                else sdk_session
                            )
                            if assistant_content and eff_session:
                                await self._save_message(
                                    session_id=eff_session,
                                    role="assistant",
                                    content=assistant_content.blocks,
                                    model=assistant_model
                                )
                            return

                        if formatted.get('type') == 'cmd_permission_request':
                            request_id = formatted.get('requestId')
                            logger.info(f"Command permission request detected from message: {request_id}, stopping to wait for user decision")
                            sdk_session = session_context.get("sdk_session_id")
                            eff_session = (
                                session_context["app_session_id"]
                                if session_context.get("app_session_id") is not None
                                else sdk_session
                            )
                            if assistant_content and eff_session:
                                await self._save_message(
                                    session_id=eff_session,
                                    role="assistant",
                                    content=assistant_content.blocks,
                                    model=assistant_model
                                )
                            return

                    # --- Conversation-complete bookkeeping (second ResultMessage check) ---
                    # ResultMessage is checked again here (after formatting) because the first
                    # check above handles error subtypes and final text extraction, while this
                    # block handles end-of-conversation persistence and the result SSE event.
                    if isinstance(message, ResultMessage):
                        logger.info(f"Conversation complete. Total messages: {message_count}")

                        # --- TSCC: conversation complete (best-effort) ---
                        try:
                            sid = session_context.get("sdk_session_id")
                            if sid and not session_context.get("had_error"):
                                await _tscc_state_manager.set_lifecycle_state(sid, "idle")
                        except Exception:
                            logger.debug("TSCC: idle lifecycle failed", exc_info=True)

                        # Slash commands (e.g. /help) may produce no assistant content if the
                        # SDK handles them silently. Synthesize a default response so the
                        # frontend always has something to display.
                        is_slash_command = display_text.strip().startswith('/') if display_text else False
                        if is_slash_command and not assistant_content:
                            command_name = display_text.strip().split()[0] if display_text else '/unknown'
                            default_response = f"Command `{command_name}` executed."
                            logger.info(f"Slash command with no content, adding default response: {default_response}")
                            yield {
                                "type": "assistant",
                                "content": [{"type": "text", "text": default_response}],
                                "model": agent_config.get("model", "claude-sonnet-4-20250514")
                            }
                            assistant_content.add({"type": "text", "text": default_response})

                        # Persist the full deduplicated assistant response to the DB.
                        # This is the single write point for normal conversation completion;
                        # early-return paths (ask_user_question, cmd_permission_request) have
                        # their own persistence above.
                        if assistant_content and session_context["sdk_session_id"]:
                            eff_session = (
                                session_context["app_session_id"]
                                if session_context.get("app_session_id") is not None
                                else session_context["sdk_session_id"]
                            )
                            await self._save_message(
                                session_id=eff_session,
                                role="assistant",
                                content=assistant_content.blocks,
                                model=assistant_model
                            )

                        # Compaction notification — if the PreCompact hook fired during
                        # this turn, emit an SSE event so the frontend can notify the user.
                        if session_context.get("_compacted"):
                            compact_trigger = session_context.pop("_compact_trigger", "auto")
                            session_context.pop("_compacted", None)
                            yield {
                                "type": "context_compacted",
                                "session_id": (
                                    session_context["app_session_id"]
                                    if session_context.get("app_session_id") is not None
                                    else session_context["sdk_session_id"]
                                ),
                                "trigger": compact_trigger,
                            }

                        # Terminal SSE event — signals the frontend that the turn is complete
                        # and carries usage metrics for display.
                        usage = getattr(message, 'usage', None) or {}
                        yield {
                            "type": "result",
                            "session_id": (
                                session_context["app_session_id"]
                                if session_context.get("app_session_id") is not None
                                else session_context["sdk_session_id"]
                            ),
                            "duration_ms": getattr(message, 'duration_ms', 0),
                            "total_cost_usd": getattr(message, 'total_cost_usd', None),
                            "num_turns": getattr(message, 'num_turns', 1),
                            "usage": {
                                "input_tokens": usage.get("input_tokens"),
                                "output_tokens": usage.get("output_tokens"),
                                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                            } if usage else None,
                        }

                        # NOTE: Auto-commit moved to WorkspaceAutoCommitHook
                        # (fires at session close, not per-turn). See hooks/auto_commit_hook.py.
        finally:
            # Cancel ALL spawned reader tasks (defense-in-depth).
            # In practice only the current-generation reader should be alive,
            # but the list covers edge cases where cancellation didn't complete.
            for task in _reader_tasks:
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            logger.debug("All SDK reader tasks cancelled")

            if forwarder_task and not forwarder_task.done():
                forwarder_task.cancel()
                try:
                    await forwarder_task
                except asyncio.CancelledError:
                    pass
                logger.debug("Forwarder task cancelled")

            # Remove from _clients tracking so stale references aren't reused,
            # but keep _active_sessions intact — the session may still be valid for
            # future continue_with_answer calls via the SDK's resume mechanism.
            # Use effective_session_id since the client may be registered under
            # app_session_id (resume-fallback) or sdk_session_id (new conversation).
            eff_sid = (
                session_context["app_session_id"]
                if session_context.get("app_session_id") is not None
                else session_context.get("sdk_session_id")
            )
            if eff_sid:
                self._clients.pop(eff_sid, None)

    async def _format_message(self, message: Any, agent_config: dict, session_id: Optional[str] = None) -> Optional[dict]:
        """Format SDK message to API response format."""

        if isinstance(message, AssistantMessage):
            content_blocks = []

            for block in message.content:
                if isinstance(block, TextBlock):
                    content_blocks.append({
                        "type": "text",
                        "text": block.text
                    })
                elif isinstance(block, ToolUseBlock):
                    # Check if this is an AskUserQuestion tool call
                    if block.name == "AskUserQuestion":
                        # Return special ask_user_question event
                        questions = block.input.get("questions", [])
                        event = {
                            "type": "ask_user_question",
                            "toolUseId": block.id,
                            "questions": questions
                        }
                        # Include session_id so frontend can continue the conversation
                        if session_id:
                            event["sessionId"] = session_id
                        return event
                    # Note: Dangerous Bash command detection is handled by the human_approval_hook
                    # which runs BEFORE execution and can actually block it. Detection here
                    # would be too late - the SDK has already decided to execute the tool.
                    # The hook denial is detected in ToolResultBlock below.

                    # Regular tool use block — emit summary instead of full input
                    from core.tool_summarizer import summarize_tool_use, get_tool_category
                    summary = summarize_tool_use(block.name, block.input)
                    content_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "summary": summary,
                        "category": get_tool_category(block.name),
                    })
                elif isinstance(block, ToolResultBlock):
                    block_content = str(block.content) if block.content else ""

                    # Note: Permission request handling is now done via the queue mechanism
                    # and emitted directly in the message loop before formatting.
                    # This ToolResultBlock will just contain the normal tool output.

                    from core.tool_summarizer import truncate_tool_result
                    truncated_content, was_truncated = truncate_tool_result(block_content)
                    content_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": truncated_content,
                        "is_error": getattr(block, 'is_error', False),
                        "truncated": was_truncated,
                    })

            if content_blocks:
                return {
                    "type": "assistant",
                    "content": content_blocks,
                    "model": getattr(message, 'model', agent_config.get("model", "claude-sonnet-4-20250514"))
                }

        elif isinstance(message, ResultMessage):
            # Return None here, we handle ResultMessage separately to include session_id
            return None

        return None

    async def continue_with_answer(
        self,
        agent_id: str,
        session_id: str,
        tool_use_id: str,
        answers: dict[str, str],
        enable_skills: bool = False,
        enable_mcp: bool = False,
    ) -> AsyncIterator[dict]:
        """Continue conversation by providing answers to AskUserQuestion.

        Reuses the existing long-lived client for the session if available,
        otherwise falls back to creating a new client with --resume.

        Args:
            agent_id: The agent ID
            session_id: The session ID (required for conversation continuity)
            tool_use_id: The tool_use_id from the AskUserQuestion event (for reference)
            answers: Dictionary mapping question text to answer text
            enable_skills: Whether to enable skills
            enable_mcp: Whether to enable MCP servers

        Yields:
            Formatted messages from the agent
        """
        # Get agent config
        agent_config = await db.agents.get(agent_id)
        if not agent_config:
            yield {
                "type": "error",
                "error": f"Agent {agent_id} not found",
            }
            return

        logger.info(f"Continuing conversation with answer for agent {agent_id}, session {session_id}")
        logger.info(f"Tool use ID: {tool_use_id}, Answers: {answers}")

        # Format answers as a user message
        answer_message = json.dumps({"answers": answers}, indent=2)

        # Defer user answer save — pass to _execute_on_session so the answer
        # is saved under the correct session ID even if resume-fallback occurs.
        # (Previously saved eagerly here, which caused duplicates on restart.)

        # Delegate to shared session execution pattern.
        # Track effective_sid for context monitoring (same pattern as run_conversation).
        effective_sid: str | None = None
        # Capture SDK usage data from the result event for inline context
        # monitoring (local to this generator — safe for multi-tab).
        last_input_tokens: Optional[int] = None
        last_model: Optional[str] = None

        async for event in self._execute_on_session(
            agent_config=agent_config,
            query_content=answer_message,
            display_text=answer_message[:100],
            session_id=session_id,
            enable_skills=enable_skills,
            enable_mcp=enable_mcp,
            is_resuming=True,
            content=None,
            user_message=answer_message,
            agent_id=agent_id,
            app_session_id=session_id,
            deferred_user_content=[{"type": "text", "text": f"User answers:\n{answer_message}"}],
        ):
            # Capture session_id and usage data from the result event
            if event.get("type") == "result":
                if event.get("session_id"):
                    effective_sid = event["session_id"]
                _usage = event.get("usage")
                if _usage:
                    last_input_tokens = self._sum_usage_input_tokens(_usage)
                    # Normalize cumulative SDK usage by num_turns
                    # (same logic as run_conversation — see comment there).
                    _n_turns = event.get("num_turns") or 1
                    if _n_turns > 1:
                        last_input_tokens = last_input_tokens // _n_turns
                last_model = agent_config.get("model")
            yield event

        # Post-response context monitor (same helper as run_conversation)
        if effective_sid:
            try:
                turns = self._user_turn_counts.get(effective_sid, 0) + 1
                self._user_turn_counts[effective_sid] = turns

                warning_event = self._build_context_warning(last_input_tokens, last_model)
                if warning_event:
                    if warning_event["level"] in ("warn", "critical"):
                        logger.info(
                            "Context monitor [%s]: %s (%d%%, ~%dK tokens)",
                            warning_event["level"], effective_sid,
                            warning_event["pct"], warning_event["tokensEst"] // 1000,
                        )
                    else:
                        logger.debug(
                            "Context monitor [ok]: %d%% after %d turns",
                            warning_event["pct"], turns,
                        )
                    yield warning_event
            except Exception:
                logger.debug("Context monitor check failed", exc_info=True)

    async def continue_with_cmd_permission(
        self,
        agent_id: str,
        session_id: str,
        request_id: str,
        decision: str,  # "approve" or "deny"
        feedback: Optional[str] = None,
        enable_skills: bool = False,
        enable_mcp: bool = False,
    ) -> AsyncIterator[dict]:
        """Continue conversation after user makes a permission decision.

        Args:
            agent_id: The agent ID
            session_id: The session ID
            request_id: The permission request ID
            decision: User's decision ("approve" or "deny")
            feedback: Optional feedback from user
            enable_skills: Whether to enable skills
            enable_mcp: Whether to enable MCP servers

        Yields:
            Formatted messages from the agent
        """
        # Get agent config
        agent_config = await db.agents.get(agent_id)
        if not agent_config:
            yield {
                "type": "error",
                "error": f"Agent {agent_id} not found",
            }
            return

        # Get permission request details from in-memory store
        permission_request = _pm.get_pending_request(request_id)
        if not permission_request:
            yield {
                "type": "error",
                "error": f"Permission request {request_id} not found",
            }
            return

        # Update permission request status in memory
        _pm.update_pending_request(request_id, {
            "status": decision,
            "decided_at": datetime.now().isoformat(),
            "user_feedback": feedback,
        })

        logger.info(f"Permission decision for request {request_id}: {decision}")
        logger.info(f"Continuing conversation for agent {agent_id}, session {session_id}")

        # Parse the original command from permission request
        tool_input = permission_request.get("tool_input", "{}")
        if isinstance(tool_input, str):
            tool_input = json.loads(tool_input)
        command = tool_input.get("command", "unknown command")

        # Get the session_key used by the hook (stored in permission_request)
        # This ensures the approval is stored with the same key the hook will check
        perm_session_id = permission_request.get("session_id", session_id)
        logger.info(f"Using permission session_id for approval: {perm_session_id}")

        # Format decision as a user message
        if decision == "approve":
            decision_message = f"User APPROVED the command. Please proceed with executing: {command}"
            # Store approval in CmdPermissionManager (shared, persistent, filesystem-backed)
            # Falls back to legacy per-session PermissionManager if CmdPermissionManager
            # rejects the pattern as overly broad.
            try:
                self._cmd_pm.approve(command)
                logger.info(f"Command approved via CmdPermissionManager: {command[:50]}...")
            except ValueError as exc:
                logger.warning(f"CmdPermissionManager rejected pattern: {exc}, falling back to per-session approval")
                _pm.approve_command(perm_session_id, command)
        else:
            reason = feedback if feedback else "User denied the command"
            decision_message = f"User DENIED the command '{command}'. Reason: {reason}. Please acknowledge this and continue without executing that command."

        # CRITICAL: Notify the waiting hook to continue execution
        # This will unblock the original SDK client that's waiting in the hook
        set_permission_decision(request_id, decision)
        logger.info(f"Permission decision sent to waiting hook: {request_id} -> {decision}")

        # Save user decision to database
        await self._save_message(
            session_id=session_id,
            role="user",
            content=[{"type": "text", "text": decision_message}]
        )

        # The original stream will continue processing and send results back
        # Just return a simple acknowledgment here to close this new stream
        yield {
            "type": "permission_acknowledged",
            "request_id": request_id,
            "decision": decision,
        }
        logger.info(f"Permission decision processed, original stream will handle execution")

    async def disconnect_all(self):
        """Disconnect all active clients and long-lived sessions.

        Four-phase shutdown optimised for the 10s Tauri grace period:

        **Phase 0 — Metrics snapshot**
            Log session count, ``activity_extracted`` counts, and pending
            hook tasks for post-mortem diagnostics.

        **Phase 1a — Parallel HookContext construction**
            Build all HookContexts concurrently via ``asyncio.gather``.
            Failed builds are logged and excluded from subsequent phases.

        **Phase 1b — Inline DailyActivity extraction**
            Run DA extraction directly (NOT via BackgroundHookExecutor)
            for every session that hasn't already been extracted.  Each
            session gets a 5 s per-task timeout; the entire DA batch has
            an 8 s global cap.  This mirrors ``_extract_activity_early``.

        **Phase 1c — Idempotent hooks (fire-and-forget)**
            Queue remaining hooks (auto-commit, distillation, evolution)
            via the executor with ``skip_hooks=["daily_activity_extraction"]``.

        **Phase 1d — Session resource cleanup**
            ``_cleanup_session(skip_hooks=True)`` for every session.
            Must happen AFTER DA extraction — cleanup pops sessions from
            ``_active_sessions``, which would remove info hooks still need.

        **Phase 2 — Drain**
            ``drain(timeout=8.0)`` gives idempotent hooks best-effort
            completion time.  With many sessions most will be cancelled;
            all are idempotent so this is acceptable.
        """
        t0 = time.monotonic()

        # ── Phase 0: Snapshot & metrics ──────────────────────────────
        sessions = list(self._active_sessions.items())
        if not sessions:
            logger.info("Shutdown: no active sessions — fast return")
            # Still cancel the cleanup loop and clear transient clients
            if self._cleanup_task and not self._cleanup_task.done():
                self._cleanup_task.cancel()
            self._clients.clear()
            return

        extracted_count = sum(
            1 for _, info in sessions if info.get("activity_extracted")
        )
        pending_hooks = (
            self._hook_executor.pending_count if self._hook_executor else 0
        )
        logger.info(
            "Shutdown Phase 0: %d sessions (%d with activity_extracted), "
            "%d pending hook tasks",
            len(sessions),
            extracted_count,
            pending_hooks,
        )

        # ── Phase 1a: Parallel HookContext construction ──────────────
        t1a = time.monotonic()
        raw_contexts = await asyncio.gather(
            *[self._build_hook_context(sid, info) for sid, info in sessions],
            return_exceptions=True,
        )

        # Pair each result with its (session_id, info) and filter errors
        ctx_pairs: list[tuple[str, dict, "HookContext"]] = []
        for (sid, info), result in zip(sessions, raw_contexts):
            if isinstance(result, BaseException):
                logger.error(
                    "Shutdown: HookContext build failed for %s: %s", sid, result
                )
            else:
                ctx_pairs.append((sid, info, result))

        logger.info(
            "Shutdown Phase 1a: built %d/%d HookContexts in %.2fs",
            len(ctx_pairs),
            len(sessions),
            time.monotonic() - t1a,
        )

        # ── Phase 1b: Inline DailyActivity extraction ────────────────
        # Find the DA hook by name (same pattern as _extract_activity_early).
        # Prefer _hook_executor.hooks (public attr); fall back to
        # _hook_manager._hooks only if executor is absent (defensive,
        # should not happen after hook-execution-decoupling spec).
        da_hook = None
        if self._hook_executor:
            for hook in self._hook_executor.hooks:
                if getattr(hook, "name", "") == "daily_activity_extraction":
                    da_hook = hook
                    break
        elif self._hook_manager:
            for hook in self._hook_manager._hooks:
                if getattr(hook, "name", "") == "daily_activity_extraction":
                    da_hook = hook
                    break

        # Build DA task list for sessions not yet extracted
        da_session_map: list[tuple[int, str, dict]] = []  # (index, sid, info)
        da_tasks: list = []
        if da_hook:
            for i, (sid, info, ctx) in enumerate(ctx_pairs):
                if not info.get("activity_extracted"):
                    da_tasks.append(
                        asyncio.wait_for(da_hook.execute(ctx), timeout=5.0)
                    )
                    da_session_map.append((i, sid, info))

        if da_tasks:
            t1b = time.monotonic()
            logger.info(
                "Shutdown Phase 1b: running DA extraction for %d sessions",
                len(da_tasks),
            )
            try:
                da_results = await asyncio.wait_for(
                    asyncio.gather(*da_tasks, return_exceptions=True),
                    timeout=8.0,
                )
                # Mark successful extractions
                for (idx, sid, info), result in zip(da_session_map, da_results):
                    if isinstance(result, BaseException):
                        elapsed = time.monotonic() - t1b
                        logger.warning(
                            "Shutdown: DA extraction failed/timed-out for %s "
                            "(%.2fs): %s",
                            sid,
                            elapsed,
                            result,
                        )
                    else:
                        # Safe to mutate: gather() has returned, so this is
                        # sequential.  Phase 1c/1d read the same info refs
                        # but run strictly after this loop completes.
                        info["activity_extracted"] = True
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - t1b
                logger.warning(
                    "Shutdown Phase 1b: global 8s DA timeout reached after "
                    "%.2fs — proceeding to Phase 1c",
                    elapsed,
                )
            finally:
                da_elapsed = time.monotonic() - t1b
                logger.info("Shutdown Phase 1b: DA phase completed in %.2fs", da_elapsed)
        else:
            logger.info(
                "Shutdown Phase 1b: no DA extractions needed "
                "(all sessions already extracted or no DA hook)"
            )

        # ── Phase 1c: Fire idempotent hooks via executor ─────────────
        if self._hook_executor:
            for sid, info, ctx in ctx_pairs:
                try:
                    self._hook_executor.fire(
                        ctx, skip_hooks=["daily_activity_extraction"]
                    )
                except Exception as exc:
                    logger.error(
                        "Shutdown: idempotent hook fire failed for %s: %s",
                        sid,
                        exc,
                    )

        # ── Phase 1d: Session resource cleanup ───────────────────────
        for sid, info, ctx in ctx_pairs:
            await self._cleanup_session(sid, skip_hooks=True)
        # Also clean up sessions that failed HookContext build
        built_sids = {sid for sid, _, _ in ctx_pairs}
        for sid, info in sessions:
            if sid not in built_sids:
                await self._cleanup_session(sid, skip_hooks=True)

        # Clean up any remaining transient clients
        for session_id, client in list(self._clients.items()):
            try:
                logger.info(f"Disconnecting client for session {session_id}")
                await client.interrupt()
            except Exception as e:
                logger.error(f"Error disconnecting client {session_id}: {e}")
        self._clients.clear()

        # Cancel the cleanup loop
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()

        # ── Phase 2: Drain background hooks (idempotent only) ────────
        if self._hook_executor:
            pending = self._hook_executor.pending_count
            logger.info("Shutdown Phase 2: %d hook tasks in flight before drain", pending)
            t2 = time.monotonic()
            done, cancelled = await self._hook_executor.drain(timeout=8.0)
            drain_elapsed = time.monotonic() - t2
            logger.info(
                "Shutdown Phase 2 drain: %d done, %d cancelled in %.2fs",
                done,
                cancelled,
                drain_elapsed,
            )
            if cancelled:
                logger.warning(
                    "Shutdown: %d hook tasks cancelled (DA extraction may be lost if incomplete)",
                    cancelled,
                )

        total_elapsed = time.monotonic() - t0
        logger.info("Shutdown disconnect_all completed in %.2fs", total_elapsed)

    async def interrupt_session(self, session_id: str) -> dict:
        """Interrupt a running session.

        Looks up the client from ``_active_sessions`` (persistent between
        turns) rather than ``_clients`` (transient, only exists during
        active streaming and is popped in the finally block of
        ``_run_query_on_client``).  Falls back to ``_clients`` for
        backward compatibility with sessions that haven't been stored
        in ``_active_sessions`` yet (e.g. mid-init).

        Args:
            session_id: The session ID to interrupt

        Returns:
            Dict with status information
        """
        # Primary lookup: _active_sessions (persistent, survives stream end)
        client = None
        info = self._active_sessions.get(session_id)
        if info:
            client = info.get("client")
        # Fallback: _clients (transient, only during active streaming)
        if not client:
            client = self._clients.get(session_id)
        if not client:
            logger.warning(f"No active client found for session {session_id}")
            return {
                "success": False,
                "message": f"No active session found with ID {session_id}",
            }

        try:
            logger.info(f"Interrupting session {session_id}")
            # Set interrupted flag BEFORE calling interrupt() so the error handler
            # in _run_query_on_client can distinguish user-initiated stops from
            # genuine errors. We match by client reference (not key) because
            # _active_sessions may be keyed by app_session_id while _clients
            # is keyed by the session_id passed from the frontend.
            for sid, info in self._active_sessions.items():
                if info.get("client") is client:
                    info["interrupted"] = True
                    logger.info(f"Set interrupted flag on _active_sessions[{sid}]")
                    break
            await client.interrupt()
            logger.info(f"Session {session_id} interrupted successfully")
            return {
                "success": True,
                "message": "Session interrupted successfully",
            }
        except Exception as e:
            logger.error(f"Error interrupting session {session_id}: {e}")
            return {
                "success": False,
                "message": f"Failed to interrupt session: {str(e)}",
            }

    async def compact_session(self, session_id: str, instructions: Optional[str] = None) -> dict:
        """Trigger manual compaction of a session's context window.

        Sends the ``/compact`` slash command to the Claude CLI subprocess via
        ``client.query()``.  The CLI compresses the conversation history into a
        summary, freeing context space for further turns.

        The client is looked up in ``_active_sessions`` (the long-lived store
        that persists between turns), NOT ``_clients`` (which only exists during
        active streaming).  A per-session lock prevents this from racing with
        an active ``_run_query_on_client`` stream.

        Args:
            session_id: The session ID to compact.
            instructions: Optional natural-language guidance for what the
                compaction should preserve (appended after ``/compact``).

        Returns:
            Dict with ``success`` bool and ``message`` string.
        """
        # Look up the long-lived client (persists between turns)
        client = self._get_active_client(session_id)
        if not client:
            logger.warning(f"compact_session: no active client for {session_id}")
            return {
                "success": False,
                "message": f"No active session found with ID {session_id}",
            }

        # Acquire session lock to prevent racing with an active stream.
        # If a stream is running, the lock will be held — use non-blocking
        # try to avoid deadlock and return an informative error instead.
        session_lock = self._get_session_lock(session_id)
        if session_lock.locked():
            return {
                "success": False,
                "message": "Session is currently processing a message. Wait for it to finish before compacting.",
            }

        async with session_lock:
            try:
                command = "/compact"
                if instructions:
                    command = f"/compact {instructions}"
                logger.info(f"Compacting session {session_id}: {command}")

                # Send the slash command via query() and drain the response
                # stream so the CLI processes it fully.
                await client.query(prompt=command, session_id=session_id)
                async for _msg in client.receive_response():
                    pass  # drain

                return {
                    "success": True,
                    "message": "Session compacted successfully",
                }
            except Exception as e:
                logger.error(f"Error compacting session {session_id}: {e}")
                return {
                    "success": False,
                    "message": f"Failed to compact session: {str(e)}",
                }

    async def run_skill_creator_conversation(
        self,
        skill_name: str,
        skill_description: str,
        user_message: Optional[str] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> AsyncIterator[dict]:
        """Run a skill creation conversation with a specialized Skill Creator Agent.

        This creates a temporary agent configuration specifically for skill creation,
        using the skill-creator skill to guide the process.

        Args:
            skill_name: Name of the skill to create
            skill_description: Description of what the skill should do
            user_message: Optional follow-up message for iterating on the skill
            session_id: Optional session ID for continuing conversation
            model: Optional model to use (defaults to claude-sonnet-4-5-20250514)

        Yields:
            Formatted messages from the agent
        """
        # Check if resuming or new session
        # For new sessions, session_id will be captured from SDK's init message
        is_resuming = session_id is not None

        # Build the initial prompt or use the follow-up message
        if user_message:
            # This is a follow-up message for iteration
            prompt = user_message
        else:
            # Initial skill creation request
            prompt = f"""Please create a new skill with the following specifications:

**Skill Name:** {skill_name}
**Skill Description:** {skill_description}

Use the skill-creator skill (invoke /skill-creator) to guide your skill creation process. Follow the workflow:
1. Understand the skill requirements from the description above
2. Plan reusable contents (scripts, references, assets) if needed
3. Initialize the skill using the init_skill.py script
4. Edit SKILL.md and create any necessary files
5. Test any scripts you create

Create the skill in the `.claude/skills/` directory within the current workspace."""

        # Build system prompt for skill creator agent
        system_prompt = SKILL_CREATOR_SYSTEM_PROMPT_TEMPLATE.format(
            skill_name=skill_name,
            skill_description=skill_description,
        )

        # Create temporary agent config for skill creation
        agent_config = {
            "name": f"skill-creator-{session_id[:8] if session_id else 'new'}",
            "description": "Temporary agent for skill creation",
            "system_prompt": system_prompt,
            "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill","TodoWrite","Task"],
            "permission_mode": "bypassPermissions",
            "working_directory": None,  # Uses cached SwarmWorkspace path via initialization_manager
            "global_user_mode": False,  # Use workspace dir, not home dir
            "enable_tool_logging": True,
            "enable_safety_checks": True,
            "model": model or "claude-sonnet-4-5-20250929",  # Default to Sonnet 4.5
        }

        logger.info(f"Running skill creator conversation for '{skill_name}', session {session_id}, model {agent_config['model']}, is_resuming={is_resuming}")

        # Per-session concurrency guard — prevents double-send corruption.
        # For new sessions (session_id is None), generate an ephemeral UUID
        # so parallel new skill-creator sessions don't collide.
        lock_key = session_id or str(uuid4())
        is_ephemeral_lock = (session_id is None)
        if is_ephemeral_lock:
            logger.info(f"Using ephemeral lock key {lock_key} for new skill creator session")
        else:
            logger.debug(f"Using stable lock key {lock_key} for skill creator session")
        session_lock = self._get_session_lock(lock_key)

        if session_lock.locked():
            logger.warning(
                "Skill creator session %s is already executing — rejecting concurrent request",
                lock_key,
            )
            yield _build_error_event(
                code="SESSION_BUSY",
                message="This skill creation session is still processing. Please wait for it to finish.",
                suggested_action="Wait for the current response to complete, then try again.",
            )
            return

        # Defer session_start and store_session for resumed sessions until
        # after the SDK client path is determined (same pattern as
        # run_conversation). This prevents duplicate session_start events
        # when the backend restarts and falls back to a fresh SDK session.

        try:
            async with session_lock:
                # Configure Claude environment variables
                # TODO(task-9.2): Replace with self._config once AppConfigManager is
                # wired into AgentManager constructor.
                from core.app_config_manager import AppConfigManager as _ACM
                _cfg = _ACM()
                _cfg.load()
                async with _env_lock:
                    _configure_claude_environment(_cfg)

                # Track the actual SDK session_id
                session_context = {"sdk_session_id": session_id}  # Will be updated for new sessions
                # Track app_session_id for resume-fallback (same pattern as _execute_on_session)
                session_context["app_session_id"] = session_id if is_resuming else None
                assistant_content = ContentBlockAccumulator()

                # Try to reuse existing long-lived client for resume
                reused_client = self._get_active_client(session_id) if is_resuming else None

                # Default: no context injection needed for non-resuming requests
                agent_config["needs_context_injection"] = False

                try:
                    if reused_client and session_id:
                        # Reuse existing client
                        agent_config["needs_context_injection"] = False
                        client = reused_client
                        logger.info(f"Reusing long-lived client for skill creator, session {session_id}")
                        self._clients[session_id] = client

                        # Deferred save for resumed conversations (reused client path):
                        if session_context.get("app_session_id") is not None:
                            yield {
                                "type": "session_start",
                                "sessionId": session_context["app_session_id"],
                            }
                            title = f"Creating skill: {skill_name}"
                            await session_manager.store_session(session_context["app_session_id"], "skill-creator", title)
                            await self._save_message(
                                session_id=session_context["app_session_id"],
                                role="user",
                                content=[{"type": "text", "text": prompt}],
                            )

                        async for event in self._run_query_on_client(
                            client=client,
                            query_content=prompt,
                            display_text=f"Creating skill: {skill_name}",
                            agent_config=agent_config,
                            session_context=session_context,
                            assistant_content=assistant_content,
                            is_resuming=is_resuming,
                            content=None,
                            user_message=prompt,
                            agent_id="skill-creator",
                        ):
                            if event.get("type") == "result":
                                event["skill_name"] = skill_name
                            yield event

                        # PATH B cleanup (same pattern as _execute_on_session)
                        if session_context.get("had_error") and session_id:
                            logger.info(
                                f"PATH B (skill-creator): reused client for {session_id} "
                                f"had error, evicting from active sessions"
                            )
                            info = self._active_sessions.pop(session_id, None)
                            if info and info.get("wrapper"):
                                try:
                                    await asyncio.wait_for(
                                        info["wrapper"].__aexit__(None, None, None),
                                        timeout=5.0,
                                    )
                                except (asyncio.TimeoutError, Exception):
                                    logger.warning(
                                        f"PATH B (skill-creator): wrapper disconnect "
                                        f"timed out or failed for {session_id}"
                                    )
                            self._clients.pop(session_id, None)
                    else:
                        # No active client — start fresh (--resume won't work with SDK 0.1.34+)
                        if is_resuming:
                            logger.info(f"No active client for skill creator session {session_id}, starting fresh")
                            is_resuming = False
                            session_context["sdk_session_id"] = None
                            # Observability: log the resume-fallback path
                            if session_context.get("app_session_id") is not None:
                                logger.info(
                                    f"Resume-fallback in run_skill_creator_conversation: "
                                    f"no active client for app session {session_context['app_session_id']}, "
                                    f"creating fresh SDK session"
                                )
                            # Flag for context injection: we lost the SDK client, so
                            # inject previous conversation context into the system prompt.
                            agent_config["needs_context_injection"] = True
                            agent_config["resume_app_session_id"] = session_id

                        # Deferred save for resumed conversations (fresh client path):
                        if session_context.get("app_session_id") is not None:
                            yield {
                                "type": "session_start",
                                "sessionId": session_context["app_session_id"],
                            }
                            title = f"Creating skill: {skill_name}"
                            await session_manager.store_session(session_context["app_session_id"], "skill-creator", title)
                            await self._save_message(
                                session_id=session_context["app_session_id"],
                                role="user",
                                content=[{"type": "text", "text": prompt}],
                            )

                        options = await self._build_options(agent_config, enable_skills=True, enable_mcp=False)
                        logger.info(f"Skill creator options - allowed_tools: {options.allowed_tools}")
                        logger.info(f"Working directory: {options.cwd}")

                        wrapper = _ClaudeClientWrapper(options=options)
                        # Hold _env_lock during client creation so the spawned
                        # subprocess inherits the correct os.environ values.
                        async with _env_lock:
                            _configure_claude_environment(_cfg)
                            client = await wrapper.__aenter__()
                        logger.info(f"ClaudeSDKClient created for skill creation")

                        try:
                            async for event in self._run_query_on_client(
                                client=client,
                                query_content=prompt,
                                display_text=f"Creating skill: {skill_name}",
                                agent_config=agent_config,
                                session_context=session_context,
                                assistant_content=assistant_content,
                                is_resuming=is_resuming,
                                content=None,
                                user_message=prompt,
                                agent_id="skill-creator",
                            ):
                                if event.get("type") == "result":
                                    event["skill_name"] = skill_name
                                yield event
                        except Exception:
                            try:
                                await wrapper.__aexit__(None, None, None)
                            except Exception:
                                pass
                            raise

                        # Store for reuse — key by effective_session_id so the next
                        # resume finds the client under the original tab session ID.
                        final_session_id = session_context["sdk_session_id"]
                        effective_session_id = (
                            session_context["app_session_id"]
                            if session_context.get("app_session_id") is not None
                            else final_session_id
                        )
                        if effective_session_id:
                            self._active_sessions[effective_session_id] = {
                                "client": client,
                                "wrapper": wrapper,
                                "created_at": time.time(),
                                "last_used": time.time(),
                                "activity_extracted": False,
                                "failure_tracker": ToolFailureTracker(),
                            }
                            logger.info(f"Stored long-lived client for skill creator session {effective_session_id}")

                except Exception as e:
                    error_traceback = traceback.format_exc()
                    logger.error(f"Error in skill creation conversation: {e}")
                    logger.error(f"Full traceback:\n{error_traceback}")
                    # Clean up broken session — use effective_session_id
                    eff_sid = (
                        session_context["app_session_id"]
                        if session_context.get("app_session_id") is not None
                        else session_context.get("sdk_session_id")
                    )
                    if eff_sid and eff_sid in self._active_sessions:
                        await self._cleanup_session(eff_sid, skip_hooks=True)
                    yield _build_error_event(
                        code="SKILL_CREATION_ERROR",
                        message=str(e),
                        detail=error_traceback,
                    )
        finally:
            # Clean up ephemeral lock keys to prevent unbounded memory growth.
            # Non-ephemeral keys are cleaned up by _cleanup_session().
            if is_ephemeral_lock:
                self._session_locks.pop(lock_key, None)


# Global instance
agent_manager = AgentManager()
