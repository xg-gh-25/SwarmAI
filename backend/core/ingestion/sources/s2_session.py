"""The session record the after-session review reads (S7), and the post-session hook
that queues the review.

S2's own extraction (a user-words pass and a main pass) is gone (design
2026-10-30-cdf-agent-decides D4): the session's own agent reviews it (s7_review), reading
this record. What stays here:

- ``windows`` splits the record into consecutive windows of at most ``limit``
  serialized characters along item boundaries; one item larger than a window is cut into
  numbered ``part`` fragments. Nothing is dropped.
- ``window_roots`` registers what a window shows that the send path did not (agent and
  tool content), so the review's read set is exactly what it saw.
- ``_transcript_sync``, ``_input_rows``, ``_user_events_by_message``, ``_with_anchors``,
  ``_root_ids``: the record and its citable anchors.
- ``SessionExtractionHook``: queues the session's review (s2_jobs) when it goes idle.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from typing import Any



logger = logging.getLogger(__name__)

PAGE_SIZE = 200
# 150,000 characters stays under a 200K-token window even if every character is CJK
# (about one token each), with room for the prompt, schema and reply.
WINDOW_CHARS = 150_000


def windows(items: list[dict[str, Any]], key: str, *, limit: int = WINDOW_CHARS,
            overlap: float = 0.0) -> list[list[dict[str, Any]]]:
    """Consecutive windows of ``items``; ``key`` names an item's id for its fragments.

    With ``overlap`` > 0 a window opens with the tail of the previous window (whole
    items, at most ``overlap * limit`` characters), so a decision and its reason that
    straddle a boundary can still be read together. Carried items are repeats; they
    are never the only content of a window.
    """
    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    sizes: list[int] = []
    carried = 0  # leading items of ``current`` repeated from the previous window

    def close() -> None:
        nonlocal current, sizes, carried
        if len(current) > carried:
            out.append(current)
        tail: list[dict[str, Any]] = []
        tail_sizes: list[int] = []
        budget = int(limit * overlap)
        for item, item_size in zip(reversed(current[carried:]), reversed(sizes[carried:])):
            if item_size > budget:
                break
            budget -= item_size
            tail.insert(0, item)
            tail_sizes.insert(0, item_size)
        current, sizes, carried = tail, tail_sizes, len(tail)

    for item in items:
        text = json.dumps(item, ensure_ascii=False, default=str)
        if len(text) > limit:
            close()
            current, sizes, carried = [], [], 0
            step = limit - 200  # room for the fragment's own fields
            pieces = [text[i:i + step] for i in range(0, len(text), step)]
            for index, piece in enumerate(pieces, 1):
                out.append([{"part_of": item.get(key), "role": item.get("role"), "part": index,
                             "parts": len(pieces), "json_fragment": piece}])
            continue
        if current and sum(sizes) + len(text) + 2 > limit:
            if len(current) == carried:
                current, sizes, carried = [], [], 0  # repeats alone never make a window
            else:
                close()
                if current and sum(sizes) + len(text) + 2 > limit:
                    current, sizes, carried = [], [], 0
        current.append(item)
        sizes.append(len(text) + 2)
    if len(current) > carried:
        out.append(current)
    return out




_S2_REF = "s2:"  # ref prefix of the inputs S2 registers itself


def _existing_input(conn: sqlite3.Connection, session_id: str, ref: str,
                    content_hash: str) -> list[str]:
    rows = conn.execute(
        "SELECT event_id, ref, content_hash FROM session_inputs WHERE session_id=? "
        "AND (ref=? OR ref LIKE ?)", (session_id, ref, ref + "#field:%")).fetchall()
    if any(row[1] == ref and row[2] == content_hash for row in rows):
        return [row[0] for row in rows]
    return []


def _blocks(message: dict[str, Any]) -> list[tuple[str, Any]]:
    """(block type, content) of one transcript row; a string content is one block."""
    content = message.get("content")
    if isinstance(content, list):
        return [(str(b.get("type") or "text"), b.get("content", b.get("text", b)))
                for b in content if isinstance(b, dict)]
    return [("text", content)]


def window_roots(session_id: str, chunk: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    """Register what this window shows the extractor that the send path did not.

    Returns, per chunk item, the event ids registered for it (empty for user rows,
    which the send path registered): the main pass shows them as that item's anchors.

    Tool results are external (``record_tool_result``, measured fields split out); the
    agent's own text, thinking and tool calls are ``agent``. User text was registered
    when it was sent. The same (ref, content) is reused, not registered twice. Design
    §12 r16.13: without this the main pass read only user roots and came out untainted.
    """
    import hashlib
    from core import session_inputs
    plan: list[tuple[int, str, str, Any]] = []  # (item index, kind, ref, content)
    for position, item in enumerate(chunk):
        if "json_fragment" in item:
            if item.get("role") != "user":
                plan.append((position, "tool_result",
                             f"{_S2_REF}{item.get('part_of')}#part{item['part']}",
                             item["json_fragment"]))
            continue
        role = item.get("role")
        if role not in ("assistant", "tool"):
            continue  # user rows are registered at send; system rows by their own path
        for index, (block_type, content) in enumerate(_blocks(item)):
            if content in (None, "", []):
                continue
            kind = "tool_result" if (block_type == "tool_result" or role == "tool") else "agent_message"
            plan.append((position, kind, f"{_S2_REF}{item.get('id')}#{index}:{block_type}", content))
    per_item: list[list[str]] = [[] for _ in chunk]
    if not plan:
        return [()] * len(chunk)
    with sqlite3.connect(str(session_inputs._db_path()), timeout=5.0) as conn:
        for position, kind, ref, content in plan:
            raw = content if isinstance(content, str) else session_inputs._canonical(content)
            digest = hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest()
            found = _existing_input(conn, session_id, ref, digest)
            if found:
                per_item[position] += found
            elif kind == "tool_result":
                per_item[position] += session_inputs.record_tool_result(session_id, content, ref=ref)
            else:
                per_item[position].append(session_inputs.record_session_input(
                    session_id, kind, "agent", raw, ref=ref))
    return [tuple(dict.fromkeys(ids)) for ids in per_item]


def _user_events_by_message(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """{message id: event ids} for user-sent events (their ref is the message id, or
    ``<id>#<part>`` for a quote, attachment or code block split out at send)."""
    out: dict[str, list[str]] = {}
    for row in rows:
        ref = str(row.get("ref") or "")
        if ref and not ref.startswith(_S2_REF):
            out.setdefault(ref.split("#", 1)[0], []).append(str(row["event_id"]))
    return out


def _with_anchors(chunk: list[dict[str, Any]], own: list[tuple[str, ...]],
                  user_events: dict[str, list[str]]) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """The chunk as shown to the extractor, each item carrying the anchors it may be
    cited by, and the window's evidence allow-list (exactly those anchors)."""
    shown, anchors = [], []
    for item, ids in zip(chunk, own, strict=True):
        if item.get("role") == "user":
            ids = tuple(user_events.get(str(item.get("id") or item.get("part_of")), ()))
        item_anchors = [f"event:{event}" for event in ids]
        shown.append({**item, "anchors": item_anchors})
        anchors += item_anchors
    return shown, tuple(dict.fromkeys(anchors))


def _transcript_sync(session_id: str) -> list[dict[str, Any]]:
    """The whole sent transcript, read-only, in send order (what list_by_session returns)."""
    from core import session_inputs
    path = session_inputs._db_path()
    if not path.exists():
        return []
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id=? AND (sent IS NULL OR sent != 0) "
            "ORDER BY created_at, rowid", (session_id,)).fetchall()
    out = []
    for row in rows:
        item = {k: v for k, v in dict(row).items() if not k.endswith("_seg")}
        for key, value in item.items():
            if isinstance(value, str) and value[:1] in ("[", "{"):
                try:
                    item[key] = json.loads(value)
                except ValueError:
                    pass
        out.append(item)
    return out


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


class SessionExtractionHook:
    """Single post-session replacement for the retired extraction hook family."""

    name = "cdf_v2_session_extraction"

    async def execute(self, context: Any) -> None:
        # Queue only (§12 r16.13). The idle hook fires 120 s after the last turn; the job's
        # not-before waits 8 more minutes and is pushed by later activity (review §16).
        if context.deleting:
            # on a Hive, a deleted session's record gets its lessons and ``closed``
            from core import hive_records
            await asyncio.to_thread(hive_records.on_session_deleted, context.session_id)
        if not context.deleting:
            from core.ingestion import s2_jobs
            await asyncio.to_thread(
                s2_jobs.request, context.session_id, "session_end",
                sdk_session_id=getattr(context, "sdk_session_id", None),
                delay=s2_jobs.REVIEW_DELAY)
