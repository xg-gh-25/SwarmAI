"""Context-window usage of a session — the backend is the only authority.

The chat input's context ring shows how full a session's context window is.
That number belongs to the backend: it is either the size of the last request
the main agent sent (live), or — when no subprocess holds the conversation —
what ``--resume`` would reload from the CLI transcript, or nothing at all when
the next spawn rebuilds the context from scratch. The frontend only displays
whatever snapshot the backend last produced.

A snapshot uses the ``context_warning`` SSE event shape that every frontend
consumer already reads, plus provenance:

    {"type": "context_warning", "level": "ok|warn|critical", "pct": int|None,
     "tokensEst": int, "window": int, "message": str,
     "source": "live|transcript|none", "basis": <see below>,
     "measuredAt": float}   # epoch seconds — consumers keep the newest

Public symbols:
- ``build_snapshot``           — snapshot from a token count (level/pct/message
  come from ``PromptBuilder.build_context_warning``, the one place thresholds live).
- ``none_snapshot``            — "no live context; the next turn rebuilds it".
- ``read_transcript_usage``    — newest context size recorded in a CLI transcript.
- ``snapshot_from_transcript`` — the above, resolved from an sdk session id.

Transcript reading is file I/O: call it only off the event loop (the read
endpoint uses ``asyncio.to_thread``), never from the streaming path.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ``basis`` values:
#   last_call     — the last main-agent request's input (AssistantMessage.usage)
#   post_compact  — the CLI's own post-compaction size (compact_boundary)
#   turn_average  — fallback when a turn produced no per-request usage
#   none          — nothing to report (``none_snapshot``)

_NONE_MESSAGE = "No live context — the next turn rebuilds it."


def build_snapshot(
    tokens: Optional[int],
    model: Optional[str],
    *,
    source: str,
    basis: str,
    measured_at: Optional[float] = None,
    is_resumed_first: bool = False,
) -> Optional[dict]:
    """Return a snapshot for ``tokens`` of context, or None when not positive."""
    from .prompt_builder import PromptBuilder

    evt = PromptBuilder.build_context_warning(
        tokens, model, is_resumed_first=is_resumed_first,
    )
    if evt is None:
        return None
    evt["window"] = PromptBuilder.get_model_context_window(model)
    evt["source"] = source
    evt["basis"] = basis
    evt["measuredAt"] = time.time() if measured_at is None else measured_at
    return evt


def none_snapshot(measured_at: Optional[float] = None) -> dict:
    """A snapshot meaning there is no context to report (the ring shows empty)."""
    return {
        "type": "context_warning", "level": "ok", "pct": None, "tokensEst": 0,
        "window": None, "message": _NONE_MESSAGE, "source": "none",
        "basis": "none",
        "measuredAt": time.time() if measured_at is None else measured_at,
    }


@dataclass(frozen=True)
class TranscriptUsage:
    """Newest context size found in a transcript.

    ``tokens`` is None when the newest event is a compaction that did not report
    its resulting size — the old pre-compaction value is no longer true.
    """
    tokens: Optional[int]
    model: Optional[str]
    basis: str
    measured_at: Optional[float]


def _parse_ts(value: object) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _usage_tokens(usage: object) -> int:
    if not isinstance(usage, dict):
        return 0
    from .prompt_builder import PromptBuilder
    return PromptBuilder.sum_usage_input_tokens(usage)


def _real_model(row: dict) -> Optional[str]:
    msg = row.get("message")
    model = msg.get("model") if isinstance(msg, dict) else None
    # Synthetic rows carry "<synthetic>"; they say nothing about the window.
    if isinstance(model, str) and model and not model.startswith("<"):
        return model
    return None


def _iter_lines_backwards(path: Path, chunk_size: int):
    """Yield the file's lines from the last to the first, reading from EOF."""
    with path.open("rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        tail = b""
        while pos > 0:
            step = min(chunk_size, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + tail
            lines = buf.split(b"\n")
            tail = lines[0]  # may be partial — completed by the next chunk
            for raw in reversed(lines[1:]):
                if raw.strip():
                    yield raw
        if tail.strip():
            yield tail


# read_transcript_usage results keyed by the file's exact state
# (path, mtime_ns, size): an unchanged file gives the same answer, so a hit can
# never be stale. Bounded LRU; guarded because reads run on worker threads.
_CACHE_MAX = 64
_cache: "OrderedDict[tuple, Optional[TranscriptUsage]]" = OrderedDict()
_cache_lock = threading.Lock()


def read_transcript_usage(path: Path, *, chunk_size: int = 256 * 1024) -> Optional[TranscriptUsage]:
    """Newest context size recorded in a CLI transcript (cached by file state)."""
    try:
        st = path.stat()
    except OSError as exc:
        logger.warning("context_usage: cannot stat transcript %s: %s", path, exc)
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    result = _scan_transcript_usage(path, chunk_size)
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return result


def _scan_transcript_usage(path: Path, chunk_size: int) -> Optional[TranscriptUsage]:
    """Return the newest context size recorded in a CLI transcript, or None.

    Walks backwards from EOF over main-chain records (``isSidechain`` rows belong
    to sub-agents, which have their own context). The first relevant record wins:

    * an assistant row with non-zero usage → that request's input size;
    * a ``compact_boundary`` → its ``postTokens`` (None if absent); the window's
      model is taken from the nearest earlier assistant row.

    Zero-usage assistant rows (written right after a compaction) are skipped.
    """
    found: Optional[TranscriptUsage] = None
    try:
        for raw in _iter_lines_backwards(path, chunk_size):
            # Only assistant rows and compaction boundaries matter; skipping the
            # rest before json.loads avoids parsing large tool-result rows.
            if b'"assistant"' not in raw and b"compact_boundary" not in raw:
                continue
            try:
                row = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(row, dict) or row.get("isSidechain"):
                continue
            kind = row.get("type")
            if found is not None:
                # Only the model is still missing (compaction found first).
                if kind == "assistant" and _real_model(row):
                    return TranscriptUsage(found.tokens, _real_model(row),
                                           found.basis, found.measured_at)
                continue
            if kind == "system" and row.get("subtype") == "compact_boundary":
                md = row.get("compactMetadata")
                post = md.get("postTokens") if isinstance(md, dict) else None
                tokens = post if isinstance(post, int) and post > 0 else None
                found = TranscriptUsage(tokens, None, "post_compact",
                                        _parse_ts(row.get("timestamp")))
                continue
            if kind == "assistant":
                msg = row.get("message")
                tokens = _usage_tokens(msg.get("usage") if isinstance(msg, dict) else None)
                if tokens > 0:
                    return TranscriptUsage(tokens, _real_model(row), "last_call",
                                           _parse_ts(row.get("timestamp")))
    except OSError as exc:
        logger.warning("context_usage: cannot read transcript %s: %s", path, exc)
        return None
    return found


def snapshot_from_transcript(
    sdk_session_id: Optional[str],
    fallback_model: Optional[str],
    requested_at: Optional[float] = None,
) -> dict:
    """Snapshot of what ``--resume`` of ``sdk_session_id`` would reload.

    No id, no transcript, or nothing measurable in it → ``none_snapshot``.
    ``requested_at`` (the time the request arrived) stamps the answer: the
    answer describes the backend's state as of that request, so it must beat
    any older value the frontend holds (a transcript row's own timestamp could
    lose to a "none" pushed moments earlier) while the caller's post-read live
    check keeps it from beating a value recorded during the read. Without it,
    the transcript row's timestamp is used.
    Blocking file I/O: run in a worker thread.
    """
    if not sdk_session_id:
        return none_snapshot(measured_at=requested_at)
    from .leak_transcript import find_transcript

    path = find_transcript(sdk_session_id)  # validates the id: no path/glob injection
    if path is None:
        return none_snapshot(measured_at=requested_at)
    usage = read_transcript_usage(path)
    stamp = requested_at if requested_at is not None else (usage.measured_at if usage else None)
    if usage is None or usage.tokens is None:
        return none_snapshot(measured_at=stamp)
    snap = build_snapshot(
        usage.tokens, usage.model or fallback_model,
        source="transcript", basis=usage.basis, measured_at=stamp,
    )
    return snap if snap is not None else none_snapshot(measured_at=stamp)
