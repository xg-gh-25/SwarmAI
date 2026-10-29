"""Append-only provenance ledger for content entering a session.

- ``record_session_input`` stores one immutable input event and its evidence root.
- ``record_tool_result`` splits allowlisted measurements from untrusted tool text.
- ``session_input_ids`` returns the persisted ledger carried into lifecycle hooks.
- ``user_authored_text`` verifies and returns only a person's authored message text.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Sequence

from config import get_app_data_dir

Trust = Literal["user", "external", "agent", "measured"]


def _db_path():
    return get_app_data_dir() / "data.db"


_MEASURED_KEYS = frozenset({
    "exit_code", "returncode", "passed", "failed", "pass_count", "fail_count",
    "n_pass", "n_fail", "n_not_run", "duration", "duration_ms", "duration_seconds",
})


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _root_kind(kind: str, trust: Trust) -> str:
    lowered = kind.lower()
    if trust == "measured" or lowered == "measured_field":
        return "measured_field"
    if "channel" in lowered:
        return "channel_message"
    if lowered in {"web", "web_document", "fetched_web"}:
        return "web_document"
    if trust == "user":
        return "user_message"
    return "tool_text"


def _record_one(session_id: str, kind: str, trust: Trust, content: Any, *,
                ref: str | None = None) -> str:
    if not session_id or not kind or trust not in {"user", "external", "agent", "measured"}:
        raise ValueError("session_id, kind, and a valid trust are required")
    raw = content if isinstance(content, str) else _canonical(content)
    event_id = f"input:{uuid.uuid4().hex}"
    content_hash = hashlib.sha256(raw.encode("utf-8", errors="surrogatepass")).hexdigest()
    root_kind = _root_kind(kind, trust)
    root_trust = trust if trust in {"user", "measured"} else "external"
    locator = str(ref or raw) if root_kind == "web_document" else f"{session_id}:{event_id}"
    from core.ingestion.api_v2 import register_root
    root_id = register_root(root_kind, locator, raw, root_trust)
    conn = sqlite3.connect(str(_db_path()), timeout=5.0)
    try:                       # "with connect()" commits but never closes the connection
        with conn:
            conn.execute(
                "INSERT INTO session_inputs(event_id, session_id, kind, trust, content_hash, ref, "
                "root_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, session_id, kind, trust, content_hash, ref, root_id,
                 datetime.now(timezone.utc).isoformat()),
            )
    finally:
        conn.close()
    return event_id


def _measured_fields(value: Any, path: tuple[str, ...] = ()) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = (*path, str(key))
            if isinstance(item, Mapping):
                rows.extend(_measured_fields(item, child))
            elif str(key).lower() in _MEASURED_KEYS or (path and path[0].lower() == "health"):
                if item is None or isinstance(item, (bool, int, float, str)):
                    rows.append((".".join(child), item))
    return rows


def record_tool_result(session_id: str, content: Any, *, ref: str | None = None) -> tuple[str, ...]:
    """Record external tool text plus one measured root per allowlisted structured field."""
    text = content if isinstance(content, str) else _canonical(content)
    event_ids = [_record_one(session_id, "tool_result", "external", text, ref=ref)]
    structured = content
    if isinstance(content, str):
        try:
            structured = json.loads(content)
        except (TypeError, ValueError):
            structured = None
    for path, value in _measured_fields(structured):
        event_ids.append(_record_one(
            session_id, "measured_field", "measured", _canonical(value),
            ref=f"{ref or 'tool_result'}#field:{path}",
        ))
    return tuple(event_ids)


def record_session_input(
    session_id: str,
    kind: str,
    trust: Trust,
    content: str,
    *,
    ref: str | None = None,
) -> str:
    """Append one immutable input, register its root, and return the primary event id."""
    if kind == "tool_result":
        return record_tool_result(session_id, content, ref=ref)[0]
    return _record_one(session_id, kind, trust, content, ref=ref)


def session_input_ids(session_id: str) -> tuple[str, ...]:
    """Return a session's immutable input ids in insertion order."""
    if not _db_path().exists():
        return ()
    try:
        uri = f"file:{_db_path().resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
            rows = conn.execute(
                "SELECT event_id FROM session_inputs WHERE session_id = ? ORDER BY created_at, event_id",
                (session_id,),
            ).fetchall()
    except sqlite3.Error:
        return ()
    return tuple(str(row[0]) for row in rows)


