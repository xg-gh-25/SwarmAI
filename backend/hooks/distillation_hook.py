"""Distillation trigger hook — auto-distills undistilled DailyActivity files.

Checks the count of undistilled DailyActivity files after each session
close.  When the threshold (>3) is exceeded, runs a lightweight
rule-based distillation directly in the hook (no agent session needed),
writing curated entries to MEMORY.md via ``locked_read_modify_write()``.

Falls back to the flag-file approach if direct distillation fails,
so the next agent session can pick it up.

Key public symbols:

- ``DistillationTriggerHook``  — Implements ``SessionLifecycleHook``.
- ``UNDISTILLED_THRESHOLD``    — Minimum undistilled files to trigger (3).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from core.session_hooks import HookContext
from core.initialization_manager import initialization_manager
from core.daily_activity_writer import parse_frontmatter, write_frontmatter
from scripts.locked_write import locked_read_modify_write

logger = logging.getLogger(__name__)

UNDISTILLED_THRESHOLD = 3
FLAG_FILENAME = ".needs_distillation"
SCAN_DAYS = 30  # Only check files from last 30 days

# Patterns to identify distillation-worthy content.
# These must be specific enough to avoid false positives from common words
# like "confirmed" (the file exists), "always" (run tests), "never" (seen this).
# Each pattern requires a decision/lesson-oriented verb phrase, not just a keyword.
_DECISION_PATTERNS = re.compile(
    r"(?:decided to \w+|chose to \w+|will use \w+|going with \w+|switched to \w+|"
    r"adopted \w+|the approach is \w+|opted for \w+|selected \w+ (?:as|for|over|instead))",
    re.IGNORECASE,
)
_LESSON_PATTERNS = re.compile(
    r"(?:lesson learned|learned that|mistake was|fixed by \w+|root cause (?:was|is)|"
    r"workaround[: ]|should have \w+|next time \w+|"
    r"bug was \w+|issue was \w+|problem was \w+|important to \w+ before)",
    re.IGNORECASE,
)


class DistillationTriggerHook:
    """Checks undistilled DailyActivity count and runs direct distillation.

    Unlike the previous flag-based approach, this hook distills directly
    using ``locked_write.py`` via subprocess.  If direct distillation
    fails, it falls back to writing a ``.needs_distillation`` flag for
    the next agent session.
    """

    name = "distillation_trigger"

    async def execute(self, context: HookContext) -> None:
        """Scan DailyActivity files and distill if threshold exceeded."""
        ws_path = initialization_manager.get_cached_workspace_path()
        da_dir = Path(ws_path) / "Knowledge" / "DailyActivity"

        if not da_dir.exists():
            return

        undistilled_files = await asyncio.to_thread(
            self._get_undistilled_files, da_dir
        )

        if len(undistilled_files) <= UNDISTILLED_THRESHOLD:
            logger.debug(
                "Undistilled count %d <= %d, no distillation needed",
                len(undistilled_files),
                UNDISTILLED_THRESHOLD,
            )
            return

        logger.info(
            "Distillation threshold exceeded (%d > %d), running direct distillation",
            len(undistilled_files),
            UNDISTILLED_THRESHOLD,
        )

        # Attempt direct distillation
        try:
            distilled_count = await asyncio.to_thread(
                self._distill_files, undistilled_files, Path(ws_path)
            )
            logger.info(
                "Direct distillation complete: %d files processed, entries promoted to MEMORY.md",
                distilled_count,
            )
            # Clean up any stale flag file
            flag_path = da_dir / FLAG_FILENAME
            if flag_path.exists():
                flag_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(
                "Direct distillation failed (%s), falling back to flag file",
                exc,
            )
            self._write_flag(da_dir, len(undistilled_files))

    @staticmethod
    def _get_undistilled_files(da_dir: Path) -> list[Path]:
        """Get DailyActivity files where distilled != true.

        Only checks files from the last 30 days to bound scan scope.
        Returns sorted list (oldest first) for chronological processing.
        """
        files = []
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
                files.append(f)
        return sorted(files, key=lambda f: f.stem)

    def _distill_files(self, files: list[Path], ws_path: Path) -> int:
        """Extract entries from DailyActivity files and write to MEMORY.md.

        Returns the number of files successfully distilled.
        """
        memory_path = ws_path / ".context" / "MEMORY.md"

        distilled_count = 0
        for da_file in files:
            try:
                content = da_file.read_text(encoding="utf-8")
                _, body = parse_frontmatter(content)
                file_date = da_file.stem  # YYYY-MM-DD

                # Extract decisions and lessons
                decisions = self._extract_decisions(body)
                lessons = self._extract_lessons(body)

                # Write to MEMORY.md via direct function call
                for decision in decisions:
                    self._run_locked_write(
                        memory_path,
                        "Key Decisions",
                        f"- {file_date}: {decision}",
                    )

                for lesson in lessons:
                    self._run_locked_write(
                        memory_path,
                        "Lessons Learned",
                        f"- {file_date}: {lesson}",
                    )

                # Mark file as distilled
                fm, body_text = parse_frontmatter(content)
                fm["distilled"] = True
                fm["distilled_date"] = date.today().isoformat()
                new_content = write_frontmatter(fm, body_text)
                da_file.write_text(new_content, encoding="utf-8")

                distilled_count += 1
                logger.debug("Distilled %s: %d decisions, %d lessons",
                             da_file.name, len(decisions), len(lessons))
            except Exception as exc:
                logger.warning("Failed to distill %s: %s", da_file.name, exc)
                continue

        return distilled_count

    @staticmethod
    def _extract_decisions(body: str) -> list[str]:
        """Extract decision-worthy lines from DailyActivity body."""
        decisions = []
        in_decisions_section = False
        for line in body.splitlines():
            stripped = line.strip()
            # Track Key Decisions subsections
            if stripped.startswith("### Key Decisions"):
                in_decisions_section = True
                continue
            if stripped.startswith("### "):
                in_decisions_section = False
                continue
            # Lines in Key Decisions sections
            if in_decisions_section and stripped.startswith("- ") and stripped != "- (none)":
                entry = stripped[2:].strip()
                if len(entry) > 15:  # Skip trivially short entries
                    decisions.append(entry[:200])
            # Lines elsewhere that match decision patterns
            elif stripped.startswith("- ") and _DECISION_PATTERNS.search(stripped):
                entry = stripped[2:].strip()
                if len(entry) > 15:
                    decisions.append(entry[:200])
        return decisions[:10]  # Cap to prevent MEMORY.md bloat

    @staticmethod
    def _extract_lessons(body: str) -> list[str]:
        """Extract lesson-worthy lines from DailyActivity body."""
        lessons = []
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("- ") and _LESSON_PATTERNS.search(stripped):
                entry = stripped[2:].strip()
                if len(entry) > 15 and entry != "(none)":
                    lessons.append(entry[:200])
        return lessons[:5]  # Cap to prevent MEMORY.md bloat

    @staticmethod
    def _run_locked_write(
        memory_path: Path,
        section: str,
        text: str,
    ) -> None:
        """Write to MEMORY.md via direct locked_read_modify_write call.

        Uses direct function import instead of subprocess to avoid
        PyInstaller bundle issue where sys.executable != Python.
        """
        try:
            locked_read_modify_write(memory_path, section, text, mode="prepend")
        except SystemExit as e:
            logger.warning("locked_write failed for section %s: exit code %s", section, e.code)
        except Exception as e:
            logger.warning("locked_write failed for section %s: %s", section, e)

    @staticmethod
    def _write_flag(da_dir: Path, count: int) -> None:
        """Write the .needs_distillation flag file as a fallback."""
        flag_path = da_dir / FLAG_FILENAME
        flag_path.write_text(
            f"undistilled_count={count}\n"
            f"flagged_at={datetime.now().isoformat()}\n",
            encoding="utf-8",
        )


def _is_distilled(content: str) -> bool:
    """Parse YAML frontmatter and check distilled field."""
    if not content.startswith("---"):
        return False
    end = content.find("---", 3)
    if end == -1:
        return False
    frontmatter = content[3:end].strip()
    return "distilled: true" in frontmatter
