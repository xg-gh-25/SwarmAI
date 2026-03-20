"""Dynamic system prompt builder for agents.

Assembles non-file system prompt sections: identity, safety principles,
workspace path, datetime, and runtime metadata.  All file-based context
(SWARMAI.md, SOUL.md, IDENTITY.md, USER.md, etc.) is now loaded by
``ContextDirectoryLoader`` from ``~/.swarm-ai/.context/``.

Each section is produced by a private ``_section_*()`` method that returns
``Optional[str]``; ``None`` means the section is skipped.
"""

import logging
import platform
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class SystemPromptBuilder:
    """Builds non-file system prompt sections for a Claude agent."""

    def __init__(
        self,
        working_directory: str,
        agent_config: dict,
        channel_context: Optional[dict] = None,
        add_dirs: Optional[list[str]] = None,
    ):
        self.working_directory = working_directory
        self.agent_config = agent_config
        self.channel_context = channel_context
        self.add_dirs = add_dirs or []

    # SDK identity line to strip (injected by Claude Agent SDK at runtime)
    _SDK_IDENTITY_LINE = 'You are a Claude agent, built on Anthropic\'s Claude Agent SDK.'

    def build(self) -> str:
        """Assemble and return the full system prompt string.

        Post-processes the assembled prompt to strip any SDK-injected
        identity line, avoiding the need for a counter-instruction that
        wastes tokens on every API call.
        """
        sections = [
            self._section_identity(),
            self._section_safety(),
            self._section_large_content(),
            self._section_workspace(),
            self._section_selected_dirs(),
            self._section_datetime(),
            self._section_runtime(),
        ]

        prompt = "\n\n".join(s for s in sections if s)
        # Strip SDK identity injection if present (saves ~30 tokens vs counter-instruction)
        prompt = prompt.replace(self._SDK_IDENTITY_LINE, "").strip()
        logger.debug(f"System prompt built ({len(prompt)} chars)")
        return prompt

    def _section_identity(self) -> str:
        name = self.agent_config.get("name", "Assistant")
        description = self.agent_config.get("description", "")
        line = f"You are {name}, a personal assistant running inside SwarmAI."
        if description:
            line += f" {description}"
        return line

    def _section_safety(self) -> str:
        # AI-alignment guardrails complementing the operational safety
        # rules in AGENT.md (destructive commands, trash>rm, etc.)
        # and the priority hierarchy in SWARMAI.md.
        return (
            "## Safety Principles\n"
            "- You have no independent goals beyond helping the user.\n"
            "- Never attempt self-preservation, self-replication, or resource acquisition.\n"
            "- Do not seek to expand your own access or capabilities.\n"
            "- Prioritize safety over task completion.\n"
            "- Do not manipulate or deceive to gain permissions.\n"
            "- When uncertain, ask instead of guessing."
        )

    @staticmethod
    def _section_large_content() -> str:
        """Guidance for progressive processing of large MCP tool responses.

        The CLI has a hardcoded 10MB JSONRPC buffer — any single tool
        response exceeding this crashes the subprocess.  This section
        teaches the agent to avoid triggering it.
        """
        return (
            "## Large Content Processing\n\n"
            "SDK limitation: individual tool responses must be <10MB. "
            "When working with files, images, or attachments, use "
            "progressive processing:\n\n"
            "1. **ASSESS** — Get the list/count/metadata first, without "
            "fetching content\n"
            "2. **PROCESS** — Fetch items one at a time; only items under "
            "500KB (plain text, small JSON) may be batched 2-3 at a time\n"
            "3. **EXTRACT** — After each fetch, summarize key findings "
            "as text notes\n"
            "4. **SYNTHESIZE** — After all items processed, combine text "
            "findings into answer\n\n"
            "Applies when:\n"
            "- Fetching >3 attachments, images, or files\n"
            "- Reading files likely >5MB (large codebases, logs, data)\n"
            "- Any MCP tool call that returns file/image content\n\n"
            "For large text files: use offset/limit parameters (500 lines "
            "per chunk).\n"
            "For multiple binaries: fetch strictly one at a time.\n\n"
            "Never skip or truncate items. Process ALL content through "
            "progressive extraction."
        )

    def _section_workspace(self) -> str:
        return f"Your working directory is: `{self.working_directory}`"

    def _section_selected_dirs(self) -> Optional[str]:
        if not self.add_dirs:
            return None
        lines = ["## Selected Working Directories"]
        for d in self.add_dirs:
            lines.append(f"- `{d}`")
        return "\n".join(lines)

    def _section_datetime(self) -> str:
        utc_now = datetime.now(timezone.utc)
        local_now = utc_now.astimezone()
        tz_name = local_now.strftime("%Z") or "Local"
        return (
            f"Current date/time: {utc_now.strftime('%Y-%m-%d %H:%M UTC')} "
            f"/ {local_now.strftime('%Y-%m-%d %H:%M')} {tz_name}"
        )

    def _section_runtime(self) -> str:
        name = self.agent_config.get("name", "Assistant")
        os_name = platform.system()
        arch = platform.machine()
        channel = (
            self.channel_context.get("channel_type", "direct")
            if self.channel_context
            else "direct"
        )
        parts = [f"agent={name}", f"os={os_name} ({arch})", f"channel={channel}"]
        # Model comes from build_agent_config (which reads config.json for default agent).
        # Fallback to resolve_default_model() if agent_config has no model (e.g. stale DB record).
        model = self.agent_config.get("model")
        if not model or model in ("default", "None"):
            from core.agent_defaults import resolve_default_model
            model = resolve_default_model()
        if model and model not in ("default", "None", None):
            parts.insert(1, f"model={model}")
        return "`" + " | ".join(parts) + "`"
