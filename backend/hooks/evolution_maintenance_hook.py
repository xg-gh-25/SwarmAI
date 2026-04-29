"""Post-session evolution maintenance hook.

Runs at session close to perform code-enforced EVOLUTION.md housekeeping
that was previously prompt-dependent (and never fired in practice):

- ``EvolutionMaintenanceHook``  — Implements ``SessionLifecycleHook``.
  Scans EVOLUTION.md entries, deprecates idle entries (>30 days),
  prunes deprecated entries with zero usage, and logs all actions
  to EVOLUTION_CHANGELOG.jsonl.

This hook uses ``locked_write.py`` functions directly (imported as a
library) rather than shelling out, for atomicity and testability.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.session_hooks import HookContext

logger = logging.getLogger(__name__)


def _resolve_transcripts_dir(base_dir: Path) -> Path:
    """Resolve the transcript directory with most-recent-activity heuristic.

    Instead of picking the first alphabetically-sorted subdirectory,
    find the subdir whose most recent .jsonl file has the latest mtime.
    Falls back to ``base_dir`` if no subdirs contain .jsonl files.
    """
    if not base_dir.is_dir():
        return base_dir

    best_dir = None
    best_mtime = 0.0

    for subdir in base_dir.iterdir():
        if not subdir.is_dir():
            continue
        jsonl_files = list(subdir.glob("*.jsonl"))
        if not jsonl_files:
            continue
        # Find the most recent .jsonl in this subdir
        latest_mtime = max(f.stat().st_mtime for f in jsonl_files)
        if latest_mtime > best_mtime:
            best_mtime = latest_mtime
            best_dir = subdir

    return best_dir if best_dir is not None else base_dir


# Sections that contain entries with Status + Usage Count fields
_MANAGED_SECTIONS = [
    ("Capabilities Built", "E"),
    ("Competence Learned", "K"),
]

# Regex to match entry headers: ### E001 | reactive | skill | 2026-03-07
_ENTRY_HEADER_RE = re.compile(
    r"^###\s+([EOKCF]\d{3})\s*\|.*\|\s*(\d{4}-\d{2}-\d{2})\s*$",
    re.MULTILINE,
)

# Regex to extract a field value: - **Field Name**: value
_FIELD_RE_TEMPLATE = r"^- \*\*{field}\*\*:\s*(.+)$"


def _get_field(entry_block: str, field_name: str) -> str | None:
    """Extract a field value from an entry block."""
    pattern = re.compile(
        _FIELD_RE_TEMPLATE.format(field=re.escape(field_name)),
        re.MULTILINE,
    )
    match = pattern.search(entry_block)
    return match.group(1).strip() if match else None


def _parse_entries(content: str, section_name: str) -> list[dict]:
    """Parse all entries in a section into structured dicts.

    Returns a list of dicts with keys: id, date, status, usage_count,
    start_pos, end_pos, block.
    """
    from scripts.locked_write import _find_section_range

    section_range = _find_section_range(content, section_name)
    if section_range is None:
        return []

    header_end, next_section_pos = section_range
    section_text = content[header_end:next_section_pos]

    entries = []
    # Find all ### headers in this section
    headers = list(_ENTRY_HEADER_RE.finditer(section_text))

    for i, match in enumerate(headers):
        entry_id = match.group(1)
        date_str = match.group(2)
        entry_start = match.start()
        entry_end = headers[i + 1].start() if i + 1 < len(headers) else len(section_text)
        block = section_text[entry_start:entry_end]

        status = _get_field(block, "Status") or "active"
        usage_str = _get_field(block, "Usage Count") or "0"
        try:
            usage_count = int(usage_str)
        except ValueError:
            usage_count = 0

        entries.append({
            "id": entry_id,
            "date": date_str,
            "status": status,
            "usage_count": usage_count,
            "block": block,
        })

    return entries


def _append_changelog(
    changelog_path: Path,
    action: str,
    entry_id: str,
    summary: str,
    source: str = "maintenance_hook",
) -> None:
    """Append a single JSONL line to the evolution changelog.

    Uses ``fcntl.flock`` on a ``.jsonl.lock`` sidecar file to prevent
    concurrent writes from corrupting the changelog (P0 fix, Req 5.1).
    """
    line = json.dumps({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": action,
        "id": entry_id,
        "summary": summary,
        "source": source,
    })
    lock_path = changelog_path.with_suffix(".jsonl.lock")
    try:
        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                with open(changelog_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except OSError as exc:
        logger.warning("Failed to append changelog: %s", exc)


class EvolutionMaintenanceHook:
    """Code-enforced EVOLUTION.md maintenance at session close.

    Performs three operations:
    1. Deprecation — entries with status=active idle >deprecation_days → deprecated
    2. Pruning — entries with status=deprecated + usage_count=0 + deprecated >30 days → removed
    3. Changelog — all actions logged to EVOLUTION_CHANGELOG.jsonl

    Uses locked_write.py's _set_field for atomic field updates.
    """

    name = "evolution_maintenance"

    def __init__(self, context_dir: Path | None = None, deprecation_days: int = 30) -> None:
        self._context_dir = context_dir
        self._deprecation_days = deprecation_days

    def _resolve_context_dir(self) -> Path | None:
        """Resolve the .context directory path."""
        if self._context_dir:
            return self._context_dir
        # Default: ~/.swarm-ai/SwarmWS/.context/
        home = Path.home()
        ctx = home / ".swarm-ai" / "SwarmWS" / ".context"
        return ctx if ctx.is_dir() else None

    async def execute(self, context: HookContext) -> None:
        """Run maintenance on EVOLUTION.md at session close."""
        ctx_dir = self._resolve_context_dir()
        if ctx_dir is None:
            logger.debug("No .context directory found, skipping evolution maintenance")
            return

        evo_path = ctx_dir / "EVOLUTION.md"
        if not evo_path.is_file():
            return

        changelog_path = ctx_dir / "EVOLUTION_CHANGELOG.jsonl"
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=self._deprecation_days)

        try:
            content = evo_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read EVOLUTION.md: %s", exc)
            return

        # Quality gate: remove garbage entries BEFORE deprecation checks
        content = self._quality_gate(evo_path, content, changelog_path)

        deprecated_count = 0
        pruned_count = 0

        for section_name, _prefix in _MANAGED_SECTIONS:
            entries = _parse_entries(content, section_name)

            for entry in entries:
                try:
                    entry_date = datetime.strptime(
                        entry["date"], "%Y-%m-%d"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                # Deprecation: active + idle > cutoff + usage_count == 0
                if (
                    entry["status"] == "active"
                    and entry_date < cutoff
                    and entry["usage_count"] == 0
                ):
                    self._deprecate_entry(
                        evo_path, section_name, entry["id"], changelog_path
                    )
                    deprecated_count += 1
                    # Re-read content after modification
                    content = evo_path.read_text(encoding="utf-8")

                # Pruning: deprecated + usage_count == 0 + old enough
                elif (
                    entry["status"] == "deprecated"
                    and entry["usage_count"] == 0
                    and entry_date < cutoff
                ):
                    self._prune_entry(
                        evo_path, section_name, entry["id"], changelog_path
                    )
                    pruned_count += 1
                    # Re-read content after modification
                    content = evo_path.read_text(encoding="utf-8")

        if deprecated_count or pruned_count:
            logger.info(
                "Evolution maintenance: deprecated=%d, pruned=%d",
                deprecated_count,
                pruned_count,
            )

        # Run evolution cycle weekly (check last run date)
        await self._maybe_run_evolution(ctx_dir)

    # Regex: commit hash pattern (7+ hex chars at the start of description)
    _COMMIT_HASH_RE = re.compile(r"^[a-f0-9]{7}")

    def _quality_gate(
        self, evo_path: Path, content: str, changelog_path: Path
    ) -> str:
        """Quality gate: remove garbage entries and fix duplicate IDs.

        Called from execute() BEFORE deprecation checks.

        1. Acquire flock on EVOLUTION.md
        2. Re-read file content (authoritative under lock)
        3. Parse "Competence Learned" — remove entries where description
           is <20 chars OR starts with a commit hash pattern.
        4. Parse "Corrections Captured" — detect duplicate IDs, renumber
           the later occurrence to the next available C ID.
        5. Log all removals/renumbers to EVOLUTION_CHANGELOG.jsonl.
        6. Write back and release lock.

        Returns the (possibly modified) content string.
        """
        from scripts.locked_write import _find_section_range, _find_entry_in_section
        import fcntl as _fcntl

        lock_path = evo_path.with_suffix(evo_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = None
        try:
            fd = open(lock_path, "w")
            _fcntl.flock(fd, _fcntl.LOCK_EX)

            # Re-read under lock — authoritative content
            try:
                content = evo_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Cannot read EVOLUTION.md under lock: %s", exc)
                return content

            modified = False

            # ── Step 1: Clean garbage competence entries ──
            competence_entries = _parse_entries(content, "Competence Learned")
            garbage_ids: list[str] = []

            for entry in competence_entries:
                desc = _get_field(entry["block"], "Competence") or ""
                # Garbage if description has <3 words (PE-review: char count
                # was fragile — "Use uv" is 6 chars but 2 words and legitimate.
                # Word count better separates garbage from terse-but-real entries.)
                if len(desc.split()) < 3:
                    garbage_ids.append(entry["id"])
                    continue
                # Garbage if starts with commit hash pattern
                if self._COMMIT_HASH_RE.match(desc):
                    garbage_ids.append(entry["id"])
                    continue

            # Remove garbage entries (reverse order to preserve positions)
            for entry_id in reversed(garbage_ids):
                entry_range = _find_entry_in_section(content, "Competence Learned", entry_id)
                if entry_range is not None:
                    start, end = entry_range
                    content = content[:start] + content[end:]
                    modified = True
                    _append_changelog(
                        changelog_path, "quality_gate_remove", entry_id,
                        f"Removed garbage competence entry (short or commit-hash)",
                        source="quality_gate",
                    )
                    logger.debug("Quality gate: removed garbage competence %s", entry_id)

            # ── Step 2: Fix duplicate correction IDs ──
            corrections = _parse_entries(content, "Corrections Captured")
            seen_ids: dict[str, bool] = {}
            # Find max existing C-ID for renumbering
            all_c_ids = re.findall(r"### C(\d+)", content)
            next_c_num = max((int(x) for x in all_c_ids), default=0) + 1

            for entry in corrections:
                eid = entry["id"]
                if eid in seen_ids:
                    # Duplicate — renumber to next available
                    new_id = f"C{next_c_num:03d}"
                    # Find this specific duplicate occurrence in the content.
                    # Use _find_entry_in_section to locate the entry block precisely,
                    # then replace the header within that block.  This handles 3+
                    # duplicates correctly because each iteration re-parses content.
                    entry_range = _find_entry_in_section(content, "Corrections Captured", eid)
                    if entry_range is not None:
                        start, end = entry_range
                        block = content[start:end]
                        # Only rename the LAST occurrence of this ID in the section
                        # (first occurrence keeps the original ID)
                        all_positions = [m.start() + start for m in re.finditer(
                            re.escape(f"### {eid} "), content
                        )]
                        if len(all_positions) >= 2:
                            # Replace at the last position (preserves first occurrence)
                            last_pos = all_positions[-1]
                            old_header = f"### {eid} "
                            new_header = f"### {new_id} "
                            content = (
                                content[:last_pos]
                                + new_header
                                + content[last_pos + len(old_header):]
                            )
                            modified = True
                            _append_changelog(
                                changelog_path, "quality_gate_renumber", new_id,
                                f"Renumbered duplicate {eid} -> {new_id}",
                                source="quality_gate",
                            )
                            logger.debug(
                                "Quality gate: renumbered duplicate %s -> %s",
                                eid, new_id,
                            )
                            next_c_num += 1
                else:
                    seen_ids[eid] = True

            if modified:
                evo_path.write_text(content, encoding="utf-8")

        finally:
            if fd is not None:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
                except OSError:
                    pass
                fd.close()

        return content

    def _deprecate_entry(
        self, evo_path: Path, section: str, entry_id: str, changelog_path: Path
    ) -> None:
        """Set an entry's Status to deprecated via locked_write."""
        from scripts.locked_write import locked_field_modify, LockedWriteError
        try:
            locked_field_modify(
                evo_path, section, entry_id, "Status", "set-field", "deprecated"
            )
            _append_changelog(
                changelog_path, "deprecate", entry_id,
                f"Auto-deprecated: idle >{self._deprecation_days}d with 0 usage"
            )
            logger.debug("Deprecated %s in %s", entry_id, section)
        except (ValueError, LockedWriteError) as exc:
            logger.warning("Failed to deprecate %s: %s", entry_id, exc)

    async def _maybe_run_evolution(self, ctx_dir: Path) -> None:
        """Run the evolution cycle if >7 days since last run.

        Checks ``.context/.evolution_last_run`` for the last run date.
        If >7 days ago (or file doesn't exist), runs ``run_evolution_cycle()``.
        Writes today's date to the state file after a successful run.

        The heavy work (mining transcripts + LLM calls) runs in a thread
        pool to avoid blocking the asyncio event loop.

        Evolution failure never blocks session close -- all errors are caught.
        """
        state_file = ctx_dir / ".evolution_last_run"
        now = datetime.now(timezone.utc)
        run_interval_days = 7

        try:
            if state_file.exists():
                last_run_str = state_file.read_text(encoding="utf-8").strip()
                try:
                    last_run = datetime.strptime(last_run_str, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    last_run = datetime.min.replace(tzinfo=timezone.utc)

                days_since = (now - last_run).days
                if days_since < run_interval_days:
                    logger.debug(
                        "Evolution cycle: %d days since last run (threshold %d), skipping",
                        days_since,
                        run_interval_days,
                    )
                    return
        except OSError as exc:
            logger.debug("Cannot read evolution state file: %s", exc)

        # Time to run the evolution cycle
        logger.info("Evolution cycle: triggering (>%d days since last run)", run_interval_days)
        try:
            from core.evolution_optimizer import run_evolution_cycle

            # Resolve skills_dir: use the backend module's own location
            # (Path(__file__) → hooks/ → parent → backend/)
            backend_dir = Path(__file__).resolve().parent.parent
            skills_dir = backend_dir / "skills"
            if not skills_dir.is_dir():
                logger.debug("Evolution cycle: skills_dir not found at %s, skipping", skills_dir)
                return

            # Transcripts directory: Claude Code session transcripts
            # Pass the base projects/ dir so rglob("*.jsonl") in
            # SessionMiner._iter_transcripts finds ALL transcripts
            # across all project subdirectories (Gap 2 fix).
            transcripts_dir = Path.home() / ".claude" / "projects"

            evals_dir = ctx_dir / "SkillEvals"

            # CRITICAL: run_evolution_cycle is CPU+I/O heavy (mines 1000+
            # transcripts, calls Bedrock LLM). Running it synchronously
            # inside this async hook blocks the event loop for minutes,
            # freezing FastAPI, SSE streams, and health checks — causing
            # "Backend crash" on the frontend. Offload to thread pool.
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, run_evolution_cycle, skills_dir, transcripts_dir, evals_dir
            )
            logger.info("Evolution cycle complete: %s", result.to_dict())

            # Write today's date to state file ONLY if cycle actually ran
            # (not lock-rejected or errored — prevents resetting the 7-day
            # interval when no work was done)
            if not result.errors:
                state_file.write_text(
                    now.strftime("%Y-%m-%d"), encoding="utf-8"
                )
        except Exception as exc:
            logger.warning("Evolution cycle failed (non-blocking): %s", exc)

    def _prune_entry(
        self, evo_path: Path, section: str, entry_id: str, changelog_path: Path
    ) -> None:
        """Remove a deprecated entry from EVOLUTION.md with file locking."""
        from scripts.locked_write import _find_entry_in_section, LOCK_TIMEOUT
        import fcntl

        lock_path = evo_path.with_suffix(evo_path.suffix + ".lock")
        fd = None
        try:
            fd = open(lock_path, "w")
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        logger.warning("Lock timeout pruning %s", entry_id)
                        return
                    time.sleep(0.1)

            content = evo_path.read_text(encoding="utf-8")
            entry_range = _find_entry_in_section(content, section, entry_id)
            if entry_range is None:
                return

            start, end = entry_range
            new_content = content[:start] + content[end:]
            evo_path.write_text(new_content, encoding="utf-8")

            _append_changelog(
                changelog_path, "prune", entry_id,
                f"Auto-pruned: deprecated + 0 usage + idle >{self._deprecation_days}d"
            )
            logger.debug("Pruned %s from %s", entry_id, section)
        except OSError as exc:
            logger.warning("Failed to prune %s: %s", entry_id, exc)
        finally:
            if fd is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                fd.close()
