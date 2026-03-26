"""Skill Creator — AI-powered skill generation via SessionRouter.

Builds a specialized agent config (custom system prompt, restricted tool set,
Sonnet model) and delegates to ``SessionRouter.run_conversation()``.  The skill
creator is just a normal conversation with a purpose-built agent config.

Called from ``routers/skills.py`` via ``session_registry.run_skill_creator()``.

Public symbols:

- ``SKILL_CREATOR_SYSTEM_PROMPT_TEMPLATE`` — System prompt template
- ``run_skill_creator()``                  — Async generator yielding SSE events
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .session_router import SessionRouter

logger = logging.getLogger(__name__)

SKILL_CREATOR_SYSTEM_PROMPT_TEMPLATE = """\
You are a Skill Creator Agent specialized in creating Claude Code skills.

Your task is to help users create high-quality skills that extend Claude's capabilities.

IMPORTANT GUIDELINES:
1. Always use the skill-creator skill (invoke /skill-creator) to get guidance
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


async def run_skill_creator(
    router: "SessionRouter",
    skill_name: str,
    skill_description: str,
    user_message: Optional[str] = None,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
):
    """Run a skill creation conversation via SessionRouter.

    Builds a specialized agent config and delegates to
    ``router.run_conversation()``.  Yields SSE event dicts.
    Adds ``skill_name`` to the ``result`` event.

    Args:
        router: The SessionRouter instance to dispatch through.
        skill_name: Name of the skill to create.
        skill_description: What the skill should do.
        user_message: Optional follow-up message for iteration.
        session_id: Optional session ID for continuing conversation.
        model: Optional model override (defaults to Sonnet 4.5).
    """
    # Build the prompt
    if user_message:
        prompt = user_message
    else:
        prompt = (
            f"Please create a new skill with the following specifications:\n\n"
            f"**Skill Name:** {skill_name}\n"
            f"**Skill Description:** {skill_description}\n\n"
            f"Use the skill-creator skill (invoke /skill-creator) to guide your "
            f"skill creation process. Follow the workflow:\n"
            f"1. Understand the skill requirements from the description above\n"
            f"2. Plan reusable contents (scripts, references, assets) if needed\n"
            f"3. Initialize the skill using the init_skill.py script\n"
            f"4. Edit SKILL.md and create any necessary files\n"
            f"5. Test any scripts you create\n\n"
            f"Create the skill in the `.claude/skills/` directory within the "
            f"current workspace."
        )

    system_prompt = SKILL_CREATOR_SYSTEM_PROMPT_TEMPLATE.format(
        skill_name=skill_name,
        skill_description=skill_description,
    )

    agent_config = {
        "name": f"skill-creator-{session_id[:8] if session_id else 'new'}",
        "description": "Temporary agent for skill creation",
        "system_prompt": system_prompt,
        "allowed_tools": [
            "Bash", "Read", "Write", "Edit", "Glob", "Grep",
            "Skill", "TodoWrite", "Task",
        ],
        "permission_mode": "bypassPermissions",
        "working_directory": None,
        "global_user_mode": False,
        "enable_tool_logging": True,
        "enable_safety_checks": True,
        "model": model or "claude-sonnet-4-6",
    }

    logger.info(
        "Skill creator via SessionRouter: name=%s session=%s model=%s",
        skill_name, session_id, agent_config["model"],
    )

    async for event in router.run_conversation(
        agent_id="skill-creator",
        user_message=prompt,
        session_id=session_id,
        enable_skills=True,
        enable_mcp=False,
        agent_config=agent_config,
    ):
        if event.get("type") == "result":
            event["skill_name"] = skill_name
        yield event
