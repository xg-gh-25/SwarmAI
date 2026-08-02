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

import asyncio
import json
import logging
import os
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from core.ddd_paths import ddd_path

if TYPE_CHECKING:
    from .app_config_manager import AppConfigManager

logger = logging.getLogger(__name__)


# ── Sensitive MCP gate (G2) — single source of truth ──────────────────
# MCP servers a NON-OWNER channel sender must never reach (even TRUSTED tier):
# email = acting-as-XG + personal data; sentral = XG revenue/CRM (raw business
# data). STEERING #6 forbids both for allowlisted users. Matched by stable name
# SUBSTRING so a rename/prefix variant (user-aws-outlook-mcp, aws-outlook, …) is
# still caught, and a newly-added sensitive integration is caught by default when
# its name contains one of these — fail-closed. To share a currently-sensitive
# MCP with trusted teammates, that is a deliberate one-line removal HERE.
_SENSITIVE_MCP_SUBSTRINGS = ("aws-outlook", "aws-sentral", "outlook-mcp", "sentral-mcp")


def _is_sensitive_mcp(name: str) -> bool:
    """True if an MCP server name matches the sensitive set (G2, non-owner strip)."""
    low = name.lower()
    return any(sub in low for sub in _SENSITIVE_MCP_SUBSTRINGS)


def strip_sensitive_mcps(mcp_servers: dict) -> dict:
    """Return *mcp_servers* without the sensitive set (G2 non-owner strip).

    THE single source of the non-owner strip — the ``build_options`` TRUSTED
    branch calls this, and the test drives THIS (not a re-derived copy), so a
    regression in the strip logic is caught, not only a regression in the
    predicate (avoids the re-derivation test-theater class, M1).
    """
    return {name: cfg for name, cfg in mcp_servers.items() if not _is_sensitive_mcp(name)}


# ── DailyActivity token cap constants ──────────────────────────────
# Applied ephemerally at prompt-assembly time; disk files are never modified.
TOKEN_CAP_PER_DAILY_FILE = 2000
TRUNCATION_MARKER = "[Truncated: kept newest ~2000 tokens]"


