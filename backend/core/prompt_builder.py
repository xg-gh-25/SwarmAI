"""System prompt assembly and SDK option construction.

Isolates prompt-building, model
resolution, MCP configuration, sandbox setup, and context-warning logic
into a single IO-at-boundaries module.

This module reads context files via ``ContextDirectoryLoader`` and
configuration via ``AppConfigManager`` but performs **no** subprocess
operations, network calls, or lock management.  It is fully testable
with filesystem fixtures or mocked loaders.

Key public symbols:

- ``PromptBuilder``              — Main class; accepts ``AppConfigManager``
- ``build_system_prompt()``      — Assemble system prompt from context + runtime
- ``build_options()``            — Orchestrate helpers → ``ClaudeAgentOptions``
- ``resolve_model()``            — Model ID with Bedrock conversion
- ``resolve_allowed_tools()``    — Allowed tool list from agent config
- ``build_mcp_config()``         — MCP server dict + disallowed tools
- ``merge_user_local_mcp_servers()`` — Merge user-local MCP servers (deprecated, no-op)
- ``inject_channel_mcp()``       — Channel-specific MCP injection
- ``build_sandbox_config()``     — Sandbox settings from config.json
- ``compute_watchdog_timeout()`` — Dynamic timeout from session metrics
- ``build_context_warning()``    — Context window warning event
- ``get_model_context_window()`` — Context window size for a model
- ``sum_usage_input_tokens()``   — Sum all input token fields (static)

No subprocess lifecycle, routing, or hook logic lives here.
"""

import logging
import os
import platform
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .app_config_manager import AppConfigManager

logger = logging.getLogger(__name__)


# ── DailyActivity token cap constants ──────────────────────────────
# Applied ephemerally at prompt-assembly time; disk files are never modified.
TOKEN_CAP_PER_DAILY_FILE = 2000
TRUNCATION_MARKER = "[Truncated: kept newest ~2000 tokens]"


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


