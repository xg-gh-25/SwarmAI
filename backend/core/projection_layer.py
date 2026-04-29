"""Copy-based projection layer for skill discovery by the Claude SDK.

This module was extracted from ``agent_sandbox_manager.py`` to isolate
skill projection concerns.  ``AgentSandboxManager`` retains its
non-skill responsibilities (template copying, ``TEMPLATE_FILES``,
``ensure_templates_in_directory``).  ``ProjectionLayer`` is a new class
that owns *only* skill projection into the Claude SDK's discovery
directory (``SwarmWS/.claude/skills/``).

Skills are projected as real directory copies (via ``shutil.copytree``)
rather than symlinks, so that git tracks actual file content and detects
modifications.  Legacy symlinks from prior versions are cleaned up
transparently.

Key public symbols:

- ``ProjectionLayer``  — Singleton that projects skill copies into a
  workspace, respecting tier precedence and allowed-skills lists.

Lifecycle:
    ``ProjectionLayer`` is instantiated once at app startup (singleton),
    receiving the ``SkillManager`` singleton.  Both are created during
    ``InitializationManager.run_full_initialization`` and shared across
    the application via dependency injection.
"""

import logging
import os
import shutil
from pathlib import Path

from core.manifest_loader import ManifestLoader
from core.skill_manager import SkillManager

logger = logging.getLogger(__name__)


class ProjectionLayer:
    """Project skill copies into a workspace for Claude SDK discovery.

    Merges skills from all three tiers (built-in, user, plugin) into
    ``SwarmWS/.claude/skills/`` via ``shutil.copytree()``.  Built-in
    skills are always projected unconditionally.  User and plugin skills
    are projected based on the agent's ``allowed_skills`` list or the
    ``allow_all`` flag.

    Stale entries (both legacy symlinks and real directories pointing to
    skills no longer available) are cleaned up on every projection pass.
    Skill source paths are validated to resolve within one of the three
    known tier directories.
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
        """Project skill copies into ``workspace_path/.claude/skills/``.

        Built-in skills are **always** projected unconditionally.  For
        user and plugin skills:

        - If *allow_all* is ``True``, project everything.
        - Otherwise, project only those whose ``folder_name`` appears in
          *allowed_skills*.

        Stale entries (for skills no longer in the target set) are
        removed — both legacy symlinks and real directories.  Each skill
        source path is validated to resolve within a known tier directory
        before copying.  ``OSError`` on individual copies is caught,
        logged, and skipped so one bad entry does not block the rest.

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

        # Platform filter: Hive (EC2 Linux) excludes macOS/desktop skills
        is_hive = os.environ.get("SWARMAI_MODE") == "hive"
        _hive_excluded = {"macos", "desktop"} if is_hive else set()

        # Determine which skills to project
        allowed_set = set(allowed_skills) if allowed_skills else set()
        target_skills: dict[str, Path] = {}
        skipped_platform: list[str] = []

        for folder_name, info in cache.items():
            # Filter by platform before tier check
            if info.platform in _hive_excluded:
                skipped_platform.append(folder_name)
                continue

            if info.source_tier == "built-in":
                # Built-in skills are ALWAYS projected
                target_skills[folder_name] = info.path
            elif allow_all:
                target_skills[folder_name] = info.path
            elif folder_name in allowed_set:
                target_skills[folder_name] = info.path

        if skipped_platform:
            logger.info(
                "Hive mode: skipped %d platform-incompatible skills: %s",
                len(skipped_platform),
                ", ".join(sorted(skipped_platform)),
            )

        # Create or update copies for each target skill
        for folder_name, skill_path in target_skills.items():
            link_path = skills_dir / folder_name

            # Validate the skill source before copying
            if not self._validate_skill_source(skill_path):
                logger.warning(
                    "Skipping skill '%s': source path %s is outside "
                    "known tier directories",
                    folder_name,
                    skill_path,
                )
                continue

            # If entry already exists, remove and re-copy (clean re-copy
            # on every launch is acceptable and avoids stale content)
            if link_path.exists() or link_path.is_symlink():
                try:
                    if link_path.is_symlink():
                        # Legacy symlink — just unlink
                        link_path.unlink()
                    else:
                        shutil.rmtree(link_path)
                except OSError as exc:
                    logger.warning(
                        "Failed to remove existing entry for '%s': %s",
                        folder_name,
                        exc,
                    )
                    continue

            try:
                shutil.copytree(
                    str(skill_path.resolve()),
                    str(link_path),
                    dirs_exist_ok=True,
                )
            except OSError as exc:
                logger.error(
                    "Failed to copy skill '%s' from %s: %s",
                    folder_name,
                    skill_path,
                    exc,
                )
                continue

            # Provision npm dependencies declared in manifest.yaml
            manifest = ManifestLoader.load(skill_path)
            if manifest and manifest.dependencies.get("npm"):
                try:
                    installed = ManifestLoader.ensure_dependencies(manifest)
                    if installed:
                        logger.info(
                            "Provisioned npm deps for '%s': %s",
                            folder_name, installed,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to provision deps for '%s': %s",
                        folder_name, exc,
                    )

        # Clean up stale entries (both legacy symlinks and real directories)
        self._cleanup_stale_entries(skills_dir, set(target_skills.keys()))

    def _cleanup_stale_entries(
        self,
        skills_dir: Path,
        target_names: set[str],
    ) -> None:
        """Remove entries in *skills_dir* not present in *target_names*.

        Handles both legacy symlinks and real directories (from the
        copytree migration).  Symlinks are unlinked; real directories
        are removed via ``shutil.rmtree()``.  A warning is logged for
        each stale entry removed.

        Args:
            skills_dir: The ``SwarmWS/.claude/skills/`` directory.
            target_names: Set of folder names that *should* have
                entries.
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
            if entry.name not in target_names:
                logger.warning(
                    "Removing stale skill entry: %s",
                    entry,
                )
                try:
                    if entry.is_symlink():
                        entry.unlink()
                    elif entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()
                except OSError as exc:
                    logger.error(
                        "Failed to remove stale entry %s: %s",
                        entry,
                        exc,
                    )

    def _validate_skill_source(self, source: Path) -> bool:
        """Verify *source* resolves within a known tier directory.

        Resolves the source path to its canonical form and checks that
        it falls within one of the three skill source tier directories
        managed by the ``SkillManager``.

        Args:
            source: The path to validate (typically ``SkillInfo.path``).

        Returns:
            ``True`` if the source is within a known tier directory,
            ``False`` otherwise.
        """
        try:
            resolved = source.resolve()
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
