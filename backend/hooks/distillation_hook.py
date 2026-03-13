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
    using ``locked_read_modify_write()`` via direct function call.
    If direct distillation fails, it falls back to writing a
    ``.needs_distillation`` flag for the next agent session.
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
        """Extract entries from DailyActivity files and write to MEMORY.md + EVOLUTION.md.

        Extracts four categories:
        - Decisions → MEMORY.md "Key Decisions"
        - Lessons → MEMORY.md "Lessons Learned"
        - COE signals → MEMORY.md "COE Registry" (cross-session problem tracking)
        - Corrections → EVOLUTION.md "Corrections Captured" (agent behavior fixes)

        Returns the number of files successfully distilled.
        """
        memory_path = ws_path / ".context" / "MEMORY.md"
        evolution_path = ws_path / ".context" / "EVOLUTION.md"

        distilled_count = 0
        coe_entries: list[tuple[str, str, str]] = []  # (date, signal, topic)
        all_corrections: list[tuple[str, str]] = []  # (date, correction)

        for da_file in files:
            try:
                content = da_file.read_text(encoding="utf-8")
                fm, body = parse_frontmatter(content)
                file_date = da_file.stem  # YYYY-MM-DD

                # Extract decisions, lessons, and corrections
                decisions = self._extract_decisions(body)
                lessons = self._extract_lessons(body)
                corrections = self._extract_corrections(body)

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

                # Collect corrections for EVOLUTION.md
                for correction in corrections:
                    all_corrections.append((file_date, correction))

                # Collect COE signals — check both frontmatter flag and body content
                coe_items = self._extract_coe_entries(body)
                if coe_items:
                    for signal, topic in coe_items:
                        coe_entries.append((file_date, signal, topic))

                # Mark file as distilled
                fm["distilled"] = True
                fm["distilled_date"] = date.today().isoformat()
                new_content = write_frontmatter(fm, body)
                da_file.write_text(new_content, encoding="utf-8")

                distilled_count += 1
                logger.debug("Distilled %s: %d decisions, %d lessons, %d corrections",
                             da_file.name, len(decisions), len(lessons), len(corrections))
            except Exception as exc:
                logger.warning("Failed to distill %s: %s", da_file.name, exc)
                continue

        # Write COE registry entries
        if coe_entries:
            self._write_coe_registry(memory_path, coe_entries)

        # Auto-manage Open Threads from COE signals (code-enforced)
        if coe_entries:
            self._update_open_threads(memory_path, coe_entries)

        # Write corrections to EVOLUTION.md
        if all_corrections:
            self._write_corrections(evolution_path, all_corrections)

        return distilled_count

    @staticmethod
    def _extract_decisions(body: str) -> list[str]:
        """Extract decision-worthy lines from DailyActivity body.

        Handles both old format (### Key Decisions) and new format
        (**Decisions:**) section headers.
        """
        decisions = []
        in_decisions_section = False
        for line in body.splitlines():
            stripped = line.strip()
            # Track Decisions sections — both old and new format
            if stripped.startswith("### Key Decisions") or stripped == "**Decisions:**":
                in_decisions_section = True
                continue
            # Exit section on next header (### or **bold:**)
            if in_decisions_section and (
                stripped.startswith("### ")
                or stripped.startswith("## ")
                or (stripped.startswith("**") and stripped.endswith(":**"))
            ):
                in_decisions_section = False
                continue
            # Lines in Decisions sections
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
        """Extract lesson-worthy lines from DailyActivity body.

        Two extraction modes:
        1. Section-based: all items under **Lessons:** section (new format)
        2. Pattern-based: any line matching lesson patterns (both formats)
        """
        lessons = []
        in_lessons_section = False
        for line in body.splitlines():
            stripped = line.strip()
            # Track Lessons section (new format)
            if stripped == "**Lessons:**":
                in_lessons_section = True
                continue
            if in_lessons_section and (
                stripped.startswith("## ")
                or (stripped.startswith("**") and stripped.endswith(":**"))
            ):
                in_lessons_section = False
                continue
            # All items in Lessons section are lessons
            if in_lessons_section and stripped.startswith("- "):
                entry = stripped[2:].strip()
                if len(entry) > 15 and entry != "(none)":
                    lessons.append(entry[:200])
                continue
            # Pattern-based extraction (works on any format)
            if stripped.startswith("- ") and _LESSON_PATTERNS.search(stripped):
                entry = stripped[2:].strip()
                if len(entry) > 15 and entry != "(none)":
                    lessons.append(entry[:200])
        return lessons[:5]  # Cap to prevent MEMORY.md bloat

    @staticmethod
    def _extract_corrections(body: str) -> list[str]:
        """Extract user corrections of agent behavior from DailyActivity body.

        Looks for the **Corrections:** section written by the enriched
        DailyActivity format.
        """
        corrections = []
        in_corrections_section = False
        for line in body.splitlines():
            stripped = line.strip()
            if stripped == "**Corrections:**":
                in_corrections_section = True
                continue
            if in_corrections_section and (
                stripped.startswith("## ")
                or (stripped.startswith("**") and stripped.endswith(":**"))
            ):
                in_corrections_section = False
                continue
            if in_corrections_section and stripped.startswith("- "):
                entry = stripped[2:].strip()
                if len(entry) > 10 and entry != "(none)":
                    corrections.append(entry[:200])
        return corrections[:10]  # Cap

    def _write_corrections(
        self,
        evolution_path: Path,
        corrections: list[tuple[str, str]],
    ) -> None:
        """Write correction entries to EVOLUTION.md under 'Corrections Captured'.

        Each correction gets a C-prefixed sequential ID.
        Format matches EVOLUTION.md convention.
        """
        # Read current EVOLUTION.md to find next C-ID
        try:
            content = evolution_path.read_text(encoding="utf-8")
            existing_ids = re.findall(r"### C(\d+)", content)
            next_id = max((int(x) for x in existing_ids), default=0) + 1
        except Exception:
            next_id = 1

        for file_date, correction in corrections:
            entry_id = f"C{next_id:03d}"
            entry = (
                f"### {entry_id} | {file_date}\n"
                f"- **Correction**: {correction}\n"
                f"- **Status**: active\n"
            )
            self._run_locked_write(evolution_path, "Corrections Captured", entry)
            next_id += 1

        logger.info(
            "Wrote %d correction entries to EVOLUTION.md", len(corrections)
        )

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
    def _extract_coe_entries(body: str) -> list[tuple[str, str]]:
        """Extract COE signal and topic from DailyActivity body.

        Looks for lines like: ``**COE:** `resolution` — streaming not working``
        Returns list of (signal, topic) tuples.
        """
        entries = []
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("**COE:**"):
                # Parse: **COE:** `signal` — topic
                rest = stripped[len("**COE:**"):].strip()
                # Extract signal from backticks
                if "`" in rest:
                    signal_start = rest.index("`") + 1
                    signal_end = rest.index("`", signal_start)
                    signal = rest[signal_start:signal_end]
                    # Extract topic after " — " or " - "
                    topic_part = rest[signal_end + 1:].strip()
                    topic_part = topic_part.lstrip("—-").strip()
                    if signal in ("candidate", "resolution") and topic_part:
                        entries.append((signal, topic_part))
        return entries

    def _write_coe_registry(
        self,
        memory_path: Path,
        entries: list[tuple[str, str, str]],
    ) -> None:
        """Write COE entries to MEMORY.md under '## COE Registry'.

        Groups by topic. If a topic has both candidate + resolution entries,
        marks it as resolved.
        """
        # Group by topic
        by_topic: dict[str, list[tuple[str, str]]] = {}
        for file_date, signal, topic in entries:
            key = topic.lower().strip()
            if key not in by_topic:
                by_topic[key] = []
            by_topic[key].append((file_date, signal))

        for topic_key, events in by_topic.items():
            # Use the original casing from the first entry
            original_topic = next(
                t for _, _, t in entries if t.lower().strip() == topic_key
            )
            dates = sorted(set(d for d, _ in events))
            has_resolution = any(s == "resolution" for _, s in events)
            status = "✅ Resolved" if has_resolution else "🔍 Investigating"

            entry = (
                f"- {dates[0]}: **{original_topic}** — {status}. "
                f"Sessions: {', '.join(dates)}"
            )
            self._run_locked_write(memory_path, "COE Registry", entry)

        logger.info("Wrote %d COE registry entries to MEMORY.md", len(by_topic))

    def _update_open_threads(
        self,
        memory_path: Path,
        coe_entries: list[tuple[str, str, str]],
    ) -> None:
        """Auto-manage Open Threads from COE signals (code-enforced).

        For each COE topic:
        - If a matching thread exists: increment report count
        - If no match: create new P0 thread (COE candidates auto-promote)
        - If signal is 'resolution': mark thread as resolved

        Uses locked read-modify-write for the entire Open Threads section.
        """
        import fcntl

        try:
            # Read current content under lock
            lock_path = Path(str(memory_path) + ".lock")
            lock_path.touch(exist_ok=True)
            with open(lock_path, "r") as lock_fh:
                fcntl.flock(lock_fh, fcntl.LOCK_EX)
                try:
                    content = memory_path.read_text(encoding="utf-8")
                    updated = self._apply_open_thread_updates(content, coe_entries)
                    if updated != content:
                        memory_path.write_text(updated, encoding="utf-8")
                        logger.info("Updated Open Threads with %d COE entries", len(coe_entries))
                finally:
                    fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except Exception as exc:
            logger.warning("Failed to update Open Threads: %s", exc)

    @staticmethod
    def _apply_open_thread_updates(
        content: str,
        coe_entries: list[tuple[str, str, str]],
    ) -> str:
        """Apply COE-driven updates to Open Threads section in MEMORY.md content.

        Matching logic: case-insensitive substring match of COE topic
        against existing thread titles.

        Report count format: ``(reported Nx: session1, session2)``
        """
        lines = content.split("\n")
        # Group COE entries by normalized topic
        by_topic: dict[str, list[tuple[str, str]]] = {}
        for file_date, signal, topic in coe_entries:
            key = topic.lower().strip()
            if key not in by_topic:
                by_topic[key] = []
            by_topic[key].append((file_date, signal))

        matched_topics: set[str] = set()

        for topic_key, events in by_topic.items():
            # Try to find a matching thread line
            found = False
            for i, line in enumerate(lines):
                # Match thread lines: "- 🔴 **title**" or "- 🟡 **title**"
                if not re.match(r"^- [🔴🟡🔵] \*\*", line):
                    continue
                # Extract title between ** **
                title_match = re.search(r"\*\*(.+?)\*\*", line)
                if not title_match:
                    continue
                title = title_match.group(1).lower()
                # Fuzzy match: any significant word overlap
                topic_words = set(topic_key.split())
                title_words = set(title.split())
                overlap = topic_words & title_words
                # Match if >50% of topic words appear in title, or substring match
                if (len(overlap) >= max(1, len(topic_words) // 2)
                        or topic_key in title
                        or title in topic_key):
                    # Found match — increment report count
                    count_match = re.search(r"\(reported (\d+)x:", line)
                    if count_match:
                        old_count = int(count_match.group(1))
                        new_count = old_count + len(events)
                        lines[i] = line.replace(
                            f"reported {old_count}x:",
                            f"reported {new_count}x:",
                        )
                    # Check if resolution — update status line
                    has_resolution = any(s == "resolution" for _, s in events)
                    if has_resolution and i + 1 < len(lines):
                        status_line = lines[i + 1]
                        if "Status:" in status_line and "resolved" not in status_line.lower():
                            lines[i + 1] = f"  Status: ~~{status_line.strip().removeprefix('Status:').strip()}~~ **RESOLVED**."
                    found = True
                    matched_topics.add(topic_key)
                    break

            if not found:
                # Create new P0 thread
                original_topic = next(
                    t for _, _, t in coe_entries if t.lower().strip() == topic_key
                )
                dates = sorted(set(d for d, _ in events))
                new_entry = (
                    f"- 🔴 **{original_topic}** (reported {len(events)}x: {', '.join(dates)})\n"
                    f"  Status: COE candidate — auto-promoted from DailyActivity."
                )
                # Find P0 section to insert
                p0_idx = None
                for i, line in enumerate(lines):
                    if line.strip() == "### P0 — Blocking":
                        p0_idx = i + 1
                        break
                if p0_idx is not None:
                    lines.insert(p0_idx, new_entry)
                else:
                    # No P0 section — find Open Threads and add P0
                    for i, line in enumerate(lines):
                        if line.strip() == "## Open Threads":
                            lines.insert(i + 1, f"\n### P0 — Blocking\n{new_entry}\n")
                            break
                matched_topics.add(topic_key)

        return "\n".join(lines)

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
