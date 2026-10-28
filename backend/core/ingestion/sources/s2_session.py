"""S2 whole-session source with trusted user-utterance and tainted main passes.

The source reads every transcript page, excludes user events already claimed by S1,
enqueues the trusted pass before the main pass, and records zero-output attempts.

- ``windows`` splits a pass's input into consecutive windows of at most
  ``WINDOW_CHARS`` serialized characters along item boundaries; one item larger than a
  window is cut into numbered ``part`` fragments. Nothing is dropped. Each window is one
  extraction call with its own attempt record (found in the 2026-10-28 rehearsal: 15 of
  37 real sessions in a week were over 200K tokens, 3 over 1M, so one call per session
  failed). A unit repeated across windows is deduplicated by the writer as usual.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from typing import Any

from core.ingestion import api_v2
from core.ingestion.store import connect_ro

from . import common
from .extract_llm import EXTRACTOR_VERSION

logger = logging.getLogger(__name__)

PAGE_SIZE = 200
# 150,000 characters stays under a 200K-token window even if every character is CJK
# (about one token each), with room for the prompt, schema and reply.
WINDOW_CHARS = 150_000


def windows(items: list[dict[str, Any]], key: str, *, limit: int = WINDOW_CHARS
            ) -> list[list[dict[str, Any]]]:
    """Consecutive windows of ``items``; ``key`` names an item's id for its fragments."""
    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for item in items:
        text = json.dumps(item, ensure_ascii=False, default=str)
        if len(text) > limit:
            if current:
                out.append(current)
                current, size = [], 0
            step = limit - 200  # room for the fragment's own fields
            pieces = [text[i:i + step] for i in range(0, len(text), step)]
            for index, piece in enumerate(pieces, 1):
                out.append([{"part_of": item.get(key), "part": index, "parts": len(pieces),
                             "json_fragment": piece}])
            continue
        if current and size + len(text) + 2 > limit:
            out.append(current)
            current, size = [], 0
        current.append(item)
        size += len(text) + 2
    if current:
        out.append(current)
    return out
_ALLOWED = ("cognition", "event", "reference")


async def _whole_transcript(session_id: str) -> list[dict[str, Any]]:
    from database import db
    pages: list[list[dict[str, Any]]] = []
    before: str | None = None
    while True:
        page = await db.messages.list_by_session_paginated(
            session_id, limit=PAGE_SIZE, before_id=before)
        if not page:
            break
        pages.append(list(page))
        if len(page) < PAGE_SIZE:
            break
        before = str(page[0]["id"])
    return [row for page in reversed(pages) for row in page]


def _input_rows(session_id: str) -> list[dict[str, Any]]:
    from core import session_inputs
    path = session_inputs._db_path()
    if not path.exists():
        return []
    try:
        uri = f"file:{path.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT event_id, kind, trust, content_hash, ref, created_at "
                "FROM session_inputs WHERE session_id=? ORDER BY created_at, event_id",
                (session_id,),).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []


def _claimed_by_s1(session_id: str) -> set[str]:
    try:
        conn = connect_ro()
        try:
            rows = conn.execute(
                "SELECT evidence_range FROM extraction_attempts WHERE source='S1'").fetchall()
        finally:
            conn.close()
    except (OSError, sqlite3.Error):
        return set()
    claimed: set[str] = set()
    for row in rows:
        try:
            value = json.loads(row[0])
            if value.get("session_id") == session_id and value.get("event_id"):
                claimed.add(str(value["event_id"]).removeprefix("event:"))
        except (TypeError, ValueError, AttributeError):
            continue
    return claimed