def derive_envelope(
    session_id: str,
    source_event_ids: Sequence[str] = (),
    *,
    writer: str | None = None,
) -> dict:
    """Deprecated v1 boundary; v2 callers must use ``enqueue_unit`` evidence anchors."""
    del session_id, source_event_ids, writer
    raise DeprecationWarning("derive_envelope was removed; call api_v2.enqueue_unit")


def _split_user_input_parts(
    message: str | None, content: Sequence[object] | str | None,
) -> tuple[str, tuple[tuple[str, str, int], ...]]:
    """Return authored text and external ``(kind, text, index)`` parts."""
    import re

    external: list[tuple[str, str, int]] = []
    text = message or ""
    blocks: Any = content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            blocks = parsed if isinstance(parsed, list) else None
        except (TypeError, ValueError):
            blocks = None
        if blocks is None and not text:
            text = content
    if blocks and not isinstance(blocks, str):
        text_parts: list[str] = []
        for index, block in enumerate(blocks):
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
            else:
                external.append(("attachment", _canonical(block), index))
        if text_parts:
            text = "\n".join(text_parts)
    spans = list(re.finditer(r"```[\s\S]*?```|(?:^>.*(?:\n|$))+", text, flags=re.MULTILINE))
    authored = text
    for index, match in enumerate(spans):
        span = match.group(0)
        authored = authored.replace(span, "", 1)
        kind = "quote" if span.lstrip().startswith(">") else "fenced_content"
        external.append((kind, span, index))
    return authored, tuple(external)


InputOrigin = Literal["person", "channel_peer", "system"]


def record_user_input_parts(
    session_id: str,
    message: str | None,
    content: Sequence[object] | str | None = None,
    ref: str | None = None,
    *,
    origin: InputOrigin = "system",
) -> tuple[str, ...]:
    """Record a send and split untrusted embedded parts.

    Only ``origin="person"`` (the authenticated app user typing in a chat tab, or the
    channel owner) yields a ``user_message`` / ``user`` event. A non-owner channel
    message is one ``channel_message`` (external) event; a system-built prompt (skill
    creator, background task, any caller that did not say otherwise) is one
    ``system_prompt`` (external) event. The default is the safe one.
    """
    if origin != "person":
        raw = message if message is not None else _canonical(content)
        kind = "channel_message" if origin == "channel_peer" else "system_prompt"
        return (record_session_input(session_id, kind, "external", raw or "", ref=ref),)
    authored, external = _split_user_input_parts(message, content)
    event_ids: list[str] = []
    for kind, text, index in external:
        event_ids.append(record_session_input(
            session_id, kind, "external", text,
            ref=f"{ref or 'message'}#{kind}-{index}",
        ))
    if authored.strip():
        event_ids.insert(0, record_session_input(
            session_id, "user_message", "user", authored, ref=ref,
        ))
    return tuple(event_ids)


def user_authored_text(event_id: str) -> str | None:
    """Read and hash-check the original authored text for one user-message event."""
    if not _db_path().exists():
        return None
    try:
        uri = f"file:{_db_path().resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
            event = conn.execute(
                "SELECT session_id,kind,content_hash,ref FROM session_inputs WHERE event_id=?",
                (event_id,),).fetchone()
            if event is None or event[1] != "user_message" or not event[3]:
                return None
            row = conn.execute(
                "SELECT content FROM messages WHERE id=? AND session_id=?",
                (event[3], event[0]),).fetchone()
        if row is None:
            return None
        authored, _external = _split_user_input_parts(None, row[0])
        digest = hashlib.sha256(authored.encode("utf-8", errors="surrogatepass")).hexdigest()
        return authored if digest == event[2] else None
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None


def latest_user_event_id(session_id: str) -> str | None:
    """Return the newest authenticated ``user_message`` event for one session."""
    if not session_id or not _db_path().exists():
        return None
    try:
        uri = f"file:{_db_path().resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
            row = conn.execute(
                "SELECT event_id, kind FROM session_inputs "
                "WHERE session_id = ? AND kind IN ('user_message','channel_message','system_prompt') "
                "ORDER BY created_at DESC, event_id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        # Only when the current turn was opened by the person (§9.2).
        return str(row[0]) if row and row[1] == "user_message" else None
    except sqlite3.Error:
        return None
