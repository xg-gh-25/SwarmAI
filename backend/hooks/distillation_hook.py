"""Distillation trigger hook — auto-distills undistilled DailyActivity files.

Checks the count of undistilled DailyActivity files after each session
close.  When the threshold (>2) is exceeded, runs a lightweight
rule-based distillation directly in the hook (no agent session needed),
writing curated entries to MEMORY.md via ``_modify_content()`` under flock.

Also archives old DailyActivity files (>90 days) and enforces section
caps on MEMORY.md to prevent unbounded growth.

Falls back to the flag-file approach if direct distillation fails,
so the next agent session can pick it up.

Key public symbols:

- ``DistillationTriggerHook``  — Implements ``SessionLifecycleHook``.
- ``UNDISTILLED_THRESHOLD``    — Minimum undistilled files to trigger (2).
- ``ARCHIVE_DAYS``             — Age threshold for DailyActivity archival (90).
- ``SECTION_CAPS``             — Max entries per MEMORY.md section after distillation.
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
from scripts.locked_write import LockedWriteError
from hooks.evolution_maintenance_hook import _append_changelog

logger = logging.getLogger(__name__)

UNDISTILLED_THRESHOLD = 2
FLAG_FILENAME = ".needs_distillation"
SCAN_DAYS = 30  # Only check files from last 30 days
ARCHIVE_DAYS = 90  # Move files older than this to Archives/
SECTION_CAPS = {  # Max entries per MEMORY.md section after distillation
    "Key Decisions": 30,
    "Lessons Learned": 20,
    "COE Registry": 15,
}

# Centralized patterns — shared with summarization.py via extraction_patterns.py.
# Distillation uses the STRICT variant (runs on already-extracted DailyActivity).
from core.extraction_patterns import (
    DECISION_PATTERNS_STRICT as _DECISION_PATTERNS,
    LESSON_PATTERNS as _LESSON_PATTERNS,
    is_noise_entry as _is_noise_entry,
)

# Competence patterns: "now I know how to X" — positive capability acquisition.
# Distinct from lessons ("next time avoid X") which are corrective/negative.
# These stay here (not in extraction_patterns) because they're only used by distillation.
_COMPETENCE_PATTERNS = re.compile(
    r"(?:"
    # First-person learning
    r"(?:now )?(?:know|knows|understand|understands) (?:how to|that|about)|"
    r"can now|is able to|figured out how to|discovered that|"
    r"the (?:way|trick|method|pattern|technique) (?:is|to)|"
    # Technical facts: "X works by Y", "X points to Y"
    r"(?:works by|achieved by|accomplished via|done with|built using)|"
    r"(?:verified|confirmed) (?:that|working|live|in production)|"
    r"(?:root cause|fix) (?:was|is|:)|"
    r"(?:pipeline|system|hook|engine) (?:works|fires|runs|produces)|"
    r"uses? [\w./]+ (?:instead of|rather than|over)|"
    r"(?:requires?|needs?) (?:both |\w+ (?:to|for|before|and ))|"
    # Declarative facts: "X does not source Y", "X rejects Y"
    r"(?:does not|doesn't|cannot|can't|do not|don't) (?:source|inherit|load|"
    r"see|find|support|work|include|have|resolve|recognize|persist)|"
    r"(?:don't|do not|never) (?:\w[\w-]* ){1,3}(?:without|before|until) |"
    r"(?:blocks?|rejects?|prevents?) [\w/]+ \w+|"
    # Imperative guidance: "Use X to Y", "prefer X over Y"
    r"(?:use |prefer |always )(?:\w+ ){1,3}(?:to |for |when |instead |before |during )|"
    # Requirement patterns: "must run/use/be"
    r"must (?:run|use|be|have|call|spawn|import)|"
    # Structural observations: "X hierarchy: Y", "X order: Y"
    r"\w+ (?:hierarchy|order|precedence|priority): |"
    # Factual pointers: "X points to Y", "X refers to Y"
    r"(?:points? to|refers? to|maps? to) |"
    # Architecture: "content must be preserved in ref"
    r"(?:content|state|data) must be (?:preserved|stored|saved|maintained)"
    r")",
    re.IGNORECASE,
)


class DistillationTriggerHook:
    """Checks undistilled DailyActivity count and runs direct distillation.

    Distills directly using ``_modify_content()`` under flock (single lock
    acquisition per section).  If direct distillation fails, falls back to
    writing a ``.needs_distillation`` flag for the next agent session.
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

        # Auto-archive old DailyActivity files (>90 days)
        try:
            archived = await asyncio.to_thread(
                self._archive_old_files, da_dir, Path(ws_path)
            )
            if archived:
                logger.info("Archived %d old DailyActivity files", archived)
        except Exception as exc:
            logger.warning("DailyActivity archival failed (non-blocking): %s", exc)

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

        All entries per section are batched into a single locked_write call
        to minimize lock acquisitions (was: one call per entry).

        Returns the number of files successfully distilled.
        """
        memory_path = ws_path / ".context" / "MEMORY.md"
        evolution_path = ws_path / ".context" / "EVOLUTION.md"

        distilled_count = 0
        coe_entries: list[tuple[str, str, str]] = []  # (date, signal, topic)
        all_corrections: list[tuple[str, str]] = []  # (date, correction)
        all_competence: list[tuple[str, str]] = []  # (date, competence)

        # Collect all entries across files, then write once per section.
        # Track which files were successfully extracted (marked distilled
        # AFTER writes succeed — see GAP 12).
        all_decisions: list[str] = []
        all_lessons: list[str] = []
        extracted_files: list[tuple[Path, dict, str]] = []  # (path, frontmatter, body)

        for da_file in files:
            try:
                content = da_file.read_text(encoding="utf-8")
                fm, body = parse_frontmatter(content)
                file_date = da_file.stem  # YYYY-MM-DD

                # Extract decisions, lessons, corrections, and competence
                decisions = self._extract_decisions(body)
                lessons = self._extract_lessons(body)
                corrections = self._extract_corrections(body)
                competence = self._extract_competence(body)

                # Batch entries (write happens after the loop)
                for decision in decisions:
                    all_decisions.append(f"- {file_date}: {decision}")
                for lesson in lessons:
                    all_lessons.append(f"- {file_date}: {lesson}")

                # Collect corrections and competence for EVOLUTION.md
                for correction in corrections:
                    all_corrections.append((file_date, correction))
                for comp in competence:
                    all_competence.append((file_date, comp))

                # Collect COE signals — check both frontmatter flag and body content
                coe_items = self._extract_coe_entries(body)
                if coe_items:
                    for signal, topic in coe_items:
                        coe_entries.append((file_date, signal, topic))

                # Track for post-write marking (NOT marked here — see GAP 12)
                extracted_files.append((da_file, fm, body))

                distilled_count += 1
                logger.debug("Distilled %s: %d decisions, %d lessons, %d corrections, %d competence",
                             da_file.name, len(decisions), len(lessons), len(corrections), len(competence))
            except Exception as exc:
                logger.warning("Failed to distill %s: %s", da_file.name, exc)
                continue

        # Batched writes to MEMORY.md — one lock acquisition per section
        if all_decisions:
            self._run_locked_write(
                memory_path, "Key Decisions", "\n".join(all_decisions)
            )
        if all_lessons:
            self._run_locked_write(
                memory_path, "Lessons Learned", "\n".join(all_lessons)
            )

        # Write COE registry entries
        if coe_entries:
            self._write_coe_registry(memory_path, coe_entries)

        # Auto-manage Open Threads from COE signals (code-enforced)
        if coe_entries:
            self._update_open_threads(memory_path, coe_entries)

        # Write corrections and competence to EVOLUTION.md
        if all_corrections:
            self._write_corrections(evolution_path, all_corrections)
        if all_competence:
            self._write_competence(evolution_path, all_competence)

        # Enforce section caps on MEMORY.md to prevent unbounded growth
        if all_decisions or all_lessons or coe_entries:
            self._enforce_section_caps(memory_path)

        # Mark files as distilled AFTER all writes succeed.
        # Previous ordering (mark inside loop, write after) could lose entries:
        # if batch writes failed, already-marked files wouldn't be re-extracted.
        for da_file, fm, body in extracted_files:
            try:
                fm["distilled"] = True
                fm["distilled_date"] = date.today().isoformat()
                new_content = write_frontmatter(fm, body)
                da_file.write_text(new_content, encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to mark %s as distilled: %s", da_file.name, exc)
                # Non-fatal: file will be re-processed next time, but dedup
                # in _run_locked_write prevents duplicate MEMORY.md entries.

        # Log distillation to EVOLUTION_CHANGELOG.jsonl
        if distilled_count > 0:
            changelog_path = ws_path / ".context" / "EVOLUTION_CHANGELOG.jsonl"
            _append_changelog(
                changelog_path,
                "distill",
                f"batch-{date.today().isoformat()}",
                f"Distilled {distilled_count} DailyActivity file(s) to MEMORY.md",
                source="distillation_hook",
            )

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
                if len(entry) > 15 and not _is_noise_entry(entry):
                    decisions.append(entry[:200])
            # Lines elsewhere that match decision patterns
            elif stripped.startswith("- ") and _DECISION_PATTERNS.search(stripped):
                entry = stripped[2:].strip()
                if len(entry) > 15 and not _is_noise_entry(entry):
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

    @staticmethod
    def _extract_competence(body: str) -> list[str]:
        """Extract competence entries from DailyActivity body.

        Competence = "now I know how to X" — positive capability knowledge.
        Sources: **Lessons:** section items matching competence patterns,
        plus any line in the body matching competence patterns.

        Excludes items that also match lesson patterns (corrective/negative)
        to avoid double-counting.
        """
        competence = []
        in_lessons_section = False
        for line in body.splitlines():
            stripped = line.strip()
            if stripped == "**Lessons:**":
                in_lessons_section = True
                continue
            if in_lessons_section and (
                stripped.startswith("## ")
                or (stripped.startswith("**") and stripped.endswith(":**"))
            ):
                in_lessons_section = False
                continue
            if not stripped.startswith("- "):
                continue
            entry = stripped[2:].strip()
            if len(entry) <= 15 or entry == "(none)":
                continue
            # Must match competence pattern
            if not _COMPETENCE_PATTERNS.search(entry):
                continue
            # Skip purely corrective entries ("should have", "next time avoid")
            # but allow entries that are both competence AND lesson-like
            if re.search(r"(?:mistake was|should have|next time (?:avoid|don't))", entry, re.IGNORECASE):
                continue
            competence.append(entry[:200])
        return competence[:5]  # Cap

    def _write_competence(
        self,
        evolution_path: Path,
        competence_entries: list[tuple[str, str]],
    ) -> None:
        """Write competence entries to EVOLUTION.md under 'Competence Learned'.

        Each entry gets a K-prefixed sequential ID. All entries are batched
        into a single locked_write call.
        """
        try:
            content = evolution_path.read_text(encoding="utf-8")
            existing_ids = re.findall(r"### K(\d+)", content)
            next_id = max((int(x) for x in existing_ids), default=0) + 1
        except Exception:
            next_id = 1

        blocks: list[str] = []
        for file_date, entry in competence_entries:
            entry_id = f"K{next_id:03d}"
            blocks.append(
                f"### {entry_id} | {file_date}\n"
                f"- **Competence**: {entry}\n"
                f"- **Status**: active\n"
            )
            next_id += 1

        if blocks:
            self._run_locked_write(
                evolution_path, "Competence Learned", "\n".join(blocks)
            )

        logger.info(
            "Wrote %d competence entries to EVOLUTION.md", len(competence_entries)
        )

    def _write_corrections(
        self,
        evolution_path: Path,
        corrections: list[tuple[str, str]],
    ) -> None:
        """Write correction entries to EVOLUTION.md under 'Corrections Captured'.

        Each correction gets a C-prefixed sequential ID. All entries are
        batched into a single locked_write call.
        """
        # Read current EVOLUTION.md to find next C-ID
        try:
            content = evolution_path.read_text(encoding="utf-8")
            existing_ids = re.findall(r"### C(\d+)", content)
            next_id = max((int(x) for x in existing_ids), default=0) + 1
        except Exception:
            next_id = 1

        blocks: list[str] = []
        for file_date, correction in corrections:
            entry_id = f"C{next_id:03d}"
            blocks.append(
                f"### {entry_id} | {file_date}\n"
                f"- **Correction**: {correction}\n"
                f"- **Status**: active\n"
            )
            next_id += 1

        if blocks:
            self._run_locked_write(
                evolution_path, "Corrections Captured", "\n".join(blocks)
            )

        logger.info(
            "Wrote %d correction entries to EVOLUTION.md", len(corrections)
        )

    @staticmethod
    def _run_locked_write(
        memory_path: Path,
        section: str,
        text: str,
    ) -> None:
        """Write to MEMORY.md via flock + _modify_content (single lock).

        Deduplicates entries before writing: acquires the lock first, then
        reads existing content and skips entries whose first 60 chars
        already appear.  This prevents double-writes when the distilled
        frontmatter update fails after content extraction succeeds.

        Calls ``_modify_content`` directly under the same flock instead
        of ``locked_read_modify_write`` to avoid a nested-lock deadlock
        (flock is per-open-file-description on POSIX).
        """
        import fcntl as _fcntl
        from scripts.locked_write import _modify_content

        lock_path = memory_path.with_suffix(memory_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        try:
            fd = open(lock_path, "w")  # noqa: SIM115
            _fcntl.flock(fd, _fcntl.LOCK_EX)

            # Read current content under lock
            if memory_path.exists():
                existing = memory_path.read_text(encoding="utf-8")
            else:
                existing = ""

            # Dedup: filter out entries already present
            if existing:
                existing_lower = existing.lower()
                new_lines = []
                for line in text.splitlines():
                    entry_key = line.strip()[:60].lower()
                    if entry_key and entry_key in existing_lower:
                        continue
                    new_lines.append(line)
                if not new_lines:
                    return  # all entries already present
                text = "\n".join(new_lines)

            # Modify + write under the same lock
            new_content = _modify_content(existing, section, text, "prepend")
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            memory_path.write_text(new_content, encoding="utf-8")
        except LockedWriteError as e:
            logger.warning("locked_write failed for section %s: %s", section, e)
        except Exception as e:
            logger.warning("locked_write failed for section %s: %s", section, e)
        finally:
            if fd is not None:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
                except OSError:
                    pass
                fd.close()

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

        lock_path = memory_path.with_suffix(memory_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        try:
            fd = open(lock_path, "w")  # noqa: SIM115  — matches locked_write.py
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                content = memory_path.read_text(encoding="utf-8")
                updated = self._apply_open_thread_updates(content, coe_entries)
                if updated != content:
                    memory_path.write_text(updated, encoding="utf-8")
                    logger.info("Updated Open Threads with %d COE entries", len(coe_entries))
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception as exc:
            logger.warning("Failed to update Open Threads: %s", exc)
        finally:
            if fd is not None:
                fd.close()

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
    def _archive_old_files(da_dir: Path, ws_path: Path) -> int:
        """Move DailyActivity files older than ARCHIVE_DAYS to Knowledge/Archives/.

        Only moves files that are already distilled (distilled: true in
        frontmatter).  Un-distilled old files are left in place as a
        safety net — they'll be distilled first on the next run.

        Returns the number of files archived.
        """
        cutoff = date.today() - timedelta(days=ARCHIVE_DAYS)
        archive_dir = ws_path / "Knowledge" / "Archives"
        archived = 0

        for f in da_dir.glob("*.md"):
            try:
                file_date = date.fromisoformat(f.stem)
                if file_date >= cutoff:
                    continue
            except ValueError:
                continue

            # Only archive distilled files
            try:
                content = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if not _is_distilled(content):
                continue

            # Move to archive (shutil.move handles cross-device moves)
            import shutil
            archive_dir.mkdir(parents=True, exist_ok=True)
            dest = archive_dir / f.name
            if dest.exists():
                logger.debug("Archive target already exists, skipping: %s", dest)
                continue
            shutil.move(str(f), str(dest))
            archived += 1
            logger.debug("Archived %s → %s", f.name, dest)

        return archived

    @staticmethod
    def _enforce_section_caps(memory_path: Path) -> None:
        """Trim MEMORY.md sections to SECTION_CAPS max entries.

        Reads the file, finds each capped section, counts ``- `` prefixed
        lines, and removes the oldest (bottom) entries that exceed the cap.
        Writes back atomically under flock.

        This runs after distillation writes, so the newest entries are at
        the top of each section (prepend mode).  Oldest = bottom = trimmed.
        """
        import fcntl as _fcntl
        from scripts.locked_write import _find_section_range

        if not memory_path.exists():
            return

        lock_path = memory_path.with_suffix(memory_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        try:
            fd = open(lock_path, "w")  # noqa: SIM115  — matches locked_write.py
            _fcntl.flock(fd, _fcntl.LOCK_EX)
            try:
                content = memory_path.read_text(encoding="utf-8")
                modified = False

                for section_name, cap in SECTION_CAPS.items():
                    section_range = _find_section_range(content, section_name)
                    if section_range is None:
                        continue

                    header_end, next_header_pos = section_range
                    section_text = content[header_end:next_header_pos]
                    lines = section_text.splitlines()

                    # Count entry lines (start with "- ")
                    entry_indices = [
                        i for i, line in enumerate(lines)
                        if line.strip().startswith("- ")
                    ]

                    if len(entry_indices) <= cap:
                        continue

                    # Remove oldest entries (bottom of section)
                    to_remove = set(entry_indices[cap:])
                    trimmed_lines = [
                        line for i, line in enumerate(lines)
                        if i not in to_remove
                    ]
                    # Preserve original trailing whitespace between sections
                    new_section = "\n".join(trimmed_lines)
                    if section_text.endswith("\n"):
                        new_section += "\n"
                    content = (
                        content[:header_end]
                        + new_section
                        + content[next_header_pos:]
                    )
                    modified = True
                    removed = len(entry_indices) - cap
                    logger.info(
                        "Capped %s: removed %d oldest entries (cap=%d)",
                        section_name, removed, cap,
                    )

                if modified:
                    memory_path.write_text(content, encoding="utf-8")
            finally:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
        except Exception as exc:
            logger.warning("Section cap enforcement failed: %s", exc)
        finally:
            if fd is not None:
                fd.close()

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
