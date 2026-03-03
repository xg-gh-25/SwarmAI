"""Symlink projection layer for skill discovery by the Claude SDK.

This module was extracted from ``agent_sandbox_manager.py`` to isolate
skill symlink projection concerns.  ``AgentSandboxManager`` retains its
non-skill responsibilities (template copying, ``TEMPLATE_FILES``,
``ensure_templates_in_directory``).  ``ProjectionLayer`` is a new class
that owns *only* skill symlink projection into the Claude SDK's
discovery directory (``SwarmWS/.claude/skills/``).

Key public symbols:

- ``ProjectionLayer``  — Singleton that projects skill symlinks into a
  workspace, respecting tier precedence and allowed-skills lists.

Lifecycle:
    ``ProjectionLayer`` is instantiated once at app startup (singleton),
    receiving the ``SkillManager`` singleton.  Both are created during
    ``InitializationManager.run_full_initialization`` and shared across
    the application via dependency injection.
"""

import logging
from pathlib import Path

from core.skill_manager import SkillManager

logger = logging.getLogger(__name__)


class ProjectionLayer:
    """Project skill symlinks into a workspace for Claude SDK discovery.

    Merges skills from all three tiers (built-in, user, plugin) into
    ``SwarmWS/.claude/skills/`` via symlinks.  Built-in skills are
    always projected unconditionally.  User and plugin skills are
    projected based on the agent's ``allowed_skills`` list or the
    ``allow_all`` flag.

    Stale symlinks (pointing to skills no longer available) are cleaned
    up on every projection pass.  Symlink targets are validated to
    resolve within one of the three known tier directories.
    """

    def __init__(self, skill_manager: SkillManager) -> None:
        """Initialise with a ``SkillManager`` for skill discovery.

        Args:
            skill_manager: The application-wide ``SkillManager`` singleton
                used to query the current skill cache.
        """
        self._skill_manager = skill_manager

    async def project_skills(
        self,
        workspace_path: Path,
        allowed_skills: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        """Project symlinks into ``workspace_path/.claude/skills/``.

        Built-in skills are **always** projected unconditionally.  For
        user and plugin skills:

        - If *allow_all* is ``True``, project everything.
        - Otherwise, project only those whose ``folder_name`` appears in
          *allowed_skills*.

        Stale symlinks (for skills no longer in the target set) are
        removed.  Each symlink target is validated to resolve within a
        known tier directory before creation.  ``OSError`` on individual
        symlinks is caught, logged, and skipped so one bad entry does
        not block the rest.

        Args:
            workspace_path: Root of the SwarmWorkspace (e.g.
                ``<app_data>/SwarmWS``).
            allowed_skills: Folder names the current agent may access.
                Ignored when *allow_all* is ``True``.
            allow_all: If ``True``, project all skills from every tier.
        """
        skills_dir = workspace_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        cache = await self._skill_manager.get_cache()

        # Determine which skills to project
        allowed_set = set(allowed_skills) if allowed_skills else set()
        target_skills: dict[str, Path] = {}

        for folder_name, info in cache.items():
            if info.source_tier == "built-in":
                # Built-in skills are ALWAYS projected
                target_skills[folder_name] = info.path
            elif allow_all:
                target_skills[folder_name] = info.path
            elif folder_name in allowed_set:
                target_skills[folder_name] = info.path

        # Create or update symlinks for each target skill
        for folder_name, skill_path in target_skills.items():
            link_path = skills_dir / folder_name

            # Validate the symlink target before creating
            if not self._validate_symlink_target(skill_path):
                logger.warning(
                    "Skipping skill '%s': target path %s is outside "
                    "known tier directories",
                    folder_name,
                    skill_path,
                )
                continue

            # If symlink already exists and points to the correct target,
            # skip re-creation
            if link_path.is_symlink():
                try:
                    existing_target = link_path.resolve()
                    if existing_target == skill_path.resolve():
                        continue
                    # Target changed — remove old symlink first
                    link_path.unlink()
                except OSError as exc:
                    logger.warning(
                        "Failed to inspect existing symlink for '%s': %s",
                        folder_name,
                        exc,
                    )
                    try:
                        link_path.unlink()
                    except OSError:
                        pass

            try:
                link_path.symlink_to(skill_path.resolve())
            except OSError as exc:
                logger.error(
                    "Failed to create symlink for skill '%s' -> %s: %s",
                    folder_name,
                    skill_path,
                    exc,
                )

        # Clean up stale symlinks
        self._cleanup_stale_symlinks(skills_dir, set(target_skills.keys()))

    def _cleanup_stale_symlinks(
        self,
        skills_dir: Path,
        target_names: set[str],
    ) -> None:
        """Remove symlinks in *skills_dir* not present in *target_names*.

        Iterates over existing symlinks and removes any whose name is
        not in the expected target set.  A warning is logged for each
        stale symlink removed.

        Args:
            skills_dir: The ``SwarmWS/.claude/skills/`` directory.
            target_names: Set of folder names that *should* have
                symlinks.
        """
        try:
            entries = list(skills_dir.iterdir())
        except OSError as exc:
            logger.warning(
                "Failed to list skills directory %s: %s",
                skills_dir,
                exc,
            )
            return

        for entry in entries:
            if entry.is_symlink() and entry.name not in target_names:
                logger.warning(
                    "Removing stale skill symlink: %s",
                    entry,
                )
                try:
                    entry.unlink()
                except OSError as exc:
                    logger.error(
                        "Failed to remove stale symlink %s: %s",
                        entry,
                        exc,
                    )

    def _validate_symlink_target(self, target: Path) -> bool:
        """Verify *target* resolves within a known tier directory.

        Resolves the target path to its canonical form and checks that
        it falls within one of the three skill source tier directories
        managed by the ``SkillManager``.

        Args:
            target: The path to validate (typically ``SkillInfo.path``).

        Returns:
            ``True`` if the target is within a known tier directory,
            ``False`` otherwise.
        """
        try:
            resolved = target.resolve()
        except OSError:
            return False

        tier_roots = [
            self._skill_manager.builtin_path,
            self._skill_manager.user_skills_path,
            self._skill_manager.plugin_skills_path,
        ]

        for tier_root in tier_roots:
            try:
                resolved_root = tier_root.resolve()
                if resolved.is_relative_to(resolved_root):
                    return True
            except (OSError, ValueError):
                continue

        return False
