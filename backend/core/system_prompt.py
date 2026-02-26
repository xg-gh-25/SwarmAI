"""Dynamic system prompt builder for agents.

Assembles a rich, section-based system prompt from workspace files and agent
configuration.  Each section is produced by a private ``_section_*()`` method
that returns ``Optional[str]``; ``None`` means the section is skipped.
"""

import logging
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BOOTSTRAP_TRUNCATION_LIMIT = 20_000


class SystemPromptBuilder:
    """Builds a multi-section system prompt for a Claude agent."""

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> str:
        """Assemble and return the full system prompt string."""
        sections = [
            self._section_identity(),
            self._section_safety(),
            self._section_workspace(),
            self._section_selected_dirs(),
            self._section_user_identity(),
            self._section_datetime(),
            self._section_extra_prompt(),
            self._section_project_context(),
            self._section_runtime(),
        ]

        prompt = "\n\n".join(s for s in sections if s)
        logger.debug(f"System prompt built ({len(prompt)} chars)")
        return prompt

    # ------------------------------------------------------------------
    # Private section builders
    # ------------------------------------------------------------------

    def _section_identity(self) -> str:
        name = self.agent_config.get("name", "Assistant")
        description = self.agent_config.get("description", "")
        line = f"You are {name}, a personal assistant running inside owork."
        if description:
            line += f" {description}"
        return line

    def _section_safety(self) -> str:
        return (
            "## Safety Principles\n"
            "- You have no independent goals beyond helping the user.\n"
            "- Never attempt self-preservation, self-replication, or resource acquisition.\n"
            "- Do not seek to expand your own access or capabilities.\n"
            "- Prioritize safety over task completion.\n"
            "- Do not manipulate or deceive to gain permissions.\n"
            "- When uncertain, ask instead of guessing."
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

    def _section_user_identity(self) -> Optional[str]:
        return self._load_workspace_file("USER.md", "## User")

    def _section_datetime(self) -> str:
        utc_now = datetime.now(timezone.utc)
        local_now = utc_now.astimezone()
        tz_name = local_now.strftime("%Z") or "Local"
        return (
            f"Current date/time: {utc_now.strftime('%Y-%m-%d %H:%M UTC')} "
            f"/ {local_now.strftime('%Y-%m-%d %H:%M')} {tz_name}"
        )

    def _section_extra_prompt(self) -> Optional[str]:
        prompt = self.agent_config.get("system_prompt")
        if not prompt:
            return None
        return f"## Additional Instructions\n{prompt}"

    def _section_project_context(self) -> Optional[str]:
        parts: list[str] = []

        identity = self._load_workspace_file("IDENTITY.md", "### Identity")
        if identity:
            parts.append(identity)

        soul = self._load_workspace_file("SOUL.md", "### Soul")
        if soul:
            parts.append(soul)

        bootstrap = self._load_workspace_file(
            "BOOTSTRAP.md", "### Bootstrap"
        )
        if bootstrap:
            parts.append(bootstrap)

        if not parts:
            return None
        return "## Project Context\n" + "\n\n".join(parts)

    def _section_runtime(self) -> str:
        name = self.agent_config.get("name", "Assistant")
        model = self.agent_config.get("model", "default")
        os_name = platform.system()
        arch = platform.machine()
        channel = (
            self.channel_context.get("channel_type", "direct")
            if self.channel_context
            else "direct"
        )
        return (
            f"`agent={name} | model={model} | os={os_name} ({arch}) "
            f"| channel={channel}`"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _load_workspace_file(
        self, filename: str, header: str
    ) -> Optional[str]:
        """Read a file from the working directory and wrap with a header.

        Returns ``None`` if the file does not exist or is empty.
        For ``BOOTSTRAP.md``, the content is truncated to
        ``BOOTSTRAP_TRUNCATION_LIMIT`` characters.
        """
        path = Path(self.working_directory) / filename
        try:
            if not path.is_file():
                return None
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                return None

            if filename == "BOOTSTRAP.md" and len(content) > BOOTSTRAP_TRUNCATION_LIMIT:
                content = content[:BOOTSTRAP_TRUNCATION_LIMIT] + "\n\n[... truncated ...]"

            return f"{header}\n{content}"
        except Exception as e:
            logger.warning(f"Could not read {path}: {e}")
            return None
