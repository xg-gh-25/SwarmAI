"""Agent sandbox management for skill isolation.

This module manages skill symlinks in the unified SwarmWorkspace and provides
template management. All agents share a single workspace (SwarmWS) with skill
symlinks set up at app init and re-synced on skill CRUD events. Per-agent
restrictions are enforced via the PreToolUse hook, not filesystem visibility.

Skill source locations (checked in priority order):
    1. skill.local_path from database (exact path from DB record)
    2. ~/.claude/skills/{skill_name}     <- Plugin-installed skills
    3. workspace/.claude/skills/{skill_name}  <- User-created skills

Directory structure:
    ~/.claude/skills/                    <- Plugin-installed skills (from marketplace)
        ├── pptx/
        ├── pdf/
        └── docx/

    <app_data_dir>/SwarmWS/              <- Unified SwarmWorkspace
    └── .claude/skills/                  <- Symlinks to ALL available skills
        ├── pptx -> ~/.claude/skills/pptx
        └── my-skill-1 -> /path/to/skill/source
"""
import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from database import db

logger = logging.getLogger(__name__)


class AgentSandboxManager:
    """Manages skill symlinks and templates in the unified SwarmWorkspace."""

    TEMPLATE_FILES = [
        "AGENTS.md",
        "BOOTSTRAP.md",
        "HEARTBEAT.md",
        "IDENTITY.md",
        "SOUL.md",
        "USER.md",
    ]

    def __init__(self):
        self._templates_dir = Path(__file__).resolve().parent.parent / "templates"

    @property
    def main_workspace(self) -> Path:
        """Return the main workspace path from the cached initialization path."""
        from .initialization_manager import initialization_manager
        return Path(initialization_manager.get_cached_workspace_path())

    @property
    def main_skills_dir(self) -> Path:
        """Return the main skills directory within the workspace."""
        return self.main_workspace / ".claude" / "skills"


    def _copy_templates(self, target_dir: Path, force: bool = False) -> None:
        """Copy template files into *target_dir*/.swarmai/.

        Args:
            target_dir: Destination directory (must already exist).
            force: If ``False``, skip files that already exist in the target.
        """
        if not self._templates_dir.is_dir():
            logger.warning(f"Templates directory not found: {self._templates_dir}")
            return

        swarmai_dir = target_dir / ".swarmai"
        swarmai_dir.mkdir(parents=True, exist_ok=True)

        for filename in self.TEMPLATE_FILES:
            src = self._templates_dir / filename
            dst = swarmai_dir / filename
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



    async def get_skill_name_by_id(self, skill_id: str) -> Optional[str]:
        """Resolve a skill ID to its folder name for symlink creation.

        Looks up the skill record in the database and returns the folder_name
        field (preferred) or a sanitized version of the skill name as fallback.

        This is used by agent_sandbox_manager when building symlinks; the actual
        skill record lifecycle is managed by skill_manager.

        Args:
            skill_id: The unique identifier of the skill in the database.

        Returns:
            The skill folder name if found, None otherwise.
        """
        skill = await db.skills.get(skill_id)
        if not skill:
            logger.warning(f"Skill not found: {skill_id}")
            return None

        # Use folder_name directly if available (new schema)
        if skill.get("folder_name"):
            return skill["folder_name"]

        # Fallback: sanitize skill name
        return re.sub(r'[^a-zA-Z0-9_-]', '-', skill.get("name", "").lower())

    async def _get_skill_by_name(self, skill_name: str) -> Optional[dict]:
        """Look up a skill database record by folder name or sanitized name.

        Used internally by agent_sandbox_manager to retrieve skill metadata
        (e.g. ``local_path``) needed for symlink creation.  The skill record
        itself is owned and managed by skill_manager.

        Args:
            skill_name: The folder name to search for.

        Returns:
            Skill dict if found, None otherwise.
        """
        skills = await db.skills.list()
        for skill in skills:
            # Direct match on folder_name
            if skill.get("folder_name") == skill_name:
                return skill
            # Fallback: check sanitized name
            sanitized_name = re.sub(r'[^a-zA-Z0-9_-]', '-', skill.get("name", "").lower())
            if sanitized_name == skill_name:
                return skill
        return None

    def _get_skill_source_path(self, skill_name: str, skill_record: Optional[dict] = None) -> Optional[Path]:
        """Locate the source directory for a skill to create a symlink target.

        Checks multiple locations in priority order to find where the skill
        files physically reside.  agent_sandbox_manager only reads these paths to
        build symlinks; skill_manager is responsible for creating, updating,
        and deleting the skill files themselves.

        Priority order:
            1. ``skill.local_path`` from database (exact path from DB record)
            2. ``~/.claude/skills/{skill_name}`` (plugin-installed skills)
            3. ``workspace/.claude/skills/{skill_name}`` (user-created skills)

        Args:
            skill_name: The skill folder name.
            skill_record: Optional pre-fetched skill record from database.

        Returns:
            Path to the skill directory if found, None otherwise.
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
        """Return all available skill folder names from the filesystem.

        Scans known skill directories for folders containing a ``SKILL.md``
        file and returns a deduplicated list of their names.  This is used by
        agent_sandbox_manager when ``allow_all_skills`` is True to symlink every
        discovered skill into an agent workspace.

        Note: This method discovers skills on the filesystem.  The canonical
        skill records (metadata, lifecycle) are managed by skill_manager via
        the database.

        Locations checked:
            1. ``~/.claude/skills/`` (plugin-installed skills)
            2. ``workspace/.claude/skills/`` (user-created skills)

        Returns:
            Deduplicated list of skill folder names.
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

    async def setup_workspace_skills(self, workspace_path: Path) -> None:
        """Setup skill symlinks in SwarmWS/.claude/skills/.

        Symlinks ALL available skills. Per-agent restrictions are enforced
        by the PreToolUse hook, not by filesystem visibility.

        Called at:
        - App init (InitializationManager.run_full_initialization)
        - Skill CRUD events (create, update, delete API handlers)

        Args:
            workspace_path: The SwarmWorkspace root directory.
        """
        skills_dir = workspace_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        # Get all available skill names
        target_skill_names = set(await self.get_all_skill_names())

        # Get current symlinks
        existing_links = {p.name for p in skills_dir.iterdir() if p.is_symlink()}

        # Remove stale symlinks
        for stale in existing_links - target_skill_names:
            (skills_dir / stale).unlink()

        # Pre-fetch all skill records to avoid N+1 DB queries
        all_skills = await db.skills.list()
        skills_by_name = {}
        for skill in all_skills:
            if skill.get("folder_name"):
                skills_by_name[skill["folder_name"]] = skill
            sanitized = re.sub(r'[^a-zA-Z0-9_-]', '-', skill.get("name", "").lower())
            if sanitized not in skills_by_name:
                skills_by_name[sanitized] = skill

        # Add missing symlinks
        for skill_name in target_skill_names - existing_links:
            skill_record = skills_by_name.get(skill_name)
            source_path = self._get_skill_source_path(skill_name, skill_record)
            if source_path and source_path.exists():
                try:
                    (skills_dir / skill_name).symlink_to(source_path.resolve())
                except OSError as e:
                    logger.error(f"Failed to symlink skill {skill_name}: {e}")
            else:
                logger.warning(f"Skill source not found, skipping: {skill_name}")




    async def get_allowed_skill_names(
        self,
        skill_ids: list[str],
        allow_all_skills: bool = False
    ) -> list[str]:
        """Return the list of skill folder names an agent is permitted to use.

        This is consumed by the security layer (skill access checker hook) to
        validate tool-use requests at runtime.  agent_sandbox_manager resolves IDs
        to folder names; skill_manager owns the underlying skill records.

        Args:
            skill_ids: List of skill IDs from the agent configuration.
            allow_all_skills: If True, return all available skill names.

        Returns:
            List of skill folder names that are allowed.
        """
        if allow_all_skills:
            return await self.get_all_skill_names()

        skill_names = []
        for skill_id in skill_ids:
            skill_name = await self.get_skill_name_by_id(skill_id)
            if skill_name:
                skill_names.append(skill_name)
        return skill_names



# Global instance
agent_sandbox_manager = AgentSandboxManager()
