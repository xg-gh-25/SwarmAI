"""Migrate MEMORY.md entries to DDD lifecycle format.

Transforms:
  - YYYY-MM-DD: **Title** — body
  - YYYY-MM-DD: **Title** — body (detail)
To:
  - [type] **Title** — body (YYYY-MM-DD)
    <!-- ref:0 | last:YYYY-MM-DD | decay:active -->

Uses existing classify_entry_type() from ddd_entry_lifecycle.py for type assignment.
Preserves section structure, only transforms bullet entries with **bold title** pattern.

Usage:
    cd backend
    .venv/bin/python scripts/migrate_memory_lifecycle.py [--dry-run]
"""

import re
import sys
from datetime import date
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ddd_entry_lifecycle import classify_entry_type

# Path to MEMORY.md (SwarmWS workspace, not swarmai repo)
MEMORY_PATH = Path.home() / ".swarm-ai" / "SwarmWS" / ".context" / "MEMORY.md"

# Regex: captures "- YYYY-MM-DD: **Title** — rest" or "- YYYY-MM-DD: **Title** rest"
# Also handles entries that already have [type] prefix
_MEMORY_ENTRY_RE = re.compile(
    r"^- (?:(\d{4}-\d{2}-\d{2}):?\s*)?(?:\[(\w+)\] )?(\*\*.+?\*\*)(.*)"
)

# Metadata comment pattern (to detect already-migrated entries)
_META_RE = re.compile(r"^\s*<!-- ref:\d+ \| last:[\w\-]+ \| decay:\w+ -->$")

# Section-to-default-type mapping
SECTION_DEFAULT_TYPE = {
    "Key Decisions": "decision",
    "Lessons Learned": "guideline",  # classify_entry_type refines this
    "Recent Context": "model",
    "Open Threads": "process",
    "COE Registry": "pitfall",
    "Standing Preferences": "decision",
}


def migrate_entry(line: str, section: str) -> tuple[str, str | None]:
    """Migrate a single entry line to lifecycle format.

    Returns (migrated_line, metadata_comment) or (original_line, None) if not an entry.
    """
    m = _MEMORY_ENTRY_RE.match(line)
    if not m:
        return line, None

    date_str = m.group(1)  # May be None
    existing_type = m.group(2)  # May be None (not yet typed)
    bold_title = m.group(3)  # **Title**
    rest = m.group(4)  # Everything after title

    # Determine type
    if existing_type:
        entry_type = existing_type
    else:
        # Classify from content
        full_text = f"{bold_title} {rest}"
        entry_type = classify_entry_type(full_text)
        # Use section default if classification is too generic
        section_default = SECTION_DEFAULT_TYPE.get(section, "guideline")
        if entry_type == "guideline" and section_default != "guideline":
            entry_type = section_default

    # Build migrated line: - [type] **Title** — rest (YYYY-MM-DD)
    # If date was prefix, move to end
    if date_str and date_str not in rest:
        # Date was at beginning, move to end
        migrated = f"- [{entry_type}] {bold_title}{rest} ({date_str})"
    elif date_str:
        # Date appears somewhere in rest already
        migrated = f"- [{entry_type}] {bold_title}{rest}"
    else:
        migrated = f"- [{entry_type}] {bold_title}{rest}"

    # Build metadata comment
    ref_date = date_str or date.today().isoformat()
    metadata = f"  <!-- ref:0 | last:{ref_date} | decay:active -->"

    return migrated, metadata


def migrate_file(content: str, dry_run: bool = False) -> str:
    """Migrate all entries in MEMORY.md to lifecycle format."""
    lines = content.splitlines()
    result = []
    current_section = ""
    migrated_count = 0
    skipped_count = 0

    i = 0
    while i < len(lines):
        line = lines[i]

        # Track sections
        if line.startswith("## ") and not line.startswith("### "):
            current_section = line[3:].strip()
            # Strip date suffix from section name: "Key Decisions (2026-06-17)" → "Key Decisions"
            paren_idx = current_section.find(" (")
            if paren_idx > 0:
                current_section = current_section[:paren_idx]
            result.append(line)
            i += 1
            continue

        # Skip Memory Index block (between MEMORY_INDEX_START and MEMORY_INDEX_END)
        if "MEMORY_INDEX_START" in line:
            while i < len(lines) and "MEMORY_INDEX_END" not in lines[i]:
                result.append(lines[i])
                i += 1
            if i < len(lines):
                result.append(lines[i])  # The END marker
                i += 1
            continue

        # Check if next line is already metadata (skip already-migrated)
        if _META_RE.match(line):
            result.append(line)
            i += 1
            skipped_count += 1
            continue

        # Try to migrate entry
        if line.startswith("- ") and "**" in line:
            # Check if NEXT line is already metadata
            next_has_meta = (i + 1 < len(lines) and _META_RE.match(lines[i + 1]))
            if next_has_meta:
                # Already migrated — just add [type] if missing
                if not re.match(r"^- \[\w+\]", line):
                    migrated, meta = migrate_entry(line, current_section)
                    result.append(migrated)
                    # Keep existing metadata
                    i += 1
                    result.append(lines[i])
                    i += 1
                    migrated_count += 1
                else:
                    result.append(line)
                    i += 1
                continue

            migrated, metadata = migrate_entry(line, current_section)
            if metadata:
                result.append(migrated)
                result.append(metadata)
                migrated_count += 1
            else:
                result.append(line)
            i += 1
        else:
            result.append(line)
            i += 1

    if dry_run:
        print(f"DRY RUN: would migrate {migrated_count} entries, {skipped_count} already have metadata")
        return content

    print(f"Migrated {migrated_count} entries, {skipped_count} already have metadata")
    return "\n".join(result)


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv

    if not MEMORY_PATH.exists():
        print(f"ERROR: {MEMORY_PATH} not found")
        sys.exit(1)

    content = MEMORY_PATH.read_text()
    result = migrate_file(content, dry_run=dry_run)

    if not dry_run:
        MEMORY_PATH.write_text(result)
        print(f"Written to {MEMORY_PATH}")
