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
- ``context_assembler.py``   — 8-layer context assembly engine for agent runtime
- ``context_snapshot_cache.py`` — Version-based caching for assembled context snapshots

All public symbols from those modules are re-exported here for backward compatibility,
so existing callers require zero import changes.

Key responsibilities retained in this module:
- ``_build_options``          — Orchestrates 6 helpers to assemble ``ClaudeAgentOptions``
- ``_build_system_prompt``    — Assembles system prompt via ContextDirectoryLoader
                                (global context) + ContextAssembler (project context)
                                + SystemPromptBuilder (non-file sections)
- ``_resolve_project_id``     — Resolves project UUID from agent config or channel context
- ``_execute_on_session``     — Shared session setup / query / streaming (used by
                                ``run_conversation`` and ``continue_with_answer``)
- ``_run_query_on_client``    — Message processing loop with SSE event dispatch
- ``_format_message``         — Converts SDK messages to frontend-friendly dicts
- Session caching with 12-hour TTL and background cleanup
"""
from typing import AsyncIterator, Optional, Any
from uuid import uuid4
from datetime import datetime
from pathlib import Path
import logging
import os
import json
import re
import asyncio
import platform
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

from database import db
from config import settings, get_bedrock_model_id, get_app_data_dir
from .session_manager import session_manager
from .system_prompt import SystemPromptBuilder
from .context_assembler import ContextAssembler, DEFAULT_TOKEN_BUDGET
from .context_snapshot_cache import context_cache
from .initialization_manager import initialization_manager

logger = logging.getLogger(__name__)

# Agent defaults extracted to agent_defaults.py — re-exported for backward compatibility
from .agent_defaults import (  # noqa: F401
    DEFAULT_AGENT_ID,
    SWARM_AGENT_NAME,
    ensure_default_agent,
    get_default_agent,
    expand_allowed_skills_with_plugins,
)


# Claude environment extracted to claude_environment.py — re-exported for backward compatibility
from .claude_environment import _ClaudeClientWrapper, _configure_claude_environment, AuthenticationNotConfiguredError  # noqa: F401


# PermissionManager extracted to permission_manager.py — re-exported for backward compatibility
from .permission_manager import permission_manager as _pm

approve_command = _pm.approve_command
is_command_approved = _pm.is_command_approved
set_permission_decision = _pm.set_permission_decision
wait_for_permission_decision = _pm.wait_for_permission_decision
_permission_request_queue = _pm.get_permission_queue()

# Keep clear_session_approvals and hash_command accessible
clear_session_approvals = _pm.clear_session_approvals
_hash_command = _pm.hash_command

# CmdPermissionManager — filesystem-backed, shared across all sessions.
from .cmd_permission_manager import CmdPermissionManager

# CredentialValidator — pre-flight STS check for Bedrock credentials.
from .credential_validator import CredentialValidator

# AppConfigManager — file-based config with in-memory cache.
from .app_config_manager import AppConfigManager


# ContentBlockAccumulator extracted to content_accumulator.py — re-exported for backward compatibility
from .content_accumulator import ContentBlockAccumulator  # noqa: F401




# Security hooks extracted to security_hooks.py — re-exported for backward compatibility
from .security_hooks import (  # noqa: F401
    DANGEROUS_PATTERNS,
    check_dangerous_command,
    pre_tool_logger,
    dangerous_command_blocker,
    create_human_approval_hook,
    create_file_access_permission_handler,
    create_skill_access_checker,
)

# Telemetry integration — best-effort, never interrupts the agent SSE stream
from .telemetry_emitter import TelemetryEmitter
from .tscc_state_manager import TSCCStateManager
from .tscc_snapshot_manager import TSCCSnapshotManager as _TSCCSnapshotManagerType

_tscc_state_manager = TSCCStateManager()
_tscc_snapshot_manager: _TSCCSnapshotManagerType | None = None


def set_tscc_snapshot_manager(mgr: _TSCCSnapshotManagerType) -> None:
    """Wire the snapshot manager at app startup (called from main.py)."""
    global _tscc_snapshot_manager
    _tscc_snapshot_manager = mgr


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
5. Keep skills concise and focused - only include what Claude needs
6. Test any scripts you create before completing

The skill-creator skill provides comprehensive guidance on:
- Skill anatomy and structure
- Progressive disclosure design
- When to use scripts, references, and assets
- Best practices for SKILL.md content

Current task: Create a skill named "{skill_name}" that {skill_description}"""


