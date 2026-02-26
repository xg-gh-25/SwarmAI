"""Agent workspace management for skill isolation.

This module manages per-agent workspaces with symlinks to allowed skills,
enabling fine-grained control over which skills each agent can access.

IMPORTANT: Agent workspaces are created OUTSIDE the project tree to prevent
Claude's Skill tool from discovering unauthorized skills in parent directories.

Skill source locations (checked in priority order):
    1. skill.local_path from database (exact path from DB record)
    2. ~/.claude/skills/{skill_name}     <- Plugin-installed skills
    3. workspace/.claude/skills/{skill_name}  <- User-created skills

Directory structure:
    ~/.claude/skills/                    <- Plugin-installed skills (from marketplace)
        ├── pptx/
        ├── pdf/
        └── docx/

    /home/ubuntu/.../workspace/          <- Project workspace (user skill storage)
    └── .claude/skills/                  <- User-created skills
        ├── my-skill-1/
        └── my-skill-2/

    <app_data_dir>/workspaces/           <- Isolated agent workspaces (outside project!)
    └── {agent_id}/
        └── .claude/skills/              <- Absolute symlinks to allowed skills only
            ├── pptx -> ~/.claude/skills/pptx
            └── my-skill-1 -> /home/ubuntu/.../workspace/.claude/skills/my-skill-1
"""
import logging
import shutil
from pathlib import Path
from typing import Optional

