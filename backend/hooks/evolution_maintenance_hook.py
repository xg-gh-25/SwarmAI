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
        self._maybe_run_evolution(ctx_dir)

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

    def _maybe_run_evolution(self, ctx_dir: Path) -> None:
        """Run the evolution cycle if >7 days since last run.

        Checks ``.context/.evolution_last_run`` for the last run date.
        If >7 days ago (or file doesn't exist), runs ``run_evolution_cycle()``.
        Writes today's date to the state file after a successful run.

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
            transcripts_dir = Path.home() / ".claude" / "projects"
            # Find the project-specific subdir with the most recent .jsonl
            transcripts_dir = _resolve_transcripts_dir(transcripts_dir)

            evals_dir = ctx_dir / "SkillEvals"

            summary = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
            logger.info("Evolution cycle complete: %s", summary)

            # Write today's date to state file
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
