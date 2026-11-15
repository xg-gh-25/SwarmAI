"""Keep a CLI session transcript's images and documents under the request-size limit.

Why this exists
---------------
The model service rejects a request whose body is too large (the CLI's own limit is
32 MiB; Bedrock answers ``400 Input is too long.``). In a screenshot-heavy session the
body is almost all base64 images: every image an agent Reads stays in the history as
a tool_result, and ``--resume`` sends all of them again on every turn. The CLI's
autocompact keys on the token count, which stays far under the window (each image is
a few thousand tokens but up to a megabyte of base64), so nothing sheds them and the
session ends in a loop where every send is rejected (observed: 84 screenshots, about
30 MB of base64, 735K tokens).

This module measures the media in a transcript's live history and removes the oldest
images/documents from it, replacing each with a short text marker, so ``--resume``
sends a smaller history. Every text, tool call and tool result stays.

Public symbols:
- ``MEDIA_SHED_TRIGGER_BYTES`` — live media above this sheds before the next send.
- ``MEDIA_SHED_TARGET_BYTES``  — a shed removes the oldest media until at most this
  much is left.
- ``live_media_bytes(path)``   — bytes of image/document data the next ``--resume``
  would send, or None when the file cannot be read.
- ``shed_transcript_media(path, target_bytes)`` — back the file up byte for byte,
  then strip the oldest media in place (atomic replace); returns a ``ShedResult``.
- ``sweep_orphan_backups()`` — delete the shed backups whose transcript is gone
  (the CLI pruned it, or the session was deleted); a backup lives as long as the
  transcript it was taken from.

"Live history" is what ``--resume`` sends: the main-chain records after the last
``compact_boundary`` (records before it were compacted away, and sidechain records
belong to sub-agents). Media is an ``image`` or ``document`` block at the top level of
a user message, or inside a ``tool_result``'s content list.

Blocking file I/O: call both functions through ``asyncio.to_thread``. Shedding must
only run while no CLI process writes the file (the caller stops it first). Neither
function raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
#: The CLI's own request limit (33,554,432 bytes in the bundled CLI); the model
#: service enforces about the same.
REQUEST_LIMIT_BYTES = 32 * MIB
#: Live media above this sheds before the next send (75% of the limit, leaving room
#: for the text of a long session and the system prompt).
MEDIA_SHED_TRIGGER_BYTES = 24 * MIB
#: A shed keeps at most this much media, the most recent first. With 12 MiB kept, about
#: two dozen 500 KB screenshots fit before the next shed, so sheds (each also rewrites
#: the prompt cache) stay rare.
MEDIA_SHED_TARGET_BYTES = 12 * MIB

BACKUP_INFIX = ".pre-media-shed-"
#: A backup is removed only once the sweep has seen its transcript missing for at
#: least this long, so a transcript that is briefly absent (being moved, a volume not
#: mounted yet) never costs the backup.
ORPHAN_BACKUP_MIN_AGE_S = 24 * 3600
# backup path -> when a sweep first saw its transcript missing. In memory: a daemon
# restart starts the wait again, which only keeps a backup longer.
_ORPHAN_SINCE: dict[str, float] = {}

_MEDIA_TYPES = ("image", "document")
# Read and write with surrogateescape: a line torn mid-character by a killed writer
# round-trips byte for byte instead of failing the whole read.
_ENC = {"encoding": "utf-8", "errors": "surrogateescape"}
_MEDIA_LINE_RE = re.compile(r'"type"\s*:\s*"(?:image|document)"')
_BOUNDARY_RE = re.compile(r'"compact_boundary"')


@dataclass(frozen=True)
class ShedResult:
    """What one shed did. ``removed_blocks == 0`` means the file was not rewritten."""

    removed_blocks: int = 0
    removed_bytes: int = 0
    remaining_bytes: int = 0
    backup_path: Optional[Path] = None
    error: Optional[str] = None


def _marker(file_path: Optional[str]) -> str:
    where = f": {file_path}" if file_path else ""
    return ("[an image or document was removed from this conversation to fit the "
            f"model's request size limit{where}]")


def _block_bytes(block: dict) -> int:
    source = block.get("source")
    data = source.get("data") if isinstance(source, dict) else None
    if isinstance(data, str):
        return len(data)
    return len(json.dumps(block, ensure_ascii=False))


def _is_media(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in _MEDIA_TYPES


def _last_boundary(lines: list[str]) -> int:
    """Index of the last ``compact_boundary`` record, -1 when there is none."""
    for i in range(len(lines) - 1, -1, -1):
        if not _BOUNDARY_RE.search(lines[i]):
            continue
        try:
            rec = json.loads(lines[i])
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict) and rec.get("type") == "system" \
                and rec.get("subtype") == "compact_boundary":
            return i
    return -1


def _media_record(line: str) -> Optional[dict]:
    """The parsed main-chain user record of a line that holds media, else None."""
    if not _MEDIA_LINE_RE.search(line):
        return None
    try:
        rec = json.loads(line)
    except (ValueError, TypeError):
        return None  # a torn last line from a killed writer stays as-is
    if not isinstance(rec, dict) or rec.get("type") != "user" or rec.get("isSidechain"):
        return None
    msg = rec.get("message")
    if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
        return None
    return rec


def _media_slots(rec: dict) -> list[tuple[list, int, Optional[str]]]:
    """Every media block of a record as (the list holding it, its index, tool_use_id)."""
    out: list[tuple[list, int, Optional[str]]] = []
    content = rec["message"]["content"]
    for i, block in enumerate(content):
        if _is_media(block):
            out.append((content, i, None))
        elif isinstance(block, dict) and block.get("type") == "tool_result" \
                and isinstance(block.get("content"), list):
            inner = block["content"]
            for j, sub in enumerate(inner):
                if _is_media(sub):
                    out.append((inner, j, block.get("tool_use_id")))
    return out


def live_media_bytes(path: Path) -> Optional[int]:
    """Bytes of image/document data in the live history of ``path``; None if unreadable.

    Only lines that name a media type are parsed, so a transcript of text costs one
    read and a regex per line."""
    try:
        with open(path, **_ENC) as fh:
            lines = fh.readlines()
    except (OSError, ValueError) as exc:
        logger.warning("transcript_media.measure_failed path=%s: %s", path, exc)
        return None
    total = 0
    for line in lines[_last_boundary(lines) + 1:]:
        rec = _media_record(line)
        if rec is None:
            continue
        for holder, idx, _tid in _media_slots(rec):
            total += _block_bytes(holder[idx])
    return total


def _read_paths(lines: list[str]) -> dict[str, str]:
    """tool_use_id -> file_path for the Read calls in ``lines`` (for the marker text)."""
    out: dict[str, str] = {}
    for line in lines:
        if '"tool_use"' not in line or '"file_path"' not in line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        content = (rec.get("message") or {}).get("content") if isinstance(rec, dict) else None
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get("type") == "tool_use":
                fp = (block.get("input") or {}).get("file_path")
                if isinstance(block.get("id"), str) and isinstance(fp, str):
                    out[block["id"]] = fp
    return out


def shed_transcript_media(path: Path, target_bytes: int = MEDIA_SHED_TARGET_BYTES) -> ShedResult:
    """Strip the oldest media from the live history of ``path`` until at most
    ``target_bytes`` is left.

    Before any rewrite the file is copied byte for byte (``shutil.copy2``) to
    ``<name>.pre-media-shed-<UTC time>.bak`` next to it; the name does not end in
    ``.jsonl``, so the CLI never lists it as a session; it lives as long as the
    transcript does (``sweep_orphan_backups``).
    The rewrite goes to a temp file that replaces the original. Lines without a
    removed block keep their exact bytes. Never raises: a failure returns
    ``removed_blocks=0`` with ``error`` set and leaves the file as it was.
    """
    try:
        return _shed(path, target_bytes)
    except Exception as exc:  # noqa: BLE001 — fail-soft by contract (the caller recovers)
        logger.warning("transcript_media.shed_failed path=%s: %s", path, exc)
        return ShedResult(error=f"shed failed: {type(exc).__name__}: {exc}")


def _shed(path: Path, target_bytes: int) -> ShedResult:
    t0 = time.monotonic()
    try:
        with open(path, **_ENC) as fh:
            lines = fh.readlines()
    except (OSError, ValueError) as exc:
        return ShedResult(error=f"read failed: {exc}")
    start = _last_boundary(lines) + 1
    records: dict[int, dict] = {}
    slots: list[tuple[int, list, int, Optional[str], int]] = []
    for i in range(start, len(lines)):
        rec = _media_record(lines[i])
        if rec is None:
            continue
        records[i] = rec
        for holder, idx, tid in _media_slots(rec):
            slots.append((i, holder, idx, tid, _block_bytes(holder[idx])))
    total = sum(s[4] for s in slots)
    if total <= target_bytes:
        return ShedResult(remaining_bytes=total)

    paths = _read_paths(lines)
    changed: set[int] = set()
    removed = removed_bytes = 0
    for line_idx, holder, idx, tid, size in slots:  # file order = oldest first
        if total <= target_bytes:
            break
        holder[idx] = {"type": "text", "text": _marker(paths.get(tid) if tid else None)}
        total -= size
        removed_bytes += size
        removed += 1
        changed.add(line_idx)

    out = list(lines)
    for i in changed:
        newline = "\n" if lines[i].endswith("\n") else ""
        # ensure_ascii: a lone surrogate the CLI saved as an escape (a tool output cut
        # mid-emoji) stays an escape instead of failing the UTF-8 write.
        out[i] = json.dumps(records[i], ensure_ascii=True, separators=(",", ":")) + newline

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}{BACKUP_INFIX}{stamp}.bak")
    tmp = path.with_name(path.name + ".mediashed.tmp")
    try:
        shutil.copy2(path, backup)
        os.utime(backup, None)  # its mtime says when the backup was made
        mode = stat.S_IMODE(os.stat(path).st_mode)
        with open(tmp, "w", **_ENC) as fh:
            # the transcript's own mode (the CLI writes it 0600), before any byte lands
            os.chmod(tmp, mode)
            fh.writelines(out)
        os.replace(tmp, path)
    except (OSError, ValueError) as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        logger.warning("transcript_media.shed_failed path=%s: %s", path, exc)
        return ShedResult(remaining_bytes=total + removed_bytes, error=f"write failed: {exc}",
                          backup_path=backup if backup.exists() else None)
    logger.warning(
        "transcript_media.shed path=%s removed_blocks=%d removed_bytes=%d "
        "remaining_bytes=%d backup=%s dur_ms=%d", path, removed, removed_bytes, total,
        backup.name, (time.monotonic() - t0) * 1000,
    )
    return ShedResult(removed_blocks=removed, removed_bytes=removed_bytes,
                      remaining_bytes=total, backup_path=backup)


def sweep_orphan_backups(projects_dir: Optional[Path] = None,
                         now: Optional[float] = None) -> dict:
    """Delete shed backups whose transcript no longer exists; return counts.

    A backup holds the images a shed removed, so it is kept for as long as the
    transcript it came from: once the CLI's own retention (or a session delete)
    removed ``<sid>.jsonl``, its ``<sid>.jsonl.pre-media-shed-*.bak`` files go too,
    once sweeps have seen the transcript missing for ``ORPHAN_BACKUP_MIN_AGE_S``
    (a transcript that comes back resets the wait; any directory entry with its
    name, a dangling symlink too, counts as present). Never raises; blocking I/O
    (call through ``asyncio.to_thread``)."""
    from .leak_transcript import _projects_dir

    root = projects_dir or _projects_dir()
    now = time.time() if now is None else now
    out = {"removed": 0, "removed_bytes": 0, "kept": 0, "errors": 0}
    try:
        backups = list(root.glob(f"*/*.jsonl{BACKUP_INFIX}*.bak")) if root.is_dir() else []
    except OSError as exc:
        logger.warning("transcript_media.sweep_failed root=%s: %s", root, exc)
        out["errors"] += 1
        return out
    for bak in backups:
        try:
            transcript = bak.with_name(bak.name.split(BACKUP_INFIX, 1)[0])
            key = str(bak)
            if os.path.lexists(transcript):
                _ORPHAN_SINCE.pop(key, None)
                out["kept"] += 1
                continue
            since = _ORPHAN_SINCE.setdefault(key, now)
            if now - since < ORPHAN_BACKUP_MIN_AGE_S:
                out["kept"] += 1
                continue
            st = bak.stat()
            bak.unlink()
            _ORPHAN_SINCE.pop(key, None)
            out["removed"] += 1
            out["removed_bytes"] += st.st_size
        except OSError as exc:
            logger.warning("transcript_media.sweep_failed backup=%s: %s", bak, exc)
            out["errors"] += 1
    if out["removed"] or out["errors"]:
        logger.info("transcript_media.sweep removed=%d removed_bytes=%d kept=%d errors=%d",
                    out["removed"], out["removed_bytes"], out["kept"], out["errors"])
    return out
