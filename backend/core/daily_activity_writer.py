"""DailyActivity file writer with YAML frontmatter, JSONL sidecar, and atomic writes.

Two files per local day:

1. **Markdown** (``YYYY-MM-DD.md``) — the human-readable record, indexed for recall:
   one block per session (title, summary, Delivered, Outputs, Validation). The summary
   says where the work stopped; there is no follow-up list (design
   2026-10-31-continue-lookback-todo).
2. **JSONL sidecar** (``YYYY-MM-DD.jsonl``) — one JSON line per session with the
   structured fields; read by the prompt injection, the COE threads and the session
   statistics.

Identity rule (run_1ec5ea9d): a session has ONE block per kind in a day file. Every
producer writes through ``upsert_session_block``, which removes that session's old
marked block (``<!-- da:<kind>:<session_id> -->`` … ``<!-- /da -->``) and appends the
new one at the END of the file. Other text is never changed apart from the blank lines
around a removed block and the frontmatter's ``sessions_count`` / ``has_coe``. The
sidecar keeps one record per session (latest wins). Every writer of a day file takes
the ONE hidden ``.DailyActivity.lock`` of the directory (``_day_lock``) and writes
through a temp file + rename, so a crash mid-write cannot truncate the day. The
sidecar is best-effort — if it fails, the markdown is still written.

Key public symbols:

- ``write_daily_activity``  — Upsert a session's summary in a day file + sidecar.
- ``upsert_session_block``  — The one way to write a session's block (any kind).
- ``append_daily_text``     — Append session-less text (captures, job notes) under the lock.
- ``read_jsonl_sidecar``    — Read structured session data from a JSONL sidecar.
- ``parse_frontmatter``     — Parse YAML frontmatter from file content.
- ``write_frontmatter``     — Serialize frontmatter dict + body to string.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import tempfile
from datetime import date, datetime
from pathlib import Path

from utils.file_lock import md_lock
from typing import Any

from .credential_patterns import redact_credentials
from .session_hooks import HookContext
from .summarization import StructuredSummary


def _redact_value(value: Any) -> Any:
    """``redact_credentials`` applied to every string inside a JSON-able value."""
    if isinstance(value, str):
        return redact_credentials(value)
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    return value

logger = logging.getLogger(__name__)

# Latch so "summaries are not being sanitised" is warned once per process rather than
# once per activity write (see _sanitize_summary_injection's import guard).
_WARNED_SCANNER_IMPORT: set[bool] = set()


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


_TITLE_MIN_LEN = 8  # a tab title shorter than this ("1", "ok") is treated as garbage


def _validate_title(
    summary_title: str,
    context_title: str,
    topics: list[str],
) -> str:
    """Pick the title shown for a session.

    1. ``summary_title``: the model's title, or the one derived from the messages
       (already at least 10 characters). Any non-empty, non-numeric text is kept: a
       model title in CJK is often 4-6 characters.
    2. ``context_title``: the tab title, kept only at ``_TITLE_MIN_LEN`` or longer.
    3. The first topic of that length, then "Untitled".
    """
    cleaned = (summary_title or "").strip()
    if cleaned and cleaned != "Untitled session" and not cleaned.isdigit():
        return cleaned
    cleaned = (context_title or "").strip()
    if len(cleaned) >= _TITLE_MIN_LEN and not cleaned.isdigit():
        return cleaned
    for topic in (topics or []):
        if len(topic) >= _TITLE_MIN_LEN and not topic.isdigit():
            return topic
    return "Untitled"


def _shown_title(summary: StructuredSummary, context: HookContext) -> str:
    """The title written to both files: ``_validate_title`` on one line, checked by the
    injection scanner like the other fields (the tab-title fallback included)."""
    title = " ".join(_validate_title(summary.session_title, context.session_title, []).split())
    try:
        from core.injection_patterns import scan_text
    except ImportError:  # pragma: no cover - the sanitizer logs this once
        return title or "Untitled"
    return "Untitled" if not title or scan_text(title) else title


def _format_session_entry(summary: StructuredSummary, context: HookContext) -> str:
    """Render one complete, human-readable session block."""
    lines: list[str] = []
    session_id = context.session_id or "unknown"
    title = _shown_title(summary, context)
    ts = summary.timestamp or datetime.now().strftime("%H:%M")

    lines.append(f"## {ts} | {session_id} | {title}")
    if summary.summary_sentence:
        lines.append(f"Summary: {summary.summary_sentence}")
    lines.extend(["", "**Delivered:**"])
    lines.extend(f"- {item}" for item in summary.deliverables)
    if not summary.deliverables:
        lines.append("- None recorded")

    lines.extend(["", "**Outputs:**"])
    outputs = list(summary.key_outputs)
    outputs.extend(f"file: {path}" for path in summary.files_modified)
    outputs.extend(f"commit: {commit}" for commit in summary.git_commits)
    lines.extend(f"- {item}" for item in outputs)
    if not outputs:
        lines.append("- None recorded")

    lines.extend(["", "**Validation:**"])
    lines.append(f"- {summary.validation_status}" if summary.validation_status else "- Not recorded")
    lines.append("")
    return "\n".join(lines)


_END_MARKER = "<!-- /da -->"


def _day_lock(path: Path):
    """The ONE lock every DailyActivity write takes: a hidden ``.DailyActivity.lock``
    per directory (dot-file: library_health skips it, nothing accumulates per day).
    Never nested — a holder only reads and rewrites one file."""
    return md_lock(Path(path).parent / ".DailyActivity")


def _file_day(file_path: Path) -> str:
    """The day a day file is for: its ISO-date stem, else today."""
    try:
        return date.fromisoformat(Path(file_path).stem).isoformat()
    except ValueError:
        return date.today().isoformat()


def _start_marker(kind: str, session_id: str) -> str:
    return f"<!-- da:{kind}:{session_id} -->"


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file + rename (caller holds md_lock)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, mode)  # mkstemp creates 0600; keep the day file's mode
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _line_starts(content: str, needle: str, start: int = 0) -> "list[int]":
    """Positions where ``needle`` occupies a whole line of ``content``."""
    out = []
    i = content.find(needle, start)
    while i != -1:
        end = i + len(needle)
        if (i == 0 or content[i - 1] == "\n") and (end == len(content) or content[end] == "\n"):
            out.append(i)
        i = content.find(needle, i + 1)
    return out


def has_session_summary(content: str, session_id: str) -> bool:
    """True if ``content`` (a day file) already holds this session's summary block."""
    return bool(session_id) and _start_marker("summary", session_id) in content


