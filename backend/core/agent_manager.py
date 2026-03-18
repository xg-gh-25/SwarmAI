"""Agent execution engine: session management, response streaming, and SDK orchestration.

This module contains the ``AgentManager`` class — the core execution engine that
drives agent conversations via the Claude Agent SDK. After refactoring, it delegates
to five focused modules for specific concerns:

- ``security_hooks.py``      — Dangerous command gate, file access handler, skill access checker
- ``permission_manager.py``  — PermissionManager singleton for command approval / HITL decisions
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
- Session caching with 8-hour TTL, SIGSTOP hibernation, and orphan process reaping
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
    """Manages agent lifecycle using Claude Agent SDK.

    Uses ClaudeSDKClient for stateful, multi-turn conversations with Claude.
    Claude Code (underlying SDK) has built-in support for Skills and MCP servers.
    """

    # TTL for idle sessions before automatic cleanup (8 hours).
    # Generous TTL so users can leave for lunch, meetings, or overnight
    # work and come back to a live session.  The old 2h value forced
    # cold-start resumes during normal work patterns.  Memory is managed
    # by SIGSTOP hibernation + MAX_CONCURRENT_SUBPROCESSES cap, not TTL.
    SESSION_TTL_SECONDS = 8 * 60 * 60
    # Idle threshold for early DailyActivity extraction (30 minutes).
    # When a session has no messages for this long, extract activity
    # but keep the session alive so the user can resume.
    ACTIVITY_IDLE_SECONDS = 30 * 60
    # Idle threshold for subprocess FREEZE via SIGSTOP (5 minutes).
    # Instead of killing, we freeze the subprocess — it uses zero CPU
    # and macOS naturally pages out its memory.  On next message,
    # SIGCONT thaws it instantly (<100ms) with full context intact.
    # This eliminates the 10-20s cold-start penalty for normal idle
    # patterns (reading docs, checking Slack, lunch breaks).
    SUBPROCESS_IDLE_SECONDS = 5 * 60
    # Idle threshold for subprocess KILL (2 hours).
    # After this long, the subprocess is killed (not just frozen) to
    # fully reclaim resources.  On next message, PATH A cold-starts
    # with context injection (~10-15s).  This is the ONLY case where
    # the user experiences a resume delay during normal usage.
    SUBPROCESS_KILL_SECONDS = 2 * 60 * 60
    # Maximum concurrent live claude CLI subprocesses.
    # When exceeded, the oldest idle subprocess is disconnected before
    # spawning a new one.  Prevents unbounded RAM growth with many tabs.
    # Lowered from 3→2 (2026-03-15) because each claude CLI uses
    # 200-500MB RAM; with Kiro + browser + other tools running,
    # 3 concurrent pushes macOS into jetsam (OOM kill) territory.
    # The non-active tab resumes via PATH A (~5s slower but no SIGKILL).
    MAX_CONCURRENT_SUBPROCESSES = 2
    # Maximum PATH A retry attempts for retriable errors (exit -9, broken pipe).
    # Each retry spawns a fresh subprocess.  Backoff delay between attempts
    # gives the OS time to reclaim memory from the killed process.
    # Raised from 2→3 (2026-03-17): system often recovers on 3rd attempt
    # after dead process memory is fully reclaimed (~15-30s).
    MAX_RETRY_ATTEMPTS = 3
    # Delay (seconds) before auto-retry after a retriable error.
    # Gives macOS time to reclaim memory from the SIGKILL'd process.
    # Raised from 3→5 (2026-03-17): 3s wasn't enough for macOS to reclaim
    # 400-600MB from dead CLI + MCP processes.
    RETRY_BACKOFF_SECONDS = 5.0
    # If a spawned subprocess dies within this many seconds, classify as
    # an instant OOM kill — don't waste retry attempts, use longer backoff.
    INSTANT_KILL_THRESHOLD_SECONDS = 2.0
    # Backoff when an instant kill is detected (gives macOS time to
    # compress/swap memory and reclaim from the dead process tree).
    # Raised from 20→30 (2026-03-17): 20s was not enough for cascading
    # failures where multiple dead processes + MCP servers need cleanup.
    OOM_BACKOFF_SECONDS = 30.0
    # Global cooldown (seconds) after any -9 failure across ALL sessions.
    # Prevents the user from hammering "retry" and spawning competing
    # processes that all die under memory pressure, making things worse.
    # During cooldown, new requests get a user-friendly "recovering" message.
    SPAWN_COOLDOWN_SECONDS = 15.0
    # Minimum free memory (bytes) required before spawning a new subprocess.
    # Each claude CLI + 5 MCP servers uses ~400-600MB.  Below this threshold
    # spawns are likely to be immediately SIGKILL'd by macOS jetsam.
    MIN_FREE_MEMORY_BYTES = 512 * 1024 * 1024  # 512 MB

    # ── Dynamic watchdog timeout parameters ──
    # Base timeout before scaling (seconds).
    WATCHDOG_BASE_TIMEOUT = 180
    # Extra seconds per 100K cached tokens. Heavy sessions (350K+ cached
    # tokens) legitimately take longer for Bedrock to process.
    WATCHDOG_SECONDS_PER_100K_TOKENS = 30
    # Extra seconds per user turn (accumulated context).
    WATCHDOG_SECONDS_PER_TURN = 5
    # Absolute ceiling to prevent infinite waits (seconds).
    WATCHDOG_MAX_TIMEOUT = 600
    # Tighter timeout for cold-start resume (fresh subprocess after idle
    # kill or eviction).  A fresh subprocess should respond in ~15s;
    # 45s gives 3x margin without the 180s pain of the full watchdog.
    # Only applies to the INITIAL timeout before first real message;
    # once streaming starts, inter-message timeout uses the normal value.
    COLD_START_TIMEOUT = 45
    # Defensive threshold: if is_streaming has been True for longer than
    # this, the caller forgot the finally block.  Clear it so the cleanup
    # loop can resume normal freeze/kill duties.
    STALE_STREAMING_THRESHOLD = 10 * 60  # 10 minutes

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

    def has_active_session(self, session_id: str) -> bool:
        """Check if a session is currently active in memory."""
        return session_id in self._active_sessions

    def _start_cleanup_loop(self):
        """Start background task to clean up stale sessions."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_stale_sessions_loop())

    async def _cleanup_stale_sessions_loop(self):
        """Periodically clean up sessions that have been idle too long.

        Five-tier idle detection:
        1. **Subprocess FREEZE** (5 min idle): SIGSTOP the subprocess.
           Uses zero CPU, macOS pages out idle memory naturally.
           On next message, SIGCONT thaws instantly (<100ms) — full
           context preserved, zero user-visible delay.
        1.5. **Subprocess KILL** (2 h idle): Kill frozen subprocesses.
           After 2h the subprocess is killed to fully reclaim resources.
           On next message, PATH A cold-starts with context injection.
        2. **Activity extraction** (30 min idle): Fire the DailyActivity
           extraction hook only.  Session stays alive so the user can
           resume without losing conversation context.
        3. **Full cleanup** (8 h idle): Tear down the session and fire
           all post-session-close hooks.
        4. **Orphan sweep** (every 5 min): Kill orphaned claude CLI
           processes that survived wrapper disconnect (deadlock, crash).
        """
        _sweep_counter = 0  # Process leak sweep every 5 iterations (5 min)
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                now = time.time()
                _sweep_counter += 1

                # --- Tier 0.5: Defensive is_streaming stale guard ---
                # _get_active_client sets is_streaming=True on thaw.  If
                # a caller forgets the finally block (contract violation),
                # the session gets stuck with is_streaming=True forever.
                # Detect: is_streaming=True but last_used > 10min ago.
                for sid, info in self._active_sessions.items():
                    if info.get("is_streaming"):
                        idle_since_streaming = now - info.get("last_used", info["created_at"])
                        if idle_since_streaming > self.STALE_STREAMING_THRESHOLD:
                            logger.warning(
                                "Stale is_streaming detected for session %s "
                                "(last_used %.0fs ago) — clearing flag. "
                                "This indicates a missing finally block in a "
                                "_get_active_client caller.",
                                sid, idle_since_streaming,
                            )
                            info["is_streaming"] = False

                # --- Tier 1: Subprocess FREEZE via SIGSTOP (5 min idle) ---
                # Freeze idle subprocesses instead of killing them.  The
                # subprocess uses zero CPU and macOS naturally pages out
                # its memory.  On next message, SIGCONT thaws instantly.
                # GUARD: Skip sessions that are actively streaming or
                # already frozen.
                idle_for_freeze = [
                    (sid, info) for sid, info in self._active_sessions.items()
                    if (now - info.get("last_used", info["created_at"]) > self.SUBPROCESS_IDLE_SECONDS
                        and info.get("wrapper") is not None
                        and not info.get("is_streaming")
                        and not info.get("is_frozen"))
                ]
                for sid, info in idle_for_freeze:
                    self._freeze_subprocess(sid, info)

                # --- Tier 1.5: Subprocess KILL (2 h idle) ---
                # Kill subprocesses that have been idle for 2+ hours.
                # This is the only path that forces a cold-start resume.
                # GUARD: Skip sessions that are actively streaming.
                idle_for_kill = [
                    (sid, info) for sid, info in self._active_sessions.items()
                    if (now - info.get("last_used", info["created_at"]) > self.SUBPROCESS_KILL_SECONDS
                        and info.get("wrapper") is not None
                        and not info.get("is_streaming"))
                ]
                for sid, info in idle_for_kill:
                    wrapper = info.get("wrapper")
                    if wrapper:
                        logger.info(
                            "Subprocess idle kill for session %s "
                            "(idle %.0fs, reclaiming resources)",
                            sid, now - info.get("last_used", info["created_at"]),
                        )
                        # Thaw first if frozen — SIGKILL on a stopped process
                        # is fine on macOS/Linux, but thawing first is cleaner
                        # for graceful __aexit__.
                        if info.get("is_frozen"):
                            self._thaw_subprocess(sid, info)
                        await self._disconnect_wrapper(wrapper, f"idle-kill-{sid}")
                        info["wrapper"] = None
                        info["client"] = None
                        info["is_frozen"] = False

                # --- Tier 2: Early DailyActivity extraction (30 min idle) ---
                idle_for_extraction = [
                    (sid, info) for sid, info in self._active_sessions.items()
                    if (now - info.get("last_used", info["created_at"]) > self.ACTIVITY_IDLE_SECONDS
                        and not info.get("activity_extracted"))
                ]
                for sid, info in idle_for_extraction:
                    await self._extract_activity_early(sid, info)

                # --- Tier 3: Full cleanup (8 h TTL) ---
                stale = [
                    sid for sid, info in self._active_sessions.items()
                    if now - info.get("last_used", info["created_at"]) > self.SESSION_TTL_SECONDS
                ]
                for sid in stale:
                    logger.info(f"Cleaning up stale session {sid}")
                    await self._cleanup_session(sid)

                # --- Tier 4: Process leak sweep (every ~5 min) ---
                # COE 2026-03-15: zombie claude processes exhausted macOS vnodes.
                # Two sweeps: (a) tracked PIDs not in active sessions, (b) OS-level orphans.
                if _sweep_counter >= 5:
                    _sweep_counter = 0
                    # Sweep tracked PIDs first (fast, no subprocess spawn)
                    leaked = self.kill_tracked_leaks()
                    if leaked:
                        logger.warning(
                            "Periodic tracked-leak sweep killed %d claude process(es)", leaked,
                        )
                    # Then sweep OS-level orphans (catches processes we lost track of).
                    # Offload to thread — spawns pgrep + ps subprocesses that
                    # would otherwise block the event loop for 10-50ms.
                    orphaned = await asyncio.to_thread(self.kill_orphan_claude_processes)
                    if orphaned:
                        logger.warning(
                            "Periodic orphan sweep killed %d claude process(es)", orphaned,
                        )
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

    @staticmethod
    def _get_free_memory_bytes() -> int | None:
        """Return approximate free physical memory in bytes, or None if unknown.

        On macOS, uses ``vm_stat`` which reports in page-sized units.
        Falls back to ``/proc/meminfo`` on Linux.  Returns None on
        unsupported platforms or if the check fails — callers should
        treat None as "unknown, proceed with caution".

        NOTE: Uses synchronous ``subprocess.run`` (~2ms for vm_stat) rather
        than ``asyncio.create_subprocess_exec`` because the call is fast,
        infrequent (only on spawn + retry), and the simpler error handling
        of the sync API is worth the negligible event-loop block.
        """
        try:
            if platform.system() == "Darwin":
                # vm_stat is fast (~2ms) and always available on macOS.
                result = subprocess.run(
                    ["vm_stat"], capture_output=True, text=True, timeout=3,
                )
                if result.returncode != 0:
                    return None
                # Parse page size from first line: "Mach Virtual Memory Statistics: (page size of 16384 bytes)"
                page_size = 16384  # default for Apple Silicon
                first_line = result.stdout.split("\n")[0]
                ps_match = re.search(r"page size of (\d+)", first_line)
                if ps_match:
                    page_size = int(ps_match.group(1))
                # Sum free + inactive (reclaimable) pages
                free_pages = 0
                for line in result.stdout.split("\n"):
                    if line.startswith("Pages free:"):
                        free_pages += int(re.sub(r"[^\d]", "", line))
                    elif line.startswith("Pages inactive:"):
                        # Inactive pages are reclaimable under pressure
                        free_pages += int(re.sub(r"[^\d]", "", line))
                return free_pages * page_size
            elif platform.system() == "Linux":
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemAvailable:"):
                            return int(line.split()[1]) * 1024  # kB → bytes
                return None
            else:
                return None
        except Exception:
            return None

    def _check_memory_pressure(self) -> str | None:
        """Check if the system has enough free memory to spawn a subprocess.

        Returns:
            None if memory is sufficient (safe to spawn).
            A human-readable error message if memory is too low.
        """
        free_bytes = self._get_free_memory_bytes()
        if free_bytes is None:
            # Can't determine — proceed (fail-open)
            return None
        free_mb = free_bytes / (1024 * 1024)
        threshold_mb = self.MIN_FREE_MEMORY_BYTES / (1024 * 1024)
        if free_bytes < self.MIN_FREE_MEMORY_BYTES:
            logger.warning(
                "Memory pressure: %.0f MB free (threshold: %.0f MB) — "
                "refusing to spawn subprocess",
                free_mb, threshold_mb,
            )
            return (
                f"System memory is low ({free_mb:.0f} MB free). "
                f"Close some apps or browser tabs to free up memory, "
                f"then try again."
            )
        logger.debug("Memory check OK: %.0f MB free (threshold: %.0f MB)", free_mb, threshold_mb)
        return None

    async def _evict_idle_subprocesses(self) -> int:
        """Disconnect idle subprocesses to stay under MAX_CONCURRENT_SUBPROCESSES.

        Called before spawning a new claude CLI process.  Evicts the
        oldest idle sessions' subprocesses (but keeps their metadata
        so resume-fallback works on the next message).

        Returns the number of subprocesses evicted.
        """
        # Count sessions with live subprocesses, separating streaming from idle.
        # GUARD: Never evict sessions that are actively streaming — killing
        # a subprocess mid-stream causes "Cannot write to terminated process"
        # errors and the full retry cascade (exit code -9 → "slow to respond").
        idle_sessions = []
        streaming_count = 0
        for sid, info in self._active_sessions.items():
            if info.get("wrapper") is None:
                continue
            if info.get("is_streaming"):
                streaming_count += 1
            else:
                idle_sessions.append((sid, info))

        # Only evict idle sessions; streaming ones are untouchable.
        total_live = len(idle_sessions) + streaming_count
        evict_count = total_live - self.MAX_CONCURRENT_SUBPROCESSES + 1  # +1 for the one about to spawn
        if evict_count <= 0:
            return 0

        # Sort by last_used ascending (oldest idle first)
        idle_sessions.sort(key=lambda x: x[1].get("last_used", x[1].get("created_at", 0)))

        # Can only evict idle sessions — cap evict_count to available idle sessions
        evict_count = min(evict_count, len(idle_sessions))

        evicted = 0
        for sid, info in idle_sessions[:evict_count]:
            wrapper = info.get("wrapper")
            if wrapper:
                idle_secs = time.time() - info.get("last_used", info.get("created_at", 0))
                logger.info(
                    "Evicting idle subprocess for session %s "
                    "(idle %.0fs, %d/%d live, %d streaming) to stay under cap",
                    sid, idle_secs, total_live - evicted,
                    self.MAX_CONCURRENT_SUBPROCESSES, streaming_count,
                )
                # Thaw first if frozen — cleaner for graceful __aexit__
                if info.get("is_frozen"):
                    self._thaw_subprocess(sid, info)
                await self._disconnect_wrapper(wrapper, f"evict-cap-{sid}")
                info["wrapper"] = None
                info["client"] = None
                info["is_frozen"] = False
                evicted += 1

        return evicted

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

    def _freeze_subprocess(self, session_id: str, info: dict) -> bool:
        """Freeze a subprocess via SIGSTOP — zero CPU, instant resume via SIGCONT.

        The process stays in memory but uses zero CPU.  macOS naturally
        pages out idle process memory if it needs RAM for other things.
        On next message, ``_thaw_subprocess`` sends SIGCONT and the process
        resumes instantly (<100ms) with full context intact.

        Returns True if the process was successfully frozen.
        """
        pid = info.get("pid")
        if not pid:
            wrapper = info.get("wrapper")
            if wrapper:
                pid = wrapper.pid
        if not pid:
            return False

        try:
            os.kill(pid, 0)  # Existence check
        except ProcessLookupError:
            logger.warning(
                "Cannot freeze session %s: pid %d is dead", session_id, pid,
            )
            # Process is dead — clear references so PATH A handles it
            self._tracked_pids.discard(pid)
            info["client"] = None
            info["wrapper"] = None
            info["pid"] = None
            info["is_frozen"] = False
            return False
        except PermissionError:
            pass  # Process exists

        try:
            # Freeze children first (MCP servers, file watchers) — same
            # order as _force_kill_pid.  Without this, 5+ child processes
            # keep running while the parent is stopped.
            try:
                subprocess.run(
                    ["pkill", "-STOP", "-P", str(pid)],
                    capture_output=True, timeout=3,
                )
            except Exception:
                pass  # Best-effort — parent freeze still worthwhile
            os.kill(pid, signal.SIGSTOP)
            info["is_frozen"] = True
            idle_secs = time.time() - info.get("last_used", info.get("created_at", 0))
            logger.info(
                "Froze subprocess pid=%d for session %s (idle %.0fs)",
                pid, session_id, idle_secs,
            )
            return True
        except OSError as e:
            logger.warning(
                "Failed to SIGSTOP pid=%d for session %s: %s",
                pid, session_id, e,
            )
            return False

    def _thaw_subprocess(self, session_id: str, info: dict) -> bool:
        """Thaw a frozen subprocess via SIGCONT — instant resume, full context.

        Called by ``_get_active_client`` when the user sends a new message
        to a frozen session.  The process resumes instantly with no
        context loss — the user sees zero delay.

        Returns True if the process was successfully thawed.
        """
        if not info.get("is_frozen"):
            return True  # Already thawed

        pid = info.get("pid")
        if not pid:
            wrapper = info.get("wrapper")
            if wrapper:
                pid = wrapper.pid
        if not pid:
            info["is_frozen"] = False
            return False

        try:
            os.kill(pid, 0)  # Existence check
        except ProcessLookupError:
            logger.warning(
                "Cannot thaw session %s: pid %d is dead", session_id, pid,
            )
            self._tracked_pids.discard(pid)
            info["client"] = None
            info["wrapper"] = None
            info["pid"] = None
            info["is_frozen"] = False
            return False
        except PermissionError:
            pass

        try:
            # Thaw parent first, then children (reverse of freeze order)
            os.kill(pid, signal.SIGCONT)
            try:
                subprocess.run(
                    ["pkill", "-CONT", "-P", str(pid)],
                    capture_output=True, timeout=3,
                )
            except Exception:
                pass  # Best-effort — parent thaw is sufficient for recovery
            info["is_frozen"] = False
            logger.info(
                "Thawed subprocess pid=%d for session %s — instant resume",
                pid, session_id,
            )
            return True
        except OSError as e:
            logger.warning(
                "Failed to SIGCONT pid=%d for session %s: %s",
                pid, session_id, e,
            )
            info["is_frozen"] = False
            return False

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

    def kill_orphan_claude_processes(self, exclude_pids: set[int] | None = None) -> int:
        """Find and kill orphaned ``claude`` CLI processes.

        Called periodically to prevent vnode exhaustion. Kills processes
        whose parent is PID 1 (re-parented orphan) or the python-backend
        PID (leaked child), EXCLUDING PIDs known to be actively managed
        (in ``_active_sessions`` or the explicit ``exclude_pids`` set).

        COE 2026-03-15: 80 zombie claude processes with deadlocked file
        watchers exhausted macOS vnodes (263K -> 127) causing kernel panics.

        Args:
            exclude_pids: Additional PIDs to skip (e.g. from caller context).

        Returns the number of processes killed.
        """
        if platform.system() not in ("Darwin", "Linux"):
            return 0

        # Build set of PIDs that are actively managed — never kill these
        safe_pids = set(exclude_pids) if exclude_pids else set()
        for info in self._active_sessions.values():
            pid = info.get("pid")
            if pid:
                safe_pids.add(pid)
        # Also include all globally tracked PIDs (they have wrappers managing them)
        safe_pids |= self._tracked_pids
        # COE 2026-03-17: _streaming_pids is the last-resort safety net.
        # A PID can be in _streaming_pids but absent from both _tracked_pids
        # and _active_sessions (the exact scenario that caused the COE).
        safe_pids |= self._streaming_pids

        # SAFETY: If we have no tracked PIDs at all, PID extraction is broken.
        # In this state, we cannot distinguish active processes from orphans.
        # Only kill processes whose parent is PID 1 (truly re-parented orphans),
        # NOT children of our backend (which are likely active but untracked).
        pid_tracking_broken = len(safe_pids) == 0 and len(self._active_sessions) > 0
        if pid_tracking_broken:
            logger.debug(
                "PID tracking appears broken (0 tracked PIDs, %d active sessions). "
                "Orphan sweep will only kill re-parented processes (ppid=1), "
                "not direct children of the backend.",
                len(self._active_sessions),
            )

        killed = 0
        my_pid = os.getpid()
        try:
            result = subprocess.run(
                ["pgrep", "-x", "claude"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return 0

            pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip()]
            for pid in pids:
                if pid in safe_pids:
                    continue  # Actively managed — don't kill
                try:
                    ppid_result = subprocess.run(
                        ["ps", "-o", "ppid=", "-p", str(pid)],
                        capture_output=True, text=True, timeout=2,
                    )
                    ppid = int(ppid_result.stdout.strip()) if ppid_result.stdout.strip() else -1

                    # Kill if orphaned (ppid=1) or direct child of our backend
                    # SAFETY: When PID tracking is broken, only kill truly
                    # orphaned processes (ppid=1). Direct children (ppid=my_pid)
                    # are likely active sessions we can't track.
                    if ppid == 1 or (ppid == my_pid and not pid_tracking_broken):
                        try:
                            subprocess.run(
                                ["pkill", "-9", "-P", str(pid)],
                                capture_output=True, timeout=3,
                            )
                        except Exception:
                            pass
                        os.kill(pid, signal.SIGKILL)
                        killed += 1
                        logger.info(
                            "Killed orphan claude process pid=%d (ppid=%d)", pid, ppid,
                        )
                except (ProcessLookupError, PermissionError, ValueError):
                    pass
                except Exception as exc:
                    logger.debug("Could not inspect/kill claude pid %d: %s", pid, exc)
        except FileNotFoundError:
            pass  # pgrep not available
        except Exception as exc:
            logger.warning("Orphan claude cleanup failed: %s", exc)

        if killed:
            logger.warning("Killed %d orphan claude process(es)", killed)
        return killed

    @staticmethod
    def kill_all_claude_processes() -> int:
        """Kill ALL claude CLI processes unconditionally.

        Called at **startup** — no claude processes should be running before
        the backend starts. This is more aggressive than kill_orphan_claude_processes
        which only kills orphans/our-children.

        COE 2026-03-15: At startup, leftover processes from a crashed previous
        instance are guaranteed stale. Kill them all.

        Returns the number of processes killed.
        """
        if platform.system() not in ("Darwin", "Linux"):
            return 0

        killed = 0
        try:
            result = subprocess.run(
                ["pgrep", "-x", "claude"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return 0

            pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip()]
            for pid in pids:
                try:
                    # Kill children first
                    try:
                        subprocess.run(
                            ["pkill", "-9", "-P", str(pid)],
                            capture_output=True, timeout=3,
                        )
                    except Exception:
                        pass
                    os.kill(pid, signal.SIGKILL)
                    killed += 1
                    logger.info("Startup: killed leftover claude process pid=%d", pid)
                except (ProcessLookupError, PermissionError):
                    pass
                except Exception as exc:
                    logger.debug("Could not kill claude pid %d: %s", pid, exc)
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("Startup claude cleanup failed: %s", exc)

        if killed:
            logger.warning(
                "Startup: killed %d leftover claude process(es) from previous instance",
                killed,
            )
        return killed

    # Minimum age (seconds) before a tracked PID can be considered leaked.
    # PATH A retries register PIDs in _tracked_pids immediately, but the
    # early-client-registration that updates _active_sessions happens after
    # the SDK init message (seconds later).  During that gap, the PID
    # appears "leaked".  A 5-minute grace period prevents killing processes
    # that are actively streaming but haven't been registered yet.
    TRACKED_PID_GRACE_SECONDS = 300

    def kill_tracked_leaks(self) -> int:
        """Kill tracked PIDs that are no longer associated with any active session.

        This catches processes that leaked due to error paths removing entries
        from _active_sessions without proper disconnect.

        Respects a grace period (TRACKED_PID_GRACE_SECONDS) — freshly spawned
        PIDs are skipped even if not yet in _active_sessions, because the
        early-registration path hasn't run yet.

        Returns the number of processes killed.
        """
        if not self._tracked_pids:
            return 0

        # Collect PIDs that are still in active sessions.
        # Check BOTH info["pid"] AND wrapper.pid — during retries the
        # wrapper may have been updated but info["pid"] lagged behind.
        active_pids = set()
        for info in self._active_sessions.values():
            pid = info.get("pid")
            if pid:
                active_pids.add(pid)
            wrapper = info.get("wrapper")
            if wrapper:
                wpid = getattr(wrapper, "pid", None)
                if wpid:
                    active_pids.add(wpid)

        # Prune dead PIDs from _streaming_pids.  If a process crashes without
        # going through the finally-block cleanup, its PID stays in the set
        # forever and permanently blocks the leak sweep from detecting it.
        dead_streaming = set()
        for spid in self._streaming_pids:
            try:
                os.kill(spid, 0)  # Signal 0 = existence check
            except (ProcessLookupError, PermissionError):
                dead_streaming.add(spid)
        if dead_streaming:
            self._streaming_pids -= dead_streaming
            logger.info("Pruned %d dead PID(s) from _streaming_pids: %s",
                        len(dead_streaming), dead_streaming)

        # Leaked PIDs = tracked but not in any active session AND not streaming.
        # Snapshot _streaming_pids to avoid TOCTOU race: a concurrent stream
        # handler could add a PID between the set subtraction and the kill().
        streaming_snapshot = set(self._streaming_pids)
        leaked_pids = self._tracked_pids - active_pids - streaming_snapshot
        killed = 0
        now = time.monotonic()
        for pid in list(leaked_pids):  # copy — we mutate _tracked_pids
            # Grace period: skip PIDs spawned recently — they may be
            # mid-init and not yet registered in _active_sessions.
            spawn_time = self._pid_spawn_times.get(pid)
            if spawn_time and (now - spawn_time) < self.TRACKED_PID_GRACE_SECONDS:
                logger.debug(
                    "Skipping PID %d in leak sweep — spawned %.0fs ago "
                    "(grace period: %ds)",
                    pid, now - spawn_time, self.TRACKED_PID_GRACE_SECONDS,
                )
                continue

            try:
                # Re-check _streaming_pids right before kill to close the
                # TOCTOU window (a stream may have started since the snapshot).
                if pid in self._streaming_pids:
                    continue
                # Verify process is still alive before killing
                os.kill(pid, 0)  # Signal 0 = existence check
                self._force_kill_pid(pid)
                killed += 1
                logger.warning("Killed leaked claude process pid=%d (tracked but not in active sessions)", pid)
            except (ProcessLookupError, PermissionError):
                pass  # Already dead — just clean up tracking
            finally:
                self._tracked_pids.discard(pid)
                self._pid_spawn_times.pop(pid, None)

        if killed:
            logger.warning("Killed %d leaked claude process(es) from PID tracker", killed)

        # Reap stale entries from _pid_spawn_times that are no longer in
        # _tracked_pids.  Handles PIDs that died on their own without going
        # through _unregister_pid (crash during init, OS-level kill, etc.).
        stale_spawn_pids = set(self._pid_spawn_times.keys()) - self._tracked_pids
        for stale_pid in stale_spawn_pids:
            self._pid_spawn_times.pop(stale_pid, None)

        return killed

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
        editor_context: Optional[dict] = None,
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
            editor_context: Currently open file in the editor panel (EditorContext model)
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

        # --- Inject editor context so the agent knows which file the user is viewing ---
        # NOTE: Injected as a user-turn prefix (not system message) because the
        # Claude Agent SDK's query() API only accepts user-role content.  System
        # prompt is built once at client creation and cannot be amended per-turn.
        # The bracketed format [Editor context: ...] makes it clearly machine-
        # generated so the agent treats it as metadata, not user speech.
        if editor_context:
            # editor_context is an EditorContext Pydantic model; access via attributes
            # but fall back to dict access for backward compat with raw dicts.
            file_path = getattr(editor_context, "file_path", "") or (editor_context.get("file_path", "") if isinstance(editor_context, dict) else "")
            file_name = getattr(editor_context, "file_name", "") or (editor_context.get("file_name", "") if isinstance(editor_context, dict) else "")
            if file_path:
                editor_hint = (
                    f'[Editor context: User is currently viewing `{file_path}` ("{file_name}") '
                    f"in the editor panel. You can read this file to see its contents.]"
                )
                if isinstance(query_content, str):
                    query_content = f"{editor_hint}\n\n{query_content}"
                elif isinstance(query_content, list):
                    query_content = [
                        {"type": "text", "text": editor_hint},
                        *query_content,
                    ]

        # Get agent config — file-based for default agent, DB for custom agents
        agent_config = await build_agent_config(agent_id)
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
                last_model = self._resolve_model(agent_config)
            yield event

        # --- Post-response context monitor ---
        # Compute context usage from the SDK's ResultMessage.usage
        # normalized by num_turns (inline, no filesystem scan).
        # Emits on every turn with valid data.
        if effective_sid:
            try:
                turns = self._user_turn_counts.get(effective_sid, 0) + 1
                self._user_turn_counts[effective_sid] = turns
                # Track last known input tokens for dynamic watchdog timeout
                if last_input_tokens and last_input_tokens > 0:
                    self._session_last_input_tokens[effective_sid] = last_input_tokens

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

                # Mark session as actively streaming so the cleanup loop
                # skips it regardless of last_used timestamp. Cleared in
                # the post-stream block below (both success and error paths).
                _path_b_streaming_info = self._active_sessions.get(session_id)
                _path_b_pid: int | None = _path_b_streaming_info.get("pid") if _path_b_streaming_info else None
                self._enter_streaming(_path_b_streaming_info, _path_b_pid)

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
                finally:
                    # Clear is_streaming flag — session is now idle.
                    _path_b_done_info = self._active_sessions.get(session_id)
                    self._exit_streaming(_path_b_done_info, _path_b_pid)

                # Update last_used after PATH B streaming completes (Bug 2 fix).
                # Prevents _cleanup_stale_sessions_loop Tier 1 from killing
                # subprocesses that were actively streaming. Without this,
                # last_used stays at the value set by _get_active_client at
                # request start, causing the cleanup loop to see the session
                # as idle even though streaming just completed.
                _path_b_info = self._active_sessions.get(session_id)
                if _path_b_info:
                    _path_b_info["last_used"] = time.time()

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
                        await self._disconnect_wrapper(
                            evicted["wrapper"], f"pathB-evict-{session_id}",
                        )

                    # Signal auto-retry: reset error state, fresh accumulator
                    _need_fresh_client = True
                    session_context["had_error"] = False
                    assistant_content = ContentBlockAccumulator()

                    # FIX: Preserve session identity during PATH B → PATH A auto-retry.
                    # Without this, the retry spawns a new subprocess which gets a new
                    # SDK session ID, emits session_start with the new ID, and creates
                    # a new DB session — orphaning the original session's messages.
                    # After app restart, loadSessionMessages(new-id) returns only the
                    # retry's messages; the original conversation is lost from the UI.
                    if session_context.get("app_session_id") is None and session_id:
                        session_context["app_session_id"] = session_id
                        agent_config["needs_context_injection"] = True
                        agent_config["resume_app_session_id"] = session_id

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
                    # Tell the frontend we're cold-starting a resume — shows
                    # "Resuming session..." instead of ambiguous "Thinking..."
                    if not _need_fresh_client:  # Don't double-emit on auto-retry
                        yield {"type": "session_resuming"}
                    # Observability: log the resume-fallback path
                    if session_context.get("app_session_id") is not None:
                        logger.info(
                            f"Resume-fallback in _execute_on_session: "
                            f"no active client for app session {session_context['app_session_id']}, "
                            f"creating fresh SDK session"
                        )
                    # Flag for context injection: we lost the SDK client, so
                    # inject previous conversation context into the system prompt.
                    # MUST be set BEFORE _build_options() which calls
                    # _build_system_prompt() where the flag is consumed.
                    agent_config["needs_context_injection"] = True
                    agent_config["resume_app_session_id"] = app_session_id

                    options = await self._build_options(
                        agent_config, enable_skills, enable_mcp,
                        None, session_context, channel_context,
                    )
                    # Reset to behave as a new session
                    is_resuming = False
                    session_context["sdk_session_id"] = None

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

                # Global spawn cooldown: if a recent -9 failure occurred,
                # wait instead of spawning immediately.  This prevents the
                # user from hammering "retry" and creating competing processes
                # that all die under memory pressure — making recovery slower.
                # The cooldown only applies to non-retry spawns (retry loop
                # has its own backoff with _need_fresh_client set).
                if not _need_fresh_client:
                    _cooldown_remaining = (
                        self._last_sigkill_time + self.SPAWN_COOLDOWN_SECONDS
                        - time.time()
                    )
                    if _cooldown_remaining > 0:
                        logger.info(
                            "Spawn cooldown: %.1fs remaining after recent SIGKILL — "
                            "waiting before spawn",
                            _cooldown_remaining,
                        )
                        yield {
                            "type": "assistant",
                            "content": [{
                                "type": "text",
                                "text": (
                                    f"\n\n⏳ *Recovering from a process crash — "
                                    f"retrying in {_cooldown_remaining:.0f}s...*\n\n"
                                ),
                            }],
                        }
                        await asyncio.sleep(min(_cooldown_remaining, self.SPAWN_COOLDOWN_SECONDS))

                # Enforce max concurrent subprocess cap before spawning.
                # Disconnect oldest idle subprocesses to free RAM and prevent
                # macOS OOM-killer from SIGKILL-ing active processes.
                await self._evict_idle_subprocesses()

                # Pre-spawn memory pressure check: refuse to spawn if the
                # system is critically low on RAM.  Spawning under memory
                # pressure leads to immediate SIGKILL (-9) by macOS jetsam,
                # wasting retry attempts and showing confusing errors.
                _mem_error = self._check_memory_pressure()
                if _mem_error:
                    yield _build_error_event(
                        code="MEMORY_PRESSURE",
                        message=_mem_error,
                        suggested_action=(
                            "Close unused browser tabs, Kiro, or other heavy apps. "
                            "Your conversation is saved — try again when memory frees up."
                        ),
                    )
                    return

                _t_client_start = time.monotonic()
                logger.info("Creating new ClaudeSDKClient...")
                wrapper = _ClaudeClientWrapper(options=options)
                # Hold _env_lock during client creation so the spawned
                # subprocess inherits the correct os.environ values.
                # After __aenter__ the subprocess has its own env copy.
                #
                # FIX (2026-03-17 Sev-1): Wrap connect() in try/except so
                # -9 during subprocess init triggers the retry loop instead
                # of propagating to the outer handler as a raw error.
                # Previously, SIGKILL during connect() → initialize() was
                # unrecoverable — the user saw a scary error with no retry.
                try:
                    async with _env_lock:
                        _configure_claude_environment(self._config)
                        client = await wrapper.__aenter__()
                except Exception as _init_exc:
                    _init_error = str(_init_exc)
                    if _is_retriable_error(_init_error):
                        logger.warning(
                            "Retriable error during connect(): %s — "
                            "setting had_error for retry loop",
                            _init_error[:120],
                        )
                        if "exit code -9" in _init_error:
                            self._last_sigkill_time = time.time()
                        session_context["had_error"] = True
                        # Preserve session identity for retry
                        if session_context.get("app_session_id") is None:
                            _orig_sid = session_context.get("sdk_session_id")
                            if _orig_sid:
                                session_context["app_session_id"] = _orig_sid
                                agent_config["needs_context_injection"] = True
                                agent_config["resume_app_session_id"] = _orig_sid
                        # Create a dummy client so the retry loop's
                        # disconnect call doesn't crash on None.
                        client = None  # type: ignore[assignment]
                    else:
                        raise
                _t_client_elapsed = time.monotonic() - _t_client_start

                # Skip the streaming loop if connect() failed — go straight
                # to the retry loop below (which checks had_error).
                if session_context.get("had_error"):
                    logger.info(
                        "Connect failed in %.1fs, skipping to retry loop",
                        _t_client_elapsed,
                    )
                else:
                    logger.info(
                        "ClaudeSDKClient created in %.1fs, is_resuming=%s",
                        _t_client_elapsed, is_resuming,
                    )

                # Early registration: store client in _active_sessions NOW
                # so interrupt_session can find it during streaming.
                # For resumed sessions: keyed by app_session_id (already known).
                # For new sessions: keyed AFTER SDK init provides session_id
                # (via _wrapper in session_context — see _run_query_on_client).
                #
                # IMPORTANT: The post-stream storage code (after the yield loop)
                # may never run if the SSE consumer is cancelled. This early
                # registration is the ONLY reliable path for client persistence.
                #
                # GUARD: Skip all of this when connect() failed (had_error=True).
                # The retry loop below will handle spawning a new subprocess.
                if not session_context.get("had_error"):
                    session_context["_wrapper"] = wrapper  # For init-time registration in _run_query_on_client
                    if session_context.get("app_session_id"):
                        _early_key = session_context["app_session_id"]
                        _early_info = {
                            "client": client,
                            "wrapper": wrapper,
                            "created_at": time.time(),
                            "last_used": time.time(),
                            "activity_extracted": False,
                            "is_frozen": False,
                            "failure_tracker": ToolFailureTracker(),
                        }
                        # Register PID in global tracker immediately after spawn
                        self._register_wrapper_pid(wrapper, _early_info)
                        self._active_sessions[_early_key] = _early_info
                        session_context["_early_active_key"] = _early_key

                # Mark session as actively streaming so the cleanup loop
                # skips it regardless of last_used timestamp.
                _path_a_pid: int | None = None
                _early_streaming_info: dict | None = None
                if not session_context.get("had_error"):
                    _early_key_for_streaming = session_context.get("_early_active_key")
                    if _early_key_for_streaming:
                        _early_streaming_info = self._active_sessions.get(_early_key_for_streaming)
                        _path_a_pid = _early_streaming_info.get("pid") if _early_streaming_info else None
                    self._enter_streaming(_early_streaming_info, _path_a_pid)

                    # Determine if this is a cold-start resume (fresh subprocess
                    # after idle kill, eviction, or app restart).  Cold starts use
                    # a tighter watchdog timeout because the fresh subprocess has
                    # no cached context to process — it should respond quickly.
                    _is_cold_start = agent_config.get("needs_context_injection", False)

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
                            is_cold_start=_is_cold_start,
                        ):
                            yield event
                    except Exception as _exc:
                        # On error, disconnect the wrapper instead of keeping alive
                        await self._disconnect_wrapper(wrapper, f"error-{session_id or 'new'}")
                        # Track SIGKILL for spawn cooldown (catches -9 during connect/query)
                        if "exit code -9" in str(_exc):
                            self._last_sigkill_time = time.time()
                        raise
                    finally:
                        # Clear is_streaming flag — session is now idle.
                        _early_key_done = session_context.get("_early_active_key")
                        _early_done_info = self._active_sessions.get(_early_key_done) if _early_key_done else None
                        self._exit_streaming(_early_done_info, _path_a_pid)

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
                    # FIX: Preserve session identity during PATH A retry.
                    # The retry spawns a new subprocess with a new SDK session ID.
                    # Setting app_session_id ensures the retry's init handler
                    # suppresses session_start and saves messages under the
                    # original session ID — preventing orphaned messages.
                    if session_context.get("app_session_id") is None:
                        _original_sid = session_context.get("sdk_session_id")
                        if _original_sid:
                            session_context["app_session_id"] = _original_sid
                            agent_config["needs_context_injection"] = True
                            agent_config["resume_app_session_id"] = _original_sid

                    # PATH A auto-retry loop: retry up to MAX_RETRY_ATTEMPTS
                    # times with exponential backoff.  Each retry spawns a
                    # fresh subprocess.  The backoff gives macOS time to
                    # reclaim memory from the SIGKILL'd process, reducing
                    # the chance of consecutive OOM kills.
                    #
                    # Instant-kill detection (Fix 2026-03-16): if the process
                    # died within INSTANT_KILL_THRESHOLD_SECONDS of spawning,
                    # it's almost certainly macOS jetsam (OOM).  In that case:
                    #  - Use OOM_BACKOFF_SECONDS instead of normal backoff
                    #  - Check memory pressure before retrying
                    #  - Abort early if memory is still critically low
                    _retry_count = session_context.get("_path_a_retry_count", 0)
                    _max_retries = self.MAX_RETRY_ATTEMPTS if not _need_fresh_client else self.MAX_RETRY_ATTEMPTS + 1
                    _consecutive_instant_kills = session_context.get("_consecutive_instant_kills", 0)
                    while (
                        session_context.get("had_error")
                        and _retry_count < _max_retries
                    ):
                        _retry_count += 1
                        session_context["_path_a_retry_count"] = _retry_count

                        # Detect instant kills: if spawn-to-death < threshold,
                        # the OS is killing us immediately (OOM/jetsam).
                        _spawn_to_death = time.monotonic() - _t_client_start
                        _is_instant_kill = _spawn_to_death < self.INSTANT_KILL_THRESHOLD_SECONDS
                        if _is_instant_kill:
                            _consecutive_instant_kills += 1
                            session_context["_consecutive_instant_kills"] = _consecutive_instant_kills
                            logger.warning(
                                "Instant kill detected: process died %.1fs after spawn "
                                "(threshold: %.1fs, consecutive: %d)",
                                _spawn_to_death,
                                self.INSTANT_KILL_THRESHOLD_SECONDS,
                                _consecutive_instant_kills,
                            )
                        else:
                            # Non-instant death — the process ran for a while before
                            # failing.  Reset the OOM counter so a previous OOM episode
                            # doesn't poison future retries (e.g. user closed apps and
                            # retried 30 min later).
                            if _consecutive_instant_kills > 0:
                                logger.info(
                                    "Resetting consecutive instant-kill counter "
                                    "(process survived %.1fs, threshold: %.1fs)",
                                    _spawn_to_death,
                                    self.INSTANT_KILL_THRESHOLD_SECONDS,
                                )
                            _consecutive_instant_kills = 0
                            session_context["_consecutive_instant_kills"] = 0

                        # After 2+ consecutive instant kills, abort — retrying
                        # is futile and just wastes time under OOM.
                        if _consecutive_instant_kills >= 2:
                            logger.error(
                                "Aborting retries: %d consecutive instant kills — "
                                "system is under severe memory pressure",
                                _consecutive_instant_kills,
                            )
                            yield _build_error_event(
                                code="OOM_KILL",
                                message=(
                                    "The AI process keeps getting killed by the operating system "
                                    "due to low memory. Retrying won't help right now."
                                ),
                                suggested_action=(
                                    "Close some apps (especially Kiro, browsers with many tabs, "
                                    "or other AI tools), then try again. "
                                    "Your conversation is saved."
                                ),
                            )
                            break

                        # Choose backoff: OOM gets much longer to let OS reclaim
                        if _is_instant_kill:
                            _backoff = self.OOM_BACKOFF_SECONDS
                        else:
                            _backoff = self.RETRY_BACKOFF_SECONDS * _retry_count

                        logger.info(
                            "PATH A: auto-retry attempt %d/%d after %.1fs backoff%s",
                            _retry_count, _max_retries, _backoff,
                            " (OOM backoff)" if _is_instant_kill else "",
                        )
                        await asyncio.sleep(_backoff)

                        # Pre-retry memory check: if still under pressure, abort
                        # instead of burning the retry on another instant kill.
                        _mem_error = self._check_memory_pressure()
                        if _mem_error:
                            logger.warning("Memory still low after backoff — aborting retry")
                            yield _build_error_event(
                                code="MEMORY_PRESSURE",
                                message=_mem_error,
                                suggested_action=(
                                    "Close unused browser tabs, Kiro, or other heavy apps. "
                                    "Your conversation is saved — try again when memory frees up."
                                ),
                            )
                            break

                        session_context["had_error"] = False
                        session_context["_path_a_retried"] = True
                        assistant_content = ContentBlockAccumulator()
                        # Update last_used on early-registered session to prevent
                        # cleanup loop from interfering during retry backoff (Bug 2 defensive).
                        _early_key = session_context.get("_early_active_key")
                        if _early_key:
                            _early_info = self._active_sessions.get(_early_key)
                            if _early_info:
                                _early_info["last_used"] = time.time()
                        await self._disconnect_wrapper(wrapper, f"retry-{_retry_count}-{session_id}")

                        # Re-enter streaming state on the frontend
                        yield {"type": "reconnecting"}

                        # Visual indicator (only on first retry to avoid spam)
                        if _retry_count == 1:
                            _retry_reason = (
                                "System memory is low — waiting for resources..."
                                if _is_instant_kill
                                else "AI service was slow to respond. Retrying automatically..."
                            )
                            yield {
                                "type": "assistant",
                                "content": [{
                                    "type": "text",
                                    "text": (
                                        "\n\n---\n\n"
                                        f"⚠️ *{_retry_reason}*\n\n"
                                    ),
                                }],
                            }

                        # Aggressively evict idle subprocesses before retry
                        await self._evict_idle_subprocesses()
                        options = await self._build_options(
                            agent_config, enable_skills, enable_mcp,
                            None, session_context, channel_context,
                        )
                        _t_client_start = time.monotonic()  # Reset for instant-kill detection
                        wrapper = _ClaudeClientWrapper(options=options)
                        # FIX (2026-03-17 Sev-1): Wrap retry connect() so -9
                        # during init continues the retry loop instead of
                        # propagating to the outer handler.
                        try:
                            async with _env_lock:
                                _configure_claude_environment(self._config)
                                client = await wrapper.__aenter__()
                        except Exception as _retry_init_exc:
                            _retry_init_error = str(_retry_init_exc)
                            if _is_retriable_error(_retry_init_error):
                                logger.warning(
                                    "PATH A retry %d: connect() failed with "
                                    "retriable error: %s — will retry",
                                    _retry_count, _retry_init_error[:120],
                                )
                                if "exit code -9" in _retry_init_error:
                                    self._last_sigkill_time = time.time()
                                session_context["had_error"] = True
                                continue  # Back to while had_error loop
                            else:
                                raise
                        # Register retry PID in global tracker AND in the
                        # early-registered session info.  Without this, the
                        # tracked-leak sweep sees the PID as orphaned during
                        # long queries and kills it (COE: infinite -9 loop).
                        _early_key_pid = session_context.get("_early_active_key")
                        _early_info_pid = (
                            self._active_sessions.get(_early_key_pid)
                            if _early_key_pid else None
                        )
                        self._register_wrapper_pid(wrapper, _early_info_pid)
                        # Also update wrapper/client so eviction logic sees
                        # this session as live (not orphaned).
                        if _early_info_pid is not None:
                            _early_info_pid["wrapper"] = wrapper
                            _early_info_pid["client"] = client
                            _early_info_pid["last_used"] = time.time()
                        # Mark retry PID as streaming so leak sweep skips it
                        _retry_pid = wrapper.pid
                        self._enter_streaming(_early_info_pid, _retry_pid)
                        logger.info(
                            "PATH A retry %d: fresh client created (pid=%s, stored in session=%s)",
                            _retry_count, wrapper.pid, _early_key_pid or "none",
                        )

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
                                is_cold_start=True,  # Retry = fresh subprocess, tight timeout
                            ):
                                yield event
                        except Exception as _rexc:
                            await self._disconnect_wrapper(wrapper, f"retry-{_retry_count}-error-{session_id}")
                            # Track SIGKILL for spawn cooldown
                            if "exit code -9" in str(_rexc):
                                self._last_sigkill_time = time.time()
                            raise
                        finally:
                            # Clear retry streaming PID
                            self._exit_streaming(_early_info_pid, _retry_pid)

                    # Re-evaluate after retry loop
                    final_session_id = session_context["sdk_session_id"]
                    effective_session_id = (
                        session_context["app_session_id"]
                        if session_context.get("app_session_id") is not None
                        else final_session_id
                    )

                    if session_context.get("had_error"):
                        logger.info("Session had error (after retry), disconnecting instead of storing")
                        await self._disconnect_wrapper(wrapper, f"had-error-{session_id}")
                        # Clean up early registration
                        _early_key = session_context.get("_early_active_key")
                        if _early_key:
                            self._active_sessions.pop(_early_key, None)
                        # FIX (2026-03-18): Yield error event when all retries
                        # exhausted.  Previously the generator ended silently
                        # and the user saw "Thinking..." forever.
                        yield _build_error_event(
                            code="ALL_RETRIES_EXHAUSTED",
                            message=(
                                "The AI service couldn't start after multiple attempts. "
                                "This is usually temporary."
                            ),
                            suggested_action=(
                                "Your conversation is saved. Wait a moment, "
                                "then send your message again."
                            ),
                        )
                    elif effective_session_id:
                        _final_info = {
                            "client": client,
                            "wrapper": wrapper,
                            "created_at": time.time(),
                            "last_used": time.time(),
                            "activity_extracted": False,
                            "is_frozen": False,
                            "failure_tracker": ToolFailureTracker(),
                            "pid": wrapper.pid,
                        }
                        self._active_sessions[effective_session_id] = _final_info
                        # Ensure PID is in global tracker
                        self._register_wrapper_pid(wrapper, _final_info)
                        # Clean up early key if it differs from the final key
                        _early_key = session_context.get("_early_active_key")
                        if _early_key and _early_key != effective_session_id:
                            self._active_sessions.pop(_early_key, None)
                        logger.info(f"Stored long-lived client for session {effective_session_id}")
                    else:
                        # No effective_session_id — clean up early registration
                        _early_key = session_context.get("_early_active_key")
                        if _early_key:
                            self._active_sessions.pop(_early_key, None)
                            logger.warning(
                                "No effective_session_id after stream, "
                                "cleaned up early key %s", _early_key
                            )

        except Exception as e:
            error_traceback = traceback.format_exc()
            logger.error(f"Error in conversation: {e}")
            logger.error(f"Full traceback:\n{error_traceback}")
            # Track SIGKILL in the outer handler too — catches -9 errors
            # that escaped the inner retry logic (non-retriable or unexpected).
            if "exit code -9" in str(e):
                self._last_sigkill_time = time.time()
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
                friendly_msg, suggested = _sanitize_sdk_error(str(e))
                yield _build_error_event(
                    code="CONVERSATION_ERROR",
                    message=friendly_msg,
                    detail=error_traceback,
                    suggested_action=suggested,
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
        is_cold_start: bool = False,
    ) -> AsyncIterator[dict]:
        """Send a query on an existing client and yield SSE events.

        This is the shared message-processing loop used by both new and resumed sessions.
        The client is NOT disconnected after the response completes (caller manages lifecycle).

        Args:
            is_cold_start: True when this is a resume-fallback PATH A with a
                fresh subprocess (context injection).  Uses a tighter initial
                watchdog timeout (COLD_START_TIMEOUT) because a fresh subprocess
                should respond faster than a mid-conversation reuse.
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
        # Pre-initialize before try so the finally block can always iterate,
        # even if client.query() throws before the list is populated.
        _generation = 0
        _reader_tasks: list[asyncio.Task] = []

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
            # Event signalled when sdk_session_id becomes available (init message).
            # Replaces the 0.05s busy-poll in permission_request_forwarder.
            _session_id_ready = asyncio.Event()
            message_count = 0

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
                    # Wait for sdk_session_id via Event instead of busy-polling.
                    # The event is set by the init message handler below.
                    await _session_id_ready.wait()
                    current_session_id = session_context.get("sdk_session_id")
                    if not current_session_id:
                        logger.warning("session_id_ready fired but sdk_session_id is None")
                        return
                    session_queue = _pm.get_session_queue(current_session_id)
                    while True:
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
            # Cold-start resumes use a tighter initial timeout (COLD_START_TIMEOUT)
            # because a fresh subprocess should respond in ~15s — no reason to
            # wait 180s.  Once streaming starts, inter-message uses normal timeout.
            _WATCHDOG_INITIAL_TIMEOUT = (
                self.COLD_START_TIMEOUT if is_cold_start
                else self._compute_watchdog_timeout(
                    session_context.get("sdk_session_id")
                    or session_context.get("app_session_id")
                )
            )
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
                    _timeout_msg = (
                        f"Session couldn't start within {watchdog_timeout}s. "
                        "Your machine may be under load."
                    ) if is_cold_start and not _got_first_real_message else (
                        f"The AI service didn't respond within {watchdog_timeout}s. "
                        "This usually means the Claude backend is temporarily "
                        "overloaded or the request was too complex."
                    )
                    yield _build_error_event(
                        code="SDK_SUBPROCESS_TIMEOUT",
                        message=_timeout_msg,
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
                    raw_error = str(item["error"])
                    logger.error(f"Error from SDK reader: {raw_error}")
                    session_context["had_error"] = True

                    # Set global spawn cooldown on SIGKILL (-9) to prevent
                    # user retries from creating competing spawns that all
                    # die under memory pressure (COE 2026-03-17).
                    if "exit code -9" in raw_error:
                        self._last_sigkill_time = time.time()

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

                    # Determine if auto-retry will handle this error silently.
                    # PATH B (reused client) and PATH A (fresh client) both
                    # check `had_error` after the queue loop and retry with
                    # a friendly "reconnecting" indicator.  When retries
                    # remain, suppress the raw error event to avoid showing
                    # scary messages before the retry.
                    _retry_count = session_context.get("_path_a_retry_count", 0)
                    _max_retries = self.MAX_RETRY_ATTEMPTS
                    _will_auto_retry = (
                        _is_retriable_error(raw_error)
                        and _retry_count < _max_retries
                    )

                    if _will_auto_retry:
                        # Silent break — auto-retry path will handle the UX
                        logger.info(
                            "Suppressing error event for retriable error "
                            "(auto-retry will handle): %s", raw_error[:120]
                        )
                    else:
                        # Non-retriable or already retried — show friendly error
                        friendly_msg, suggested = _sanitize_sdk_error(raw_error)
                        # TSCC: mark lifecycle as failed (best-effort)
                        try:
                            sid = session_context.get("sdk_session_id")
                            if sid:
                                await _tscc_state_manager.set_lifecycle_state(sid, "failed")
                        except Exception:
                            logger.debug("TSCC: failed lifecycle update failed", exc_info=True)
                        yield _build_error_event(
                            code="SDK_STREAM_ERROR",
                            message=friendly_msg,
                            detail=item.get("detail") if settings.debug else None,
                            suggested_action=suggested,
                        )
                    break

                if item["source"] == "sdk":
                    message = item["message"]
                    message_count += 1

                    # --- StreamEvent: partial message deltas (token streaming) ---
                    # When include_partial_messages=True, the SDK yields StreamEvent
                    # objects with raw Anthropic API streaming events (content_block_delta,
                    # content_block_start, content_block_stop, etc.).  These are
                    # lightweight and should be forwarded to the frontend immediately
                    # for real-time text rendering — NOT accumulated into the DB.
                    # The full AssistantMessage is still yielded at the end for DB persistence.
                    if isinstance(message, StreamEvent):
                        _got_first_real_message = True
                        event_data = message.event
                        event_type = event_data.get("type", "")

                        if event_type == "content_block_delta":
                            delta = event_data.get("delta", {})
                            if delta.get("type") == "text_delta" and delta.get("text"):
                                yield {
                                    "type": "text_delta",
                                    "text": delta["text"],
                                    "index": event_data.get("index", 0),
                                }
                            elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                                yield {
                                    "type": "thinking_delta",
                                    "thinking": delta["thinking"],
                                    "index": event_data.get("index", 0),
                                }
                        elif event_type == "content_block_start":
                            block = event_data.get("content_block", {})
                            block_type = block.get("type", "")
                            if block_type == "thinking":
                                yield {
                                    "type": "thinking_start",
                                    "index": event_data.get("index", 0),
                                }
                            elif block_type == "text":
                                yield {
                                    "type": "text_start",
                                    "index": event_data.get("index", 0),
                                }
                        elif event_type == "content_block_stop":
                            yield {
                                "type": "content_block_stop",
                                "index": event_data.get("index", 0),
                            }
                        # All other stream events (message_start, message_delta, etc.)
                        # are silently consumed — they don't carry renderable content.
                        continue

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
                                # Track SIGKILL for global spawn cooldown
                                if "exit code -9" in error_text:
                                    self._last_sigkill_time = time.time()
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
                                # Determine retry eligibility BEFORE cleanup (Bug 1 fix).
                                # If auto-retry will handle this error, preserve session
                                # state (wrapper, lock, permission queue) for the retry
                                # path in _execute_on_session_inner.
                                _retry_count_ede = session_context.get("_path_a_retry_count", 0)
                                _will_auto_retry_ede = (
                                    _is_retriable_error(error_text)
                                    and _retry_count_ede < self.MAX_RETRY_ATTEMPTS
                                )

                                # Only clean up if auto-retry will NOT handle this
                                if not _will_auto_retry_ede:
                                    if eff_sid and eff_sid in self._active_sessions:
                                        await self._cleanup_session(eff_sid, skip_hooks=True)
                                        logger.info(f"Removed broken session {eff_sid} from active sessions pool")
                                if _will_auto_retry_ede:
                                    logger.info(
                                        "Suppressing error_during_execution for retriable "
                                        "error (auto-retry will handle): %s", error_text[:120]
                                    )
                                else:
                                    friendly_msg, suggested = _sanitize_sdk_error(error_text)
                                    yield _build_error_event(
                                        code="ERROR_DURING_EXECUTION",
                                        message=friendly_msg,
                                        suggested_action=suggested,
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
                            # Track SIGKILL for global spawn cooldown
                            if "exit code -9" in (raw_error or ""):
                                self._last_sigkill_time = time.time()
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

                            # Check if auto-retry will handle this error silently
                            # (same logic as SDK reader error path)
                            # Unified retry-eligibility (Bug 3 fix): use count-based
                            # condition, same as the SDK reader error path.
                            _retry_count_sdk = session_context.get("_path_a_retry_count", 0)
                            _will_auto_retry_sdk = (
                                _is_retriable_error(error_msg)
                                and _retry_count_sdk < self.MAX_RETRY_ATTEMPTS
                            )
                            if _will_auto_retry_sdk:
                                logger.info(
                                    "Suppressing is_error event for retriable error "
                                    "(auto-retry will handle): %s", error_msg[:120]
                                )
                            else:
                                friendly_msg, suggested = _sanitize_sdk_error(error_msg)
                                yield _build_error_event(
                                    code="SDK_ERROR",
                                    message=friendly_msg,
                                    suggested_action=suggested,
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
                                # With include_partial_messages=True, the text was already
                                # delivered to the frontend via text_delta events (token-by-
                                # token) and the AssistantMessage event (full blocks).
                                # ResultMessage.result is a concatenation of ALL TextBlocks
                                # from the final turn, so its blockKey ("text:<concat>")
                                # differs from any individual TextBlock's key, causing
                                # updateMessages to append it as a duplicate.
                                # We still accumulate it for DB persistence (assistant_content)
                                # but do NOT yield it as an SSE event.
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
                            _session_id_ready.set()  # Unblock permission_request_forwarder
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
                                # Observability: log the session ID mapping for debugging
                                if session_context["sdk_session_id"] != session_context["app_session_id"]:
                                    logger.info(
                                        f"Resume-fallback: mapping SDK session "
                                        f"{session_context['sdk_session_id']} → "
                                        f"app session {session_context['app_session_id']}"
                                    )
                            elif not is_resuming:
                                # New session — session_start + user message save

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

                                # CRITICAL: Anchor app_session_id so retries
                                # map to the SAME DB session instead of creating
                                # a new one on each retry attempt.  Without this,
                                # every retry yields a new session_start, and the
                                # frontend ends up pointing at the last retry's
                                # sparse session — making prior messages disappear.
                                session_context["app_session_id"] = session_context["sdk_session_id"]

                            # Early client registration for NEW sessions:
                            # The post-stream storage code in _execute_on_session
                            # may never run (SSE consumer cancellation race).
                            # Register the client NOW so _get_active_client()
                            # finds it on the next resume attempt.
                            _init_sid = session_context["sdk_session_id"]
                            _init_wrapper = session_context.get("_wrapper")
                            if _init_sid and _init_wrapper and _init_sid not in self._active_sessions:
                                # When app_session_id is set (resume-fallback), the
                                # client is already registered under the app key.
                                # Share the SAME dict so eviction of either key
                                # clears both (prevents stale client references).
                                _app_key = session_context.get("_early_active_key")
                                _existing_info = self._active_sessions.get(_app_key) if _app_key else None
                                if _existing_info:
                                    # Alias: both keys point to the same dict.
                                    # CRITICAL: update the dict's PID/wrapper/client
                                    # to reflect the NEW subprocess.  Without this,
                                    # kill_tracked_leaks() sees the new PID in
                                    # _tracked_pids but the OLD PID in info["pid"]
                                    # and kills the actively-streaming process.
                                    _old_pid = _existing_info.get("pid")
                                    _existing_info["pid"] = _init_wrapper.pid
                                    _existing_info["wrapper"] = _init_wrapper
                                    _existing_info["client"] = client
                                    _existing_info["last_used"] = time.time()
                                    _existing_info["is_frozen"] = False
                                    self._active_sessions[_init_sid] = _existing_info
                                    logger.info(
                                        "Early client registration for new session %s "
                                        "(aliased to app key %s, pid %s→%s)",
                                        _init_sid, _app_key, _old_pid,
                                        _init_wrapper.pid,
                                    )
                                else:
                                    _init_info = {
                                        "client": client,
                                        "wrapper": _init_wrapper,
                                        "created_at": time.time(),
                                        "last_used": time.time(),
                                        "activity_extracted": False,
                                        "is_frozen": False,
                                        "failure_tracker": ToolFailureTracker(),
                                        "pid": _init_wrapper.pid,
                                    }
                                    self._register_wrapper_pid(_init_wrapper, _init_info)
                                    self._active_sessions[_init_sid] = _init_info
                                    session_context["_early_active_key"] = _init_sid
                                    logger.info(
                                        "Early client registration for new session %s",
                                        _init_sid,
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

            # _active_sessions is the single source of truth for client
            # tracking. No _clients cleanup needed — it was eliminated.
            # The session stays in _active_sessions for future resume.

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
        # Get agent config — file-based for default agent, DB for custom agents
        agent_config = await build_agent_config(agent_id)
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
                last_model = self._resolve_model(agent_config)
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
        # Get agent config — file-based for default agent, DB for custom agents
        agent_config = await build_agent_config(agent_id)
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
            # Store approval in per-session PermissionManager (in-memory only)
            _pm.approve_command(perm_session_id, command)
            logger.info(f"Command approved for session {perm_session_id}: {command[:50]}...")
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
            logger.info("Shutdown: no active sessions")
            # Still cancel the cleanup loop
            if self._cleanup_task and not self._cleanup_task.done():
                self._cleanup_task.cancel()
            # Even with no active sessions, there may be tracked PIDs from
            # sessions that were cleaned up individually. Sweep them.
            leaked = self.kill_tracked_leaks()
            orphans = self.kill_orphan_claude_processes()
            if leaked or orphans:
                logger.warning(
                    "Shutdown: killed %d leaked + %d orphan process(es) in final sweep",
                    leaked, orphans,
                )
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

        # ── Phase 3: Final safety sweep — kill any remaining tracked PIDs ──
        # COE 2026-03-15: last-resort backstop. If any spawned processes
        # survived Phase 1d disconnect, force-kill them now.
        remaining_leaked = self.kill_tracked_leaks()
        if remaining_leaked:
            logger.warning(
                "Shutdown Phase 3: killed %d leaked process(es) from PID tracker",
                remaining_leaked,
            )
        # Also do an OS-level orphan sweep as absolute last resort
        final_orphans = self.kill_orphan_claude_processes()
        if final_orphans:
            logger.warning(
                "Shutdown Phase 3: killed %d orphan process(es) in final sweep",
                final_orphans,
            )

        total_elapsed = time.monotonic() - t0
        logger.info("Shutdown disconnect_all completed in %.2fs", total_elapsed)

    async def interrupt_session(self, session_id: str) -> dict:
        """Interrupt a running session.

        Looks up the client from ``_active_sessions`` which is the single
        source of truth for client tracking.  For resumed sessions, the
        client is registered at creation time (early registration) so it's
        available during streaming.

        Args:
            session_id: The session ID to interrupt

        Returns:
            Dict with status information
        """
        # Single source of truth: _active_sessions (persistent, survives stream end)
        client = None
        info = self._active_sessions.get(session_id)
        if info:
            client = info.get("client")
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
            finally:
                # _get_active_client sets is_streaming=True on thaw to guard
                # against the cleanup loop race.  We must clear it when done.
                _compact_info = self._active_sessions.get(session_id)
                if _compact_info:
                    _compact_info["last_used"] = time.time()
                self._exit_streaming(_compact_info)

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
