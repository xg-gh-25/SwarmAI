"""Hook configuration builder for Claude Agent SDK sessions.

Composes security hooks, permission hooks, skill access checkers, and
pre-compact hooks into the ``hooks`` dict for ``ClaudeAgentOptions``.

Key public symbols:

- ``build_hooks``  — Async entry point, returns (hooks_dict,
                     effective_allowed_skills, allow_all_skills)
"""

import logging
from typing import Optional, TYPE_CHECKING

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


async def build_hooks(
    agent_config: dict,
    enable_skills: bool,
    enable_mcp: bool,
    resume_session_id: Optional[str],
    session_context: Optional[dict],
    permission_manager: "PermissionManager",
) -> tuple[dict, list[str], bool]:
    """Build hook matchers for ClaudeAgentOptions.

    Composes security hooks with the PermissionManager singleton to
    produce the hooks configuration.

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
    hooks: dict = {}

    if agent_config.get("enable_tool_logging", True):
        hooks["PreToolUse"] = [
            HookMatcher(hooks=[pre_tool_logger])
        ]

    # Dangerous command gate — always attached, no sandbox conditional skip.
    # The gate prompts inline when enable_human_approval=True (default),
    # auto-denies when False.
    agent_id = agent_config.get("id", "default")
    session_key = resume_session_id or agent_id or "unknown"
    enable_human_approval = agent_config.get("enable_human_approval", True)

    if "PreToolUse" not in hooks:
        hooks["PreToolUse"] = []
    hook_session_context = (
        session_context if session_context is not None
        else {"sdk_session_id": resume_session_id or agent_id}
    )
    gate = create_dangerous_command_gate(
        hook_session_context, session_key, permission_manager,
        enable_human_approval=enable_human_approval,
    )
    hooks["PreToolUse"].append(
        HookMatcher(matcher="Bash", hooks=[gate])
    )
    logger.info(f"Dangerous command gate attached for session_key: {session_key}")

    # Skill access control
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
        if "PreToolUse" not in hooks:
            hooks["PreToolUse"] = []

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
        logger.info(
            f"Skill access checker hook added for skills: "
            f"{allowed_skill_names} (built-in: {builtin_names})"
        )

    # PreCompact hook — sets flags on session_context so
    # SessionUnit._stream_response can emit an SSE event after compaction.
    if session_context is not None:
        async def _pre_compact_hook(hook_input, tool_name, hook_context):
            trigger = getattr(hook_input, "trigger", "auto")
            logger.info(
                f"PreCompact hook fired — trigger={trigger}, "
                f"session={session_context.get('sdk_session_id')}"
            )
            session_context["_compacted"] = True
            session_context["_compact_trigger"] = trigger
            return {}

        hooks.setdefault("PreCompact", [])
        hooks["PreCompact"].append(HookMatcher(hooks=[_pre_compact_hook]))

    return hooks, effective_allowed_skills, allow_all_skills