def _find_block(content: str, kind: str, session_id: str) -> "tuple[int, int] | None":
    """Span (start, end) of this session's last COMPLETE block of ``kind``, or None.

    Markers count only as whole lines. A start marker counts only if its end
    marker follows before any other da block starts, so a broken pair (hand
    edit) is never stretched over other text — it is left alone and a fresh
    block is appended instead.
    """
    start = _start_marker(kind, session_id)
    ends = _line_starts(content, _END_MARKER)
    best = None
    for i in _line_starts(content, start):
        j = next((e for e in ends if e > i), None)
        nxt = content.find("\n<!-- da:", i + len(start))
        if j is not None and (nxt == -1 or j < nxt):
            best = (i, j + len(_END_MARKER))
    return best


def _neutralize_markers(text: str) -> str:
    """Block text may quote the markers (sessions about this feature do); quoted
    copies must never be read as a block boundary."""
    return text.replace("<!-- da:", "<!-- da-quoted:").replace(_END_MARKER, "<!-- /da-quoted -->")


def _split_frontmatter(content: str) -> "tuple[str, str]":
    """(frontmatter text incl. its closing '---' line, rest) — byte-exact."""
    if not content.startswith("---"):
        return "", content
    end = content.find("---", 3)
    if end == -1:
        return "", content
    cut = end + 3
    if content[cut:cut + 1] == "\n":
        cut += 1
    return content[:cut], content[cut:]


