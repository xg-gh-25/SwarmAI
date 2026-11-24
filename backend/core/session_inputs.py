"""Append-only provenance ledger for content entering a session.

- ``record_session_input`` stores one immutable input event and its evidence root.
- ``record_tool_result`` splits allowlisted measurements from untrusted tool text, and
  records in the row's kind whether the result is explicitly third-party content.
- ``third_party_tool`` says from a tool's name and input alone whether its result is
  explicitly third-party; ``tool_result_third_party`` reads it back for ledger rows.
- ``session_input_ids`` returns the persisted ledger carried into lifecycle hooks.
- ``user_authored_text`` verifies and returns only a person's authored message text.

Every tool result is registered the same way whatever its tool (root kind ``tool_text``,
trust ``external``). What the ledger adds is a fact for one question, where a TIMELINE
event goes (pipeline_v2, writer): whether the result is explicitly third-party content.
It is decided from the tool's name and, for a Read / Grep / Glob, the resolved path only:
WebFetch, WebSearch, any MCP tool (``mcp__*``), and a Read (or a search) of a path under
the workspace's ``Attachments/`` or the user's ``Downloads/``. A shell command is never
parsed: a Bash result is not explicitly third-party, nor is a sub-agent's (accepted
residual: a ``curl`` output, or a sub-agent's account of a page, can reach an event, which
is the extractor's own account of it; the sub-agent's own WebFetch result is recorded as
third-party). A tool the product does not know, or a Read whose path was not recorded,
cannot be told (None). The answer is kept in the row's kind (``TOOL_RESULT_KINDS``); a row
recorded without an answer keeps the kind ``tool_result`` and is told from the session
transcript's tool call, or not at all.
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


# The ledger kind of a tool result says whether it is explicitly third-party content:
# True / False when its tool call was known as it was recorded, None (kind "tool_result")
# when it was not (every row recorded before the kinds existed).
TOOL_RESULT_UNKNOWN = "tool_result"
TOOL_RESULT_THIRD_PARTY = "tool_result:third_party"
TOOL_RESULT_NOT_THIRD_PARTY = "tool_result:not_third_party"
TOOL_RESULT_KINDS: dict[str, bool | None] = {
    TOOL_RESULT_UNKNOWN: None, TOOL_RESULT_THIRD_PARTY: True, TOOL_RESULT_NOT_THIRD_PARTY: False,
}
_WEB_TOOLS = frozenset({"webfetch", "websearch"})
# tools whose result is third-party when the path they read is under a third-party folder
_PATH_TOOLS = frozenset({"read", "grep", "glob"})
# the product's own tools whose result is the session's own work (a shell command is never
# parsed; a sub-agent's result is its own account): any other tool name cannot be told
_OWN_TOOLS = frozenset({
    "bash", "bashoutput", "killshell", "killbash", "write", "edit", "multiedit", "notebookedit",
    "ls", "agent", "task", "taskoutput", "taskstop", "todowrite", "skill", "toolsearch",
    "askuserquestion", "enterplanmode", "exitplanmode", "enterworktree", "exitworktree",
    "croncreate", "crondelete", "cronlist", "sendmessage", "listagents", "schedulewakeup",
    "workflow", "reportfindings",
})


def _under(path: str, roots: Sequence[str]) -> bool:
    import os
    # casefolded: a case-insensitive file system opens ~/downloads as ~/Downloads; on one
    # that is not, the comparison errs toward third-party
    folded = path.casefold()
    return any(folded == root or folded.startswith(root + os.sep) for root in roots)


def _third_party_dirs() -> tuple[str, ...]:
    import os
    from pathlib import Path
    from config import get_swarmws
    dirs = [Path(get_swarmws()) / "Attachments", Path.home() / "Downloads"]
    return tuple(os.path.realpath(str(d)).casefold() for d in dirs)


def third_party_tool(name: str | None,
                     tool_input: Mapping[str, Any] | str | None = None) -> bool | None:
    """Whether the result of tool *name* called with *tool_input* is explicitly
    third-party content (module doc): a web fetch or search, an MCP tool, or a Read / Grep
    / Glob of a path under the workspace's ``Attachments/`` or the user's ``Downloads/``
    (the path as resolved: a link into either is under it). *tool_input* is the call's
    input, or a persisted call's display object (a Read's path; a search's is its
    pattern, so only a search whose input is known is judged by its path). False for the
    product's own tools (``_OWN_TOOLS``: a Bash command is never parsed); None when it
    cannot be told: a tool name the product does not know, or a Read without its path."""
    import os
    tool = str(name or "")
    lowered = tool.casefold()
    if lowered in _WEB_TOOLS or lowered.startswith("mcp__"):
        return True
    if lowered in _OWN_TOOLS:
        return False
    if lowered not in _PATH_TOOLS:
        return None
    if isinstance(tool_input, str):
        if lowered != "read":
            return False     # a persisted search shows its pattern, not the path it searched
        raw: Any = tool_input
    elif isinstance(tool_input, Mapping):
        raw = tool_input.get("file_path") or tool_input.get("path") or ""
        if not raw and lowered != "read":
            return False                         # a search of the session's own directory
    else:
        raw = ""
    if not isinstance(raw, str) or not raw.strip():
        return None if lowered == "read" else False
    path = os.path.expanduser(raw.strip())
    if not os.path.isabs(path):
        from config import get_swarmws
        path = os.path.join(str(get_swarmws()), path)
    return _under(os.path.realpath(path), _third_party_dirs())


def record_tool_result(session_id: str, content: Any, *, ref: str | None = None,
                       tool: tuple[str | None, Any] | None = None) -> tuple[str, ...]:
    """Record external tool text plus one measured root per allowlisted structured field.

    *tool*: ``(name, input or file path)`` of the call this result answers, when known;
    the row's kind then says whether the result is explicitly third-party
    (``third_party_tool``; one it cannot tell keeps the kind ``tool_result``). The root is
    registered the same either way."""
    text = content if isinstance(content, str) else _canonical(content)
    kind = TOOL_RESULT_UNKNOWN
    answer = third_party_tool(tool[0], tool[1]) if tool is not None else None
    if answer is not None:
        kind = TOOL_RESULT_THIRD_PARTY if answer else TOOL_RESULT_NOT_THIRD_PARTY
    event_ids = [_record_one(session_id, kind, "external", text, ref=ref)]
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
    tool: tuple[str | None, Any] | None = None,
) -> str:
    """Append one immutable input, register its root, and return the primary event id.
    *tool*: for a ``tool_result``, the call it answers (``record_tool_result``)."""
    if kind == "tool_result":
        return record_tool_result(session_id, content, ref=ref, tool=tool)[0]
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


# The ledger kinds that are no tool result. Explicitly third-party: an attachment block of
# a message (a file someone attached, as under Attachments/). Not: the person's words and
# what they pasted into their own message (a quote, a fenced block: they chose to show
# it), the agent's own text, a measurement, a prompt the product built. A channel message
# has a root kind of its own (provenance.lineage_third_party). Any other kind: None.
_THIRD_PARTY_KINDS = frozenset({"attachment"})
_OWN_KINDS = frozenset({"user_message", "message", "quote", "fenced_content", "agent_message",
                        "measured_field", "system_prompt"})
_S2_PREFIX = "s2:"            # sources/s2_session._S2_REF (imported lazily there)


def tool_calls(rows: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, Any]]:
    """``{tool_use id: (name, input or display object)}`` of the ``tool_use`` blocks in
    *rows* (transcript rows, their content a list of blocks). A persisted block holds the
    display object (``display.object``: the path of a Read), never the raw input."""
    out: dict[str, tuple[str, Any]] = {}
    for row in rows:
        content = row.get("content") if isinstance(row, Mapping) else None
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (TypeError, ValueError):
                continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, Mapping) or block.get("type") != "tool_use" or not block.get("id"):
                continue
            value: Any = block.get("input")
            if not isinstance(value, Mapping):
                display = block.get("display")
                value = display.get("object") if isinstance(display, Mapping) else None
            out[str(block["id"])] = (str(block.get("name") or ""), value)
    return out


# a session's transcript as read for legacy rows, by store and session: reused while it has
# no new row (a review's events, or the backfill, ask about the same session many times)
_TRANSCRIPTS: dict[tuple[str, str], tuple[tuple[Any, ...], list[dict[str, Any]],
                                          dict[str, tuple[str, Any]]]] = {}
_TRANSCRIPTS_KEPT = 16


def _transcript(conn: sqlite3.Connection, session_id: str
                ) -> tuple[list[dict[str, Any]], dict[str, tuple[str, Any]]]:
    """(rows, tool calls) of *session_id*'s transcript."""
    mark = tuple(conn.execute("SELECT COUNT(*), MAX(rowid) FROM messages WHERE session_id=?",
                              (session_id,)).fetchone())
    key = (str(_db_path()), session_id)
    kept = _TRANSCRIPTS.get(key)
    if kept is not None and kept[0] == mark:
        return kept[1], kept[2]
    rows = [{"id": r[0], "content": r[1]} for r in conn.execute(
        "SELECT id, content FROM messages WHERE session_id=? ORDER BY created_at, rowid",
        (session_id,))]
    calls = tool_calls(rows)
    _TRANSCRIPTS.pop(key, None)
    _TRANSCRIPTS[key] = (mark, rows, calls)
    while len(_TRANSCRIPTS) > _TRANSCRIPTS_KEPT:
        _TRANSCRIPTS.pop(next(iter(_TRANSCRIPTS)))
    return rows, calls


