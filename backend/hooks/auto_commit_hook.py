"""Smart workspace auto-commit hook with conventional commit messages.

Replaces the per-turn ``_auto_commit_workspace()`` with an intelligent
session-close commit that analyzes ``git diff --stat``, categorizes
changes by file path, and generates meaningful commit messages.

Key public symbols:

- ``WorkspaceAutoCommitHook``  — Implements ``SessionLifecycleHook``.
- ``COMMIT_CATEGORIES``        — Path prefix → commit prefix mapping.
- ``EXTENSION_CATEGORIES``     — File extension → commit prefix mapping.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess

from core.session_hooks import HookContext
from core.initialization_manager import initialization_manager

logger = logging.getLogger(__name__)

# Path prefix → conventional commit prefix
COMMIT_CATEGORIES: dict[str, str] = {
    ".context/": "framework",
    ".claude/skills/": "skills",
    "Knowledge/": "content",
    "Projects/": "project",
}

# File extension → conventional commit prefix
EXTENSION_CATEGORIES: dict[str, str] = {
    ".pdf": "output",
    ".pptx": "output",
    ".docx": "output",
    ".png": "output",
    ".jpg": "output",
}

DEFAULT_CATEGORY = "chore"


class WorkspaceAutoCommitHook:
    """Smart git commit at session close with conventional commit messages.

    Analyzes changed files via ``git diff --stat``, categorizes them by
    path pattern, generates a meaningful commit message, and skips
    trivial changes.
    """

    name = "workspace_auto_commit"

    async def execute(self, context: HookContext) -> None:
        """Analyze changes and commit with a smart message."""
        ws_path = initialization_manager.get_cached_workspace_path()
        await asyncio.to_thread(self._smart_commit, ws_path)

    def _smart_commit(self, ws_path: str) -> None:
        """Run git operations in a background thread."""
        # 1. Check for changes
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ws_path, capture_output=True, text=True,
        )
        if not status.stdout.strip():
            return  # No changes

        # 2. Stage all changes
        add_result = subprocess.run(
            ["git", "add", "-A"],
            cwd=ws_path, capture_output=True,
        )
        if add_result.returncode != 0:
            logger.warning("git add failed: %s", add_result.stderr)
            return

        # 3. Analyze staged changes
        diff_stat = subprocess.run(
            ["git", "diff", "--cached", "--stat"],
            cwd=ws_path, capture_output=True, text=True,
        )
        changed_files = self._parse_diff_stat(diff_stat.stdout)

        # 4. Generate commit message
        if not changed_files:
            message = "chore: session changes"
        elif self._is_trivial(changed_files):
            message = f"chore: session sync ({len(changed_files)} files)"
        else:
            message = self._generate_commit_message(changed_files)

        # 5. Commit
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=ws_path, capture_output=True,
        )
        logger.info("Auto-committed workspace: %s", message)

    @staticmethod
    def _parse_diff_stat(diff_output: str) -> list[str]:
        """Extract file paths from ``git diff --stat`` output."""
        files = []
        for line in diff_output.strip().splitlines():
            if "|" in line:
                file_path = line.split("|")[0].strip()
                if file_path:
                    files.append(file_path)
        return files

    @staticmethod
    def _categorize_file(file_path: str) -> str:
        """Map a file path to a conventional commit category."""
        for prefix, category in COMMIT_CATEGORIES.items():
            if file_path.startswith(prefix):
                return category
        for ext, category in EXTENSION_CATEGORIES.items():
            if file_path.endswith(ext):
                return category
        return DEFAULT_CATEGORY

    def _is_trivial(self, files: list[str]) -> bool:
        """Check if all changes are trivial (only skill config syncs)."""
        return all(
            self._categorize_file(f) in ("skills", "chore")
            for f in files
        )

    def _generate_commit_message(self, files: list[str]) -> str:
        """Generate a conventional commit message from changed files."""
        categories: dict[str, int] = {}
        for f in files:
            cat = self._categorize_file(f)
            categories[cat] = categories.get(cat, 0) + 1

        if not categories:
            return "chore: session changes"

        dominant = max(categories, key=lambda k: (categories[k], k))
        total = sum(categories.values())

        if total == 1:
            return f"{dominant}: update {files[0]}"
        elif len(categories) == 1:
            return f"{dominant}: update {total} files"
        else:
            parts = [
                f"{cat} ({n})"
                for cat, n in sorted(categories.items(), key=lambda x: -x[1])
            ]
            return f"{dominant}: {', '.join(parts)}"
