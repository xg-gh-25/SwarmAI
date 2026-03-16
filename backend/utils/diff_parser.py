"""Parse unified diff output into structured hunks with section-aware summaries.

Used by the ``GET /workspace/file/diff`` endpoint to provide human-readable
edit summaries when a user saves a file in the editor panel.

Public API:
- ``parse_unified_diff(raw_diff)`` — Parse ``git diff`` output into typed hunks
- ``format_human_summary(hunks, file_lines)`` — Section-aware summary string

Types:
- ``DiffHunk`` — A single contiguous change block
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DiffHunk:
    """A single contiguous change block from a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added_lines: List[str] = field(default_factory=list)
    removed_lines: List[str] = field(default_factory=list)
    context_before: str = ""


# Regex for unified diff hunk headers: @@ -old_start,old_count +new_start,new_count @@
_HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)


def parse_unified_diff(raw_diff: str) -> List[DiffHunk]:
    """Parse raw ``git diff --unified=3`` output into structured hunks.

    Returns an empty list for empty or invalid diffs.
    """
    if not raw_diff or not raw_diff.strip():
        return []

    hunks: List[DiffHunk] = []
    current: Optional[DiffHunk] = None

    for line in raw_diff.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            current = DiffHunk(
                old_start=int(m.group(1)),
                old_count=int(m.group(2) or "1"),
                new_start=int(m.group(3)),
                new_count=int(m.group(4) or "1"),
            )
            hunks.append(current)
            continue

        if current is None:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            current.added_lines.append(line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            current.removed_lines.append(line[1:])

    return hunks


def _find_nearest_heading(file_lines: List[str], line_number: int) -> Optional[str]:
    """Walk backwards from ``line_number`` (1-based) to find the nearest markdown heading."""
    for i in range(min(line_number - 1, len(file_lines) - 1), -1, -1):
        stripped = file_lines[i].strip()
        if stripped.startswith("#"):
            # Remove leading '#' symbols and whitespace
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return None


def format_human_summary(hunks: List[DiffHunk], file_content: str) -> str:
    """Generate a section-aware, human-readable summary of changes.

    Each hunk is summarized with its nearest markdown heading (if any),
    a brief description of what changed, and line numbers.

    Example output::

        - [Phase 1, line 34] Changed "adoption" -> "organic adoption"
        - [Timeline, lines 78-82] Deleted paragraph
        - [Evidence, line 103] Added bullet about customer engagement
    """
    if not hunks:
        return ""

    file_lines = file_content.split("\n") if file_content else []
    summaries: List[str] = []

    for hunk in hunks:
        heading = _find_nearest_heading(file_lines, hunk.new_start)
        section = heading or "top"

        added_count = len(hunk.added_lines)
        removed_count = len(hunk.removed_lines)

        # Determine the affected line range in the new file
        start_line = hunk.new_start
        end_line = hunk.new_start + max(hunk.new_count - 1, 0)
        line_ref = (
            f"line {start_line}"
            if start_line == end_line
            else f"lines {start_line}-{end_line}"
        )

        # Generate description
        if removed_count > 0 and added_count > 0:
            # Modification
            if removed_count == 1 and added_count == 1:
                old_text = hunk.removed_lines[0].strip()[:40]
                new_text = hunk.added_lines[0].strip()[:40]
                desc = f'Changed "{old_text}" -> "{new_text}"'
            else:
                desc = f"Modified {removed_count} line(s), added {added_count} line(s)"
        elif added_count > 0:
            if added_count == 1:
                preview = hunk.added_lines[0].strip()[:50]
                desc = f'Added: "{preview}"'
            else:
                desc = f"Added {added_count} line(s)"
        elif removed_count > 0:
            if removed_count == 1:
                preview = hunk.removed_lines[0].strip()[:50]
                desc = f'Deleted: "{preview}"'
            else:
                desc = f"Deleted {removed_count} line(s)"
        else:
            continue

        summaries.append(f"  - [{section}, {line_ref}] {desc}")

    return "\n".join(summaries)