def _root_ids(event_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve transcript event ids through data.db's authoritative root bindings.

    Fail closed (§9 anti selective citation): an event with no bound root, a missing
    data.db or a read error keeps the raw event id in the read set, and provenance
    treats an unknown read-set root as tainted. The read set never shrinks silently."""
    if not event_ids:
        return ()
    from core import session_inputs
    path = session_inputs._db_path()
    if not path.exists():
        return tuple(event_ids)
    try:
        uri = f"file:{path.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
            marks = ",".join("?" for _ in event_ids)
            rows = conn.execute(
                f"SELECT event_id,root_id FROM session_inputs WHERE event_id IN ({marks})",
                event_ids,
            ).fetchall()
        by_event = {str(event_id): str(root_id) for event_id, root_id in rows if root_id}
        return tuple(by_event.get(event_id, event_id) for event_id in event_ids)
    except (OSError, sqlite3.Error) as exc:
        logger.warning("S2 read-root lookup failed; read set kept as unresolved: %s", exc)
        return tuple(event_ids)


def _record_empty(session_id: str, extraction_pass: str,
                  evidence_range: dict[str, Any]) -> None:
    attempt = api_v2.ExtractionAttempt(
        source="S2", evidence_range=evidence_range,
        extractor_version=EXTRACTOR_VERSION,
        attempt_id=f"attempt:{uuid.uuid4().hex}")
    api_v2.record_extraction_attempt(attempt, status="no_unit")


def _drop_not_self_contained(outcomes: list[Any]) -> list[Any]:
    """Reserved for pipeline outcomes; source filtering happens before enqueue."""
    return outcomes


def _obviously_deictic(unit: Any) -> bool:
    text = " ".join(str(getattr(unit, field) or "")
                    for field in ("title", "what", "how", "why")
                    if hasattr(unit, field)).strip().lower()
    markers = ("按 a", "按 b", "上面", "刚才", "这个方案", "那个方案",
               "as above", "do that", "this one", "the previous one")
    return any(marker in text for marker in markers)


def _trusted_pass(session_id: str, rows: list[dict[str, Any]], claimed: set[str],
                  project_snapshot: dict[str, Any] | None, llm: Any | None) -> list[Any]:
    from core import session_inputs
    authored = getattr(session_inputs, "user_authored_text", None)
    selected = [row for row in rows if row["kind"] == "user_message"
                and row["event_id"] not in claimed]
    anchors, parts = [], []
    if authored is not None:
        for row in selected:
            text = authored(str(row["event_id"]))
            if text:
                anchors.append(f"event:{row['event_id']}")
                parts.append({"event_id": row["event_id"], "text": text})
    evidence_range = {"session_id": session_id, "pass": "user_utterance",
                      "events": [row["event_id"] for row in selected]}
    if not parts:
        _record_empty(session_id, "user_utterance", evidence_range)
        return []
    chunks = windows(parts, "event_id")
    outcomes: list[Any] = []
    for index, chunk in enumerate(chunks, 1):
        events = sorted({str(p.get("event_id") or p.get("part_of")) for p in chunk})
        outcomes += common.extract_and_enqueue(
            "S2", session_id, json.dumps(chunk, ensure_ascii=False),
            tuple(f"event:{event}" for event in events),
            evidence_range={**evidence_range, "events": events,
                            "window": index, "windows": len(chunks)},
            session_id=session_id, read_root_ids=_root_ids(tuple(events)),
            project_snapshot=project_snapshot, extraction_pass="user_utterance",
            allowed_kinds=_ALLOWED, llm=llm,
            unit_filter=lambda unit: "not_self_contained" if _obviously_deictic(unit) else None)
    return outcomes


def _main_pass(session_id: str, transcript: list[dict[str, Any]],
               rows: list[dict[str, Any]], claimed: set[str],
               project_snapshot: dict[str, Any] | None, llm: Any | None) -> list[Any]:
    owned = [row for row in rows if row["event_id"] not in claimed]
    evidence = tuple(f"event:{row['event_id']}" for row in owned)
    evidence_range = {"session_id": session_id, "pass": "main",
                      "message_count": len(transcript)}
    if not transcript:
        _record_empty(session_id, "main", evidence_range)
        return []
    # Every window keeps the whole session's read set: the conservative lineage (a unit
    # can be no cleaner than anything the session read), not a per-window guess.
    roots = _root_ids(tuple(row["event_id"] for row in owned))
    chunks = windows(transcript, "id")
    outcomes: list[Any] = []
    for index, chunk in enumerate(chunks, 1):
        outcomes += common.extract_and_enqueue(
            "S2", session_id, json.dumps(chunk, ensure_ascii=False, default=str), evidence,
            evidence_range={**evidence_range, "window": index, "windows": len(chunks)},
            session_id=session_id, read_root_ids=roots,
            project_snapshot=project_snapshot, extraction_pass="main",
            allowed_kinds=_ALLOWED, llm=llm)
    return outcomes


async def extract_session(session_id: str, *, project_snapshot: dict[str, Any] | None = None,
                          llm: Any | None = None) -> dict[str, list[Any]]:
    """Run both S2 passes in one ordered batch; the trusted pass always goes first."""
    transcript = await _whole_transcript(session_id)
    rows = await asyncio.to_thread(_input_rows, session_id)
    claimed = await asyncio.to_thread(_claimed_by_s1, session_id)
    trusted = await asyncio.to_thread(
        _trusted_pass, session_id, rows, claimed, project_snapshot, llm)
    main = await asyncio.to_thread(
        _main_pass, session_id, transcript, rows, claimed, project_snapshot, llm)
    return {"user_utterance": trusted, "main": main}


class SessionExtractionHook:
    """Single post-session replacement for the retired extraction hook family."""

    name = "cdf_v2_session_extraction"

    async def execute(self, context: Any) -> None:
        if not context.deleting:
            await extract_session(context.session_id)
