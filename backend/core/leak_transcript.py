"""Remove tool-call leak text from a Claude CLI session transcript.

A "leak" is the model writing its tool call as text (``count\\n‹invoke ...>``)
instead of a real tool_use. The CLI keeps that text in its own session
transcript, and ``--resume`` replays it to the model on every later turn, which
primes more leaks (observed: 6-8 leaks per session within 20 minutes). Cleaning
the transcript before a ``--resume`` removes the priming.

Public symbols:
- ``find_transcript(sdk_session_id)`` — path of the CLI transcript, or None.
- ``clean_transcript_leaks(path)``    — strip leak text and degenerate
  repetition runs from assistant blocks in place (atomic replace); returns the
  number of blocks changed.
- ``clean_model_text(text)``          — the same three text cleanings for one
  string (leak, glitch prefix, degenerate runs); shared with the compaction
  summary path (core.compact_summary) so stream, transcript and summary agree.

Only ASSISTANT records — plus the CLI's compaction summary (a USER record with
``isCompactSummary: true`` whose text the MODEL wrote, and which ``--resume``
replays on every later turn) — are touched: in text blocks a raw line-start ``‹invoke``
outside code spans (the signal the live stream uses) is stripped, and so is the
second glitch shape — a corrupted opener word at the start of a narration line
(``detect_glitch_prefix``), which the model also copies from history. Prose
that discusses the syntax, tool results, and user messages are left alone.

The same pass removes degenerate repetition runs (``core.output_degeneration``)
from assistant text blocks AND from tool_use input strings, replacing each run
with ``DEGENERATION_TRANSCRIPT_MARKER``. A runaway loop that was interrupted
mid-block is still written to the transcript by the CLI (up to the stop point),
and ``--resume`` would replay it; the orchestrator flags the session
(``_transcript_poisoned``) so this cleaner runs before the next spawn. Detection
uses the very function the live stream uses, so stream and history agree.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from .output_degeneration import (
    DEGENERATION_TRANSCRIPT_MARKER,
    WINDOW as _DEGEN_WINDOW,
    strip_degenerate_in_value,
    strip_degenerate_runs,
)
from .session_utils import (
    _has_glitch_word,
    detect_glitch_prefix,
    find_stream_leak_onset,
    strip_glitch_prefix,
    strip_tool_call_leak,
)

logger = logging.getLogger(__name__)

# The API rejects an empty text block in conversation history, so a block that
# was nothing but a leak keeps a neutral word (no tool-call markup).
EMPTY_FILLER = "(retrying)"

# SDK session ids are UUIDs; anything else is refused so the id can never act
# as a glob pattern or a path component.
_SID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def _projects_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "projects"


def find_transcript(sdk_session_id: Optional[str]) -> Optional[Path]:
    """Return ``<projects>/<encoded-cwd>/<sid>.jsonl`` if it exists, else None."""
    if not sdk_session_id or not _SID_RE.match(sdk_session_id):
        return None
    root = _projects_dir()
    if not root.is_dir():
        return None
    matches = [p for p in root.glob(f"*/{sdk_session_id}.jsonl") if p.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_mtime)


def clean_model_text(text: str) -> str:
    """Strip tool-call leak text, a glitch opener word and degenerate repetition
    runs from MODEL-written text. Only removes — never parses or executes the
    leaked markup. Returns ``text`` unchanged (same object) when nothing matched."""
    cleaned = text
    if find_stream_leak_onset(cleaned) != -1:
        cleaned = strip_tool_call_leak(cleaned)
    if detect_glitch_prefix(cleaned):
        cleaned = strip_glitch_prefix(cleaned)
    if len(cleaned) >= _DEGEN_WINDOW:
        cleaned = strip_degenerate_runs(cleaned, DEGENERATION_TRANSCRIPT_MARKER)
    return cleaned


def _clean_summary_record(rec: dict) -> int:
    """Clean a compaction-summary USER record in place; return blocks changed."""
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return 0
    content = msg.get("content")
    if isinstance(content, str):
        cleaned = clean_model_text(content)
        if cleaned == content:
            return 0
        msg["content"] = cleaned if cleaned.strip() else EMPTY_FILLER
        return 1
    n = 0
    for block in content if isinstance(content, list) else ():
        text = block.get("text") if isinstance(block, dict) and block.get("type") == "text" else None
        if not isinstance(text, str):
            continue
        cleaned = clean_model_text(text)
        if cleaned != text:
            block["text"] = cleaned if cleaned.strip() else EMPTY_FILLER
            n += 1
    return n


def _clean_line(line: str) -> tuple[str, int]:
    """Return (line, n_blocks_changed) for one transcript record."""
    is_summary = '"isCompactSummary":true' in line
    if ('"assistant"' not in line and not is_summary) or not (
        "<invoke" in line
        or _has_glitch_word(line)
        # A degenerate run needs a full detector window; shorter lines can't hold one.
        or len(line) >= _DEGEN_WINDOW
    ):
        return line, 0
    try:
        rec = json.loads(line)
    except (ValueError, TypeError):
        return line, 0  # a torn last line from a killed writer stays as-is
    if not isinstance(rec, dict):
        return line, 0
    if rec.get("type") == "user" and rec.get("isCompactSummary") is True:
        n = _clean_summary_record(rec)
        if not n:
            return line, 0
        newline = "\n" if line.endswith("\n") else ""
        return json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + newline, n
    if rec.get("type") != "assistant":
        return line, 0
    content = (rec.get("message") or {}).get("content")
    if not isinstance(content, list):
        return line, 0
    n = 0
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_input = block.get("input")
            cleaned_input = strip_degenerate_in_value(
                tool_input, DEGENERATION_TRANSCRIPT_MARKER
            )
            if cleaned_input is not tool_input:
                block["input"] = cleaned_input
                n += 1
            continue
        if not (isinstance(block, dict) and block.get("type") == "text"):
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        cleaned = clean_model_text(text)
        if cleaned != text:
            block["text"] = cleaned if cleaned.strip() else EMPTY_FILLER
            n += 1
    if not n:
        return line, 0
    newline = "\n" if line.endswith("\n") else ""
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + newline, n


def clean_transcript_leaks(path: Path) -> int:
    """Strip leak text and degenerate runs from ``path`` in place; return
    blocks changed.

    Blocking file I/O — call via ``asyncio.to_thread``. Must only run while no
    CLI process is writing the file (the caller kills it first). Never raises:
    a cleaning failure must not block the restart, which still --resumes.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        out, total = [], 0
        for line in lines:
            new_line, n = _clean_line(line)
            out.append(new_line)
            total += n
        if not total:
            return 0
        tmp = path.with_name(path.name + ".leakclean.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(out)
        os.replace(tmp, path)
        return total
    except Exception as exc:  # noqa: BLE001 — fail-soft by contract
        logger.warning("leak_transcript.clean_failed path=%s: %s", path, exc)
        return 0