def _blocks_of(content: Any) -> list[Any]:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            return []
    return content if isinstance(content, list) else []


def _derived(ref: str, rows: list[dict[str, Any]], calls: dict[str, tuple[str, Any]]) -> bool | None:
    """Whether the tool result a legacy ledger row (*ref*) recorded is explicitly
    third-party, from the session transcript's tool call; None when it cannot be told."""
    def answer(tool_use_id: Any) -> bool | None:
        call = calls.get(str(tool_use_id or ""))
        return None if call is None else third_party_tool(call[0], call[1])

    if not ref.startswith(_S2_PREFIX):
        return answer(ref)                       # the live path records the tool_use id
    message_id, _, part = ref[len(_S2_PREFIX):].partition("#")
    row = next((r for r in rows if str(r["id"]) == message_id), None)
    if row is None:
        return None
    blocks = _blocks_of(row["content"])
    if part.startswith("part"):
        # a fragment of a split row: third-party when any tool result in the row is
        answers = [answer(b.get("tool_use_id")) for b in blocks
                   if isinstance(b, Mapping) and b.get("type") == "tool_result"]
        if any(a is True for a in answers):
            return True
        return None if any(a is None for a in answers) else False
    index, _, block_type = part.partition(":")
    try:
        block = blocks[int(index)]
    except (ValueError, IndexError):
        return None
    if not isinstance(block, Mapping):
        return None
    if block.get("type") == "tool_result" or block_type == "tool_result":
        return answer(block.get("tool_use_id"))
    return False                                 # the agent's own text, recorded as a tool row


