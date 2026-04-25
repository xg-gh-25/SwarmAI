"""DailyActivity file writer with YAML frontmatter, JSONL sidecar, and atomic writes.

Handles two output formats per day:

1. **Markdown** (``YYYY-MM-DD.md``) — Human-readable session log with
   YAML frontmatter.  This is the "for humans" view.
2. **JSONL sidecar** (``YYYY-MM-DD.jsonl``) — One JSON line per session
   with all structured fields from ``StructuredSummary``.  This is the
   "for pipeline" view — consumed directly by ``DistillationTriggerHook``
   without regex re-parsing.

Both files are written atomically with ``fcntl.flock``.  The sidecar is
best-effort — if it fails, the markdown is still written and the legacy
regex extraction path in distillation still works.

Key public symbols:

- ``write_daily_activity``  — Append a session entry to today's file + sidecar.
- ``read_jsonl_sidecar``    — Read structured session data from JSONL sidecar.
- ``parse_frontmatter``     — Parse YAML frontmatter from file content.
- ``write_frontmatter``     — Serialize frontmatter dict + body to string.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
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


_TITLE_MIN_LEN = 8  # Titles shorter than this are treated as garbage


def _validate_title(
    summary_title: str,
    context_title: str,
    topics: list[str],
) -> str:
    """Pick the best available title, filtering out garbage.

    Cascade:
    1. summary_title (from _derive_title — highest quality)
    2. context_title (from DB/frontend — may be garbage like "1" or "ok")
    3. First topic (from extracted user messages)
    4. "Untitled"

    Each candidate must be >= _TITLE_MIN_LEN chars and not "Untitled session"
    to be accepted. Skips noise-like titles (pure digits, single words < 5 chars).
    """
    for candidate in [summary_title, context_title]:
        if not candidate or candidate == "Untitled session":
            continue
        cleaned = candidate.strip()
        if len(cleaned) < _TITLE_MIN_LEN:
            continue
        # Skip pure numbers or very short single-word titles
        if cleaned.isdigit():
            continue
        return cleaned[:60]

    # Fallback: use first substantive topic
    for topic in (topics or []):
        if len(topic) >= _TITLE_MIN_LEN and not topic.isdigit():
            return topic[:60]

    return "Untitled"


def _format_session_entry(summary: StructuredSummary, context: HookContext) -> str:
    """Format a StructuredSummary into a markdown session entry.

    Header format: ``## Session — HH:MM | session_id[:8] | Title``

    The format is designed around three questions:
    1. What was delivered? (deliverables)
    2. Where are the outputs? (key outputs)
    3. What did we learn? (lessons)

    Plus COE signal, decisions, and continuation context.
    Routine/low-signal sections are omitted entirely when empty.
    """
    lines: list[str] = []
    short_id = context.session_id[:8] if context.session_id else "unknown"
    title = _validate_title(
        summary.session_title, context.session_title, summary.topics
    )
    ts = summary.timestamp or datetime.now().strftime("%H:%M")

    # COE badge in header when applicable
    coe_badge = ""
    if summary.coe_signal == "resolution":
        coe_badge = " 🔴 COE-RESOLUTION"
    elif summary.coe_signal == "candidate":
        coe_badge = " 🟡 COE-CANDIDATE"

    lines.append(f"## {ts} | {short_id} | {title}{coe_badge}")
    lines.append("")

    # --- Deliverables (what was accomplished — the headline) ---
    if summary.deliverables:
        lines.append("**Delivered:**")
        for d in summary.deliverables:
            lines.append(f"- {d}")
        lines.append("")
    elif summary.topics:
        # Fallback: use topics if no LLM enrichment
        lines.append("**What happened:**")
        for t in summary.topics[:5]:
            lines.append(f"- {t}")
        lines.append("")

    # --- Key Outputs (where things went) ---
    if summary.key_outputs:
        lines.append("**Outputs:**")
        for o in summary.key_outputs:
            lines.append(f"- {o}")
        lines.append("")
    elif summary.files_modified:
        # Fallback: show files modified
        lines.append("**Files:** " + ", ".join(
            f"`{f.split('/')[-1]}`" for f in summary.files_modified[:8]
        ))
        lines.append("")

    # --- Decisions (only when substantive) ---
    if summary.decisions:
        lines.append("**Decisions:**")
        for d in summary.decisions:
            lines.append(f"- {d}")
        lines.append("")

    # --- Lessons (the highest-value content) ---
    if summary.lessons:
        lines.append("**Lessons:**")
        for l in summary.lessons:
            lines.append(f"- {l}")
        lines.append("")

    # --- Rejected approaches (what NOT to do) ---
    if summary.rejected_approaches:
        lines.append("**Rejected:**")
        for r in summary.rejected_approaches:
            lines.append(f"- {r}")
        lines.append("")

    # --- Corrections (agent behavior corrected by user) ---
    if summary.corrections:
        lines.append("**Corrections:**")
        for c in summary.corrections:
            lines.append(f"- {c}")
        lines.append("")

    # --- Signal-driven actions (external signal → session decision causal link) ---
    if summary.signal_driven_actions:
        lines.append("**Signal-Driven:**")
        for action in summary.signal_driven_actions:
            lines.append(f"- {action}")
        lines.append("")

    # --- Process reflection (LLM meta-analysis of how the session went) ---
    if summary.process_reflection:
        lines.append(f"**Process Reflection:** {summary.process_reflection}")
        lines.append("")

    # --- Git ground truth (actual commits during session) ---
    if summary.git_commits:
        lines.append("**Git activity:**")
        for c in summary.git_commits[:10]:  # Cap to avoid bloat
            lines.append(f"- `{c}`")
        lines.append("")

    # --- COE context (when investigating a problem) ---
    if summary.coe_signal and summary.coe_topic:
        lines.append(f"**COE:** `{summary.coe_signal}` — {summary.coe_topic}")
        lines.append("")

    # --- Continuation context ---
    tail_parts: list[str] = []
    if summary.validation_status:
        tail_parts.append(f"**Validation:** {summary.validation_status}")
    if summary.continue_from:
        tail_parts.append(f"**Next:** {summary.continue_from}")
    if tail_parts:
        lines.extend(tail_parts)
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
                fm: dict[str, Any] = {"date": today, "sessions_count": 1, "distilled": False}
                if summary.coe_signal:
                    fm["has_coe"] = True
                new_content = write_frontmatter(fm, _format_session_entry(summary, context))
            else:
                # Existing file — parse, increment, append
                fm, body = parse_frontmatter(content)
                fm["sessions_count"] = fm.get("sessions_count", 0) + 1
                if summary.coe_signal:
                    fm["has_coe"] = True
                new_body = body.rstrip("\n") + "\n\n" + _format_session_entry(summary, context)
                new_content = write_frontmatter(fm, new_body)

            # Truncate and rewrite
            fh.seek(0)
            fh.truncate()
            fh.write(new_content)
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _summary_to_jsonl_record(summary: StructuredSummary, context: HookContext) -> dict[str, Any]:
    """Convert a StructuredSummary + HookContext into a flat JSON-serializable dict.

    This is the structured sidecar format consumed by DistillationTriggerHook.
    Every field from StructuredSummary is preserved as-is — no markdown rendering,
    no information loss.  The distillation hook reads these directly instead of
    regex-parsing the markdown.
    """
    return {
        "session_id": context.session_id,
        "timestamp": summary.timestamp or datetime.now().strftime("%H:%M"),
        "session_start": context.session_start_time,
        "message_count": context.message_count,
        "title": summary.session_title,
        # Core fields (rule-based)
        "topics": summary.topics,
        "decisions": summary.decisions,
        "files_modified": summary.files_modified,
        "open_questions": summary.open_questions,
        # Enriched fields (LLM-powered)
        "deliverables": summary.deliverables,
        "key_outputs": summary.key_outputs,
        "lessons": summary.lessons,
        "rejected_approaches": summary.rejected_approaches,
        "corrections": summary.corrections,
        "process_reflection": summary.process_reflection,
        "signal_driven_actions": summary.signal_driven_actions,
        "continue_from": summary.continue_from,
        "validation_status": summary.validation_status,
        # COE fields
        "coe_signal": summary.coe_signal,
        "coe_topic": summary.coe_topic,
        # Git ground truth
        "git_commits": summary.git_commits,
    }


def _write_jsonl_sidecar(jsonl_path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line to the JSONL sidecar file.

    Uses fcntl.flock for concurrency safety (multiple sessions can close
    on the same day).  The file is created if it doesn't exist.
    """
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"

    with open(jsonl_path, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def read_jsonl_sidecar(jsonl_path: Path) -> list[dict[str, Any]]:
    """Read all session records from a JSONL sidecar file.

    Returns a list of dicts, one per session.  Skips malformed lines
    gracefully (logs warning, continues).  Returns empty list if the
    file doesn't exist.

    Used by DistillationTriggerHook to consume structured data directly
    instead of regex-parsing the markdown DailyActivity file.
    """
    if not jsonl_path.is_file():
        return []

    records: list[dict[str, Any]] = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning("Malformed JSONL line %d in %s: %s", lineno, jsonl_path, exc)
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read JSONL sidecar %s: %s", jsonl_path, exc)
    return records


async def write_daily_activity(
    summary: StructuredSummary,
    context: HookContext,
    workspace_path: Path | None = None,
) -> Path:
    """Append a session entry to today's DailyActivity file + JSONL sidecar.

    Writes two files:
    1. ``YYYY-MM-DD.md`` — human-readable markdown (always written)
    2. ``YYYY-MM-DD.jsonl`` — structured sidecar (best-effort, non-blocking)

    Creates files with YAML frontmatter if they don't exist.
    Uses atomic read-modify-write with ``fcntl.flock``.

    Returns the path to the written markdown file.
    """
    if workspace_path is None:
        from .initialization_manager import initialization_manager
        workspace_path = Path(initialization_manager.get_cached_workspace_path())

    today = date.today().isoformat()
    da_dir = workspace_path / "Knowledge" / "DailyActivity"
    file_path = da_dir / f"{today}.md"
    jsonl_path = da_dir / f"{today}.jsonl"

    # Write markdown (primary — must succeed)
    await asyncio.to_thread(_atomic_read_modify_write, file_path, summary, context)
    logger.info("Wrote DailyActivity entry to %s", file_path)

    # Write JSONL sidecar (best-effort — failure doesn't block)
    try:
        record = _summary_to_jsonl_record(summary, context)
        await asyncio.to_thread(_write_jsonl_sidecar, jsonl_path, record)
        logger.debug("Wrote JSONL sidecar to %s", jsonl_path)
    except Exception as exc:
        logger.warning("JSONL sidecar write failed (non-blocking): %s", exc)

    return file_path
