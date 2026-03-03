"""Agent sandbox template management.

This module manages template files in the unified SwarmWorkspace.  Skill
symlink projection has been extracted to ``projection_layer.py`` as part of
the filesystem-skills re-architecture.  ``AgentSandboxManager`` retains only
non-skill responsibilities:

- ``TEMPLATE_FILES``                — List of template filenames shipped with the app
- ``main_workspace``                — Property returning the cached workspace path
- ``_copy_templates``               — Copies template files into a target directory
- ``ensure_templates_in_directory`` — Public helper for global-user-mode workspaces

Directory structure:
    <app_data_dir>/SwarmWS/              <- Unified SwarmWorkspace
    └── .swarmai/                        <- Template files
        ├── AGENTS.md
        ├── BOOTSTRAP.md
        └── ...
"""
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class AgentSandboxManager:
    """Manages template files in the unified SwarmWorkspace.

    Skill symlink projection has been moved to ``ProjectionLayer``.
    This class retains only template-copying responsibilities.
    """

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



# Global instance
agent_sandbox_manager = AgentSandboxManager()