from config import settings
from database import db

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """Manages per-agent workspaces with skill isolation via symlinks."""

    TEMPLATE_FILES = [
        "AGENTS.md",
        "BOOTSTRAP.md",
        "HEARTBEAT.md",
        "IDENTITY.md",
        "SOUL.md",
        "USER.md",
    ]

    def __init__(self):
        self.main_workspace = Path(settings.agent_workspace_dir)
        self.agents_workspace = Path(settings.agent_workspaces_dir)
        self.main_skills_dir = self.main_workspace / ".claude" / "skills"
        self._templates_dir = Path(__file__).resolve().parent.parent / "templates"

    def _ensure_dirs(self):
        """Ensure required directories exist."""
        self.main_skills_dir.mkdir(parents=True, exist_ok=True)
        self.agents_workspace.mkdir(parents=True, exist_ok=True)

    def _copy_templates(self, target_dir: Path, force: bool = False) -> None:
        """Copy template files into *target_dir*.

        Args:
            target_dir: Destination directory (must already exist).
            force: If ``False``, skip files that already exist in the target.
        """
        if not self._templates_dir.is_dir():
            logger.warning(f"Templates directory not found: {self._templates_dir}")
            return

        for filename in self.TEMPLATE_FILES:
            src = self._templates_dir / filename
            dst = target_dir / filename
            if not src.is_file():
                continue
            if not force and dst.exists():
                continue
            try:
                shutil.copy2(src, dst)
                logger.debug(f"Copied template {filename} -> {dst}")
            except OSError as e:
                logger.warning(f"Failed to copy template {filename}: {e}")

    def ensure_templates_in_directory(self, directory: Path) -> None:
        """Copy template files into *directory* without overwriting existing ones.

        Intended for global-user-mode workspaces (e.g. home directory).
        """
        self._copy_templates(directory, force=False)

    def get_agent_workspace(self, agent_id: str) -> Path:
        """Get the workspace path for a specific agent."""
        return self.agents_workspace / agent_id

    def get_agent_skills_dir(self, agent_id: str) -> Path:
        """Get the skills directory path for a specific agent."""
        return self.get_agent_workspace(agent_id) / ".claude" / "skills"

    async def get_skill_name_by_id(self, skill_id: str) -> Optional[str]:
        """Get skill folder name from skill ID.

        Uses the folder_name field from the new skill schema, with fallback to name.
        """
        skill = await db.skills.get(skill_id)
        if not skill:
            logger.warning(f"Skill not found: {skill_id}")
            return None

        # Use folder_name directly if available (new schema)
        if skill.get("folder_name"):
            return skill["folder_name"]

        # Fallback: sanitize skill name
        import re
        return re.sub(r'[^a-zA-Z0-9_-]', '-', skill.get("name", "").lower())

    async def _get_skill_by_name(self, skill_name: str) -> Optional[dict]:
        """Get skill record by folder name or sanitized name.

        Args:
            skill_name: The folder name to search for

        Returns:
            Skill dict if found, None otherwise
        """
        skills = await db.skills.list()
        for skill in skills:
            # Direct match on folder_name
            if skill.get("folder_name") == skill_name:
                return skill
            # Fallback: check sanitized name
            import re
            sanitized_name = re.sub(r'[^a-zA-Z0-9_-]', '-', skill.get("name", "").lower())
            if sanitized_name == skill_name:
                return skill
        return None

    def _get_skill_source_path(self, skill_name: str, skill_record: Optional[dict] = None) -> Optional[Path]:
        """Find the source path for a skill by checking multiple locations.

        Skills can be located in:
        1. skill.local_path from database (exact path)
        2. ~/.claude/skills/{skill_name} (plugin-installed skills)
        3. workspace/.claude/skills/{skill_name} (user-created skills)

        Args:
            skill_name: The skill folder name
            skill_record: Optional pre-fetched skill record from database

        Returns:
            Path to the skill directory if found, None otherwise
        """
        # Priority 1: Use local_path from database if available
        if skill_record and skill_record.get("local_path"):
            local_path = Path(skill_record["local_path"])
            if local_path.exists():
                logger.debug(f"Found skill at local_path: {local_path}")
                return local_path

        # Priority 2: Check ~/.claude/skills/ (plugin-installed skills)
        home_skills_dir = Path.home() / ".claude" / "skills" / skill_name
        if home_skills_dir.exists():
            logger.debug(f"Found skill at ~/.claude/skills/: {home_skills_dir}")
            return home_skills_dir

        # Priority 3: Check workspace/.claude/skills/ (user-created skills)
        workspace_skills_path = self.main_skills_dir / skill_name
        if workspace_skills_path.exists():
            logger.debug(f"Found skill at workspace: {workspace_skills_path}")
            return workspace_skills_path

        return None

    async def get_all_skill_names(self) -> list[str]:
        """Get all available skill folder names from multiple locations.

        Checks:
        1. ~/.claude/skills/ (plugin-installed skills)
        2. workspace/.claude/skills/ (user-created skills)

        Returns:
            Deduplicated list of skill folder names
        """
        skill_names = set()

        # Check ~/.claude/skills/ (plugin-installed skills)
        home_skills_dir = Path.home() / ".claude" / "skills"
        if home_skills_dir.exists():
            for item in home_skills_dir.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    skill_md = item / "SKILL.md"
                    if skill_md.exists():
                        skill_names.add(item.name)

        # Check workspace/.claude/skills/ (user-created skills)
        if self.main_skills_dir.exists():
            for item in self.main_skills_dir.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    skill_md = item / "SKILL.md"
                    if skill_md.exists():
                        skill_names.add(item.name)

        return list(skill_names)

    async def rebuild_agent_workspace(
        self,
        agent_id: str,
        skill_ids: list[str],
        allow_all_skills: bool = False
    ) -> Path:
        """Rebuild an agent's workspace with symlinks to allowed skills.

        Args:
            agent_id: The agent's ID
            skill_ids: List of skill IDs the agent is allowed to access
            allow_all_skills: If True, symlink all available skills

        Returns:
            Path to the agent's workspace directory
        """
        self._ensure_dirs()

        agent_workspace = self.get_agent_workspace(agent_id)
        is_new = not agent_workspace.exists()
        agent_skills_dir = self.get_agent_skills_dir(agent_id)

        # Remove existing skills directory and recreate
        if agent_skills_dir.exists():
            shutil.rmtree(agent_skills_dir)
        agent_skills_dir.mkdir(parents=True, exist_ok=True)

        # Seed template files into brand-new workspaces
        if is_new:
            self._copy_templates(agent_workspace, force=True)

        # Determine which skills to link
        if allow_all_skills:
            # Link all available skills
            skill_names = await self.get_all_skill_names()
            logger.info(f"Agent {agent_id}: linking ALL skills ({len(skill_names)} skills)")
        else:
            # Link only specified skills
            skill_names = []
            for skill_id in skill_ids:
                skill_name = await self.get_skill_name_by_id(skill_id)
                if skill_name:
                    skill_names.append(skill_name)
                else:
                    logger.warning(f"Could not resolve skill ID to name: {skill_id}")
            logger.info(f"Agent {agent_id}: linking {len(skill_names)} skills: {skill_names}")

        # Create symlinks using ABSOLUTE paths
        # This is critical for isolated workspaces outside the project tree
        linked_count = 0
        for skill_name in skill_names:
            # Get skill record to find local_path
            skill_record = await self._get_skill_by_name(skill_name)

            # Find skill source path (checks local_path, ~/.claude/skills/, workspace/.claude/skills/)
            source_path = self._get_skill_source_path(skill_name, skill_record)

            if source_path:
                target_path = agent_skills_dir / skill_name
                # Use absolute path for symlink (required for isolated workspaces)
                absolute_source = source_path.resolve()
                try:
                    target_path.symlink_to(absolute_source)
                    linked_count += 1
                    logger.debug(f"Created symlink: {target_path} -> {absolute_source}")
                except OSError as e:
                    logger.error(f"Failed to create symlink for {skill_name}: {e}")
            else:
                logger.warning(f"Skill directory not found in any location: {skill_name}")

        logger.info(f"Agent {agent_id} workspace rebuilt: {linked_count} skills linked")
        return agent_workspace

    async def delete_agent_workspace(self, agent_id: str):
        """Delete an agent's workspace directory."""
        agent_workspace = self.get_agent_workspace(agent_id)
        if agent_workspace.exists():
            shutil.rmtree(agent_workspace)
            logger.info(f"Deleted workspace for agent {agent_id}")
        else:
            logger.debug(f"No workspace to delete for agent {agent_id}")

    async def get_allowed_skill_names(
        self,
        skill_ids: list[str],
        allow_all_skills: bool = False
    ) -> list[str]:
        """Get list of allowed skill names for permission checking.

        Args:
            skill_ids: List of skill IDs
            allow_all_skills: If True, return all available skill names

        Returns:
            List of skill folder names that are allowed
        """
        if allow_all_skills:
            return await self.get_all_skill_names()

        skill_names = []
        for skill_id in skill_ids:
            skill_name = await self.get_skill_name_by_id(skill_id)
            if skill_name:
                skill_names.append(skill_name)
        return skill_names

    def workspace_exists(self, agent_id: str) -> bool:
        """Check if an agent's workspace exists."""
        return self.get_agent_workspace(agent_id).exists()


# Global instance
workspace_manager = WorkspaceManager()
