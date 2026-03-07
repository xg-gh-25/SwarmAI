"""DailyActivity file writer with YAML frontmatter and atomic writes.

Handles the file format, frontmatter management, and concurrent-safe
writes for DailyActivity files at
``Knowledge/DailyActivity/YYYY-MM-DD.md``.

Uses its own atomic read-modify-write with ``fcntl.flock`` for
concurrency safety (separate from ``locked_write.py`` which is
MEMORY.md-specific).

Key public symbols:

- ``write_daily_activity``  — Append a session entry to today's file.
- ``parse_frontmatter``     — Parse YAML frontmatter from file content.
- ``write_frontmatter``     — Serialize frontmatter dict + body to string.
"""

from __future__ import annotations

import asyncio
import fcntl
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .session_hooks import HookContext
from .summarization import StructuredSummary

logger = logging.getLogger(__name__)


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a DailyActivity file.

    Returns ``(frontmatter_dict, body_content)``.  Normalizes values:
    booleans to Python ``bool``, integers to ``int``, strings as-is.
    If no frontmatter is found, returns ``({}, content)``.
    """
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content

    raw = content[3:end].strip()
    body = content[end + 3:].lstrip("\n")
    fm: dict[str, Any] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        # Normalize types
        if val.lower() in ("true", "false"):
            fm[key] = val.lower() == "true"
        elif val.isdigit():
            fm[key] = int(val)
        else:
            fm[key] = val
    return fm, body


def write_frontmatter(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize frontmatter dict and body back to file content.

    Round-trip safe with ``parse_frontmatter`` when compared via
    semantic equality (booleans as booleans, integers as integers).
    """
    lines = ["---"]
    for key, val in frontmatter.items():
        if isinstance(val, bool):
            lines.append(f"{key}: {str(val).lower()}")
        elif isinstance(val, int):
            lines.append(f"{key}: {val}")
        else:
            lines.append(f'{key}: "{val}"')
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body


def _format_session_entry(summary: StructuredSummary, context: HookContext) -> str:
    """Format a StructuredSummary into a markdown session entry.

    Header format: ``## Session — HH:MM | session_id[:8] | Title``
    This groups by time + session ID + title for traceability.
    """
    lines: list[str] = []
    short_id = context.session_id[:8] if context.session_id else "unknown"
    title = summary.session_title or context.session_title or "Untitled"
    ts = summary.timestamp or datetime.now().strftime("%H:%M")
    lines.append(f"## Session — {ts} | {short_id} | {title}")
    lines.append("")

    lines.append("### What Happened")
    if summary.topics:
        for t in summary.topics:
            lines.append(f"- {t}")
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("### Key Decisions")
    if summary.decisions:
        for d in summary.decisions:
            lines.append(f"- {d}")
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("### Files Modified")
    if summary.files_modified:
        for f in summary.files_modified:
            lines.append(f"- {f}")
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("### Open Questions")
    if summary.open_questions:
        for q in summary.open_questions:
            lines.append(f"- {q}")
    else:
        lines.append("(none)")
    lines.append("")

    return "\n".join(lines)


def _atomic_read_modify_write(file_path: Path, summary: StructuredSummary, context: HookContext) -> None:
    """Atomic read-modify-write with fcntl.flock for concurrency safety."""
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Open or create the file
    with open(file_path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            content = fh.read()

            if not content.strip():
                # New file — create with frontmatter
                today = date.today().isoformat()
                fm = {"date": today, "sessions_count": 1, "distilled": False}
                new_content = write_frontmatter(fm, _format_session_entry(summary, context))
            else:
                # Existing file — parse, increment, append
                fm, body = parse_frontmatter(content)
                fm["sessions_count"] = fm.get("sessions_count", 0) + 1
                new_body = body.rstrip("\n") + "\n\n" + _format_session_entry(summary, context)
                new_content = write_frontmatter(fm, new_body)

            # Truncate and rewrite
            fh.seek(0)
            fh.truncate()
            fh.write(new_content)
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


async def write_daily_activity(
    summary: StructuredSummary,
    context: HookContext,
    workspace_path: Path | None = None,
) -> Path:
    """Append a session entry to today's DailyActivity file.

    Creates the file with YAML frontmatter if it doesn't exist.
    Uses atomic read-modify-write with ``fcntl.flock``.

    Returns the path to the written file.
    """
    if workspace_path is None:
        from .initialization_manager import initialization_manager
        workspace_path = Path(initialization_manager.get_cached_workspace_path())

    today = date.today().isoformat()
    da_dir = workspace_path / "Knowledge" / "DailyActivity"
    file_path = da_dir / f"{today}.md"

    await asyncio.to_thread(_atomic_read_modify_write, file_path, summary, context)
    logger.info("Wrote DailyActivity entry to %s", file_path)
    return file_path
