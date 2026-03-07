"""Distillation trigger hook — flags undistilled DailyActivity files.

Checks the count of undistilled DailyActivity files after each session
close.  When the threshold (>7) is exceeded, writes a
``.needs_distillation`` flag file.  The next session's
``_build_system_prompt()`` reads this flag and injects a system-level
instruction requesting the agent to run ``s_memory-distill``.

Key public symbols:

- ``DistillationTriggerHook``  — Implements ``SessionLifecycleHook``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from core.session_hooks import HookContext
from core.initialization_manager import initialization_manager

logger = logging.getLogger(__name__)

UNDISTILLED_THRESHOLD = 7
FLAG_FILENAME = ".needs_distillation"
SCAN_DAYS = 30  # Only check files from last 30 days


class DistillationTriggerHook:
    """Checks undistilled DailyActivity count and writes flag if needed.

    Since ``s_memory-distill`` is an agent skill requiring a live SDK
    session, and the session is closing, the hook cannot invoke it
    directly.  Instead it writes a flag file that the next session's
    system prompt picks up.
    """

    name = "distillation_trigger"

    async def execute(self, context: HookContext) -> None:
        """Scan DailyActivity files and write flag if threshold exceeded."""
        ws_path = initialization_manager.get_cached_workspace_path()
        da_dir = Path(ws_path) / "Knowledge" / "DailyActivity"

        if not da_dir.exists():
            return

        undistilled_count = await asyncio.to_thread(
            self._count_undistilled, da_dir
        )

        if undistilled_count > UNDISTILLED_THRESHOLD:
            logger.info(
                "Distillation threshold exceeded (%d > %d), setting flag",
                undistilled_count,
                UNDISTILLED_THRESHOLD,
            )
            flag_path = da_dir / FLAG_FILENAME
            flag_path.write_text(
                f"undistilled_count={undistilled_count}\n"
                f"flagged_at={datetime.now().isoformat()}\n",
                encoding="utf-8",
            )
        else:
            logger.debug(
                "Undistilled count %d <= %d, no flag needed",
                undistilled_count,
                UNDISTILLED_THRESHOLD,
            )

    @staticmethod
    def _count_undistilled(da_dir: Path) -> int:
        """Count DailyActivity files where distilled != true.

        Only checks files from the last 30 days to bound scan scope.
        Older files should already be archived by s_memory-distill.
        """
        count = 0
        cutoff = date.today() - timedelta(days=SCAN_DAYS)
        for f in da_dir.glob("*.md"):
            try:
                file_date = date.fromisoformat(f.stem)
                if file_date < cutoff:
                    continue
            except ValueError:
                continue  # Skip non-date filenames
            try:
                content = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not _is_distilled(content):
                count += 1
        return count


def _is_distilled(content: str) -> bool:
    """Parse YAML frontmatter and check distilled field."""
    if not content.startswith("---"):
        return False
    end = content.find("---", 3)
    if end == -1:
        return False
    frontmatter = content[3:end].strip()
    return "distilled: true" in frontmatter
