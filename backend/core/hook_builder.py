"""Hook configuration builder for Claude Agent SDK sessions.

Composes security hooks, permission hooks, skill access checkers, and
lifecycle hooks into the ``hooks`` dict for ``ClaudeAgentOptions``.

Key public symbols:

- ``HookRegistry``   — Register hooks by event, chain execution with
                       5s timeout per hook, first 'block' wins.
- ``build_hooks``    — Async entry point, returns (hooks_dict,
                       effective_allowed_skills, allow_all_skills)
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Awaitable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .permission_manager import PermissionManager

from claude_agent_sdk import HookMatcher

from .security_hooks import (
    pre_tool_logger,
    create_dangerous_command_gate,
    create_skill_access_checker,
)
from .agent_defaults import expand_allowed_skills_with_plugins

logger = logging.getLogger(__name__)

# Per-hook timeout — hooks that exceed this are killed silently.
HOOK_TIMEOUT_SECONDS = 5.0

# Type alias for SDK hook callback
HookCallback = Callable[..., Awaitable[dict[str, Any]]]


class HookRegistry:
    """Register SDK hooks by event type with chained execution.

    Multiple hooks for the same event execute sequentially in registration
    order.  If any hook returns ``{"decision": "block"}``, the chain
    short-circuits.  Each hook has a 5-second timeout — hanging hooks are
    killed silently.

    Hooks with a ``matcher`` parameter (e.g., ``matcher="Bash"``) are
    registered as separate HookMatcher entries, not chained with
    unmatched hooks for the same event.
    """

    def __init__(self) -> None:
        # Key: (event, matcher|None) -> list of (name, hook_fn)
        self._hooks: dict[tuple[str, Optional[str]], list[tuple[str, HookCallback]]] = defaultdict(list)

    def register(
        self,
        event: str,
        hook: HookCallback,
        name: str,
        matcher: Optional[str] = None,
    ) -> None:
        """Register a hook for an SDK event.

        Args:
            event: SDK hook event name (e.g., "PreToolUse", "PostToolUse")
            hook: Async callable with signature (input_data, tool_use_id, context) -> dict
            name: Human-readable name for logging
            matcher: Optional tool matcher (e.g., "Bash", "Skill")
        """
        self._hooks[(event, matcher)].append((name, hook))

    def build_sdk_hooks(self) -> dict[str, list]:
        """Build the hooks dict for ClaudeAgentOptions.

        Returns:
            Dict mapping event names to lists of HookMatcher objects.
        """
        result: dict[str, list] = defaultdict(list)

        for (event, matcher), hooks in self._hooks.items():
            if len(hooks) == 1:
                name, fn = hooks[0]
                hm = HookMatcher(hooks=[fn])
                if matcher:
                    hm = HookMatcher(matcher=matcher, hooks=[fn])
                result[event].append(hm)
            else:
                # Chain multiple hooks into a single callable
                chained = self._build_chain(hooks)
                hm = HookMatcher(hooks=[chained])
                if matcher:
                    hm = HookMatcher(matcher=matcher, hooks=[chained])
                result[event].append(hm)

        return dict(result)

    def _build_chain(
        self, hooks: list[tuple[str, HookCallback]]
    ) -> HookCallback:
        """Build a chained hook from multiple hooks.

        Execution: sequential, first 'block' wins, 5s timeout per hook.
        Results are merged: last non-empty additionalContext wins.
        """

        async def chained(input_data: Any, tool_use_id: Any, context: Any) -> dict:
            combined: dict[str, Any] = {}

            for name, hook in hooks:
                try:
                    result = await asyncio.wait_for(
                        hook(input_data, tool_use_id, context),
                        timeout=HOOK_TIMEOUT_SECONDS,
                    )
                    if not result:
                        continue

                    # Short-circuit on block decision
                    if result.get("decision") == "block":
                        return result

                    # Merge hookSpecificOutput — later hooks override earlier
                    if "hookSpecificOutput" in result:
                        combined.setdefault("hookSpecificOutput", {})
                        combined["hookSpecificOutput"].update(result["hookSpecificOutput"])
                    # Merge other valid top-level keys
                    for key in ("decision", "reason", "systemMessage"):
                        if key in result and result[key] is not None:
                            combined[key] = result[key]

                except asyncio.TimeoutError:
                    logger.warning(
                        "Hook '%s' timed out after %.1fs — skipping",
                        name, HOOK_TIMEOUT_SECONDS,
                    )
                except Exception:
                    logger.exception("Hook '%s' raised an error — skipping", name)

            return combined

        return chained


async def build_hooks(
    agent_config: dict,
    enable_skills: bool,
    enable_mcp: bool,
    resume_session_id: Optional[str],
    session_context: Optional[dict],
    permission_manager: "PermissionManager",
) -> tuple[dict, list[str], bool]:
    """Build hook matchers for ClaudeAgentOptions.

    Uses HookRegistry to compose all hooks, then returns the SDK-compatible
    hooks dict along with skill access info.

    Args:
        agent_config: Agent configuration dictionary.
        enable_skills: Whether skills are enabled.
        enable_mcp: Whether MCP servers are enabled.
        resume_session_id: Optional session ID for resumed sessions.
        session_context: Optional session context dict for hook tracking.
        permission_manager: The PermissionManager instance.

    Returns:
        Tuple of (hooks_dict, effective_allowed_skills, allow_all_skills).
    """
    registry = HookRegistry()

    # ── PreToolUse: tool logger ──────────────────────────────
    if agent_config.get("enable_tool_logging", True):
        registry.register("PreToolUse", pre_tool_logger, "pre_tool_logger")

    # ── PreToolUse: dangerous command gate (Bash-scoped) ─────
    agent_id = agent_config.get("id", "default")
    session_key = resume_session_id or agent_id or "unknown"
    enable_human_approval = agent_config.get("enable_human_approval", True)

    hook_session_context = (
        session_context if session_context is not None
        else {"sdk_session_id": resume_session_id or agent_id}
    )
    gate = create_dangerous_command_gate(
        hook_session_context, session_key, permission_manager,
        enable_human_approval=enable_human_approval,
    )
    registry.register("PreToolUse", gate, "dangerous_command_gate", matcher="Bash")
    logger.info(f"Dangerous command gate attached for session_key: {session_key}")

    # ── Skill access control ─────────────────────────────────
    allowed_skills = agent_config.get("allowed_skills", [])
    allow_all_skills = agent_config.get("allow_all_skills", False)
    plugin_ids = agent_config.get("plugin_ids", [])
    global_user_mode = agent_config.get("global_user_mode", True)

    if global_user_mode:
        allow_all_skills = True
        allowed_skills = []
        plugin_ids = []
        logger.info("Global User Mode: forcing allow_all_skills=True, ignoring allowed_skills")

    effective_allowed_skills = await expand_allowed_skills_with_plugins(
        allowed_skills, plugin_ids, allow_all_skills
    )

    allowed_skill_names = list(effective_allowed_skills)
    logger.info(
        f"Agent skill access: allow_all={allow_all_skills}, "
        f"{len(effective_allowed_skills)} skills "
        f"({len(allowed_skills)} explicit + {len(plugin_ids)} plugins)"
    )

    if enable_skills and not allow_all_skills:
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
        registry.register("PreToolUse", skill_checker, "skill_access_checker", matcher="Skill")
        logger.info(
            f"Skill access checker hook added for skills: "
            f"{allowed_skill_names} (built-in: {builtin_names})"
        )

    # ── PreCompact: flag session_context ─────────────────────
    if session_context is not None:
        async def _pre_compact_hook(hook_input, tool_name, hook_context):
            trigger = getattr(hook_input, "trigger", "auto")
            logger.info(
                f"PreCompact hook fired — trigger={trigger}, "
                f"session={session_context.get('sdk_session_id')}"
            )
            session_context["_compacted"] = True
            return {}

        registry.register("PreCompact", _pre_compact_hook, "pre_compact_flag")

    # ── Notification: capture rate limits and errors ──────────
    if session_context is not None:
        async def _notification_hook(hook_input, tool_name, hook_context):
            message = hook_input.get("message", "") if isinstance(hook_input, dict) else getattr(hook_input, "message", "")
            notif_type = hook_input.get("notification_type", "") if isinstance(hook_input, dict) else getattr(hook_input, "notification_type", "")
            logger.info(
                "notification_hook: type=%s message=%s session=%s",
                notif_type, message[:120],
                session_context.get("sdk_session_id"),
            )
            session_context["_last_notification"] = {
                "type": notif_type,
                "message": message,
            }
            return {}

        registry.register("Notification", _notification_hook, "notification_capture")

    # ── Stop: capture session stop reason ────────────────────
    if session_context is not None:
        async def _stop_hook(hook_input, tool_name, hook_context):
            stop_active = hook_input.get("stop_hook_active", False) if isinstance(hook_input, dict) else getattr(hook_input, "stop_hook_active", False)
            logger.info(
                "stop_hook: stop_hook_active=%s session=%s",
                stop_active,
                session_context.get("sdk_session_id"),
            )
            session_context["_stop_info"] = {
                "stop_hook_active": stop_active,
            }
            return {}

        registry.register("Stop", _stop_hook, "stop_capture")

    # ── Runtime hooks (correction capture, error detection) ──
    if session_context is not None:
        try:
            from core.runtime_hooks import register_runtime_hooks
            register_runtime_hooks(registry, session_context)
        except ImportError:
            logger.debug("runtime_hooks not available — skipping")
        except Exception:
            logger.exception("Failed to register runtime hooks — skipping")

    # ── Code Intelligence: inject dependency context on Read/Grep ──
    if agent_config.get("code_intel_enabled", True):
        try:
            from core.code_intel.code_intel_hook import create_code_intel_hook
            ci_hook = create_code_intel_hook()

            async def _code_intel_wrapper(input_data, tool_use_id, hook_context):
                data = input_data if isinstance(input_data, dict) else getattr(input_data, "__dict__", {})
                tool_name = data.get("tool_name", "")
                tool_input = data.get("tool_input", {})
                return ci_hook(tool_name, tool_input)

            registry.register("PreToolUse", _code_intel_wrapper, "code_intel_context")
            logger.info("Code intelligence hook registered")
        except ImportError:
            logger.debug("code_intel not available — skipping")
        except Exception:
            logger.exception("Failed to register code_intel hook — skipping")

    return registry.build_sdk_hooks(), effective_allowed_skills, allow_all_skills