def input_origins(event_ids: Sequence[str]) -> dict[str, bool | None]:
    """``{event id: explicitly third-party?}`` for ledger rows: True / False when the row
    says (its kind), a legacy tool result told from the session transcript's tool call,
    None when it cannot be told (no such row, an unknown kind, no transcript, no tool
    call, a tool it cannot tell). Read-only."""
    ids = [str(e) for e in dict.fromkeys(event_ids)]
    out: dict[str, bool | None] = {e: None for e in ids}
    if not ids or not _db_path().exists():
        return out
    try:
        uri = f"file:{_db_path().resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
            marks = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT event_id, session_id, kind, ref FROM session_inputs WHERE event_id IN ({marks})",
                ids).fetchall()
            for event_id, session_id, kind, ref in rows:
                kind = str(kind or "")
                if kind in TOOL_RESULT_KINDS and TOOL_RESULT_KINDS[kind] is not None:
                    out[event_id] = TOOL_RESULT_KINDS[kind]
                elif kind == TOOL_RESULT_UNKNOWN:
                    found, calls = _transcript(conn, session_id)
                    out[event_id] = _derived(str(ref or ""), found, calls)
                elif kind in _THIRD_PARTY_KINDS:
                    out[event_id] = True
                elif kind in _OWN_KINDS:
                    out[event_id] = False
    except (OSError, sqlite3.Error):
        return {e: None for e in ids}
    return out