def _truncate_daily_content(content: str, cap: int = TOKEN_CAP_PER_DAILY_FILE) -> str:
    """Truncate DailyActivity content to fit within a token budget.

    Uses word-based truncation, keeping the *tail* (newest entries) since
    DailyActivity files are append-only.  The number of words to keep is
    ``cap / LATIN_TOKENS_PER_WORD`` — the inverse of the calibrated
    token-estimation coefficient used by
    ``ContextDirectoryLoader.estimate_tokens``.  Derived from the SAME
    constant as the forward estimate (Gate-1 finding A, run_3f25a73a): a
    hardcoded inverse (the old ``* 3 / 4``) silently broke when the
    coefficient was recalibrated, leaving the truncated file ~65% over cap.

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
    from .context_directory_loader import (
        ContextDirectoryLoader,
        LATIN_TOKENS_PER_WORD,
    )

    token_count = ContextDirectoryLoader.estimate_tokens(content)
    if token_count <= cap:
        return content
    words = content.split()
    words_to_keep = max(1, int(cap / LATIN_TOKENS_PER_WORD))
    truncated = " ".join(words[-words_to_keep:])
    return f"{TRUNCATION_MARKER}\n\n{truncated}"


# Human-readable labels for the fullscreen nav overlays (swarm:show-* event ids).
# Superset of desktop/src/components/layout/useExclusiveOverlay.ts ALL_SHOW_EVENTS
# by design: any overlay that sets the active-event singleton (incl. Library, which
# uses useExclusiveOverlay('swarm:show-library') even though it's absent from
# ALL_SHOW_EVENTS) can surface here, so the map may carry labels beyond that list.
# Falls back to the raw event id for anything unlabeled.
_OVERLAY_LABELS = {
    "swarm:show-swarmws": "Workspace explorer",
    "swarm:show-brain-hub": "Brain Hub",
    "swarm:show-context": "Context / Memory (C&M)",
    "swarm:show-pipeline": "Pipeline",
    "swarm:show-pollinate": "Pollinate",
    "swarm:show-history": "History",
    "swarm:show-todo": "ToDo",
    "swarm:show-jobs": "Jobs & Runs",
    "swarm:show-library": "Library",
}


def _overlay_label(event_id: str) -> str:
    """Map a swarm:show-* event id to a human label (falls back to the raw id)."""
    return _OVERLAY_LABELS.get(event_id, event_id)


def _render_ui_context_section(editor_context: Optional[dict]) -> str:
    """Render the agent's UI-state proprioception block (SENSE, request-time).

    Superset of the legacy "## Currently Open File" injection. Given the
    request-time UI snapshot (open file + Canvas state + which nav overlay is
    open), produce the system-prompt section that lets the agent perceive what
    it is currently showing the user.

    Backward-compat (AC2): a legacy file-only payload ({file_path, file_name}
    with no canvas/active_overlay) degrades to the EXACT original
    "## Currently Open File" wording — no behavior change for old clients.

    Returns "" when there is nothing to report (no file, no canvas, no overlay).
    """
    if not editor_context:
        return ""

    file_path = editor_context.get("file_path", "") or ""
    file_name = editor_context.get("file_name", "") or ""
    canvas = editor_context.get("canvas") or None
    active_overlay = editor_context.get("active_overlay") or None

    has_file = bool(file_path)
    has_canvas = bool(canvas) and (
        canvas.get("open")
        or canvas.get("output_count")
        or canvas.get("collapsed")
        or canvas.get("pinned")
        or canvas.get("muted")
    )
    has_overlay = bool(active_overlay)

    # Nothing to report.
    if not (has_file or has_canvas or has_overlay):
        return ""

    # Legacy path: file only, no richer UI state → keep the original wording
    # verbatim (backward-compat).
    if has_file and not has_canvas and not has_overlay:
        return (
            f"\n\n## Currently Open File\n"
            f"The user has `{file_name}` open in the editor "
            f"(`{file_path}`). Consider this file as relevant "
            f"context when responding."
        )

    # Superset path: report the full UI state the agent is currently presenting.
    lines = [
        "\n\n## Current UI State",
        "This is what you are currently showing the user in the app "
        "(a request-time snapshot — it may change as they interact):",
    ]
    if has_file:
        lines.append(f"- Open file: `{file_name}` (`{file_path}`)")
    if has_canvas:
        state_bits = []
        state_bits.append("open" if canvas.get("open") else "closed")
        count = int(canvas.get("output_count") or 0)
        state_bits.append(f"{count} output{'s' if count != 1 else ''} listed")
        if canvas.get("pinned"):
            state_bits.append("pinned")
        if canvas.get("muted"):
            state_bits.append("muted")
        if canvas.get("collapsed"):
            state_bits.append("collapsed")
        lines.append(f"- Canvas (output panel): {', '.join(state_bits)}")
    if has_overlay:
        lines.append(f"- Open overlay: {_overlay_label(active_overlay)}")
    return "\n".join(lines)


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
        "claude-opus-4-8": 1_000_000,
        "claude-opus-4-6": 1_000_000,
        "claude-sonnet-4-6": 1_000_000,
    }
    _DEFAULT_CONTEXT_WINDOW: int = 1_000_000

    # ── Context warning thresholds (percentage of context window) ──
    # # assumes a 1M-token window (our default models). These are PERCENTAGES,
    # so they scale correctly across window sizes — but the UX intent ("start a
    # new tab" at 85%) was tuned for 1M. The hard notice (critical@85) is the
    # Root 2 AC2 hard signal: it is informational ONLY — it never auto-kills or
    # auto-discards the session (the user chooses to start a fresh tab).
    _CONTEXT_WARN_PCT: int = 70
    _CONTEXT_CRITICAL_PCT: int = 85

    def __init__(self, config: "AppConfigManager") -> None:
        self._config = config

    # ------------------------------------------------------------------
    # resolve_model
    # ------------------------------------------------------------------

    # Models that get 1M context — the CLI uses [1m] suffix as a signal.
    _1M_MODELS = {"claude-opus-4-8", "claude-opus-4-6", "claude-sonnet-4-6"}

    def resolve_model(self, agent_config: dict) -> Optional[str]:
        """Resolve the model identifier, respecting per-session overrides.

        Priority: agent_config["model"] (per-channel/per-tab override)
                > config.json default_model (global default)

        When Bedrock is enabled, translates to a Bedrock inference profile ID.
        For 4.6 models, appends ``[1m]`` so the CLI uses the full 1M context
        window.  The CLI strips this suffix before calling the API.

        Returns:
            The resolved model string, or ``None`` if not configured.
        """
        from config import get_bedrock_model_id

        # Per-session override (e.g. channel model) takes precedence over
        # the global default.  session_router sets agent_config["model"] from
        # channel_context["model"] before calling build_options().
        model = agent_config.get("model")
        if not model and self._config is not None:
            model = self._config.get("default_model")
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

        # Append [1m] for 1M-capable models so the CLI uses full context window.
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

    # Tool-name groups gated by the legacy enable_* flags. Restriction now
    # expressed as a BLACKLIST (resolve_disallowed_tools) rather than rebuilt
    # into an implicit whitelist (see resolve_allowed_tools docstring).
    _BASH_TOOLS = ("Bash",)
    # NotebookEdit is a file-MUTATING built-in — it MUST be denied when file
    # tools are off, else enable_file_tools=False leaks notebook writes
    # (Gate-2 adversarial finding, run_9cfdb08d). Audit new file/exec built-ins
    # into these groups whenever the SDK adds tools.
    _FILE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit")
    _WEB_TOOLS = ("WebFetch", "WebSearch")

    def resolve_allowed_tools(self, agent_config: dict) -> list[str]:
        """Resolve the EXPLICIT allowed-tool whitelist from agent configuration.

        Returns ``agent_config["allowed_tools"]`` verbatim when set (an opt-in
        whitelist for deliberately-restricted agents), else ``[]``.

        **Blacklist model (run_9cfdb08d, 2026-06-28 — whitelist→blacklist flip):**
        An empty result makes ``build_agent_options`` pass ``allowed_tools=None``
        to the SDK, which means *default-allow all built-ins*. The previous code
        rebuilt an implicit 8-tool whitelist from the ``enable_*`` flags here; that
        whitelist silently DISABLED every unlisted built-in (AskUserQuestion — which
        broke the pipeline's in-band HITL — plus NotebookEdit and any future tool).
        Capability default is now allow; restriction is carried by
        ``resolve_disallowed_tools`` (the ``enable_*=False`` → deny mapping).

        **Scope note (why this is NOT a regression of the "allowlist > denylist"
        DDD decision, 2026-05-10):** that lesson governs SECURITY-ADMISSION gates
        (data redaction, channel file access), which remain fail-closed allowlists
        and are UNTOUCHED — ``file_access_handler`` (channel file sandbox),
        MCP-strip, and ``dangerous_command_gate``/``_is_irreversible_external_op``
        (C041 star-zeroing protection). This change is scoped to *capability
        surface* (which built-in tools an agent may call), where default-allow +
        a small blacklist is the intended model (XG, run_9cfdb08d: agent autonomy,
        refine the blacklist in use). Residual accepted: a NEW built-in Anthropic
        ships is default-allowed until explicitly blacklisted — the symmetric cost
        of default-allow, deemed acceptable for an owner-trusted agent.

        Args:
            agent_config: Agent configuration dictionary.

        Returns:
            Explicit allowed-tool list, or ``[]`` for default-allow.
        """
        return list(agent_config.get("allowed_tools", []))

    def resolve_disallowed_tools(self, agent_config: dict) -> list[str]:
        """Map the legacy ``enable_*`` flags to a tool BLACKLIST.

        Preserves the restriction the implicit whitelist used to enforce, without
        the whitelist's silent-disable-everything-unlisted side effect:

        - ``enable_bash_tool=False``  → deny ``Bash``
        - ``enable_file_tools=False`` → deny ``Read/Write/Edit/Glob/Grep``
        - ``enable_web_tools=False``  → deny ``WebFetch/WebSearch``

        Default (each flag ``True`` or absent) contributes nothing → ``[]``.
        Note: for the DEFAULT/system agent every flag is ``True``
        (``agent_defaults.py`` + ``default-agent.json``), so this returns ``[]``
        and web/bash/file stay allowed — the move only restricts custom DB agents
        that explicitly set a flag ``False`` (where ``enable_web_tools`` schema
        default is ``False``). Merged into ``disallowed_tools`` at build time.
        """
        disallowed: list[str] = []
        if not agent_config.get("enable_bash_tool", True):
            disallowed.extend(self._BASH_TOOLS)
        if not agent_config.get("enable_file_tools", True):
            disallowed.extend(self._FILE_TOOLS)
        if not agent_config.get("enable_web_tools", True):
            disallowed.extend(self._WEB_TOOLS)
        return disallowed

    # ------------------------------------------------------------------
    # build_mcp_config
    # ------------------------------------------------------------------

    def build_mcp_config(
        self,
        working_directory: str,
        enable_mcp: bool,
        channel_context: Optional[dict] = None,
        extra_always: Optional[set[str]] = None,
    ) -> tuple[dict, list[str], list[dict]]:
        """Build MCP server configuration with tier-based lazy loading.

        Delegates to ``mcp_config_loader.load_mcp_config_tiered()`` which
        reads ``.claude/mcps/mcp-catalog.json`` and ``.claude/mcps/mcp-dev.json``
        and filters by tier (always/channel/ondemand).

        Args:
            working_directory: Workspace root path.
            enable_mcp: Whether MCP servers are enabled.
            channel_context: If provided, also loads ``channel`` tier MCPs.
            extra_always: MCP names to force-load regardless of tier
                (from per-session ``_extra_mcps``).

        Returns:
            Tuple of ``(mcp_servers, disallowed_tools, deferred)`` where
            *deferred* is metadata about MCPs not loaded (for system prompt).
        """
        from .mcp_config_loader import load_mcp_config_tiered
        return load_mcp_config_tiered(
            Path(working_directory), enable_mcp,
            channel_context=channel_context,
            extra_always=extra_always,
        )

    @staticmethod
    def format_deferred_mcp_section(deferred: list[dict]) -> str:
        """Format deferred MCPs as a system prompt section.

        Returns an empty string if no MCPs are deferred.
        """
        if not deferred:
            return ""

        lines = [
            "\n## Deferred MCPs (available but not loaded)",
            "These MCP tool servers are installed but NOT started (to save memory).",
            "When you need one of these MCPs, tell the user which MCP you need and why.",
            "The user's UI will show an 'Enable' button to load it. Once loaded,",
            "it will be available for all subsequent messages in this session.\n",
        ]
        for item in deferred:
            desc = item.get("description", "")
            tier = item.get("tier", "ondemand")
            desc_part = f" — {desc}" if desc else ""
            lines.append(f"- **{item['name']}** [{tier}]{desc_part}")

        return "\n".join(lines)

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

    def build_sandbox_config(self) -> dict:
        """Build sandbox configuration from ``config.json`` (sole source of truth).

        **Always returns a dict, never None.**  When sandbox is disabled, returns
        ``{"enabled": False}`` so the CLI receives an explicit disable signal.
        Returning ``None`` would omit the ``--settings`` flag entirely, and newer
        CLI versions auto-enable sandbox under ``bypassPermissions`` mode as a
        safety fallback — defeating the user's intent to disable it.

        Sandbox is an **app-level** setting, not per-agent.  All sandbox config
        lives in ``config.json`` via ``AppConfigManager``.  Per-agent
        ``sandbox_enabled`` DB column is a legacy no-op (kept for migration
        safety, never read).

        Returns:
            Sandbox settings dict (always present; ``enabled`` may be False).
        """
        cfg = self._config
        sandbox_enabled = cfg.get("sandbox_enabled_default", False) if cfg else False

        # Sandbox only works on macOS/Linux, not Windows
        if sandbox_enabled and platform.system() == "Windows":
            logger.warning("Sandbox is not supported on Windows, disabling")
            sandbox_enabled = False

        if not sandbox_enabled:
            return {"enabled": False}

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
        # Strip [1m] suffix appended by resolve_model() for CLI context signal
        if base.endswith("[1m]"):
            base = base[:-4]
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
        terminal_context: Optional[dict] = None,
        context_percent_used: float = 0.0,
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
        # Defaults for variables set inside the try block — ensures
        # resume context injection (which runs outside the try) can
        # use them even if ContextDirectoryLoader fails early.
        model_context_window = 200_000
        is_channel = channel_context is not None
        _t_total_start = time.perf_counter()
        try:
            context_dir = Path(working_directory) / ".context"
            # Reserve headroom for ephemeral injections (DailyActivity, Bootstrap,
            # resume context) that are appended after the token-budgeted assembly.
            # Resume context can now be up to 150K tokens on 1M models, but
            # it's appended to system_prompt which the 1M model accommodates
            # directly.  The headroom here only ensures context files don't
            # over-allocate within their own budget tier.  Keep it moderate —
            # aggressive headroom starves context files unnecessarily.
            RESUME_CONTEXT_HEADROOM = 5000  # moderate headroom, resume budget enforced in build_resume_context
            EPHEMERAL_HEADROOM = 2 * TOKEN_CAP_PER_DAILY_FILE + RESUME_CONTEXT_HEADROOM
            base_budget = agent_config.get("context_token_budget", DEFAULT_TOKEN_BUDGET)
            loader = ContextDirectoryLoader(
                context_dir=context_dir,
                token_budget=max(base_budget - EPHEMERAL_HEADROOM, base_budget // 2),
                templates_dir=Path(__file__).resolve().parent.parent / "context",
            )
            _t0 = time.perf_counter()
            loader.ensure_directory()
            _t_ensure = time.perf_counter() - _t0

            model = self.resolve_model(agent_config)
            model_context_window = self.get_model_context_window(model)

            # Session-type-aware context exclusion (L3):
            # - Group channels: exclude personal files (MEMORY, USER)
            # - Owner DM: full context (same as chat tab — full Brain)
            # - Non-owner channel DMs: exclude heavy low-value files (EVOLUTION, PROJECTS)
            # - Chat tabs: full context (no exclusion)
            exclude_files: set[str] | None = None
            if channel_context and channel_context.get("is_group"):
                exclude_files = set(GROUP_CHANNEL_EXCLUDE)
                logger.info("Group channel detected — excluding %s from context", exclude_files)
            elif channel_context and not channel_context.get("is_owner"):
                # Non-owner channel DM — lightweight context
                exclude_files = set(CHANNEL_LIGHT_EXCLUDE)
                logger.info("Non-owner channel DM — light context, excluding %s", exclude_files)
            # Owner DM and chat tabs: full context (no exclusion)
            #
            # NOTE: EVOLUTION.md loads UNCONDITIONALLY on desktop (like KNOWLEDGE.md).
            # The old O2 "coding-session-only" gate (removed run_6d2cc624) excluded it
            # from non-coding sessions to save ~5K tokens — but that premise is stale:
            # EVOLUTION.md is now the cognitive failure registry (CLASS A/A′/B/C), and
            # those biases surface in ANY session, not just coding. PRI08 (power >
            # token budget) makes the 5K on a 91K/1M budget a worthwhile trade.
            # Channel exclusions below are unchanged (group / non-owner still drop it).

            # Memory injection is always active — auto-selects full injection
            # (< 30K tokens) or selective mode (≥ 30K).  No config flag needed.
            #
            # NO guess-keyword (pure-filesystem recall design §1.3, 2026-06-28):
            # at prompt-assembly time the user's real message does NOT exist yet,
            # so the old `get_focus_keywords()` (briefing-focus titles as a query
            # proxy) was a structural mis-match — selecting MEMORY sections against
            # a GUESS. We pass NO query here; selective mode falls back to its
            # rule-based section loading (recent/pinned), and the REAL query-driven
            # recall happens AFTER the first user message via
            # session_router._maybe_inject_recall (runtime leg, real query).
            memory_keyword_hint = ""

            _t1 = time.perf_counter()
            context_text = loader.load_all(
                model_context_window=model_context_window,
                exclude_filenames=exclude_files,
                memory_smart=True,
                user_message=memory_keyword_hint,
                session_signals={
                    "is_channel": channel_context is not None,
                    "is_resume": bool(agent_config.get("resume_app_session_id")),
                    "is_first_session_today": not (
                        Path(working_directory) / "Knowledge" / "DailyActivity"
                        / f"{datetime.now().strftime('%Y-%m-%d')}.md"
                    ).exists() if not (channel_context) else False,
                },
                # Adaptive memory budget: 0.0 at session init (full budget),
                # non-zero when prompt is rebuilt mid-session (e.g. --resume).
                context_percent_used=context_percent_used,
            )
            _t_load = time.perf_counter() - _t1

            # recall#G ephemeral-budget observability (run_a16d61ad, design §G):
            # the 11 context files are token-budgeted inside load_all(), but the
            # EPHEMERAL sections appended below (DailyActivity, briefing, user
            # suggestions, sibling digest, editor, deferred-MCP) are NOT — they
            # just `+=` onto context_text against a fixed EPHEMERAL_HEADROOM
            # reservation with no combined ceiling. On a 1M model that is fine
            # (the design bets on it), but a silent overshoot means ephemeral
            # content quietly eats the budget the context files were supposed to
            # keep. Capture the budgeted-loader baseline HERE so the
            # injection-complete log below can report ephemeral = total − loader
            # and WARN (not crash, not truncate) when it exceeds the reservation.
            _loader_tok = ContextDirectoryLoader.estimate_tokens(context_text)

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

            # ── DailyActivity reading — most recent file only (ephemeral) ──
            # Only today's file is injected. Yesterday's actionable items
            # are already covered by Proactive Briefing (L0-L4) and Memory
            # Index. The agent can Read older files on demand.
            # Skipped for channel sessions: quick exchanges don't need logs.
            daily_activity_dir = Path(working_directory) / "Knowledge" / "DailyActivity"
            if daily_activity_dir.is_dir() and not is_channel:
                da_files = sorted(
                    [f for f in daily_activity_dir.glob("*.md") if f.stem[:4].isdigit()],
                    key=lambda f: f.stem,
                    reverse=True,
                )[:2]  # last 2 days (pure-filesystem recall design §3.1, DoD3)
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
            _t_briefing_start = time.perf_counter()
            if not is_channel:
                try:
                    from .proactive_intelligence import build_session_briefing
                    # build_session_briefing is now filesystem-ONLY (run_05b42b8b,
                    # SwarmAI TECH.md purity invariant): it reads only MEMORY.md +
                    # DailyActivity to produce the Suggested-focus section. The DB /
                    # eval / glob-all-pipelines legs that made it a loop-starvation
                    # risk (99s peak, the RP53 concern this OFFLOAD once mitigated)
                    # are DELETED, not offloaded. The to_thread wrap is kept as cheap
                    # insurance (fs reads are still I/O), but the function no longer
                    # opens a sqlite connection on the assembly path.
                    briefing = await asyncio.to_thread(build_session_briefing, working_directory)
                    if briefing:
                        context_text += f"\n\n{briefing}"
                except Exception as exc:
                    logger.warning("Proactive intelligence injection failed: %s", exc)
            _t_briefing = time.perf_counter() - _t_briefing_start

            # ── UserObserver Suggestions ──
            # Inject pending USER.md update suggestions if the file exists
            # and has content. Written by UserObserverHook, consumed here.
            if not is_channel:
                try:
                    suggestions_path = Path(working_directory) / ".context" / "user_suggestions.md"
                    if suggestions_path.exists():
                        suggestions_text = suggestions_path.read_text(encoding="utf-8").strip()
                        if suggestions_text and len(suggestions_text) < 2048:
                            context_text += f"\n\n## Pending User Profile Suggestions\n{suggestions_text}"
                except Exception as exc:
                    logger.debug("User suggestions injection skipped: %s", exc)

            # ── Skill Registry injection removed (2026-04-14) ──
            # The Claude Agent SDK injects a comprehensive skill list via
            # system-reminder (with triggers, descriptions, DO NOT USE).
            # The compact registry was a duplicate index (~200 tokens).
            # SkillGuard security scanning is independent (PreToolUse hook).

            # ── Layer 6: Recalled Knowledge — moved to session_router.py ──
            # Pre-session recall with proactive keywords replaced by
            # post-first-message recall (G3) that uses the user's actual
            # query.  See _maybe_inject_recall() in session_router.py.

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

            # NOTE: Resume context injection moved OUTSIDE this try block
            # (after the except) so it runs even when ContextDirectoryLoader
            # fails.  See "Resume context injection" section below.

            # ── UI-state context injection (ephemeral, per-request) ──
            # Agent proprioception (SENSE): the request-time snapshot of the
            # agent's own UI — open file + Canvas state + which nav overlay is
            # open. Superset of the legacy "## Currently Open File" (which a
            # file-only payload still degrades to). See _render_ui_context_section.
            context_text += _render_ui_context_section(editor_context)

            # ── Terminal context injection (P2 — observable terminal) ──
            # When the user explicitly attaches a terminal's output (a human
            # action in the terminal panel), the session gets a READ-ONLY view
            # of that terminal's recent output + cwd, so "why did this build
            # fail?" works without copy-paste. Single direction: terminal →
            # session. The session never writes to the terminal (P3 deferred).
            if terminal_context:
                buffer_tail = terminal_context.get("buffer_tail", "")
                term_cwd = terminal_context.get("cwd", "")
                if buffer_tail:
                    cwd_note = f" (cwd `{term_cwd}`)" if term_cwd else ""
                    context_text += (
                        f"\n\n## Attached Terminal Output{cwd_note}\n"
                        f"The user attached recent output from an integrated "
                        f"terminal. Treat it as relevant context (e.g. a build "
                        f"log or command result) when responding:\n\n"
                        f"```\n{buffer_tail}\n```"
                    )

            # ── Deferred MCP list (Lazy MCP Loading) ──
            _deferred = agent_config.get("_deferred_mcps")
            if _deferred:
                deferred_section = self.format_deferred_mcp_section(_deferred)
                if deferred_section:
                    context_text += f"\n\n{deferred_section}"

            if context_text:
                existing = agent_config.get("system_prompt", "") or ""
                agent_config["system_prompt"] = (
                    existing + "\n\n" + context_text if existing else context_text
                )
                _t_total = time.perf_counter() - _t_total_start
                _est_tokens = ContextDirectoryLoader.estimate_tokens(context_text)
                # recall#G: ephemeral = everything appended after the budgeted
                # loader baseline (briefing/suggestions/digest/editor/MCP; resume
                # is injected later, separately, and is budget-enforced in
                # build_resume_context). _loader_tok is set right after load_all();
                # guard against the rare path where it wasn't (defensive).
                _ephemeral_tok = max(_est_tokens - locals().get("_loader_tok", 0), 0)
                logger.info(
                    "Injected centralized context: %d chars, ~%d tokens "
                    "(loader=%d, ephemeral=%d / headroom=%d), "
                    "timing: total=%.2fs (ensure=%.3fs, load=%.3fs, briefing=%.3fs)",
                    len(context_text), _est_tokens,
                    locals().get("_loader_tok", 0), _ephemeral_tok, EPHEMERAL_HEADROOM,
                    _t_total, _t_ensure, _t_load, _t_briefing,
                )
                # WARN (never truncate, never crash): the ephemeral sections are
                # cognition/continuity content — we do NOT clip them mid-assembly
                # (that would silently drop a briefing health-warning or a user
                # suggestion). We surface the overshoot so it is VISIBLE instead of
                # silently eating the context-file budget. The 1M model absorbs it;
                # the warning is the signal to revisit what ephemeral content costs.
                #
                # NOTE (conservative threshold): EPHEMERAL_HEADROOM also reserves
                # RESUME_CONTEXT_HEADROOM (5K), but resume context is appended LATER
                # (into system_prompt, not context_text) and is NOT in _ephemeral_tok
                # here. So the threshold is intentionally LOOSER than what's measured
                # — this UNDER-warns (fires only on real briefing/digest blowups),
                # never false-alarms. That bias is deliberate: a spurious warning
                # every session would be worse than missing a marginal overshoot.
                if _ephemeral_tok > EPHEMERAL_HEADROOM:
                    logger.warning(
                        "ephemeral context (%d tok) exceeds reserved headroom "
                        "(%d tok) by %d — briefing/digest/suggestions are eating "
                        "into the context-file budget. Not truncated (cognition "
                        "content); revisit ephemeral section sizes.",
                        _ephemeral_tok, EPHEMERAL_HEADROOM,
                        _ephemeral_tok - EPHEMERAL_HEADROOM,
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

        # ── Resume context injection (independent of ContextDirectoryLoader) ──
        # CRITICAL: This MUST run outside the ContextDirectoryLoader try block.
        # Previously it was inside, and any upstream exception (e.g. zlib
        # decompression errors) would jump to the except, silently skipping
        # resume context injection — causing total loss of prior conversation
        # on "resume" after retry exhaustion (COE: 2026-04-02).
        try:
            if agent_config.get("needs_context_injection") and agent_config.get("resume_app_session_id"):
                from .context_injector import build_resume_context
                resume_ctx = await build_resume_context(
                    agent_config["resume_app_session_id"],
                    model_context_window=model_context_window,
                    is_channel=is_channel,
                    working_directory=working_directory,
                )
                if resume_ctx:
                    # Inject into system_prompt directly — context_text may
                    # have been lost if ContextDirectoryLoader failed.
                    existing = agent_config.get("system_prompt", "") or ""
                    agent_config["system_prompt"] = (
                        existing + f"\n\n{resume_ctx}" if existing else resume_ctx
                    )
                    logger.info(
                        "Resume context injected: ~%d tokens (independent path)",
                        len(resume_ctx) // 4,  # rough estimate, no loader dependency
                    )
                else:
                    logger.info("Resume context skipped: no injectable messages")
            else:
                logger.debug(
                    "Resume context not requested: needs_injection=%s, resume_session=%s",
                    agent_config.get("needs_context_injection"),
                    bool(agent_config.get("resume_app_session_id")),
                )
        except Exception as exc:
            logger.error(
                "Resume context injection FAILED — user will lose prior context: %s",
                exc, exc_info=True,
            )

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

    # Valid effort levels accepted by the Claude SDK / CLI.
    _VALID_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})

    def _build_thinking_config(self, channel_context: dict | None = None) -> dict | None:
        """Build thinking configuration from app config.

        Reads ``thinking_mode`` from config.json:

        - ``"adaptive"`` (default) — let the model decide when to think
        - ``"enabled"``  — always think, with optional ``thinking_budget_tokens``
        - ``"disabled"`` — never use extended thinking

        **Session-type awareness:** The global ``thinking_mode`` is the
        *desktop-facing* source of truth. Channel sessions (``channel_context``
        not None) run unattended and are cost/latency sensitive, so they force
        ``adaptive`` regardless of the global value — letting the model skip
        thinking on simple questions. The one exception is a globally
        ``disabled`` mode: that is a hard kill (the operator explicitly turned
        thinking off) and the channel override must NOT resurrect it.

        Returns a ThinkingConfig dict or None (which lets the SDK decide).

        ⚠️ KNOWN LIMITATION — desktop thinking renders EMPTY on Bedrock, and no
        transport fixes it. The bundled ``claude`` CLI gates the thinking
        ``display`` field behind a *provider allow-list* (first-party Anthropic
        API, AWS-platform, Azure foundry) and STRIPS it for Bedrock + Vertex.
        On our Bedrock path, neither a ``display`` key in this dict NOR
        ``extra_args["thinking-display"]`` reaches the request body — the CLI
        drops it before send. Verified empirically (CLI 2.1.183 AND 2.1.185):
        dict-display, extra_args, and control all yield 0 thinking chars, while
        raw boto3 with ``display="summarized"`` yields 187 chars. So this is an
        UPSTREAM CLI BUG, not a Bedrock platform limitation (the backend returns
        the summary when asked directly). Tracking: anthropics/claude-code#63358
        (open). See SwarmAI/IMPROVEMENT.md. Until the CLI adds Bedrock to its
        allow-list, do not bother adding ``display`` here — it is a dead write.
        (Also keeps PIT59 satisfied: no unknown subfield for older SDKs to drop.)
        """
        # Defensive: config is contractually an AppConfigManager (always
        # truthy), so this only fires if a caller passes None. Using `is None`
        # (not falsy) avoids masking a real None as "empty config" and keeps
        # an empty-dict test config falling through to the .get() defaults.
        if self._config is None:
            return {"type": "adaptive"}

        mode = self._config.get("thinking_mode", "adaptive")

        if mode == "disabled":
            return {"type": "disabled"}

        # Channel sessions force adaptive (cost-efficient, model self-decides).
        # disabled was already handled above, so it is never overridden here.
        if channel_context is not None:
            return {"type": "adaptive"}

        if mode == "enabled":
            budget = self._config.get("thinking_budget_tokens", 10000)
            return {"type": "enabled", "budget_tokens": int(budget)}
        else:
            # Default: adaptive — model decides when thinking is useful
            return {"type": "adaptive"}

    def _build_effort(self, channel_context: dict | None = None) -> str | None:
        """Resolve the ``effort`` level for thinking depth.

        Reads ``thinking_effort`` from config.json.  Valid values:
        ``"low"``, ``"medium"``, ``"high"`` (default), ``"xhigh"``, ``"max"``.

        Returns the effort string, or ``None`` if thinking is disabled
        (effort is meaningless without thinking).

        ``channel_context`` is accepted for call-site symmetry with
        :meth:`_build_thinking_config`. Effort stays meaningful for channel
        sessions: they force *adaptive* (not disabled), so thinking can still
        occur and a depth level still applies. Only a globally ``disabled``
        mode yields ``None`` — and that short-circuit fires for desktop and
        channel alike, since disabled is a hard kill the channel cannot undo.
        """
        # Defensive: see _build_thinking_config — `is None`, not falsy, so an
        # empty-dict test config falls through to the .get() default.
        if self._config is None:
            return "high"

        # If thinking is disabled, effort is irrelevant — for ALL session types.
        # (Channel forces adaptive, never disabled, so this only trips when the
        # operator globally disabled thinking.)
        if self._config.get("thinking_mode") == "disabled":
            return None

        effort = self._config.get("thinking_effort", "high")
        if effort not in self._VALID_EFFORT_LEVELS:
            logger.warning(
                "Invalid thinking_effort %r — falling back to 'high'", effort
            )
            return "high"

        # Channel sessions cap at "high" for cost/latency — desktop uses full config.
        if channel_context is not None and effort in ("xhigh", "max"):
            return "high"

        return effort

    def _build_thinking_display(self, channel_context: dict | None = None) -> str | None:
        """Resolve the thinking ``display`` mode → CLI ``--thinking-display`` flag.

        Opus 4.8 silently changed the thinking ``display`` default from
        ``"summarized"`` (Opus 4.6) to ``"omitted"``. Under ``"omitted"`` the
        model still produces thinking blocks but streams them with EMPTY text —
        so the desktop UI shows a "Thinking…" spinner with no reasoning content.
        Setting ``--thinking-display summarized`` is *intended* to restore the
        visible summary.

        ⚠️ KNOWN LIMITATION (ineffective on Bedrock — upstream CLI bug):
        On our Bedrock path this flag is a NO-OP. The bundled ``claude`` CLI
        gates the thinking ``display`` field behind a provider allow-list
        (first-party Anthropic API, AWS-platform, Azure foundry) and STRIPS it
        for Bedrock + Vertex before building the request — so the value we emit
        here never reaches the API. This is NOT a Bedrock platform limitation:
        the Bedrock backend honors ``display="summarized"`` when sent via raw
        boto3 (verified: 187 chars of summary). It is an upstream CLI omission
        (Bedrock left off the allow-list). Tracking: anthropics/claude-code#63358
        (open, no maintainer fix as of 2026-06-21; reproduced on CLI 2.1.183 AND
        the 2.1.185 pre-release). We keep emitting the flag anyway because it is
        free, harmless, and becomes correct the moment the CLI adds Bedrock to
        its allow-list — no code change needed on our side when upstream lands.

        **Transport:** carried via ``extra_args["thinking-display"]`` (rendered
        by the SDK as ``--thinking-display <value>``), NOT via a ``display`` key
        in the thinking config dict — see :meth:`_build_thinking_config`. The
        ``extra_args`` key MUST be dash-free (``"thinking-display"``, not
        ``"--thinking-display"``): the SDK prepends ``--``, so a dashed key
        renders as ``----thinking-display`` and silently no-ops. (Neither
        transport currently works on Bedrock per the limitation above; this note
        documents the intended wiring for when the CLI is fixed.)

        Returns ``"summarized"`` for desktop sessions with thinking active, or
        ``None`` when no flag should be emitted:

        - ``None`` if thinking is globally ``disabled`` — no thinking, no display.
        - ``None`` for channel sessions — zero-streaming human-like delivery
          (the thinking summary is never rendered to Slack), and channels
          already force adaptive + cap effort. Mirrors :meth:`_build_effort`'s
          channel handling.

        ``channel_context`` is accepted for call-site symmetry with
        :meth:`_build_thinking_config` and :meth:`_build_effort`.
        """
        # Defensive: see _build_thinking_config — `is None`, not falsy, so an
        # empty-dict test config falls through to the default.
        if self._config is None:
            return "summarized"

        # No thinking → no display to show.
        if self._config.get("thinking_mode") == "disabled":
            return None

        # Channel sessions skip display: zero-streaming delivery never renders
        # the thinking summary, so emitting the flag would only add cost.
        if channel_context is not None:
            return None

        return "summarized"

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Knowledge Recall (Layer 6) — moved to session_router._recall_for_query()
    # Post-first-message injection via _maybe_inject_recall() in session_router.py
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # L3: Active Session Digest
    # ------------------------------------------------------------------

    async def _build_active_session_digest(
        self, current_session_id: str,
    ) -> str:
        """Build a lightweight digest of what sibling sessions are doing.

        Returns a markdown section like:
            ## Active Sessions (sibling context)
            - [Tab, 5m ago] active
            - [Channel, 2m ago] active

        Only includes alive sessions (STREAMING, IDLE, WAITING_INPUT).

        SYSTEM-PROMPT PURITY (run_05b42b8b, SwarmAI TECH.md invariant): this digest
        is sourced ENTIRELY from the in-memory session registry (unit source +
        last_used) — it does NOT read the database. The prior implementation did a
        per-sibling indexed message lookup to show each tab's last message text;
        that was the 2nd database source on the system-prompt assembly path (the 1st
        was build_session_briefing's Radar-todos). The judgment-relevant signal is
        "a sibling session IS active" (R29 parallel-session coordination), which the
        in-memory registry already carries; the last-message CONTENT was feed data
        that belongs to the dashboard, not the system prompt. HARD invariant: no
        database read on this path.
        """
        from . import session_registry
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

            # Time since last activity (in-memory — no DB)
            elapsed_s = now - unit.last_used
            if elapsed_s < 60:
                time_ago = f"{int(elapsed_s)}s ago"
            elif elapsed_s < 3600:
                time_ago = f"{int(elapsed_s / 60)}m ago"
            else:
                time_ago = f"{int(elapsed_s / 3600)}h ago"

            source = "Channel" if unit.is_channel_session else "Tab"
            lines.append(f"- [{source}, {time_ago}] active")

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
        terminal_context: Optional[dict] = None,
        extra_mcps: Optional[set[str]] = None,
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
            editor_context: Optional request-time UI snapshot (open file +
                Canvas state + active nav overlay); rendered by
                _render_ui_context_section. Wire field kept as `editor_context`
                for backward-compat.
            terminal_context: Optional attached-terminal context (P2) with
                buffer_tail/cwd — a read-only view of an integrated terminal the
                user explicitly attached. Single direction: terminal → session.

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
            # to read ~/.swarm-ai/SwarmWS/MEMORY.md, the hook
            # returns "deny" before the tool executes.
            #
            # L3 SHARED LANE (run_c220f153): additionally grant READ-ONLY access
            # to the DDD docs of projects EXPLICITLY marked shareable, so a
            # teammate can get project-specific help. Fail-closed — only
            # shareable==True projects contribute paths; exact-file + read-only
            # so .artifacts/ pipeline internals stay hidden and docs can't be
            # corrupted (both were adversarial-found leaks in the dir-grant
            # design that this replaces).
            shared_ddd_files = await self._collect_shareable_ddd_paths()
            file_access_handler = create_file_access_permission_handler(
                [_channel_sender_dir],
                readonly_files=shared_ddd_files,
            )
            logger.info(
                "Non-owner channel: file_access scoped to [%s] + %d shared DDD "
                "doc(s) (read-only)",
                _channel_sender_dir, len(shared_ddd_files),
            )
        elif global_user_mode:
            file_access_handler = None
        else:
            allowed_directories = [working_directory]
            extra_dirs = agent_config.get("allowed_directories", [])
            if extra_dirs:
                allowed_directories.extend(extra_dirs)
            file_access_handler = create_file_access_permission_handler(allowed_directories)

        # 4. Build MCP server configuration (file-based, tier-aware lazy loading)
        mcp_servers, mcp_disallowed_tools, deferred_mcps = self.build_mcp_config(
            working_directory, enable_mcp,
            channel_context=channel_context,
            extra_always=extra_mcps,
        )

        # 4a. Store deferred MCPs for system prompt injection
        if deferred_mcps:
            agent_config["_deferred_mcps"] = deferred_mcps

        # 5. Build sandbox configuration
        sandbox_settings = self.build_sandbox_config()

        # 6. Inject channel-specific MCP servers
        mcp_servers = self.inject_channel_mcp(mcp_servers, channel_context, working_directory)

        # 6a. Non-owner channel sessions: MCP access depends on permission tier.
        #   - PUBLIC:  strip to channel-tools only (no access to owner's integrations)
        #   - TRUSTED: keep MCPs EXCEPT the sensitive set (G2) — a trusted teammate
        #     must not reach XG's email (act-as-XG) or revenue/CRM data through the
        #     bot. STEERING #6 forbids external actions as XG + raw personal data
        #     even for allowlisted users; behavioral prose alone is not a gate.
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
                # TRUSTED (and any non-owner, non-public tier): fail-closed strip
                # of the sensitive set via the shared single-source helper (so the
                # test drives THIS code, not a copy — M1).
                safe_mcps = strip_sensitive_mcps(mcp_servers)
                stripped = len(mcp_servers) - len(safe_mcps)
                if stripped:
                    logger.info(
                        "Trusted channel user: stripped %d sensitive MCP server(s) "
                        "(email/revenue), kept %d",
                        stripped, len(safe_mcps),
                    )
                mcp_servers = safe_mcps

        # 7. Resolve model (with Bedrock conversion if needed)
        model = self.resolve_model(agent_config)

        # 8. Build system prompt (reads context files — stays per-session)
        system_prompt_config = await self.build_system_prompt(
            agent_config, working_directory, channel_context, editor_context,
            terminal_context=terminal_context,
        )

        # Assemble final options
        permission_mode = agent_config.get("permission_mode", "bypassPermissions")
        max_buffer_size = int(os.environ.get("MAX_BUFFER_SIZE", 10 * 1024 * 1024))

        # Build add_dirs from sandbox_additional_write_paths config.
        # Expand ~ to actual home directory — the SDK CLI needs absolute paths.
        add_dirs: list[str] = []
        raw_write_paths = self._config.get("sandbox_additional_write_paths", "") if self._config else ""
        if raw_write_paths:
            add_dirs = [
                os.path.expanduser(p.strip()) for p in raw_write_paths.split(",")
                if p.strip()
            ]

        # Build extra CLI args for features not yet in ClaudeAgentOptions.
        extra_args: dict[str, str | None] = {}

        # Build thinking configuration from app config.
        # Supports: "adaptive" (default), "enabled" (with budget), "disabled"
        thinking_config = self._build_thinking_config(channel_context=channel_context)
        effort = self._build_effort(channel_context=channel_context)

        # Thinking display: Opus 4.8 defaults display to "omitted" (empty thinking
        # blocks → "Thinking…" spinner with no content). Restore the summary via the
        # CLI flag. Carried in extra_args (rendered as `--<key> <value>`), NOT in
        # thinking_config — version-robust across SDK builds that drop unknown
        # thinking-dict subfields (PIT59). Key is dash-free; SDK prepends `--`.
        thinking_display = self._build_thinking_display(channel_context=channel_context)
        if thinking_display is not None:
            extra_args["thinking-display"] = thinking_display

        # ── Max turns: per-message API roundtrip limit ─────────────
        #
        # DISCOVERY (2026-06-01): CLI default maxTurns = 100.
        # When SDK consumer passes max_turns=None, CLI uses its internal
        # default of 100 turns. Pipeline runs routinely hit 100+ turns
        # (8 stages × 12-15 turns/stage). At turn 101, CLI emits
        # ResultMessage(is_error=True, subtype="error_max_turns") and
        # exits — causing "Interrupted" in the UI mid-pipeline.
        #
        # FIX: Explicit DESKTOP_MAX_TURNS (500) for desktop, CHANNEL_MAX_TURNS
        # (100) for channels. Both sourced from session_healing so the CLI limit
        # and the HealthSensor turn_approaching/wrap-up thresholds share ONE
        # definition — they MUST stay equal or the heal triggers become
        # unreachable (the original bug). See session_healing.py.
        # Evidence: run_bbe3f167 hit exactly 101 turns at pipeline stage 7/8.
        #
        # Safety: task_budget (800K desktop / 400K channel) is the independent
        # cost cap. User can always press Stop for true runaway scenarios.
        from .session_healing import CHANNEL_MAX_TURNS, DESKTOP_MAX_TURNS

        max_turns = agent_config.get("max_turns") or None
        if channel_context and (max_turns is None or max_turns > CHANNEL_MAX_TURNS):
            # Channel: generous limit for skill execution (was 15, too small).
            # Real safety comes from task_budget=400K, not turn count.
            # 100 covers all skills (max observed: ~60 turns for complex research).
            max_turns = CHANNEL_MAX_TURNS
        elif not channel_context and max_turns is None:
            # Desktop: override CLI default (100) with generous limit.
            # Pipeline full-profile runs need 150-300 turns typically;
            # complex goal-profile or multi-milestone work can exceed 400.
            # Self-healing triggers at max_turns-20 for seamless refresh.
            max_turns = DESKTOP_MAX_TURNS

        # ── Task budget: per-task token limit for CLI autocompact ─────
        #
        # DISCOVERY (2026-06-01): CLI default task_budget = 128K tokens.
        # When a single user→agent interaction chain (including all tool
        # call results) exceeds 128K, CLI triggers autocompact mid-task,
        # destroying accumulated context and causing the agent to "forget"
        # what it just read.
        #
        # For 1M models this is absurdly conservative — deep code
        # investigations routinely hit 200-400K in tool results alone.
        # The 128K default makes complex tasks fail reliably.
        #
        # FIX: Set 800K for desktop chat tabs (user present, can stop).
        # Channel sessions get 400K (unattended but needs to read large
        # docs; max_turns=100 is the independent safety cap for runaway).
        #
        # Evidence: session 59b18ce8 compacted 3 times at ~100-123K
        # tokens while investigating a bug in a 3200-line file.
        # Context window was 1M — compact threshold should be 987K.
        # But task_budget (128K) fired first.
        #
        # Risk: Higher budget = more expensive runaway if agent loops.
        # Mitigated by: max_turns=100 (channel), user stop (desktop),
        # CompactionGuard (tool loop detection).
        if channel_context:
            # Channel: unattended but must handle large docs.
            # max_turns=100 caps total interaction length independently.
            task_budget = {"total": 400_000}
        else:
            # Desktop chat: user is present, generous budget
            task_budget = {"total": 800_000}

        return ClaudeAgentOptions(
            system_prompt=system_prompt_config,
            # Empty list → SDK default-allow (the intended default-agent behavior).
            # MUST pass [] (not None): claude-agent-sdk 0.2.109
            # _apply_skills_defaults() does `list(self._options.allowed_tools)`
            # UNCONDITIONALLY (no None guard) → `list(None)` raises
            # "'NoneType' object is not iterable" at spawn, breaking EVERY session
            # (new + resume). With []: list([])==[] is falsy → the --allowedTools
            # flag is omitted → CLI default-allows all built-ins. Same intent as the
            # old `else None`, minus the crash. NOTE (run_9cfdb08d Gate-2): an
            # EXPLICIT allowed_tools=[] is treated as "unset → allow-all", NOT
            # "deny-all". [] is reserved/unused; lock-down uses disallowed_tools.
            allowed_tools=allowed_tools or [],
            # Disallow Task* tools — we don't use them and their presence
            # triggers periodic "task tools haven't been used" system-reminder
            # noise (~100 tokens × 10+ per session = 1K+ wasted tokens).
            disallowed_tools=[
                *(mcp_disallowed_tools or []),
                # enable_*=False restriction, carried as a blacklist (run_9cfdb08d).
                *self.resolve_disallowed_tools(agent_config),
                "TaskCreate", "TaskGet", "TaskList",
                "TaskOutput", "TaskStop", "TaskUpdate",
            ],
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
            effort=effort,
            max_turns=max_turns,
            task_budget=task_budget,
        )

    # Canonical DDD doc set — single source: project_registry.DDD_CANONICAL_DOCS (Run 0).
    # Only these four docs are ever shared to a non-owner — never CONTEXT.md,
    # never .artifacts/, never archives, NEVER spec-details/ (a derived projection,
    # deliberately excluded from the canonical set → never in the shared lane).
    from core.project_registry import DDD_CANONICAL_DOCS as _SHAREABLE_DDD_DOCS

    async def _collect_shareable_ddd_paths(self) -> list[str]:
        """Collect exact file paths of DDD docs for projects marked shareable.

        L3 SHARED LANE (run_c220f153). Scans ``Projects/*/.project.json`` for
        an explicit ``shareable: true`` flag and returns the exact paths of
        that project's four canonical DDD docs that ACTUALLY EXIST. These are
        passed to the file-access handler as ``readonly_files`` so a non-owner
        teammate can Read (not Write) project-specific domain knowledge.

        FAIL-CLOSED by construction:
          * ``shareable`` absent/false  -> project contributes nothing
          * only the 4 canonical docs   -> .artifacts/ pipeline internals,
                                           CONTEXT.md, archives stay hidden
          * only EXISTING files         -> no phantom paths
          * any error (bad JSON, IO)    -> that project skipped; total failure
                                           returns ``[]`` (deny, never open)

        Returns:
            Sorted list of absolute file paths, read-only-granted. Empty if no
            project is shareable or on any failure.
        """
        from .initialization_manager import initialization_manager

        def _scan() -> list[str]:
            paths: list[str] = []
            try:
                ws = initialization_manager.get_cached_workspace_path()
                projects_dir = Path(ws) / "Projects"
                if not projects_dir.is_dir():
                    return []
                for project_dir in projects_dir.iterdir():
                    meta_file = project_dir / ".project.json"
                    if not (project_dir.is_dir() and meta_file.is_file()):
                        continue
                    try:
                        raw = json.loads(meta_file.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        # Fail-closed: an unreadable project is NOT shared.
                        continue
                    if raw.get("shareable", False) is not True:
                        continue
                    # Containment anchor: the project dir's REAL path. A granted
                    # doc must resolve to a real file DIRECTLY inside it.
                    proj_real = os.path.realpath(project_dir)
                    for doc in self._SHAREABLE_DDD_DOCS:
                        doc_path = ddd_path(project_dir, doc)
                        # SYMLINK ESCAPE GUARD (adversarial M1, run_c220f153):
                        # is_file() FOLLOWS symlinks, and the handler realpath-
                        # resolves the grant — so a symlink `TECH.md ->
                        # ../../MEMORY.md` inside a shareable project would mint a
                        # read grant for the owner's private file. Reject any
                        # symlink component AND verify the resolved path is still
                        # a direct child of the project dir. Fail-closed.
                        if doc_path.is_symlink():
                            continue
                        if not doc_path.is_file():
                            continue
                        doc_real = os.path.realpath(doc_path)
                        # Containment: the resolved doc must live INSIDE the project
                        # tree (fail-closed on symlink/.. escape). Six-section change
                        # (run_3a636c88): a migrated doc resolves to
                        # <proj>/2-understanding/<doc>, so the anchor is "still under
                        # proj_real", NOT "direct child of proj_real" (the old check
                        # rejected every migrated shareable doc). os.path.commonpath
                        # confirms containment without allowing a ../ escape.
                        try:
                            if os.path.commonpath([doc_real, proj_real]) != proj_real:
                                continue  # resolved outside the project dir
                        except ValueError:
                            continue  # different drives / relative — fail-closed
                        paths.append(str(doc_path))
            except Exception as exc:  # noqa: BLE001 - fail-closed on ANY error
                logger.warning(
                    "shareable-DDD scan failed (fail-closed, 0 shared): %s: %s",
                    type(exc).__name__, exc,
                )
                return []
            return sorted(paths)

        return await asyncio.to_thread(_scan)
