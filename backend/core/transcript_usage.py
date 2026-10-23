"""Measured token usage from Claude CLI transcripts, per API response.

Why this exists: the daemon's ``token_usage`` table gets one row when a TURN ends
(the CLI's result message), and a pipeline run usually lives inside one long
turn. A stage or run window therefore often holds no row at all, and every figure
built on those rows reads "unavailable" or 0. The CLI transcript
(``<CLAUDE_CONFIG_DIR>/projects/<encoded-cwd>/<sdk-session-id>.jsonl``) carries
``message.usage`` on every assistant record with a UTC timestamp, so any window
can be measured while the turn is still running. Sub-agents write their own files
under ``<transcript stem>/subagents/agent-*.jsonl``.

The transcript has no price, so this module measures TOKENS only. A dollar figure
comes from ``token_usage`` rows (the CLI's own ``total_cost_usd``) or not at all.

Public symbols:
- ``valid_session_id(sid)`` — the SDK session id check, before an id is stored
- ``resolve_transcripts(ids)`` — transcript paths for SDK session ids that exist
  (each id is checked by ``leak_transcript.find_transcript``; anything that is not
  a session id never becomes a path)
- ``find_subagent_transcripts(transcripts)`` — the sub-agent files of those transcripts
- ``usage_in_window(paths, lo, hi, include_subagents=True)`` — token sums of the
  responses inside ``[lo, hi]`` (epoch seconds) in ``token_usage``'s key names
  (``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
  ``cache_creation_tokens``), plus ``tokens`` (input + output + cache creation,
  cache reads excluded — the same definition ``artifact_cli._apply_token_cost`` and
  ``run_protocol.usage.measure`` use), ``responses`` and
  ``source="transcript"``; None when no response falls inside the window.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime
from typing import Iterable, Optional

from core.leak_transcript import _SID_RE, find_transcript

_USAGE_KEYS = (
    ("input_tokens", "input_tokens"),
    ("output_tokens", "output_tokens"),
    ("cache_read_input_tokens", "cache_read_tokens"),
    ("cache_creation_input_tokens", "cache_creation_tokens"),
)


# MAX_TRANSCRIPT_IDS / valid_session_id live in the import-free core/session_ids.py
# (the stage commands use them on every call); re-exported here for the readers.
from core.session_ids import MAX_TRANSCRIPT_IDS, valid_session_id  # noqa: E402,F401


def resolve_transcripts(ids: Iterable) -> list[str]:
    """Existing transcript paths for the first ``MAX_TRANSCRIPT_IDS`` valid ids
    (deduped, input order kept)."""
    out: list[str] = []
    seen: set = set()
    valid = [i for i in dict.fromkeys(i for i in (ids or []) if valid_session_id(i))]
    for sid in valid[:MAX_TRANSCRIPT_IDS]:
        path = find_transcript(sid)
        if path is not None and str(path) not in seen:
            seen.add(str(path))
            out.append(str(path))
    return out


def find_subagent_transcripts(transcripts: Iterable[str]) -> list[str]:
    """Sub-agent files of the given transcripts: ``<stem>/subagents/agent-*.jsonl``
    (regular files only — a FIFO of that name would block ``open``)."""
    out: list[str] = []
    for owner in dict.fromkeys(transcripts or []):
        if owner.endswith(".jsonl"):
            found = glob.glob(os.path.join(owner[:-len(".jsonl")], "subagents", "agent-*.jsonl"))
            out.extend(p for p in sorted(found) if os.path.isfile(p))
    return out


def _epoch(value) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _scan(path: str, main_thread: bool, best: dict) -> None:
    """Fold ``path``'s responses into ``best`` {message id: [first ts, usage]}.

    One response is logged as several records with their own timestamps; they are
    merged across the whole file (and across files) BEFORE any window test, and the
    response is placed by its FIRST record, so a response straddling a stage edge is
    counted in exactly one stage. The fullest usage (largest output) is kept. A
    malformed record is skipped, never fatal to the whole measurement."""
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            if '"usage"' not in line:
                continue  # cheap prefilter: only assistant responses carry usage
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict) or rec.get("type") != "assistant":
                    continue
                if main_thread and rec.get("isSidechain"):
                    continue
                t = _epoch(rec.get("timestamp"))
                msg = rec.get("message")
                usage = msg.get("usage") if isinstance(msg, dict) else None
                mid = (msg.get("id") or rec.get("uuid")) if isinstance(msg, dict) else None
                if t is None or not isinstance(usage, dict) or not isinstance(mid, str) or not mid:
                    continue
                prev = best.get(mid)
                if prev is None:
                    best[mid] = [t, usage]
                    continue
                prev[0] = min(prev[0], t)
                if _int(usage.get("output_tokens")) >= _int(prev[1].get("output_tokens")):
                    prev[1] = usage
            except Exception:  # noqa: BLE001 — RecursionError, odd types: skip the record
                continue


def usage_in_window(paths: Iterable[str], lo: float, hi: float,
                    include_subagents: bool = True) -> Optional[dict]:
    """Token sums of the responses inside ``[lo, hi]`` (see module docstring)."""
    mains = list(dict.fromkeys(p for p in (paths or []) if isinstance(p, str)))
    if not mains:
        return None
    best: dict = {}
    for p in mains:
        _scan(p, True, best)
    if include_subagents:
        for p in find_subagent_transcripts(mains):
            _scan(p, False, best)
    inside = [usage for first, usage in best.values() if lo <= first <= hi]
    if not inside:
        return None
    out = {name: 0 for _, name in _USAGE_KEYS}
    for usage in inside:
        for raw, name in _USAGE_KEYS:
            out[name] += _int(usage.get(raw))
    out["tokens"] = out["input_tokens"] + out["output_tokens"] + out["cache_creation_tokens"]
    out["responses"] = len(inside)
    out["source"] = "transcript"
    return out


def usage_for_subagent(path: str) -> Optional[dict]:
    """Token sums for one completed sub-agent transcript, without sidechain filtering."""
    if not isinstance(path, str) or not os.path.isfile(path):
        return None
    best: dict = {}
    _scan(path, False, best)
    if not best:
        return None
    out = {name: 0 for _, name in _USAGE_KEYS}
    for _first, usage in best.values():
        for raw, name in _USAGE_KEYS:
            out[name] += _int(usage.get(raw))
    out["tokens"] = out["input_tokens"] + out["output_tokens"] + out["cache_creation_tokens"]
    out["responses"] = len(best)
    out["source"] = "subagent-transcript"
    return out
