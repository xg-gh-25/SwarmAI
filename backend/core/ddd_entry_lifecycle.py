"""DDD Entry Lifecycle — per-entry knowledge tracking, decay, and archival.

Per-entry reference tracking for DDD knowledge documents (primarily IMPROVEMENT.md).
Each bullet entry can have:
- Type classification: [guideline], [pitfall], [decision], [model], [process]
- Reference count: how many times this entry influenced a decision
- Last referenced date: when this entry was last used
- Decay state: active → dormant → archived

Decay rules:
- active → dormant: 90 days without reference (180d if ref >= 10)
- dormant → archived: 90 more days (180 total / 360 for high-ref)
- New entries (< 30 days old): immune to decay (grace period)
- Evergreen sections: entries within are immune

Public API:
    EntryMetadata        — dataclass for per-entry state
    DecayTransition      — dataclass for state change records
    parse_entries(content) → list[EntryMetadata]
    inject_entry_metadata(content, entries) → str
    bump_references(entries, text, today) → int
    assess_decay(entries, today) → list[DecayTransition]
    classify_entry_type(text) → str
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# ── Constants ─────────────────────────────────────────────────────────────────

VALID_TYPES = ("guideline", "pitfall", "decision", "model", "process")
DEFAULT_TYPE = "guideline"

# Grace period: new entries are immune to decay
GRACE_PERIOD_DAYS = 30

# Decay thresholds (days since last reference)
DORMANT_THRESHOLD_DAYS = 90
ARCHIVED_THRESHOLD_DAYS = 180

# High-ref entries (ref >= HIGH_REF_THRESHOLD) get extended grace (2x)
HIGH_REF_THRESHOLD = 10
HIGH_REF_MULTIPLIER = 2

# Type classification signal words (order matters — first match wins)
# NOTE: Signals must be distinctive. Common words like "→", "pipeline", "step"
# appear in ALL entry types and shouldn't trigger classification alone.
_TYPE_SIGNALS: dict[str, list[str]] = {
    "pitfall": ["bug", "broke", "break", "failed", "failure", "regression",
                "race condition", "silent", "crash", "hang", "corrupt",
                "anti-pattern", "wrong", "mistake"],
    "decision": ["chose", "chosen", "selected", "instead of",
                 "approach:", "vs ", "trade-off", "tradeoff",
                 "we decided", "architecture decision"],
    "process": ["workflow:", "state machine:", "lifecycle:",
                "sequence of steps", "procedure:", "protocol:"],
    "model": ["entity", "schema", "field", "relationship", "data structure",
              "data model", "table schema"],
    # guideline is the fallback — most entries are lessons/recommendations
    "guideline": ["pattern:", "rule:", "lesson:", "should", "prefer",
                  "always", "never", "must", "principle", "best practice",
                  "roi", "saves", "prevents", "eliminates", "tip:"],
}

# Regex for entry bullet with optional type prefix
# Matches: "- [type] **Title** — description (date, run)"
# Or:      "- **Title** — description (date, run)"
_ENTRY_RE = re.compile(
    r"^- (?:\[(\w+)\] )?\*\*(.+?)\*\*"
)

# Regex for inline metadata comment
# Matches: "  <!-- ref:N | last:YYYY-MM-DD | decay:state -->"
_META_RE = re.compile(
    r"^\s*<!-- ref:(\d+) \| last:([\w\-]+) \| decay:(\w+) -->$"
)

# Regex for date extraction from entry text "(YYYY-MM-DD, ...)"
_DATE_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})")


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class EntryMetadata:
    """Per-entry knowledge lifecycle state."""
    title: str
    entry_type: str = DEFAULT_TYPE
    ref_count: int = 0
    last_referenced: Optional[date] = None
    decay_state: str = "active"  # active | dormant | archived
    created_date: Optional[date] = None
    section: str = ""  # Which ## section this belongs to
    line_number: int = 0  # Line in the file (for injection)
    raw_text: str = ""  # Full bullet text (for archival)

    def __post_init__(self):
        if self.entry_type not in VALID_TYPES:
            self.entry_type = DEFAULT_TYPE
        if self.decay_state not in ("active", "dormant", "archived"):
            self.decay_state = "active"

    def to_comment(self) -> str:
        """Serialize to inline metadata HTML comment."""
        last_str = (
            self.last_referenced.isoformat()
            if self.last_referenced
            else "none"
        )
        return f"  <!-- ref:{self.ref_count} | last:{last_str} | decay:{self.decay_state} -->"


@dataclass
class DecayTransition:
    """Record of a decay state change."""
    entry: EntryMetadata
    old_state: str
    new_state: str
    reason: str


# ── Public API ────────────────────────────────────────────────────────────────


def classify_entry_type(text: str) -> str:
    """Classify a knowledge entry's type from its text content.

    Uses signal word matching with priority ordering:
    1. pitfall (strongest signals — bug/failure language)
    2. decision (chose/selected language)
    3. guideline (pattern/rule/lesson — most common)
    4. model/process (rare, very specific signals)
    5. Default: guideline (safe for ambiguous cases)

    Ambiguous entries default to 'guideline' since most lessons are
    recommendations/best practices.
    """
    text_lower = text.lower()

    # Priority 1: pitfall signals are strong and unambiguous
    for signal in _TYPE_SIGNALS["pitfall"]:
        if signal in text_lower:
            return "pitfall"

    # Priority 2: decision signals
    for signal in _TYPE_SIGNALS["decision"]:
        if signal in text_lower:
            return "decision"

    # Priority 3: guideline (most common — lessons, patterns, rules)
    for signal in _TYPE_SIGNALS["guideline"]:
        if signal in text_lower:
            return "guideline"

    # Priority 4: process and model (rare, need very specific signals)
    for signal in _TYPE_SIGNALS["process"]:
        if signal in text_lower:
            return "process"
    for signal in _TYPE_SIGNALS["model"]:
        if signal in text_lower:
            return "model"

    return DEFAULT_TYPE


def parse_entries(content: str) -> list[EntryMetadata]:
    """Parse all knowledge entries from DDD markdown content.

    Extracts entries from bullet lists (- **Title** ...) with optional
    [type] prefix and optional inline metadata comment.

    Returns list of EntryMetadata in document order.
    """
    if not content or not content.strip():
        return []

    lines = content.splitlines()
    entries: list[EntryMetadata] = []
    current_section = ""
    i = 0

    while i < len(lines):
        line = lines[i]

        # Track section headers
        if line.startswith("## ") and not line.startswith("### "):
            current_section = line[3:].strip()
            i += 1
            continue

        # Check for entry bullet
        m = _ENTRY_RE.match(line)
        if m:
            entry_type = m.group(1) or ""
            title = m.group(2)

            # Extract created_date from entry text
            created_date = _extract_date(line)

            # Collect full entry text (may span multiple lines until next - or <!-- or ##)
            raw_lines = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if next_line.startswith("- ") or next_line.startswith("## "):
                    break
                if _META_RE.match(next_line):
                    break
                if next_line.strip() == "":
                    # Blank line might separate entries
                    # Look ahead: if next non-blank is a new entry or section, stop
                    k = j + 1
                    while k < len(lines) and lines[k].strip() == "":
                        k += 1
                    if k >= len(lines) or lines[k].startswith("- ") or lines[k].startswith("## "):
                        break
                raw_lines.append(next_line)
                j += 1

            raw_text = "\n".join(raw_lines)

            # Check for metadata comment on next line
            ref_count = 0
            last_referenced = None
            decay_state = "active"

            meta_line_idx = j
            if meta_line_idx < len(lines):
                meta_match = _META_RE.match(lines[meta_line_idx])
                if meta_match:
                    ref_count = int(meta_match.group(1))
                    last_str = meta_match.group(2)
                    if last_str != "none":
                        try:
                            last_referenced = date.fromisoformat(last_str)
                        except ValueError:
                            last_referenced = None
                    decay_state = meta_match.group(3)
                    j = meta_line_idx + 1  # Skip the metadata line

            # Classify type if not explicitly tagged
            if not entry_type:
                entry_type = classify_entry_type(raw_text)

            entries.append(EntryMetadata(
                title=title,
                entry_type=entry_type,
                ref_count=ref_count,
                last_referenced=last_referenced,
                decay_state=decay_state,
                created_date=created_date,
                section=current_section,
                line_number=i,
                raw_text=raw_text,
            ))
            i = j
        else:
            i += 1

    return entries


def inject_entry_metadata(content: str, entries: list[EntryMetadata]) -> str:
    """Write/update inline metadata comments for entries in content.

    For each entry in `entries`, finds the matching bullet by title and
    adds or replaces the metadata comment line immediately after the entry.

    Returns updated content string.
    """
    if not content or not entries:
        return content

    lines = content.splitlines()
    result_lines: list[str] = []
    # Key by (title, section) to handle duplicate titles across sections
    entry_map: dict[tuple[str, str], EntryMetadata] = {}
    for e in entries:
        entry_map[(e.title, e.section)] = e
    # Also build title-only fallback for entries without section context
    title_map: dict[str, EntryMetadata] = {}
    for e in entries:
        if e.title not in title_map:
            title_map[e.title] = e
    skip_next_meta = False
    current_section = ""
    i = 0

    while i < len(lines):
        line = lines[i]

        # Track section headers for section-aware lookup
        if line.startswith("## ") and not line.startswith("### "):
            current_section = line[3:].strip()
            result_lines.append(line)
            i += 1
            continue

        if skip_next_meta:
            # Check if this line is an old metadata comment to skip
            if _META_RE.match(line):
                i += 1
                skip_next_meta = False
                continue
            skip_next_meta = False

        # Check if this is an entry bullet
        m = _ENTRY_RE.match(line)
        if m:
            title = m.group(2)
            result_lines.append(line)

            # Lookup: prefer (title, section), fall back to title-only
            entry = entry_map.get((title, current_section)) or title_map.get(title)
            if entry:
                # Collect continuation lines (indented, non-metadata, non-entry)
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if next_line.startswith("- ") or next_line.startswith("## "):
                        break
                    if _META_RE.match(next_line):
                        # Skip old metadata — we'll inject new one
                        i += 1
                        break
                    if next_line.strip() == "":
                        # Keep blank line but check if metadata follows
                        peek = i + 1
                        if peek < len(lines) and _META_RE.match(lines[peek]):
                            result_lines.append(next_line)
                            i = peek + 1  # Skip old metadata
                            break
                        # Check if next content is a new entry or section
                        peek2 = i + 1
                        while peek2 < len(lines) and lines[peek2].strip() == "":
                            peek2 += 1
                        if peek2 >= len(lines) or lines[peek2].startswith("- ") or lines[peek2].startswith("## "):
                            break
                    result_lines.append(next_line)
                    i += 1

                # Inject new metadata comment
                result_lines.append(entry.to_comment())
                continue
            else:
                i += 1
                continue
        else:
            result_lines.append(line)
            i += 1

    # Preserve trailing newline
    trailing = content.endswith("\n")
    result = "\n".join(result_lines)
    if trailing and not result.endswith("\n"):
        result += "\n"
    return result


def bump_references(
    entries: list[EntryMetadata], text: str, today: date
) -> int:
    """Bump reference count for entries whose titles appear in text.

    Uses case-insensitive title match with minimum length guard (15 chars)
    to prevent false positives on short titles like "Build" or "API".
    Mutates entries in-place.
    """
    text_lower = text.lower()
    bumped = 0

    # Minimum title length to prevent false positives from common short words
    _MIN_TITLE_LEN = 15

    for entry in entries:
        title_lower = entry.title.lower()
        if len(title_lower) < _MIN_TITLE_LEN:
            continue  # Skip short titles — too many false positives
        if title_lower in text_lower:
            entry.ref_count += 1
            entry.last_referenced = today
            if entry.decay_state == "dormant":
                entry.decay_state = "active"  # Revive
            bumped += 1

    return bumped


def assess_decay(
    entries: list[EntryMetadata],
    today: date,
    evergreen_sections: set[str] | None = None,
) -> list[DecayTransition]:
    """Assess decay state for all entries. Returns transitions to apply.

    Decay rules:
    - Evergreen sections: entries within are immune (never decay)
    - Grace period: entries < 30 days old are immune
    - active → dormant: 90 days since last_referenced (180 if ref >= 10)
    - dormant → archived: 90 more days (180 total / 360 for high-ref)
    - Entries already archived are skipped
    - Entries with no date info are treated as infinitely old (decay immediately)
    """
    transitions: list[DecayTransition] = []
    _evergreen = evergreen_sections or set()

    for entry in entries:
        if entry.decay_state == "archived":
            continue

        # Evergreen section immunity
        if entry.section in _evergreen:
            continue

        # Grace period for new entries
        if entry.created_date:
            age_days = (today - entry.created_date).days
            if age_days < GRACE_PERIOD_DAYS:
                continue

        # Determine effective thresholds
        dormant_threshold = DORMANT_THRESHOLD_DAYS
        archived_threshold = ARCHIVED_THRESHOLD_DAYS
        if entry.ref_count >= HIGH_REF_THRESHOLD:
            dormant_threshold *= HIGH_REF_MULTIPLIER
            archived_threshold *= HIGH_REF_MULTIPLIER

        # Calculate days since last reference
        if entry.last_referenced:
            days_since_ref = (today - entry.last_referenced).days
        elif entry.created_date:
            days_since_ref = (today - entry.created_date).days
        else:
            # No date info — treat as infinitely old (triggers decay)
            days_since_ref = archived_threshold + 1

        # Check transitions
        if entry.decay_state == "active":
            if days_since_ref >= dormant_threshold:
                transitions.append(DecayTransition(
                    entry=entry,
                    old_state="active",
                    new_state="dormant",
                    reason=f"{days_since_ref}d since last reference (threshold: {dormant_threshold}d)",
                ))
        elif entry.decay_state == "dormant":
            if days_since_ref >= archived_threshold:
                transitions.append(DecayTransition(
                    entry=entry,
                    old_state="dormant",
                    new_state="archived",
                    reason=f"{days_since_ref}d since last reference (threshold: {archived_threshold}d)",
                ))

    return transitions


def archive_entries(
    project_dir: "Path", entries: list[EntryMetadata]
) -> int:
    """Move entries to IMPROVEMENT-archive.md and return count archived.

    Creates archive file if it doesn't exist. Appends entries with their
    full raw_text + metadata comment. Marks entries as 'archived' in-place.

    Args:
        project_dir: Path to the project directory (e.g., Projects/SwarmAI/)
        entries: Entries to archive (should be dormant or otherwise marked)

    Returns:
        Number of entries successfully archived.
    """
    from pathlib import Path as _Path

    if not entries:
        return 0

    archive_path = _Path(project_dir) / "IMPROVEMENT-archive.md"

    # Build archive content to append
    archive_lines: list[str] = []
    for entry in entries:
        # Use raw_text if available, otherwise reconstruct
        if entry.raw_text:
            archive_lines.append(entry.raw_text)
        else:
            archive_lines.append(
                f"- [{entry.entry_type}] **{entry.title}** — (archived)"
            )
        # Add metadata with archived state
        entry.decay_state = "archived"
        archive_lines.append(entry.to_comment())
        archive_lines.append("")  # Blank separator

    new_content = "\n".join(archive_lines)

    # Write to archive file (create or append)
    if archive_path.exists():
        existing = archive_path.read_text(encoding="utf-8")
        if not existing.endswith("\n"):
            existing += "\n"
        archive_path.write_text(
            existing + "\n" + new_content, encoding="utf-8"
        )
    else:
        header = "# Archived Knowledge Entries\n\n"
        header += "_Entries archived by the Knowledge Lifecycle decay engine. "
        header += "These entries had no references for 180+ days._\n\n"
        archive_path.write_text(
            header + new_content, encoding="utf-8"
        )

    return len(entries)


# ── Stage Knowledge Injection ─────────────────────────────────────────────────

# Pipeline stages get type-filtered knowledge sorted by relevance (ref count).
# Each stage has an affinity map: {type: max_entries_of_that_type}
STAGE_KNOWLEDGE_AFFINITY: dict[str, dict[str, int]] = {
    "evaluate": {"decision": 5, "model": 3, "process": 2},
    "think":    {"decision": 5, "guideline": 3, "pitfall": 2},
    "plan":     {"decision": 3, "model": 3, "process": 5},
    "build":    {"guideline": 7, "pitfall": 5, "decision": 2},
    "review":   {"pitfall": 7, "guideline": 5},
    "test":     {"pitfall": 5, "guideline": 3},
    "deliver":  {"process": 3, "guideline": 2},
    "reflect":  {"guideline": 3, "pitfall": 2},
}


def get_stage_knowledge(
    entries: list[EntryMetadata], stage: str
) -> list[EntryMetadata]:
    """Get type-filtered, relevance-sorted entries for a pipeline stage.

    Returns entries matching the stage's type affinity, sorted by ref_count
    descending (most-referenced first). Excludes dormant and archived entries.

    Args:
        entries: All parsed entries from IMPROVEMENT.md
        stage: Pipeline stage name (lowercase: evaluate, think, plan, build, etc.)

    Returns:
        Filtered + sorted list of entries for injection into the stage context.
    """
    affinity = STAGE_KNOWLEDGE_AFFINITY.get(stage.lower())
    if not affinity:
        return []

    # Filter: only active entries
    active = [e for e in entries if e.decay_state == "active"]

    # Group by type, sort each group by ref_count descending
    result: list[EntryMetadata] = []
    for entry_type, max_count in affinity.items():
        typed = [e for e in active if e.entry_type == entry_type]
        typed.sort(key=lambda e: e.ref_count, reverse=True)
        result.extend(typed[:max_count])

    # Final sort: highest ref_count first across all types
    result.sort(key=lambda e: e.ref_count, reverse=True)
    return result


# ── Private Helpers ───────────────────────────────────────────────────────────


def _extract_date(text: str) -> Optional[date]:
    """Extract the first YYYY-MM-DD date from entry text."""
    m = _DATE_RE.search(text)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    return None