def upsert_session_block(
    file_path: Path, session_id: str, kind: str, text: str, *,
    count_session: bool = False, has_coe: bool = False,
) -> bool:
    """Write ``text`` as this session's one block of ``kind`` in ``file_path``.

    Removes the existing marked block for (kind, session_id) and appends the new
    one at the end (the newest write is always last), or appends a new block. ``count_session`` adds 1 to the frontmatter's
    ``sessions_count`` when a NEW block is appended (summaries only), so a
    session re-summarized N times still counts once. Returns True when a new
    block was appended. Holds the day file's md_lock for the whole read-write.
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)  # md_lock's sidecar lives there
    # Credentials never reach a day file: entries quote user messages verbatim
    # ("What happened"), and a pasted token would otherwise be stored, indexed
    # for recall and injected into later prompts.
    text = _neutralize_markers(redact_credentials(text))
    if not session_id or session_id == "unknown":
        # No identity to key on: a shared placeholder would make every id-less
        # session overwrite the others' block, so append it like a capture.
        append_daily_text(file_path, "\n" + text.strip("\n") + "\n")
        return True
    block = f"{_start_marker(kind, session_id)}\n{text.strip(chr(10))}\n{_END_MARKER}"
    with _day_lock(file_path):
        content = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        span = _find_block(content, kind, session_id)
        appended = span is None
        if span is not None:
            # MOVE, not in place: the prompt injects the file's tail as "newest", so a
            # session's latest write must be last (memory_injection.select_daily_activity).
            before = content[:span[0]].rstrip("\n")
            after = content[span[1]:].lstrip("\n")
            content = before + ("\n\n" + after if after.strip() else "")
        content = (content.rstrip("\n") + "\n\n" + block + "\n") if content.strip() else block + "\n"
        fm_text, body = _split_frontmatter(content)
        if not fm_text:
            fm = {"date": _file_day(file_path),
                  "sessions_count": 1 if (count_session and appended) else 0,
                  "distilled": False}
            if has_coe:
                fm["has_coe"] = True
            content = write_frontmatter(fm, body)
        elif (count_session and appended) or (has_coe and "has_coe" not in fm_text):
            fm, _ = parse_frontmatter(content)
            if count_session and appended:
                fm["sessions_count"] = int(fm.get("sessions_count", 0) or 0) + 1
            if has_coe:
                fm["has_coe"] = True
            content = write_frontmatter(fm, body)
        _atomic_write(file_path, content)
    return appended


def append_daily_text(file_path: Path, text: str) -> None:
    """Append session-less text (a capture line, a job note) under the day file's lock."""
    text = redact_credentials(text)
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with _day_lock(file_path):
        content = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        if not content.strip():
            content = write_frontmatter(
                {"date": _file_day(file_path), "sessions_count": 0, "distilled": False}, "")
        sep = "" if content.endswith("\n") or not content else "\n"
        _atomic_write(file_path, content + sep + text)


def _atomic_read_modify_write(file_path: Path, summary: StructuredSummary, context: HookContext) -> None:
    """Upsert this session's summary block (one per session per day)."""
    upsert_session_block(
        file_path, context.session_id or "unknown", "summary",
        _format_session_entry(summary, context),
        count_session=True, has_coe=bool(summary.coe_signal),
    )


def _summary_to_jsonl_record(summary: StructuredSummary, context: HookContext) -> dict[str, Any]:
    """The sidecar row: every field something reads, flat and JSON-serializable.

    ``message_count`` is the number of messages this row summarizes (that day's), and
    ``enriched`` whether the model wrote it; together they let the hook skip a session
    whose row is already complete.
    """
    return {
        "session_id": context.session_id,
        "timestamp": summary.timestamp or datetime.now().strftime("%H:%M"),
        "session_start": context.session_start_time,
        "message_count": context.message_count,
        "enriched": summary.enriched,
        "title": _shown_title(summary, context),
        "summary_sentence": summary.summary_sentence,
        "topics": summary.topics,
        "decisions": summary.decisions,
        "files_modified": summary.files_modified,
        "open_questions": summary.open_questions,
        "deliverables": summary.deliverables,
        "key_outputs": summary.key_outputs,
        "validation_status": summary.validation_status,
        "coe_signal": summary.coe_signal,
        "coe_topic": summary.coe_topic,
        "git_commits": summary.git_commits,
    }


def _write_jsonl_sidecar(jsonl_path: Path, record: dict[str, Any]) -> None:
    """Upsert ``record`` into the JSONL sidecar: one line per session_id, latest wins.

    Lines that do not parse, or carry another session_id, are kept as they are.
    Written under the sidecar's md_lock through a temp file + rename.
    """
    jsonl_path = Path(jsonl_path)
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    record = _redact_value(record)  # same rule as the day file; distillation reads this
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    key_field = "session_id"
    sid = record.get(key_field)
    if not sid or sid == "unknown":
        sid = None  # no identity: append, never replace another id-less record
    with _day_lock(jsonl_path):
        old = jsonl_path.read_text(encoding="utf-8").splitlines() if jsonl_path.exists() else []
        out: list[str] = []
        replaced = False
        for raw in old:
            try:
                same = sid is not None and json.loads(raw).get(key_field) == sid
            except (ValueError, AttributeError):
                same = False
            if same:
                if not replaced:
                    out.append(line)
                    replaced = True
                continue
            out.append(raw)
        if not replaced:
            out.append(line)
        _atomic_write(jsonl_path, "\n".join(out) + "\n")