class PromptBuilder:
    """System prompt and SDK option construction.

    IO-at-boundaries: reads context files and config via ContextDirectoryLoader
    and AppConfigManager.  Does NOT spawn subprocesses, make network calls,
    or hold locks.  Testable with filesystem fixtures or mocked loaders.
    No subprocess lifecycle, routing, or hook logic.
    """

    # ── Watchdog timeout parameters ────────────────────────────────
    WATCHDOG_BASE_TIMEOUT: int = 180
    WATCHDOG_SECONDS_PER_100K_TOKENS: int = 30
    WATCHDOG_SECONDS_PER_TURN: int = 5
    WATCHDOG_MAX_TIMEOUT: int = 600

    # ── Model context window sizes (tokens) for L0/L1 selection ───
    # Claude 4.6: 1M context GA on Bedrock (no beta header needed)
    _MODEL_CONTEXT_WINDOWS: dict[str, int] = {
        "claude-opus-4-6": 1_000_000,
        "claude-sonnet-4-6": 1_000_000,
    }
    _DEFAULT_CONTEXT_WINDOW: int = 1_000_000

    # ── Context warning thresholds (percentage of context window) ──
    _CONTEXT_WARN_PCT: int = 70
    _CONTEXT_CRITICAL_PCT: int = 85

    def __init__(self, config: "AppConfigManager") -> None:
        self._config = config

    # ------------------------------------------------------------------
    # resolve_model
    # ------------------------------------------------------------------

    # 4.6 models that get 1M context — the CLI uses [1m] suffix as a signal.
    _1M_MODELS = {"claude-opus-4-6", "claude-sonnet-4-6"}

    def resolve_model(self, agent_config: dict) -> Optional[str]:
        """Resolve the model identifier from config.json (single source of truth).

        When Bedrock is enabled, translates to a Bedrock inference profile ID.
        For 4.6 models, appends ``[1m]`` so the CLI uses the full 1M context
        window.  The CLI strips this suffix before calling the API.

        Returns:
            The resolved model string, or ``None`` if not configured.
        """
        from config import get_bedrock_model_id

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

        # Append [1m] for 4.6 models so the CLI uses 1M context window.
        # The CLI strips [1m] before sending to the API — Bedrock never sees it.
        if model and not model.endswith("[1m]"):
            base = model.replace("us.anthropic.", "").rstrip(":0")
            if base.endswith("-v1"):
                base = base[:-3]
            if base in self._1M_MODELS:
                model = model + "[1m]"

        return model

    # ------------------------------------------------------------------
    # resolve_allowed_tools
    # ------------------------------------------------------------------

    def resolve_allowed_tools(self, agent_config: dict) -> list[str]:
        """Resolve the list of allowed tool names from agent configuration.

        Uses ``allowed_tools`` from config directly when present.  Otherwise
        falls back to the individual enable flags (``enable_bash_tool``,
        ``enable_file_tools``, ``enable_web_tools``) for backwards compat.

        Args:
            agent_config: Agent configuration dictionary.

        Returns:
            List of allowed tool name strings.
        """
        allowed_tools = list(agent_config.get("allowed_tools", []))

        if not allowed_tools:
            if agent_config.get("enable_bash_tool", True):
                allowed_tools.append("Bash")

            if agent_config.get("enable_file_tools", True):
                for tool_name in ["Read", "Write", "Edit", "Glob", "Grep"]:
                    allowed_tools.append(tool_name)

            if agent_config.get("enable_web_tools", True):
                for tool_name in ["WebFetch", "WebSearch"]:
                    allowed_tools.append(tool_name)

        return allowed_tools

    # ------------------------------------------------------------------
    # build_mcp_config
    # ------------------------------------------------------------------

    def build_mcp_config(
        self,
        working_directory: str,
        enable_mcp: bool,
        lazy: bool = False,
    ) -> tuple[dict, list[str]]:
        """Build MCP server configuration from file-based layers.

        Delegates to ``mcp_config_loader.load_mcp_config()`` which reads
        ``.claude/mcps/mcp-catalog.json`` and ``.claude/mcps/mcp-dev.json``.

        When ``lazy=True``, only ``builder-mcp`` is included in the initial
        config. Other MCPs (outlook, slack, sentral, taskei) are loaded
        on-demand via MCP hot-swap (Phase 4).

        Args:
            working_directory: Workspace root path.
            enable_mcp: Whether MCP servers are enabled.
            lazy: If True, only include builder-mcp (Phase 4 optimization).

        Returns:
            Tuple of ``(mcp_servers, disallowed_tools)`` in the format
            expected by ``ClaudeAgentOptions``.
        """
        from .mcp_config_loader import load_mcp_config
        mcp_servers, disallowed_tools = load_mcp_config(Path(working_directory), enable_mcp)

        if lazy and mcp_servers:
            # Keep only builder-mcp for initial spawn
            filtered = {
                name: config for name, config in mcp_servers.items()
                if "builder" in name.lower()
            }
            if filtered:
                logger.info(
                    "Lazy MCP: loading %d/%d servers (builder-mcp only)",
                    len(filtered), len(mcp_servers),
                )
                return filtered, disallowed_tools

        return mcp_servers, disallowed_tools

    # ------------------------------------------------------------------
    # merge_user_local_mcp_servers
    # ------------------------------------------------------------------

    def merge_user_local_mcp_servers(
        self,
        mcp_servers: dict,
        disallowed_tools: list[str],
        used_names: set,
    ) -> None:
        """Load user-local MCP servers.  DEPRECATED — kept for backward compat.

        This method is a no-op.  User-local MCP servers are now managed
        entirely through the two-layer file system in ``.claude/mcps/``.
        """
        pass

    # ------------------------------------------------------------------
    # inject_channel_mcp
    # ------------------------------------------------------------------

    def inject_channel_mcp(
        self,
        mcp_servers: dict,
        channel_context: Optional[dict],
        working_directory: str,
    ) -> dict:
        """Inject channel-specific MCP servers.  Delegates to mcp_config_loader.

        When ``channel_context`` is provided, a ``channel-tools`` MCP server
        entry is added so the agent can interact with the originating channel.

        Args:
            mcp_servers: Current MCP server configuration dict.
            channel_context: Optional channel context for channel-based execution.
            working_directory: Workspace root path.

        Returns:
            The (possibly updated) mcp_servers dict.
        """
        from .mcp_config_loader import inject_channel_mcp as _inject
        return _inject(mcp_servers, channel_context, working_directory)

    # ------------------------------------------------------------------
    # build_sandbox_config
    # ------------------------------------------------------------------

    def build_sandbox_config(self, agent_config: dict) -> Optional[dict]:
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

        excluded_commands: list[str] = []
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
            },
        }
        logger.info(f"Sandbox enabled: {sandbox_settings}")
        return sandbox_settings

    # ------------------------------------------------------------------
    # compute_watchdog_timeout
    # ------------------------------------------------------------------

    def compute_watchdog_timeout(
        self,
        session_id: Optional[str] = None,
        input_tokens: int = 0,
        user_turns: int = 0,
    ) -> int:
        """Compute a dynamic watchdog timeout based on session complexity.

        Scales the base timeout by:
        - Cached/input tokens: +30s per 100K tokens (heavy sessions need more time)
        - User turns: +5s per turn (accumulated context grows with conversation)

        Capped at ``WATCHDOG_MAX_TIMEOUT`` to prevent infinite waits.
        Returns ``WATCHDOG_BASE_TIMEOUT`` when no session data is available.

        Accepts metrics as explicit parameters (IO-at-boundaries) rather
        than reading from internal state dicts.

        Args:
            session_id: Optional session ID (used only for logging).
            input_tokens: Last known input token count for the session.
            user_turns: Number of user turns in the session.

        Returns:
            Watchdog timeout in seconds, clamped to [base, max].
        """
        timeout = self.WATCHDOG_BASE_TIMEOUT

        # Scale by input token count
        if input_tokens > 0:
            hundreds_of_k = input_tokens / 100_000
            timeout += int(hundreds_of_k * self.WATCHDOG_SECONDS_PER_100K_TOKENS)

        # Scale by conversation depth (user turns)
        if user_turns > 0:
            timeout += user_turns * self.WATCHDOG_SECONDS_PER_TURN

        clamped = min(timeout, self.WATCHDOG_MAX_TIMEOUT)
        if clamped != self.WATCHDOG_BASE_TIMEOUT:
            logger.debug(
                "Dynamic watchdog: %ds (base=%d, tokens=%d, turns=%d) for session %s",
                clamped,
                self.WATCHDOG_BASE_TIMEOUT,
                input_tokens,
                user_turns,
                session_id[:8] if session_id else "?",
            )
        return clamped

    # ------------------------------------------------------------------
    # build_context_warning
    # ------------------------------------------------------------------

    @classmethod
    def build_context_warning(
        cls,
        input_tokens: Optional[int],
        model: Optional[str],
        *,
        is_resumed_first: bool = False,
    ) -> Optional[dict]:
        """Build a context_warning SSE event dict from SDK usage data.

        Returns ``None`` if *input_tokens* is invalid (``None``, 0, negative).
        Uses named threshold constants ``_CONTEXT_WARN_PCT`` and
        ``_CONTEXT_CRITICAL_PCT`` for level classification.

        This is a classmethod because it only uses class-level constants
        (thresholds, model window sizes) — no instance state needed.

        Args:
            input_tokens: Total input tokens from the last SDK response.
            model: Model identifier (used to look up context window size).
            is_resumed_first: True when this is the first response after
                resuming a previous session (e.g. app restart).  Adjusts
                the message to explain that context is accumulated from
                the prior conversation, avoiding user confusion.

        Returns:
            A dict with keys ``type``, ``level``, ``pct``, ``tokensEst``,
            ``message`` — or ``None`` if below thresholds or invalid input.
        """
        if input_tokens is None or input_tokens <= 0:
            return None
        window = cls.get_model_context_window(model)
        pct = round((input_tokens / window) * 100) if window > 0 else 0
        level = (
            "critical" if pct >= cls._CONTEXT_CRITICAL_PCT
            else "warn" if pct >= cls._CONTEXT_WARN_PCT
            else "ok"
        )
        tokens_k = input_tokens // 1000
        window_k = window // 1000

        # On resume, explain the context is from a prior conversation
        prefix = "Resumed session — " if is_resumed_first else ""

        if pct >= cls._CONTEXT_CRITICAL_PCT:
            msg = (
                f"{prefix}context is {pct}% full "
                f"(~{tokens_k}K/{window_k}K tokens). "
                f"Start a new tab for best results."
            )
        elif pct >= cls._CONTEXT_WARN_PCT:
            msg = (
                f"{prefix}context is at {pct}% "
                f"(~{tokens_k}K/{window_k}K tokens). "
                f"Consider a new tab if more heavy tasks remain."
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

    # ------------------------------------------------------------------
    # get_model_context_window
    # ------------------------------------------------------------------

    @classmethod
    def get_model_context_window(cls, model: Optional[str]) -> int:
        """Return the context window size for a model ID.

        Strips Bedrock prefix/suffix for lookup.  Defaults to 200K.
        Claude 4.6 models return 1M (GA on Bedrock since 2026-03).

        This is a classmethod because it only uses class-level model
        window mappings — no instance state needed.

        Args:
            model: Model identifier string (may include Bedrock prefix).

        Returns:
            Context window size in tokens.
        """
        if not model:
            return cls._DEFAULT_CONTEXT_WINDOW
        base = model.replace("us.anthropic.", "").rstrip(":0")
        if base.endswith("-v1"):
            base = base[:-3]
        return cls._MODEL_CONTEXT_WINDOWS.get(base, cls._DEFAULT_CONTEXT_WINDOW)

    # ------------------------------------------------------------------
    # sum_usage_input_tokens (static)
    # ------------------------------------------------------------------

    @staticmethod
    def sum_usage_input_tokens(usage: dict) -> int:
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

    # ------------------------------------------------------------------
    # build_system_prompt
    # ------------------------------------------------------------------

    async def build_system_prompt(
        self,
        agent_config: dict,
        working_directory: str,
        channel_context: Optional[dict] = None,
        editor_context: Optional[dict] = None,
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

        Args:
            agent_config: Agent configuration dictionary (mutated in place
                to store ``system_prompt`` and ``_system_prompt_metadata``).
            working_directory: Workspace root path.
            channel_context: Optional channel context for group-channel
                exclusion of personal files.

        Returns:
            The complete system prompt string.
        """
        from .context_directory_loader import (
            ContextDirectoryLoader,
            CONTEXT_FILES,
            GROUP_CHANNEL_EXCLUDE,
            CHANNEL_LIGHT_EXCLUDE,
            DEFAULT_TOKEN_BUDGET,
        )
        from .system_prompt import SystemPromptBuilder

        # ── 1. Centralized context directory (global context) ──────────
        # Reset system_prompt to avoid duplication when _build_options is
        # called twice with the same agent_config (resume-fallback path).
        agent_config["system_prompt"] = ""
        prompt_metadata: dict = {"files": [], "total_tokens": 0, "full_text": ""}
        context_text = ""
        try:
            context_dir = Path(working_directory) / ".context"
            # Reserve headroom for ephemeral injections (DailyActivity, Bootstrap,
            # resume context) that are appended after the token-budgeted assembly.
            # For 1M models, headroom is negligible vs budget — keep it small.
            # The resume context budget (up to 200K for 1M models) is separate
            # from context files and doesn't need to be subtracted here.
            RESUME_CONTEXT_HEADROOM = 2000  # small headroom, actual budget enforced in build_resume_context
            EPHEMERAL_HEADROOM = 2 * TOKEN_CAP_PER_DAILY_FILE + RESUME_CONTEXT_HEADROOM
            base_budget = agent_config.get("context_token_budget", DEFAULT_TOKEN_BUDGET)
            loader = ContextDirectoryLoader(
                context_dir=context_dir,
                token_budget=max(base_budget - EPHEMERAL_HEADROOM, base_budget // 2),
                templates_dir=Path(__file__).resolve().parent.parent / "context",
            )
            loader.ensure_directory()

            model = self.resolve_model(agent_config)
            model_context_window = self.get_model_context_window(model)

            # Session-type-aware context exclusion (L3):
            # - Group channels: exclude personal files (MEMORY, USER)
            # - Channel DMs: exclude heavy low-value files (EVOLUTION, PROJECTS)
            # - Chat tabs: full context (no exclusion)
            exclude_files: set[str] | None = None
            if channel_context and channel_context.get("is_group"):
                exclude_files = set(GROUP_CHANNEL_EXCLUDE)
                logger.info("Group channel detected — excluding %s from context", exclude_files)
            elif channel_context:
                # Channel DM (Slack/Feishu personal) — lightweight context
                exclude_files = set(CHANNEL_LIGHT_EXCLUDE)
                logger.info("Channel DM detected — light context, excluding %s", exclude_files)

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

            # ── Session-type: channel sessions skip heavy ephemeral context ──
            is_channel = channel_context is not None

            # ── DailyActivity reading — last 2 files by date (ephemeral) ──
            # Skipped for channel sessions: Slack/Feishu DMs are quick exchanges
            # that don't need yesterday's session logs (~4K tokens saved).
            daily_activity_dir = Path(working_directory) / "Knowledge" / "DailyActivity"
            if daily_activity_dir.is_dir() and not is_channel:
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
            # Skipped for channel sessions: briefing is for session planning,
            # not quick chat exchanges (~2K tokens saved).
            if not is_channel:
                try:
                    from .proactive_intelligence import build_session_briefing
                    briefing = build_session_briefing(working_directory)
                    if briefing:
                        context_text += f"\n\n{briefing}"
                except Exception as exc:
                    logger.warning("Proactive intelligence injection failed: %s", exc)

            # ── L3: Active Session Digest (sibling awareness) ──────────
            # Inject a brief summary of what other active sessions are doing
            # so Tabs know about Channel activity and vice versa.
            # Lightweight: just last user message per sibling, ~50 tokens each.
            try:
                digest = await self._build_active_session_digest(
                    current_session_id=agent_config.get("resume_app_session_id") or "",
                )
                if digest:
                    context_text += f"\n\n{digest}"
            except Exception as exc:
                logger.debug("Active session digest failed (non-fatal): %s", exc)

            # ── Resume context injection (ephemeral, for resumed sessions) ──
            if agent_config.get("needs_context_injection") and agent_config.get("resume_app_session_id"):
                from .context_injector import build_resume_context
                resume_ctx = await build_resume_context(
                    agent_config["resume_app_session_id"],
                    model_context_window=model_context_window,
                )
                if resume_ctx:
                    context_text += f"\n\n{resume_ctx}"
                    logger.info(
                        "Resume context injected: ~%d tokens",
                        ContextDirectoryLoader.estimate_tokens(resume_ctx),
                    )
                else:
                    logger.info("Resume context skipped: no injectable messages")

            # ── Editor context injection (ephemeral, per-request) ──
            if editor_context:
                file_path = editor_context.get("file_path", "")
                file_name = editor_context.get("file_name", "")
                if file_path:
                    context_text += (
                        f"\n\n## Currently Open File\n"
                        f"The user has `{file_name}` open in the editor "
                        f"(`{file_path}`). Consider this file as relevant "
                        f"context when responding."
                    )

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
        context_text_final = agent_config.get("system_prompt", "") or ""
        if context_text_final:
            return f"{builder_text}\n\n{context_text_final}"
        return builder_text

    # ------------------------------------------------------------------
    # _build_thinking_config
    # ------------------------------------------------------------------

    def _build_thinking_config(self) -> dict | None:
        """Build thinking configuration from app config.

        Reads ``thinking_mode`` from config.json:

        - ``"adaptive"`` (default) — let the model decide when to think
        - ``"enabled"``  — always think, with optional ``thinking_budget_tokens``
        - ``"disabled"`` — never use extended thinking

        Returns a ThinkingConfig dict or None (which lets the SDK decide).
        """
        if not self._config:
            return {"type": "adaptive"}

        mode = self._config.get("thinking_mode", "adaptive")

        if mode == "disabled":
            return {"type": "disabled"}
        elif mode == "enabled":
            budget = self._config.get("thinking_budget_tokens", 10000)
            return {"type": "enabled", "budget_tokens": int(budget)}
        else:
            # Default: adaptive — model decides when thinking is useful
            return {"type": "adaptive"}

    # ------------------------------------------------------------------
    # L3: Active Session Digest
    # ------------------------------------------------------------------

    async def _build_active_session_digest(
        self, current_session_id: str,
    ) -> str:
        """Build a lightweight digest of what sibling sessions are doing.

        Returns a markdown section like:
            ## Active Sessions (sibling context)
            - [Tab, 5m ago] Deploy the new feature to staging
            - [Channel, 2m ago] 帮我查下昨天的 meeting notes

        Only includes alive sessions (STREAMING, IDLE, WAITING_INPUT).
        Costs ~50 tokens per sibling — negligible.
        """
        from . import session_registry
        from database import db
        import time

        router = getattr(session_registry, "session_router", None)
        if not router:
            return ""

        lines: list[str] = []
        now = time.time()

        for unit in router.list_units():
            if unit.session_id == current_session_id:
                continue
            if not unit.is_alive:
                continue

            # Time since last activity
            elapsed_s = now - unit.last_used
            if elapsed_s < 60:
                time_ago = f"{int(elapsed_s)}s ago"
            elif elapsed_s < 3600:
                time_ago = f"{int(elapsed_s / 60)}m ago"
            else:
                time_ago = f"{int(elapsed_s / 3600)}h ago"

            source = "Channel" if unit.is_channel_session else "Tab"

            # Get last user message from DB (cheap — indexed query)
            try:
                last_msg = await db.messages.get_last_by_session(
                    unit.session_id, role="user",
                )
                text = (last_msg.get("content", "") if isinstance(last_msg, dict) else "")
                if isinstance(text, list):
                    # content is a list of blocks — extract text
                    text = " ".join(
                        b.get("text", "") for b in text
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                text = (text or "(no message)")[:100]
            except Exception:
                text = "(unavailable)"

            lines.append(f"- [{source}, {time_ago}] {text}")

        if not lines:
            return ""

        return "## Active Sessions (sibling context)\n" + "\n".join(lines)

    # ------------------------------------------------------------------
    # build_options
    # ------------------------------------------------------------------

    async def build_options(
        self,
        agent_config: dict,
        enable_skills: bool,
        enable_mcp: bool,
        resume_session_id: Optional[str] = None,
        session_context: Optional[dict] = None,
        channel_context: Optional[dict] = None,
        editor_context: Optional[dict] = None,
    ) -> "ClaudeAgentOptions":
        """Orchestrate helper methods to assemble ClaudeAgentOptions.

        Delegates each concern to a focused helper and assembles the final
        options object from their results.  Contains no inline business logic
        — only orchestration and final assembly.

        Args:
            agent_config: Agent configuration dictionary.
            enable_skills: Whether to enable skills.
            enable_mcp: Whether to enable MCP servers.
            resume_session_id: Optional session ID to resume.
            session_context: Optional session context dict for hook tracking.
            channel_context: Optional channel context for channel-based execution.
            editor_context: Optional editor context with file_path/file_name of the open file.

        Returns:
            A fully assembled ``ClaudeAgentOptions`` instance.
        """
        from claude_agent_sdk import ClaudeAgentOptions
        from .security_hooks import create_file_access_permission_handler
        from .hook_builder import build_hooks
        from .permission_manager import permission_manager as _pm
        from .initialization_manager import initialization_manager

        logger.debug(f"agent_config:{agent_config}")

        # 1. Resolve allowed tools
        allowed_tools = self.resolve_allowed_tools(agent_config)

        # 1a. For non-owner channel sessions, determine the sender-scoped
        # sandbox directory.  File tools stay available so the agent can
        # work with files created during this session, but access is
        # restricted to ONLY that directory via file_access_handler (step 3).
        _channel_sender_dir: str | None = None
        if channel_context:
            sender = channel_context.get("sender_identity", {})
            tier = sender.get("permission_tier", "public")
            if tier != "owner":
                sender_id = sender.get("external_id", "anonymous")
                _channel_sender_dir = str(
                    Path(initialization_manager.get_cached_workspace_path())
                    / "channel_files"
                    / sender_id
                )
                # Ensure the directory exists so the agent can use it
                Path(_channel_sender_dir).mkdir(parents=True, exist_ok=True)
                logger.info(
                    "Channel permission tier '%s': file access scoped to %s",
                    tier, _channel_sender_dir,
                )

        # 2. Build hooks
        hooks, effective_allowed_skills, allow_all_skills = await build_hooks(
            agent_config, enable_skills, enable_mcp,
            resume_session_id, session_context,
            _pm,
        )

        # 3. Resolve working directory and file access
        working_directory = initialization_manager.get_cached_workspace_path()

        # setting_sources tells Claude SDK where to discover skills/config.
        # "project" means: look in {cwd}/.claude/ subdirectory for skills.
        setting_sources = ["project"]
        global_user_mode = agent_config.get("global_user_mode", True)

        if _channel_sender_dir:
            # Non-owner channel session: restrict file access to the
            # sender-scoped directory ONLY.  The agent can read/write files
            # created during this session but CANNOT access the owner's
            # workspace, personal files, or any other path.
            # This is the STRUCTURAL enforcement — even if the agent tries
            # to read /Users/gawan/.swarm-ai/SwarmWS/MEMORY.md, the hook
            # returns "deny" before the tool executes.
            file_access_handler = create_file_access_permission_handler(
                [_channel_sender_dir]
            )
            logger.info(
                "Non-owner channel: file_access_handler scoped to [%s]",
                _channel_sender_dir,
            )
        elif global_user_mode:
            file_access_handler = None
        else:
            allowed_directories = [working_directory]
            extra_dirs = agent_config.get("allowed_directories", [])
            if extra_dirs:
                allowed_directories.extend(extra_dirs)
            file_access_handler = create_file_access_permission_handler(allowed_directories)

        # 4. Build MCP server configuration (file-based, no DB)
        mcp_servers, mcp_disallowed_tools = self.build_mcp_config(working_directory, enable_mcp)

        # 5. Build sandbox configuration
        sandbox_settings = self.build_sandbox_config(agent_config)

        # 6. Inject channel-specific MCP servers
        mcp_servers = self.inject_channel_mcp(mcp_servers, channel_context, working_directory)

        # 6a. Non-owner channel sessions: MCP access depends on permission tier.
        #   - TRUSTED: keep ALL enabled MCPs (skills + MCPs = full agent capability)
        #   - PUBLIC:  strip to channel-tools only (no access to owner's integrations)
        if _channel_sender_dir and mcp_servers and channel_context:
            sender = channel_context.get("sender_identity", {})
            tier = sender.get("permission_tier", "public")
            if tier == "public":
                safe_mcps = {
                    name: config for name, config in mcp_servers.items()
                    if name == "channel-tools"
                }
                stripped = len(mcp_servers) - len(safe_mcps)
                if stripped:
                    logger.info(
                        "Public channel user: stripped %d MCP servers "
                        "(kept only channel-tools)",
                        stripped,
                    )
                mcp_servers = safe_mcps
            else:
                logger.info(
                    "Trusted channel user: keeping all %d MCP servers",
                    len(mcp_servers),
                )

        # 7. Resolve model (with Bedrock conversion if needed)
        model = self.resolve_model(agent_config)

        # 8. Build system prompt (reads context files — stays per-session)
        system_prompt_config = await self.build_system_prompt(
            agent_config, working_directory, channel_context, editor_context,
        )

        # Assemble final options
        permission_mode = agent_config.get("permission_mode", "bypassPermissions")
        max_buffer_size = int(os.environ.get("MAX_BUFFER_SIZE", 10 * 1024 * 1024))

        # Build add_dirs from sandbox_additional_write_paths config.
        add_dirs: list[str] = []
        raw_write_paths = self._config.get("sandbox_additional_write_paths", "") if self._config else ""
        if raw_write_paths:
            add_dirs = [
                p.strip() for p in raw_write_paths.split(",")
                if p.strip()
            ]

        # Build extra CLI args for features not yet in ClaudeAgentOptions.
        extra_args: dict[str, str | None] = {}

        # Build thinking configuration from app config.
        # Supports: "adaptive" (default), "enabled" (with budget), "disabled"
        thinking_config = self._build_thinking_config()

        return ClaudeAgentOptions(
            system_prompt=system_prompt_config,
            allowed_tools=allowed_tools if allowed_tools else None,
            disallowed_tools=mcp_disallowed_tools if mcp_disallowed_tools else [],
            mcp_servers=mcp_servers if mcp_servers else None,
            plugins=None,
            permission_mode=permission_mode,
            model=model,
            stderr=lambda msg: logger.error(msg),
            cwd=_channel_sender_dir or working_directory,
            setting_sources=setting_sources,
            hooks=hooks if hooks else None,
            resume=resume_session_id,
            sandbox=sandbox_settings,
            can_use_tool=file_access_handler,
            max_buffer_size=max_buffer_size,
            add_dirs=add_dirs if add_dirs else None,
            extra_args=extra_args,
            include_partial_messages=True,
            enable_file_checkpointing=True,
            thinking=thinking_config,
        )