class AgentManager:
    """Manages agent lifecycle using Claude Agent SDK.

    Uses ClaudeSDKClient for stateful, multi-turn conversations with Claude.
    Claude Code (underlying SDK) has built-in support for Skills and MCP servers.
    """

    # TTL for idle sessions before automatic cleanup (12 hours)
    SESSION_TTL_SECONDS = 12 * 60 * 60

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
        # Injected components (set at startup via main.py)
        self._config: AppConfigManager | None = config_manager
        self._cmd_pm: CmdPermissionManager | None = cmd_permission_manager
        self._credential_validator: CredentialValidator | None = credential_validator

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

    def _start_cleanup_loop(self):
        """Start background task to clean up stale sessions."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_stale_sessions_loop())

    async def _cleanup_stale_sessions_loop(self):
        """Periodically clean up sessions that have been idle too long."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                now = time.time()
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

    async def _cleanup_session(self, session_id: str):
        """Disconnect and remove a stored session client."""
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

    def _get_active_client(self, session_id: str) -> ClaudeSDKClient | None:
        """Get an existing long-lived client for a session, if available."""
        info = self._active_sessions.get(session_id)
        if info:
            info["last_used"] = time.time()
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
    ) -> dict:
        """Build MCP server configuration dict from agent's mcp_ids.

        Iterates over the agent's ``mcp_ids``, looks up each MCP server record
        from the database, and assembles the ``mcp_servers`` dict keyed by
        server name (to keep tool names short for Bedrock's 64-char limit).

        No workspace filtering — all agent MCPs are included.

        Args:
            agent_config: Agent configuration dictionary.
            enable_mcp: Whether MCP servers are enabled.

        Returns:
            Dict mapping server names to their connection configuration.
        """
        mcp_servers: dict = {}

        if not (enable_mcp and agent_config.get("mcp_ids")):
            return mcp_servers

        used_names: set = set()  # Track used names to handle collisions
        for mcp_id in agent_config["mcp_ids"]:
            mcp_config = await db.mcp_servers.get(mcp_id)
            if mcp_config:
                connection_type = mcp_config.get("connection_type", "stdio")
                config = mcp_config.get("config", {})

                # Use server name as the key for shorter tool names
                # Handle name collisions by appending suffix
                server_name = mcp_config.get("name", mcp_id)
                base_name = server_name
                suffix = 1
                while server_name in used_names:
                    server_name = f"{base_name}_{suffix}"
                    suffix += 1
                used_names.add(server_name)

                if connection_type == "stdio":
                    mcp_servers[server_name] = {
                        "type": "stdio",
                        "command": config.get("command"),
                        "args": config.get("args", []),
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

        return mcp_servers

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
                "command": sys.executable,
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

    async def _build_system_prompt(
        self,
        agent_config: dict,
        working_directory: str,
        channel_context: Optional[dict],
    ) -> Any:
        """Build the system prompt with centralized context directory + project context.

        Assembly order:
        1. ContextDirectoryLoader — global context from ~/.swarm-ai/.context/
        2. ContextAssembler — project-scoped context (only when project_id exists)
        3. SystemPromptBuilder — non-file sections (safety, datetime, runtime)

        The entire assembly is wrapped in try/except so agent execution is
        never blocked by context assembly failures.
        """
        # ── 1. Centralized context directory (global context) ──────────
        try:
            from .context_directory_loader import ContextDirectoryLoader
            context_dir = get_app_data_dir() / ".context"
            loader = ContextDirectoryLoader(
                context_dir=context_dir,
                token_budget=agent_config.get("context_token_budget", DEFAULT_TOKEN_BUDGET),
                templates_dir=Path(__file__).resolve().parent.parent / "context",
            )
            loader.ensure_directory()

            model = self._resolve_model(agent_config)
            model_context_window = self._get_model_context_window(model)
            context_text = loader.load_all(model_context_window=model_context_window)

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
        except Exception as e:
            logger.warning("ContextDirectoryLoader failed: %s", e)

        # ── 2. Project-scoped context (ContextAssembler) ───────────────
        try:
            ws_config = await db.workspace_config.get_config()
            if ws_config:
                workspace_path = ws_config.get("file_path", "")
                if not workspace_path:
                    workspace_path = str(get_app_data_dir() / "SwarmWS")

                project_id = self._resolve_project_id(agent_config, channel_context)
                thread_id = agent_config.get("thread_id")

                if project_id:
                    token_budget = agent_config.get(
                        "context_token_budget", DEFAULT_TOKEN_BUDGET
                    )
                    assembler = ContextAssembler(
                        workspace_path=workspace_path,
                        token_budget=token_budget,
                    )
                    result = await context_cache.get_or_assemble(
                        assembler, project_id, thread_id, token_budget,
                    )

                    context_parts = [
                        f"## {layer.name}\n{layer.content}"
                        for layer in result.layers
                        if layer.content.strip()
                    ]
                    if result.truncation_summary:
                        context_parts.append(result.truncation_summary)

                    context_text = "\n\n".join(context_parts)
                    if context_text:
                        existing = agent_config.get("system_prompt", "") or ""
                        agent_config["system_prompt"] = (
                            existing + "\n\n" + context_text
                            if existing
                            else context_text
                        )
                        logger.info(
                            "Injected project context: %d layers, %d tokens",
                            len(result.layers),
                            result.total_token_count,
                        )
        except Exception as e:
            logger.warning("Failed to assemble project context: %s", e)

        # ── 3. SystemPromptBuilder (non-file sections only) ────────────
        sdk_add_dirs = agent_config.get("add_dirs", [])
        prompt_builder = SystemPromptBuilder(
            working_directory=working_directory,
            agent_config=agent_config,
            channel_context=channel_context,
            add_dirs=sdk_add_dirs,
        )
        return prompt_builder.build()

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
        mcp_servers = await self._build_mcp_config(agent_config, enable_mcp)

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

        # Delegate to shared session execution pattern
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
            yield event

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
        """
        # Configure Claude environment variables
        try:
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
        options = await self._build_options(
            agent_config, enable_skills, enable_mcp,
            session_id if is_resuming else None,
            session_context, channel_context,
        )
        logger.info(f"Built options - allowed_tools: {options.allowed_tools}, permission_mode: {options.permission_mode}, resume: {session_id if is_resuming else None}")
        logger.info(f"MCP servers: {list(options.mcp_servers.keys()) if options.mcp_servers else None}")
        logger.info(f"Working directory: {options.cwd}")

        # Collect assistant response content for saving (with O(1) deduplication)
        assistant_content = ContentBlockAccumulator()

        # Start the stale session cleanup loop if not already running
        self._start_cleanup_loop()

        # Check if we can reuse an existing long-lived client for resume
        reused_client = self._get_active_client(session_id) if is_resuming else None

        try:
            if reused_client and session_id:
                # PATH B: Reuse existing long-lived client
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

            else:
                # PATH A: Create new client (manually managed, not async with)
                # If resuming but no active client exists (server restart, TTL
                # expiry), the long-lived CLI subprocess is gone and --resume
                # cannot work (SDK 0.1.34+ doesn't persist transcripts to disk).
                # Start a fresh session instead.
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

                logger.info(f"Creating new ClaudeSDKClient...")
                wrapper = _ClaudeClientWrapper(options=options)
                client = await wrapper.__aenter__()
                logger.info(f"ClaudeSDKClient created, is_resuming={is_resuming}")

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
                    logger.info(f"Session had error, disconnecting instead of storing")
                    try:
                        await wrapper.__aexit__(None, None, None)
                    except Exception:
                        pass
                elif effective_session_id:
                    self._active_sessions[effective_session_id] = {
                        "client": client,
                        "wrapper": wrapper,
                        "created_at": time.time(),
                        "last_used": time.time(),
                    }
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
            if eff_sid and eff_sid in self._active_sessions:
                await self._cleanup_session(eff_sid)
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
        # --- TSCC telemetry (best-effort, never interrupts SSE stream) ---
        thread_id = session_context.get("sdk_session_id") or agent_id
        telemetry = TelemetryEmitter(thread_id)

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
                    """Async generator for multimodal content."""
                    msg = {
                        "type": "user",
                        "message": {"role": "user", "content": query_content},
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

            async def sdk_message_reader():
                """Read SDK messages and put them in the combined queue.

                Drains the SDK response stream into combined_queue. On error, pushes
                an error sentinel so the main loop can break cleanly. Always pushes
                sdk_done as a termination signal regardless of success or failure.
                """
                try:
                    async for message in client.receive_response():
                        await combined_queue.put({"source": "sdk", "message": message})
                except Exception as e:
                    error_traceback = traceback.format_exc()
                    logger.error(f"SDK message reader error: {e}")
                    logger.error(f"SDK error traceback:\n{error_traceback}")
                    if hasattr(e, 'stderr'):
                        logger.error(f"SDK stderr: {e.stderr}")  # type: ignore[attr-defined]
                    if hasattr(e, 'stdout'):
                        logger.error(f"SDK stdout: {e.stdout}")  # type: ignore[attr-defined]
                    await combined_queue.put({"source": "error", "error": str(e), "detail": error_traceback})
                finally:
                    await combined_queue.put({"source": "sdk_done"})
                    logger.debug("SDK message reader finished")

            async def permission_request_forwarder():
                """Monitor the global permission queue and forward requests for this session.

                Permission requests arrive on a shared global queue from security hooks.
                Only requests matching this session's ID are forwarded; mismatched requests
                are put back so other sessions can claim them. The sleep(0.01) prevents
                busy-looping when repeatedly re-queuing mismatched requests.
                """
                try:
                    while True:
                        request = await _permission_request_queue.get()
                        current_session_id = session_context["sdk_session_id"]
                        if request.get("sessionId") == current_session_id:
                            logger.info(f"Forwarding permission request {request.get('requestId')} to combined queue for session {current_session_id}")
                            await combined_queue.put({"source": "permission", "request": request})
                        else:
                            logger.debug(f"Request {request.get('requestId')} for session {request.get('sessionId')} doesn't match current session {current_session_id}, putting back")
                            await _permission_request_queue.put(request)
                            await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    logger.debug("Permission request forwarder cancelled")
                    raise

            sdk_reader_task = asyncio.create_task(sdk_message_reader())
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

            while True:
                item = await combined_queue.get()

                if item["source"] == "sdk_done":
                    logger.info("SDK iterator finished, exiting message loop")
                    break

                if item["source"] == "permission":
                    request = item["request"]
                    logger.info(f"Emitting permission request: {request.get('requestId')}")
                    yield {"type": "cmd_permission_request", **request}
                    continue

                if item["source"] == "error":
                    logger.error(f"Error from SDK reader: {item['error']}")
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
                            error_text = message.result or "Session failed. This may be a stale session — please start a new conversation."
                            logger.warning(f"SDK error_during_execution: {error_text}")
                            session_context["had_error"] = True
                            # TSCC: mark lifecycle as failed
                            try:
                                sid = session_context.get("sdk_session_id")
                                if sid:
                                    await _tscc_state_manager.set_lifecycle_state(sid, "failed")
                            except Exception:
                                logger.debug("TSCC telemetry: failed lifecycle failed", exc_info=True)
                            # Remove broken session from reuse pool
                            # Use effective_session_id so we find the entry
                            # even after resume-fallback remapping.
                            eff_sid = (
                                session_context["app_session_id"]
                                if session_context.get("app_session_id") is not None
                                else session_context.get("sdk_session_id")
                            )
                            if eff_sid and eff_sid in self._active_sessions:
                                await self._cleanup_session(eff_sid)
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
                                logger.debug("TSCC telemetry: failed lifecycle failed", exc_info=True)
                            yield _build_error_event(
                                code="SDK_ERROR",
                                message=error_msg,
                            )

                        else:
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
                            logger.info(f"Captured SDK session_id from init: {session_context['sdk_session_id']}")

                            # Update telemetry thread_id now that we have the real session ID
                            telemetry = TelemetryEmitter(session_context["sdk_session_id"])
                            try:
                                await _tscc_state_manager.get_or_create_state(
                                    session_context["sdk_session_id"], None, display_text[:50] if display_text else "Chat"
                                )
                                await _tscc_state_manager.set_lifecycle_state(session_context["sdk_session_id"], "active")
                                evt = telemetry.agent_activity("SwarmAgent", "Starting conversation")
                                await _tscc_state_manager.apply_event(session_context["sdk_session_id"], evt)
                                yield evt
                            except Exception:
                                logger.debug("TSCC telemetry: lifecycle init failed", exc_info=True)

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

                    # --- Format and dispatch non-system messages ---
                    # _format_message converts SDK message types (AssistantMessage, ToolUseMessage,
                    # etc.) into SSE-friendly dicts. Returns None for messages that shouldn't
                    # be forwarded to the frontend.
                    formatted = await self._format_message(message, agent_config, session_context["sdk_session_id"])
                    if formatted:
                        logger.debug(f"Formatted message type: {formatted.get('type')}")

                        # --- TSCC telemetry for assistant messages (best-effort) ---
                        try:
                            sid = session_context.get("sdk_session_id")
                            if sid and formatted.get('type') == 'assistant':
                                for block in (formatted.get('content') or []):
                                    if block.get('type') == 'tool_use':
                                        tool_name = block.get('name', 'unknown')
                                        evt = telemetry.tool_invocation(tool_name, f"Using {tool_name}")
                                        await _tscc_state_manager.apply_event(sid, evt)
                                        yield evt
                                        # Detect source file references from tool input
                                        tool_input = block.get('input', {})
                                        file_path = tool_input.get('file_path') or tool_input.get('path') or tool_input.get('filename')
                                        if file_path and isinstance(file_path, str):
                                            src_evt = telemetry.sources_updated(file_path, "Project")
                                            await _tscc_state_manager.apply_event(sid, src_evt)
                                            yield src_evt
                                    elif block.get('type') == 'text' and block.get('text'):
                                        text_preview = block['text'][:80]
                                        evt = telemetry.agent_activity("SwarmAgent", text_preview)
                                        await _tscc_state_manager.apply_event(sid, evt)
                                        yield evt
                        except Exception:
                            logger.debug("TSCC telemetry: assistant event emission failed", exc_info=True)

                        # Accumulate assistant content blocks for later DB persistence.
                        # The accumulator deduplicates by block key (text content, tool_use id,
                        # tool_result tool_use_id) so repeated blocks from streaming don't
                        # produce duplicate entries in the saved message.
                        if formatted.get('type') == 'assistant' and formatted.get('content'):
                            assistant_content.extend(formatted['content'])
                            assistant_model = formatted.get('model')

                        yield formatted

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
                                    # Snapshot when pausing for user input
                                    if _tscc_snapshot_manager:
                                        state = await _tscc_state_manager.get_state(sid)
                                        if state:
                                            _tscc_snapshot_manager.create_snapshot(
                                                sid, state, "Waiting for user input"
                                            )
                            except Exception:
                                logger.debug("TSCC telemetry: paused lifecycle/snapshot failed", exc_info=True)
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

                        # --- TSCC telemetry: conversation complete (best-effort) ---
                        try:
                            sid = session_context.get("sdk_session_id")
                            if sid and not session_context.get("had_error"):
                                await _tscc_state_manager.set_lifecycle_state(sid, "idle")
                                # Create snapshot at conversation completion
                                if _tscc_snapshot_manager:
                                    state = await _tscc_state_manager.get_state(sid)
                                    if state:
                                        _tscc_snapshot_manager.create_snapshot(
                                            sid, state, "Conversation turn completed"
                                        )
                        except Exception:
                            logger.debug("TSCC telemetry: idle lifecycle / snapshot failed", exc_info=True)

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

                        # Terminal SSE event — signals the frontend that the turn is complete
                        # and carries usage metrics for display.
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
                        }
        finally:
            # Cleanup: cancel both background tasks regardless of how we exited the loop
            # (normal completion, error, or early return). The await-after-cancel pattern
            # ensures the tasks have fully stopped before we proceed.
            if sdk_reader_task and not sdk_reader_task.done():
                sdk_reader_task.cancel()
                try:
                    await sdk_reader_task
                except asyncio.CancelledError:
                    pass
                logger.debug("SDK reader task cancelled")

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

                    # Regular tool use block
                    content_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })
                elif isinstance(block, ToolResultBlock):
                    block_content = str(block.content) if block.content else None

                    # Note: Permission request handling is now done via the queue mechanism
                    # and emitted directly in the message loop before formatting.
                    # This ToolResultBlock will just contain the normal tool output.

                    content_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": block_content,
                        "is_error": getattr(block, 'is_error', False)
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

        # Delegate to shared session execution pattern
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
            yield event

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
        """Disconnect all active clients and long-lived sessions."""
        # Clean up long-lived sessions
        for session_id in list(self._active_sessions.keys()):
            await self._cleanup_session(session_id)
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

    async def interrupt_session(self, session_id: str) -> dict:
        """Interrupt a running session.

        Args:
            session_id: The session ID to interrupt

        Returns:
            Dict with status information
        """
        client = self._clients.get(session_id)
        if not client:
            logger.warning(f"No active client found for session {session_id}")
            return {
                "success": False,
                "message": f"No active session found with ID {session_id}",
            }

        try:
            logger.info(f"Interrupting session {session_id}")
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

        # Defer session_start and store_session for resumed sessions until
        # after the SDK client path is determined (same pattern as
        # run_conversation). This prevents duplicate session_start events
        # when the backend restarts and falls back to a fresh SDK session.

        # Configure Claude environment variables
        # TODO(task-9.2): Replace with self._config once AppConfigManager is
        # wired into AgentManager constructor.
        from core.app_config_manager import AppConfigManager as _ACM
        _cfg = _ACM()
        _cfg.load()
        _configure_claude_environment(_cfg)

        # Track the actual SDK session_id
        session_context = {"sdk_session_id": session_id}  # Will be updated for new sessions
        # Track app_session_id for resume-fallback (same pattern as _execute_on_session)
        session_context["app_session_id"] = session_id if is_resuming else None
        assistant_content = ContentBlockAccumulator()

        # Try to reuse existing long-lived client for resume
        reused_client = self._get_active_client(session_id) if is_resuming else None

        try:
            if reused_client and session_id:
                # Reuse existing client
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
                await self._cleanup_session(eff_sid)
            yield _build_error_event(
                code="SKILL_CREATION_ERROR",
                message=str(e),
                detail=error_traceback,
            )


# Global instance
agent_manager = AgentManager()
