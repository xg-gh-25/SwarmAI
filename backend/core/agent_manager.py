"""Legacy AgentManager — DEPRECATED, retained only for skill creator flow.

The multi-session architecture (SessionUnit, SessionRouter, PromptBuilder,
LifecycleManager) is the active code path for ALL chat endpoints.  See
``session_registry.py`` for the module-level singletons.

This module is kept ONLY because ``run_skill_creator_conversation()``
is deeply entangled with ``_run_query_on_client()`` and ``_build_options()``.
Once the skill creator is migrated to SessionRouter, this file can be
deleted entirely.

Re-exports from extracted modules are preserved for backward compatibility.
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
import signal
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
from claude_agent_sdk.types import StreamEvent

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
    build_agent_config,
    agent_exists,
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

# CredentialValidator — pre-flight STS check for Bedrock credentials.
from .credential_validator import CredentialValidator

# AppConfigManager — file-based config with in-memory cache.
from .app_config_manager import AppConfigManager


# ContentBlockAccumulator extracted to content_accumulator.py
from .content_accumulator import ContentBlockAccumulator  # noqa: F401 — used internally




# Security hooks extracted to security_hooks.py — used internally by hook_builder
from .security_hooks import (
    pre_tool_logger,
    create_file_access_permission_handler,
    create_skill_access_checker,
)

# MCP config and hook building extracted to dedicated modules
from .mcp_config_loader import (
    load_mcp_config as _load_mcp_config_fn,
    inject_channel_mcp as _inject_channel_mcp_fn,
)
from .hook_builder import build_hooks as _build_hooks_fn

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


# ---------------------------------------------------------------------------
# SDK error sanitization — translate raw CLI errors to user-friendly messages
# ---------------------------------------------------------------------------

# Patterns: (regex, friendly_message, suggested_action)
_SDK_ERROR_PATTERNS: list[tuple[str, str, str]] = [
    (
        r"(?:Cannot write to terminated process|Command failed with exit code -9|exit code: -9)",
        "The AI service connection was interrupted.",
        "This is usually temporary. Your conversation is saved — just send your message again.",
    ),
    (
        r"exit code: -(?:6|11|15)",
        "The AI service process ended unexpectedly.",
        "Your conversation is saved. Send your message again to continue.",
    ),
    (
        r"(?:SIGTERM|SIGKILL|signal \d+)",
        "The AI service was stopped by the system.",
        "This can happen during high memory usage. Your conversation is saved.",
    ),
    (
        r"(?:broken pipe|connection reset|EPIPE|ECONNRESET)",
        "Lost connection to the AI service.",
        "Reconnecting automatically. If this persists, try restarting the app.",
    ),
]

def _sanitize_sdk_error(raw_error: str) -> tuple[str, str | None]:
    """Map raw SDK error strings to user-friendly messages.

    Returns (friendly_message, suggested_action).  If no pattern matches,
    returns the original message with a generic suggestion.
    """
    for pattern, friendly, action in _SDK_ERROR_PATTERNS:
        if re.search(pattern, raw_error, re.IGNORECASE):
            return friendly, action
    # No match — return original but add a generic suggestion
    return raw_error, "Your conversation is saved. Send your message again to continue."


def _is_retriable_error(raw_error: str) -> bool:
    """Check if this SDK error is transient and should be auto-retried silently.

    When True, the error event should NOT be yielded to the frontend — the
    auto-retry path (PATH B → PATH A) will handle the UX with a softer
    "reconnecting" indicator instead.

    Covers two categories:
    1. Process-level failures (OOM kill, broken pipe) — the CLI died
    2. Bedrock API transient errors (throttling, overload, 5xx) — the API
       returned a retriable status code but the CLI didn't retry internally
    """
    retriable_patterns = [
        # Process-level failures
        r"exit code: -9",
        r"Cannot write to terminated process",
        r"Command failed with exit code -9",
        r"broken pipe",
        r"EPIPE",
        # Bedrock / Anthropic API transient errors
        r"throttl",                          # ThrottlingException, throttled, etc.
        r"too many requests",                # HTTP 429
        r"rate.?limit",                      # rate_limit, rate limit exceeded
        r"service.?unavailable",             # HTTP 503
        r"internal.?server.?error",          # HTTP 500
        r"overloaded",                       # Anthropic overloaded_error
        r"capacity",                         # InsufficientCapacity (Bedrock)
        r"ECONNRESET",                       # Connection reset
        r"connection reset",
        r"SDK_SUBPROCESS_TIMEOUT",           # Our own watchdog timeout
    ]
    for pattern in retriable_patterns:
        if re.search(pattern, raw_error, re.IGNORECASE):
            return True
    return False


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
    """DEPRECATED — retained only for skill creator flow.

    The multi-session architecture (SessionUnit, SessionRouter, PromptBuilder,
    LifecycleManager) handles all chat endpoints. This class is kept only
    because ``run_skill_creator_conversation()`` depends on ``_run_query_on_client()``
    and ``_build_options()``. Delete this class once skill creator is migrated.
    """

    # ── Dynamic watchdog timeout parameters ──
    WATCHDOG_BASE_TIMEOUT = 180
    WATCHDOG_SECONDS_PER_100K_TOKENS = 30
    WATCHDOG_SECONDS_PER_TURN = 5
    WATCHDOG_MAX_TIMEOUT = 600
    COLD_START_TIMEOUT = 45

    def __init__(
        self,
        config_manager: AppConfigManager | None = None,
        credential_validator: CredentialValidator | None = None,
    ):
        # NOTE: self._clients was removed — _active_sessions is the single
        # source of truth for client tracking. See "Session Lifecycle
        # Invariants" in swarmai-dev-rules.md.
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
        self._credential_validator: CredentialValidator | None = credential_validator
        # Session lifecycle hook manager (set at startup via set_hook_manager)
        self._hook_manager = None  # type: SessionLifecycleHookManager | None
        # Background hook executor — fire-and-forget, never blocks chat path
        self._hook_executor = None  # type: BackgroundHookExecutor | None
        # Per-session user turn counter for context monitoring.
        # Key: effective session_id, Value: cumulative user turns.
        self._user_turn_counts: dict[str, int] = {}
        # Per-session last known input token count (for dynamic watchdog).
        # Updated after each successful response from the SDK.
        self._session_last_input_tokens: dict[str, int] = {}
        # Global PID registry — tracks ALL spawned claude CLI process PIDs.
        # Safety net: even if _active_sessions loses a reference (error path,
        # race condition), we still know about the process for cleanup.
        # PIDs are added at spawn time, removed on confirmed disconnect/kill.
        # COE 2026-03-15: prevents vnode exhaustion from leaked processes.
        self._tracked_pids: set[int] = set()
        # PID spawn timestamps — grace period for kill_tracked_leaks.
        # Freshly spawned PIDs may not yet be in _active_sessions (init
        # message + early registration takes a few seconds). Without this,
        # the leak sweep kills actively-streaming processes.
        self._pid_spawn_times: dict[int, float] = {}
        # PIDs that are currently streaming a response.  Maintained
        # independently of _active_sessions as a last-resort safety net:
        # the leak sweep MUST NOT kill any PID in this set.
        # COE 2026-03-17: sweep killed pid 80348 mid-stream because it
        # was tracked but somehow absent from _active_sessions.
        self._streaming_pids: set[int] = set()
        # Global spawn cooldown: timestamp of the last -9 (SIGKILL) failure.
        # During SPAWN_COOLDOWN_SECONDS after a failure, new spawn requests
        # get a "recovering" message instead of creating competing processes
        # that all die under memory pressure.  COE 2026-03-17: user clicking
        # "retry" 3-4 times during OOM recovery made cascading failures worse.
        self._last_sigkill_time: float = 0.0

    def configure(
        self,
        config_manager: AppConfigManager,
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

    def _register_wrapper_pid(self, wrapper: _ClaudeClientWrapper, session_info: dict | None = None) -> int | None:
        """Register a wrapper's PID in the global tracker. Called right after spawn.

        Args:
            wrapper: The wrapper whose PID to register.
            session_info: Optional session info dict to store the PID in.

        Returns:
            The PID if found, None otherwise.
        """
        pid = wrapper.pid
        if pid:
            self._tracked_pids.add(pid)
            self._pid_spawn_times[pid] = time.monotonic()
            if session_info is not None:
                session_info["pid"] = pid
            logger.debug("Registered claude PID %d in global tracker (total tracked: %d)", pid, len(self._tracked_pids))
        return pid

    def _unregister_pid(self, pid: int | None) -> None:
        """Remove a PID from ALL global trackers after confirmed disconnect/kill."""
        if pid:
            self._tracked_pids.discard(pid)
            self._pid_spawn_times.pop(pid, None)
            self._streaming_pids.discard(pid)

    def _enter_streaming(self, info: dict | None, pid: int | None) -> None:
        """Mark a session + PID as actively streaming.

        Centralises the dual-write to ``info["is_streaming"]`` and
        ``_streaming_pids`` so they can never diverge.  Safe with
        ``info=None`` or ``pid=None`` (partial no-ops).
        """
        if info is not None:
            info["is_streaming"] = True
        if pid:
            self._streaming_pids.add(pid)

    def _exit_streaming(self, info: dict | None, pid: int | None = None) -> None:
        """Clear the streaming flag for a session + PID.

        Mirror of ``_enter_streaming``.  Always call in a ``finally``
        block to guarantee cleanup.

        If *pid* is not provided, it is extracted from ``info["pid"]``.
        """
        if info is not None:
            info["is_streaming"] = False
            if pid is None:
                pid = info.get("pid")
        if pid:
            self._streaming_pids.discard(pid)

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
            pid = info.get("pid")
            if wrapper:
                # Thaw first if frozen — cleaner for graceful __aexit__
                if info.get("is_frozen"):
                    self._thaw_subprocess(session_id, info)
                await self._disconnect_wrapper(wrapper, session_id)
            # Clean up PID from all trackers (belt-and-suspenders with _unregister_pid)
            if pid:
                self._pid_spawn_times.pop(pid, None)
                self._streaming_pids.discard(pid)
        # Clean up per-session permission queue and session lock
        _pm.remove_session_queue(session_id)
        self._session_locks.pop(session_id, None)
        # Clean up per-session approved commands to prevent unbounded memory growth
        _pm.clear_session_approvals(session_id)
        # Clean up system prompt metadata to prevent unbounded memory growth
        _system_prompt_metadata.pop(session_id, None)
        # Clean up context monitor turn counter and token tracker
        self._user_turn_counts.pop(session_id, None)
        self._session_last_input_tokens.pop(session_id, None)

    async def _disconnect_wrapper(
        self, wrapper: _ClaudeClientWrapper, label: str, timeout: float = 5.0,
    ) -> None:
        """Gracefully disconnect a wrapper, force-killing the subprocess on timeout.

        1. Attempt graceful ``__aexit__`` with a timeout.
        2. If that fails, use stored PID (reliable) or extract from SDK chain
           (fallback) and send SIGKILL to it plus its child tree.

        This prevents zombie ``claude`` CLI processes from accumulating and
        exhausting macOS vnodes (which caused kernel panics — COE 2026-03-15).
        """
        # Use wrapper.pid (captured at spawn time — reliable) first,
        # fall back to chain extraction (fragile but better than nothing).
        pid = wrapper.pid or self._extract_wrapper_pid(wrapper)

        try:
            await asyncio.wait_for(
                wrapper.__aexit__(None, None, None),
                timeout=timeout,
            )
            logger.info("Disconnected client for %s (pid=%s)", label, pid)
        except asyncio.TimeoutError:
            logger.warning(
                "Graceful disconnect timed out for %s (pid=%s) after %.1fs — force killing",
                label, pid, timeout,
            )
            self._force_kill_pid(pid)
        except Exception as e:
            logger.warning(
                "Error disconnecting %s (pid=%s): %s — force killing",
                label, pid, e,
            )
            self._force_kill_pid(pid)
        finally:
            # Always unregister PID from global tracker after disconnect attempt
            self._unregister_pid(pid)
            # Also remove from streaming safety net — a disconnected process
            # can't be streaming.  Belt-and-suspenders cleanup.
            if pid:
                self._streaming_pids.discard(pid)

    @staticmethod
    def _extract_wrapper_pid(wrapper: _ClaudeClientWrapper) -> int | None:
        """Best-effort PID extraction from the SDK client chain.

        Walks: wrapper.client → _query → _transport._process.pid
        (ClaudeSDKClient stores _query and _transport directly on the instance.)
        Returns None if any link is missing (defensive).
        """
        try:
            client = wrapper.client
            if client is None:
                return None
            # ClaudeSDKClient stores _query directly
            query = getattr(client, "_query", None)
            if query is None:
                return None
            transport = getattr(query, "_transport", None)
            if transport is None:
                return None
            process = getattr(transport, "_process", None)
            if process is None:
                return None
            return getattr(process, "pid", None)
        except Exception:
            return None

    @staticmethod
    def _force_kill_pid(pid: int | None) -> None:
        """Send SIGKILL to a process and its entire child tree. Best-effort, never raises.

        Uses ``pkill -P`` to kill children first (recursively), then kills
        the parent.  Does NOT use ``os.killpg`` because claude CLI processes
        typically share the backend's process group — killing the group would
        kill us too.

        COE 2026-03-15: This is the last-resort backstop that prevents zombie
        claude processes from exhausting macOS vnodes and causing kernel panics.
        """
        if pid is None:
            return
        # Kill children first (claude spawns node/file-watcher subprocesses)
        try:
            subprocess.run(
                ["pkill", "-9", "-P", str(pid)],
                capture_output=True, timeout=3,
            )
        except Exception:
            pass
        # Kill the main process
        try:
            os.kill(pid, signal.SIGKILL)
            logger.info("Force-killed claude pid %d", pid)
        except (ProcessLookupError, PermissionError):
            pass  # Already dead
        except Exception as exc:
            logger.debug("Could not force-kill pid %d: %s", pid, exc)

    def _get_active_client(self, session_id: str) -> ClaudeSDKClient | None:
        """Get an existing long-lived client for a session, if available.

        Returns None if:
        - The session is not in ``_active_sessions``.
        - The session's subprocess was idle-disconnected (wrapper=None).
        - The subprocess PID is no longer alive (crash, OOM-kill, SIGKILL).

        In all cases the caller falls through to PATH A (fresh client
        with context injection), which is the designed resume-fallback.

        **CONTRACT:** When the returned client was thawed from a frozen
        state, ``is_streaming`` is set to ``True`` on the session info to
        guard against the cleanup loop killing the just-thawed process.
        **Every caller MUST clear ``is_streaming`` in a ``finally`` block**
        when done with the client.  Failing to do so will permanently
        prevent the cleanup loop from freezing or killing the session.

        **COE 2026-03-15:** Prior to the liveness check, a dead subprocess
        was silently reused — the Python ``client`` object was still valid
        but the underlying CLI process was gone.  ``_run_query_on_client``
        would then fail with "Not connected", "exit code -9", or "Cannot
        write to terminated process" on the very first write.
        """
        info = self._active_sessions.get(session_id)
        if not info:
            return None

        info["last_used"] = time.time()
        # Reset early-extraction flag so new activity gets captured
        # after the next idle period.
        info["activity_extracted"] = False

        client = info.get("client")
        if client is None:
            # Subprocess was killed (idle >2h, eviction, or crash) — fall through to PATH A
            logger.info(
                "Session %s exists but subprocess was killed, "
                "will use resume-fallback (context injection)",
                session_id,
            )
            return None

        # ── Thaw frozen subprocess ─────────────────────────────────
        # If the subprocess was SIGSTOP'd by Tier 1 cleanup, thaw it
        # with SIGCONT — instant resume (<100ms), full context intact.
        if info.get("is_frozen"):
            if self._thaw_subprocess(session_id, info):
                # Liveness check AFTER thaw — the process may have died
                # while frozen (macOS OOM-kill, kernel panic).  Without
                # this, a dead-but-thawed subprocess is returned to the
                # caller causing the first-message-after-crash COE.
                pid = info.get("pid")
                if pid:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        logger.warning(
                            "Session %s subprocess pid=%d died while frozen, "
                            "falling through to resume-fallback",
                            session_id, pid,
                        )
                        self._tracked_pids.discard(pid)
                        info["client"] = None
                        info["wrapper"] = None
                        info["pid"] = None
                        return None
                    except PermissionError:
                        pass  # Process exists but different owner — treat as alive

                # Successfully thawed + verified alive — mark as streaming
                # IMMEDIATELY to close the race window with the cleanup
                # loop's Tier 1.5 kill check.  Without this, the cleanup
                # loop could see the session as thawed + idle and kill it
                # before the caller sets is_streaming=True in
                # _execute_on_session.  The caller's finally block clears
                # is_streaming after the stream completes.
                self._enter_streaming(info, info.get("pid"))
                logger.info(
                    "Session %s thawed from frozen state — liveness verified, "
                    "instant resume (is_streaming set to guard against cleanup race)",
                    session_id,
                )
                return client
            else:
                # Thaw failed (process died while frozen) — fall through to PATH A
                logger.warning(
                    "Session %s thaw failed — subprocess died while frozen, "
                    "falling through to resume-fallback",
                    session_id,
                )
                info["client"] = None
                info["wrapper"] = None
                return None

        # ── Liveness check ──────────────────────────────────────────
        # Verify the subprocess PID is still alive before handing
        # the client back.  After a kernel panic, macOS OOM-kill, or
        # crash the PID is gone but the Python object is still valid.
        pid = info.get("pid")
        if pid:
            try:
                os.kill(pid, 0)  # Signal 0 = existence check, no-op if alive
            except ProcessLookupError:
                # Process is dead — evict session and fall through to PATH A
                logger.warning(
                    "Session %s subprocess pid=%d is dead (crash/OOM-kill), "
                    "evicting and falling through to resume-fallback",
                    session_id, pid,
                )
                self._tracked_pids.discard(pid)
                # Async disconnect not possible here (sync method), but the
                # process is already dead so just clear the references.
                info["client"] = None
                info["wrapper"] = None
                info["pid"] = None
                return None
            except PermissionError:
                pass  # Process exists but owned by another user — unlikely, treat as alive

        return client

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

    def _build_mcp_config(
        self,
        workspace_path: str,
        enable_mcp: bool,
    ) -> tuple[dict, list[str]]:
        """Build MCP server configuration from file-based layers.

        Delegates to ``mcp_config_loader.load_mcp_config()`` which reads
        ``.claude/mcps/mcp-catalog.json`` and ``.claude/mcps/mcp-dev.json``.
        Synchronous — no DB access.
        """
        from pathlib import Path
        return _load_mcp_config_fn(Path(workspace_path), enable_mcp)

    def _add_mcp_server_to_dict(
        self,
        mcp_config: dict,
        mcp_servers: dict,
        disallowed_tools: list[str],
        used_names: set,
    ) -> None:
        """Add a single MCP server entry. Delegates to mcp_config_loader."""
        from .mcp_config_loader import add_mcp_server_to_dict
        add_mcp_server_to_dict(mcp_config, mcp_servers, disallowed_tools, used_names)

    def _merge_user_local_mcp_servers(
        self,
        mcp_servers: dict,
        disallowed_tools: list[str],
        used_names: set,
    ) -> None:
        """Load user-local MCP servers. DEPRECATED — kept for backward compat."""
        pass

    async def _build_hooks(
        self,
        agent_config: dict,
        enable_skills: bool,
        enable_mcp: bool,
        resume_session_id: Optional[str] = None,
        session_context: Optional[dict] = None,
    ) -> tuple[dict, list[str], bool]:
        """Build hook matchers. Delegates to hook_builder."""
        return await _build_hooks_fn(
            agent_config, enable_skills, enable_mcp,
            resume_session_id, session_context,
            _pm,
        )


    def _build_sandbox_config(self, agent_config: dict) -> Optional[dict]:
        """Build the sandbox configuration dict from agent and app settings.

        Reads sandbox settings from ``config.json`` via ``AppConfigManager``
        (single source of truth), falling back to ``DEFAULT_CONFIG`` values.
        Returns ``None`` when sandboxing is disabled or unsupported (Windows).

        Args:
            agent_config: Agent configuration dictionary.

        Returns:
            Sandbox settings dict or ``None`` if sandboxing is disabled.
        """
        cfg = self._config
        sandbox_default = cfg.get("sandbox_enabled_default", True) if cfg else True
        sandbox_enabled = agent_config.get("sandbox_enabled", sandbox_default)

        # Sandbox only works on macOS/Linux, not Windows
        if sandbox_enabled and platform.system() == "Windows":
            logger.warning("Sandbox is not supported on Windows, disabling")
            sandbox_enabled = False

        if not sandbox_enabled:
            return None

        excluded_commands = []
        raw_excluded = cfg.get("sandbox_excluded_commands", "docker") if cfg else "docker"
        if raw_excluded:
            excluded_commands = [cmd.strip() for cmd in raw_excluded.split(",") if cmd.strip()]

        auto_allow_bash = cfg.get("sandbox_auto_allow_bash", True) if cfg else True
        allow_unsandboxed = cfg.get("sandbox_allow_unsandboxed", False) if cfg else False
        allowed_hosts_raw = cfg.get("sandbox_allowed_hosts", "*") if cfg else "*"

        sandbox_settings = {
            "enabled": True,
            "autoAllowBashIfSandboxed": auto_allow_bash,
            "excludedCommands": excluded_commands,
            "allowUnsandboxedCommands": allow_unsandboxed,
            "network": {
                "allowLocalBinding": True,
                "allowedHosts": [h.strip() for h in allowed_hosts_raw.split(",") if h.strip()],
            }
        }
        logger.info(f"Sandbox enabled: {sandbox_settings}")
        return sandbox_settings

    def _inject_channel_mcp(
        self,
        mcp_servers: dict,
        channel_context: Optional[dict],
        working_directory: str,
    ) -> dict:
        """Inject channel-specific MCP servers. Delegates to mcp_config_loader."""
        return _inject_channel_mcp_fn(mcp_servers, channel_context, working_directory)


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
    # Claude 4.6: 1M context GA on Bedrock (no beta header needed, unified pricing)
    # Claude 4.5: 1M still beta — stay at 200K unless beta explicitly enabled
    _MODEL_CONTEXT_WINDOWS: dict[str, int] = {
        "claude-opus-4-6": 1_000_000,
        "claude-sonnet-4-6": 1_000_000,
        "claude-sonnet-4-5-20250929": 200_000,
        "claude-opus-4-5-20251101": 200_000,
    }
    _DEFAULT_CONTEXT_WINDOW: int = 200_000
    _CONTEXT_WARN_PCT: int = 70
    _CONTEXT_CRITICAL_PCT: int = 85

    def _get_model_context_window(self, model: Optional[str]) -> int:
        """Return the context window size for a model ID.

        Strips Bedrock prefix/suffix for lookup.  Defaults to 200K.
        Claude 4.6 models return 1M (GA on Bedrock since 2026-03).
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

    def _compute_watchdog_timeout(self, session_id: Optional[str]) -> int:
        """Compute a dynamic watchdog timeout based on session complexity.

        Scales the base timeout by:
        - Cached/input tokens: +30s per 100K tokens (heavy sessions need more time)
        - User turns: +5s per turn (accumulated context grows with conversation)

        Capped at WATCHDOG_MAX_TIMEOUT to prevent infinite waits.
        Returns WATCHDOG_BASE_TIMEOUT when no session data is available.
        """
        timeout = self.WATCHDOG_BASE_TIMEOUT
        if not session_id:
            return timeout

        # Scale by last known input token count
        last_tokens = self._session_last_input_tokens.get(session_id, 0)
        if last_tokens > 0:
            hundreds_of_k = last_tokens / 100_000
            timeout += int(hundreds_of_k * self.WATCHDOG_SECONDS_PER_100K_TOKENS)

        # Scale by conversation depth (user turns)
        turns = self._user_turn_counts.get(session_id, 0)
        if turns > 0:
            timeout += turns * self.WATCHDOG_SECONDS_PER_TURN

        clamped = min(timeout, self.WATCHDOG_MAX_TIMEOUT)
        if clamped != self.WATCHDOG_BASE_TIMEOUT:
            logger.debug(
                "Dynamic watchdog: %ds (base=%d, tokens=%d, turns=%d) for session %s",
                clamped, self.WATCHDOG_BASE_TIMEOUT, last_tokens, turns,
                session_id[:8] if session_id else "?",
            )
        return clamped

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

            # ── Proactive Intelligence briefing (ephemeral) ──
            # Injects a compact session briefing (~200-400 tokens) built from
            # Open Threads, DailyActivity continue-from hints, and pattern signals.
            # No LLM call — pure text parsing. Never blocks agent startup.
            try:
                from .proactive_intelligence import build_session_briefing
                briefing = build_session_briefing(working_directory)
                if briefing:
                    context_text += f"\n\n{briefing}"
            except Exception as exc:
                logger.warning("Proactive intelligence injection failed: %s", exc)

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

        # 1. Resolve allowed tools
        allowed_tools = self._resolve_allowed_tools(agent_config)

        # 2. Build hooks
        hooks, effective_allowed_skills, allow_all_skills = await self._build_hooks(
            agent_config, enable_skills, enable_mcp,
            resume_session_id, session_context,
        )

        # 3. Resolve working directory and file access (inlined, no _resolve_workspace_mode)
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

        # 4. Build MCP server configuration (file-based, no DB)
        mcp_servers, mcp_disallowed_tools = self._build_mcp_config(working_directory, enable_mcp)

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

        # Build add_dirs from sandbox_additional_write_paths config.
        # This passes --add-dir to the CLI, granting write access to directories
        # outside the working directory (e.g., the source tree for self-evolution).
        # Read from config.json (single source of truth) via AppConfigManager.
        add_dirs: list[str] = []
        raw_write_paths = self._config.get("sandbox_additional_write_paths", "") if self._config else ""
        if raw_write_paths:
            add_dirs = [
                p.strip() for p in raw_write_paths.split(",")
                if p.strip()
            ]

        # Build extra CLI args for features not yet in ClaudeAgentOptions.
        # IMPORTANT: The bundled CLI (currently 2.1.71) errors on unknown
        # flags.  Only add flags that the bundled version supports.
        # --name requires CLI ≥2.1.76 — uncomment when SDK bundles it.
        extra_args: dict[str, str | None] = {}
        # TODO(sdk-upgrade): uncomment when claude-agent-sdk bundles CLI ≥2.1.76
        # session_name = (session_context or {}).get("app_session_id")
        # if session_name:
        #     extra_args["name"] = session_name[:12]  # short, readable

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
            add_dirs=add_dirs if add_dirs else None,
            extra_args=extra_args,
            # Enable partial message streaming so the SDK yields StreamEvent
            # objects with token-level deltas (content_block_delta, etc.)
            # instead of buffering the entire response into one AssistantMessage.
            include_partial_messages=True,
            # Track file changes per user message for future undo/rewind.
            # Enables ClaudeSDKClient.rewind_files() — free safety net.
            enable_file_checkpointing=True,
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
                        finally:
                            # _get_active_client sets is_streaming=True on thaw
                            # to guard against cleanup loop race.  Clear it.
                            _sc_info = self._active_sessions.get(session_id)
                            if _sc_info:
                                _sc_info["last_used"] = time.time()
                            self._exit_streaming(_sc_info)

                        # PATH B cleanup (same pattern as _execute_on_session)
                        if session_context.get("had_error") and session_id:
                            logger.info(
                                f"PATH B (skill-creator): reused client for {session_id} "
                                f"had error, evicting from active sessions"
                            )
                            info = self._active_sessions.pop(session_id, None)
                            if info and info.get("wrapper"):
                                await self._disconnect_wrapper(
                                    info["wrapper"], f"skill-creator-pathB-{session_id}",
                                )
                    else:
                        # No active client — start fresh (--resume won't work with SDK 0.1.34+)
                        if is_resuming:
                            logger.info(f"No active client for skill creator session {session_id}, starting fresh")
                            # Tell the frontend we're cold-starting a resume
                            yield {"type": "session_resuming"}
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
                        # Register skill-creator PID in global tracker.
                        # Also store in early-registered session to prevent
                        # tracked-leak sweep from killing it mid-query.
                        _skill_early_key = session_context.get("_early_active_key")
                        _skill_early_info = (
                            self._active_sessions.get(_skill_early_key)
                            if _skill_early_key else None
                        )
                        self._register_wrapper_pid(wrapper, _skill_early_info)
                        if _skill_early_info is not None:
                            _skill_early_info["wrapper"] = wrapper
                            _skill_early_info["client"] = client
                        logger.info("ClaudeSDKClient created for skill creation (pid=%s)", wrapper.pid)

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
                            await self._disconnect_wrapper(wrapper, f"skill-creator-error-{session_id}")
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
                            _skill_info = {
                                "client": client,
                                "wrapper": wrapper,
                                "created_at": time.time(),
                                "last_used": time.time(),
                                "activity_extracted": False,
                                "is_frozen": False,
                                "failure_tracker": ToolFailureTracker(),
                                "pid": wrapper.pid,
                            }
                            self._register_wrapper_pid(wrapper, _skill_info)
                            self._active_sessions[effective_session_id] = _skill_info
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
                    # Track SIGKILL for global spawn cooldown
                    if "exit code -9" in str(e):
                        self._last_sigkill_time = time.time()
                    friendly_msg, suggested = _sanitize_sdk_error(str(e))
                    yield _build_error_event(
                        code="SKILL_CREATION_ERROR",
                        message=friendly_msg,
                        detail=error_traceback,
                        suggested_action=suggested,
                    )
        finally:
            # Clean up ephemeral lock keys to prevent unbounded memory growth.
            # Non-ephemeral keys are cleaned up by _cleanup_session().
            if is_ephemeral_lock:
                self._session_locks.pop(lock_key, None)


# Global instance
agent_manager = AgentManager()
