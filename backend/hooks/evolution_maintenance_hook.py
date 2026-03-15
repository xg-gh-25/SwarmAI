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

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.session_hooks import HookContext

logger = logging.getLogger(__name__)

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
    """Append a single JSONL line to the evolution changelog."""
    line = json.dumps({
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": action,
        "id": entry_id,
        "summary": summary,
        "source": source,
    })
    try:
        with open(changelog_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
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
