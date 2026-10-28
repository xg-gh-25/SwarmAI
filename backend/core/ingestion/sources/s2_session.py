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
# Each main-pass window repeats up to this share of the previous one (design §12 r16.13).
WINDOW_OVERLAP = 0.1


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


_ALLOWED = ("cognition", "event", "reference")


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
    owned = [row for row in rows if row["event_id"] not in claimed
             and not str(row.get("ref") or "").startswith(_S2_REF)]
    user_events = _user_events_by_message(owned)
    evidence_range = {"session_id": session_id, "pass": "main",
                      "message_count": len(transcript)}
    if not transcript:
        _record_empty(session_id, "main", evidence_range)
        return []
    # A window's read set is exactly what it shows the extractor: the session's user
    # inputs plus this window's own agent and tool content (window_roots).
    user_roots = _root_ids(tuple(row["event_id"] for row in owned))
    chunks = windows(transcript, "id", overlap=WINDOW_OVERLAP)
    outcomes: list[Any] = []
    for index, chunk in enumerate(chunks, 1):
        # Each item shows the anchors it can be cited by; the allow-list is exactly the
        # window's anchors (2026-10-29: a session-wide list of bare user event ids, with
        # no way to tell which message each was, left 262 of 273 windows empty).
        own = window_roots(session_id, chunk)
        shown, evidence = _with_anchors(chunk, own, user_events)
        roots = tuple(dict.fromkeys(user_roots + _root_ids(tuple(e for ids in own for e in ids))))
        outcomes += common.extract_and_enqueue(
            "S2", session_id, json.dumps(shown, ensure_ascii=False, default=str), evidence,
            evidence_range={**evidence_range, "window": index, "windows": len(chunks)},
            session_id=session_id, read_root_ids=roots,
            project_snapshot=project_snapshot, extraction_pass="main",
            allowed_kinds=_ALLOWED, llm=llm)
    return outcomes


def extract_session_sync(session_id: str, *, project_snapshot: dict[str, Any] | None = None,
                         llm: Any | None = None) -> dict[str, list[Any]]:
    """Both S2 passes, trusted first, in the calling thread (the S2 job executor)."""
    transcript = _transcript_sync(session_id)
    rows = _input_rows(session_id)
    claimed = _claimed_by_s1(session_id)
    trusted = _trusted_pass(session_id, rows, claimed, project_snapshot, llm)
    main = _main_pass(session_id, transcript, rows, claimed, project_snapshot, llm)
    return {"user_utterance": trusted, "main": main}


async def extract_session(session_id: str, *, project_snapshot: dict[str, Any] | None = None,
                          llm: Any | None = None) -> dict[str, list[Any]]:
    """Run both S2 passes in one ordered batch; the trusted pass always goes first.

    The same work as extract_session_sync, awaited off the event loop. Production asks
    through s2_jobs.request and the job executor calls extract_session_sync."""
    return await asyncio.to_thread(extract_session_sync, session_id,
                                   project_snapshot=project_snapshot, llm=llm)


class SessionExtractionHook:
    """Single post-session replacement for the retired extraction hook family."""

    name = "cdf_v2_session_extraction"

    async def execute(self, context: Any) -> None:
        # Queue only: S2 on a long session outlasts the 180 s hook budget (§12 r16.13).
        if not context.deleting:
            from core.ingestion import s2_jobs
            await asyncio.to_thread(s2_jobs.request, context.session_id, "session_end")
