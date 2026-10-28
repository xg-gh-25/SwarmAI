"""Post-session EVOLUTION.md maintenance report (read-only).

- ``EvolutionMaintenanceHook`` -- implements ``SessionLifecycleHook``. At session
  close it counts active entries idle past ``deprecation_days`` with zero usage
  and logs the count. It never changes EVOLUTION.md: CDF's ingestion writer is
  the only autonomous writer (design 2026-10-26 §8), and the prompt is bounded by
  the §12 injection selector, not by evicting entries.
- ``_parse_entries`` -- the ``### <ID> | <date>`` block reader it uses.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.session_hooks import HookContext

logger = logging.getLogger(__name__)


_MANAGED_SECTIONS = [
    ("Capabilities Built", "E"),
    ("Competence Learned", "K"),
]

# Regex to match entry headers in both formats:
#   Old: ### E001 | reactive | skill | 2026-03-07
#   New: ### E001 | 2026-03-07
_ENTRY_HEADER_RE = re.compile(
    r"^###\s+([EOKCF]\d{3})\s*\|.*?(\d{4}-\d{2}-\d{2})\s*$",
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

def _find_section_range(content: str, section: str):
    """Find the start and end positions of a markdown section.

    Looks for a ``## Section Name`` header and returns the range from
    the end of that header line to the start of the next ``##`` header
    (or end of file).

    Returns:
        tuple[int, int] | None: (insert_pos, next_header_pos) or None
            if the section is not found.  ``insert_pos`` is the position
            right after the section header line (including its newline).
            ``next_header_pos`` is the start of the next ``##`` header
            or len(content).
    """
    # Strip leading "## " prefix if present, then match exactly
    clean_section = re.sub(r"^#+\s*", "", section).strip()
    pattern = re.compile(
        r"^(##\s+" + re.escape(clean_section) + r")\s*$",
        re.MULTILINE,
    )
    match = pattern.search(content)
    if match is None:
        return None

    # Position right after the header line
    header_end = match.end()
    # Skip the newline after the header if present
    if header_end < len(content) and content[header_end] == "\n":
        header_end += 1

    # Find the next ## header or end of file
    next_header = re.search(r"^##\s+", content[header_end:], re.MULTILINE)
    if next_header:
        next_header_pos = header_end + next_header.start()
    else:
        next_header_pos = len(content)

    return (header_end, next_header_pos)


def _parse_entries(content: str, section_name: str) -> list[dict]:
    """Parse all entries in a section into structured dicts.

    Returns a list of dicts with keys: id, date, status, usage_count,
    start_pos, end_pos, block.
    """
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
            # Non-numeric values like "Daily", "Weekly", "Occasional" mean
            # the capability IS actively used — treat as high count to prevent
            # accidental deprecation.
            usage_count = 999 if usage_str.strip() else 0

        entries.append({
            "id": entry_id,
            "date": date_str,
            "status": status,
            "usage_count": usage_count,
            "block": block,
        })

    return entries


class EvolutionMaintenanceHook:
    """Read-only EVOLUTION.md maintenance report at session close."""

    name = "evolution_maintenance"

    def __init__(self, context_dir: Path | None = None, deprecation_days: int = 30) -> None:
        self._context_dir = context_dir
        self._deprecation_days = deprecation_days

    def _resolve_context_dir(self) -> Path | None:
        """Resolve the .context directory path."""
        if self._context_dir:
            return self._context_dir
        # Default: <SwarmWS>/.context/
        from config import get_swarmws
        ctx = get_swarmws() / ".context"
        return ctx if ctx.is_dir() else None

    async def execute(self, context: HookContext) -> None:
        """Report maintenance candidates without mutating EVOLUTION.md."""
        ctx_dir = self._resolve_context_dir()
        if ctx_dir is None:
            return
        evo_path = ctx_dir / "EVOLUTION.md"
        if not evo_path.is_file():
            return
        try:
            content = evo_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read EVOLUTION.md: %s", exc)
            return
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._deprecation_days)
        stale = 0
        for section_name, _prefix in _MANAGED_SECTIONS:
            for entry in _parse_entries(content, section_name):
                try:
                    entry_date = datetime.strptime(
                        entry["date"], "%Y-%m-%d"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if entry_date < cutoff and entry["usage_count"] == 0:
                    stale += 1
        if stale:
            logger.info("Evolution maintenance: %d read-only candidate(s)", stale)