def read_jsonl_sidecar(jsonl_path: Path) -> list[dict[str, Any]]:
    """Read all session records from a JSONL sidecar file.

    Returns a list of dicts, one per session.  Skips malformed lines
    gracefully (logs warning, continues).  Returns empty list if the
    file doesn't exist.

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


# Free-text StructuredSummary fields that carry text drawn from the conversation and
# are stored, recalled and injected into later prompts. Structural fields
# (files_modified, git_commits, timestamps, coe_signal) are not prose sinks.
_SUMMARY_INJECTION_FIELDS: tuple[str, ...] = (
    "session_title", "summary_sentence", "validation_status", "coe_topic",
    "topics", "decisions", "open_questions", "deliverables", "key_outputs",
)


def _sanitize_summary_injection(summary: StructuredSummary) -> None:
    """Redact injection-pattern-matching free-text fields IN PLACE to "".

    A str field that matches is blanked; a list[str] field has matching items
    dropped, and a Next item whose text matches is dropped. Blanking (not a marker
    token) means no marker leaks into a recalled store (Gate-1 F7). Best-effort — a
    scan failure must never block the activity write.
    """
    try:
        from core.injection_patterns import scan_text
    except Exception as exc:  # pragma: no cover - import guard
        # WARNING, and once per process: this import either always works or always
        # fails, so a failure does not degrade sanitisation — it disables it ENTIRELY
        # while the activity write keeps succeeding. Unsanitised text then lands in
        # DailyActivity, which is loaded back into context on later sessions, so a
        # silent failure here turns a write path into a persistence path for
        # injection patterns. Not blocking the write is still right (docstring).
        if not _WARNED_SCANNER_IMPORT:
            _WARNED_SCANNER_IMPORT.add(True)
            logger.warning("injection scanner unavailable; DailyActivity summaries are "
                           "NOT being sanitised for this process: %s", exc)
        return
    for fname in _SUMMARY_INJECTION_FIELDS:
        val = getattr(summary, fname, None)
        if isinstance(val, str):
            if scan_text(val):
                logger.warning("Redacted DailyActivity field %r: injection pattern", fname)
                setattr(summary, fname, "")
        elif isinstance(val, list):
            kept = [item for item in val if not (isinstance(item, str) and scan_text(item))]
            if len(kept) != len(val):
                logger.warning(
                    "Dropped %d injected item(s) from DailyActivity field %r",
                    len(val) - len(kept), fname,
                )
                setattr(summary, fname, kept)


def render_session(summary: StructuredSummary, context: HookContext) -> "tuple[str, dict[str, Any]]":
    """The marked day-file block and the sidecar row of *summary*, through the same
    injection gate and credential redaction as ``write_daily_activity`` — for a
    writer that rebuilds a whole day file at once (scripts/da_resummarize.py)."""
    _sanitize_summary_injection(summary)
    text = _neutralize_markers(redact_credentials(_format_session_entry(summary, context)))
    block = (f"{_start_marker('summary', context.session_id)}\n"
             f"{text.strip(chr(10))}\n{_END_MARKER}")
    return block, _redact_value(_summary_to_jsonl_record(summary, context))


async def write_daily_activity(
    summary: StructuredSummary,
    context: HookContext,
    workspace_path: Path | None = None,
    *,
    day: date | None = None,
) -> Path:
    """Upsert this session's summary in the day file (default today) + JSONL sidecar.

    The markdown is the human-readable primary record. The JSONL sidecar keeps
    the structured fields read by the injection and the other readers.
    """
    if workspace_path is None:
        from .initialization_manager import initialization_manager
        workspace_path = Path(initialization_manager.get_cached_workspace_path())

    # ── Write-time injection gate (run_6af300b3): sanitize BEFORE either write ──
    # Both the .md (written first, below) and the JSONL sidecar are serialized from
    # this same `summary`, and both are read back into later prompts. So the gate
    # sits at the SOURCE (this summary), not one serializer. A matching field is
    # blanked to "" (empty, not a marker) so no redaction token leaks into a
    # recalled store. See injection_patterns.
    _sanitize_summary_injection(summary)

    day_name = (day or date.today()).isoformat()
    da_dir = workspace_path / "Knowledge" / "DailyActivity"
    file_path = da_dir / f"{day_name}.md"
    jsonl_path = da_dir / f"{day_name}.jsonl"

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
