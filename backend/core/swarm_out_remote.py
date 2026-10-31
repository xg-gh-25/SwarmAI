"""Swarm Out remote session: what the Host forwards to a Hive and what it lets back.

A Hive session this Host dispatched can be opened in a chat tab (design
docs/design/2026-10-29-swarm-out-hive-session-tab.md). Its HTTP calls go through the
proxy in ``routers/swarm_out_remote.py`` and its live events through the relay in
``core/swarm_out_relay.py``. Both use this module, which holds no connection:

- ``resolve_session(sid)`` — the dispatch-log row of a session this Host sent, or an
  error: only such a session can be reached, and its Hive comes from the row (§6.1).
- ``ROUTES`` / ``match_route`` — the chat endpoints that are forwarded, with the body
  keys and query keys each may carry; nothing else is (§6.2).
- ``build_body`` — the upstream body rebuilt from the route's key list, ``agent_id``
  fixed, ``session_id`` required and equal to the path's sid; never the client's body.
- ``clean_event`` — a Hive event as the Host may pass it on: whitelisted type, every
  session id it names equal to the sid, and on a proxied POST stream no ``eventSeq``
  (the tab's turn router counts one numbering per session: the relay's) (§3, §6.3).
- ``iter_sse_lines`` — SSE lines from a byte stream, refusing a line over the cap.
- ``filter_headers`` — the response headers a client may see.

Everything a Hive sends is untrusted: a compromised or prompt-injected agent there must
not be able to open files on this machine (``ui_command``, ``file_changed`` are never
passed), rebind a tab to another session, or flood the daemon.
"""
from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Mapping, Optional
from urllib.parse import quote

from core import swarm_out

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_LINE_BYTES = 1024 * 1024        # one SSE line from a Hive
MAX_BODY_BYTES = 8 * 1024 * 1024    # one non-stream response from a Hive
MAX_QUERY_VALUE = 4096

#: the event types a tab renders (desktop useChatStreamingLifecycle handlers); never
#: ``ui_command`` (it can open files on this machine), ``file_changed`` (Canvas opens
#: local files of that name), ``turn_deliverables`` (local paths), ``session_cleared``
#: (the tab moves to its ``newSessionId``: a Hive must not pick the tab's session),
#: ``events_hello`` / ``resync`` (they describe the Hive's own event log)
EVENT_TYPES = frozenset({
    "accepted", "queued", "status", "session_start", "session_resuming", "reconnecting",
    "recovery_exhausted", "turn_start", "turn_end", "turn_phase", "turn_limit_reached",
    "still_working", "assistant", "text", "text_start", "text_delta", "thinking",
    "thinking_start", "thinking_delta", "thinking_progress", "tool_use", "tool_result",
    "content_block_stop", "user", "user_steered", "result", "error",
    "ask_user_question", "cmd_permission_request", "subagent_progress",
    "session_activity", "session_state", "context_warning",
    "context_compacting", "context_compacted", "compaction_guard",
    "transcript_row", "transcript_delete", "background_agents", "background_finished",
    "mcp_health_warning", "system_prompt_metadata", "resume_boundary",
    # a POST stream's keep-alive: the tab's stall timer resets on it
    "heartbeat",
})

_CONTENT_TYPES = frozenset({"text", "image", "document"})


class RemoteError(Exception):
    """A request the proxy refuses; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Route:
    __slots__ = ("method", "template", "stream", "body", "query")

    def __init__(self, method: str, template: str, *, stream: bool = False,
                 body: tuple[str, ...] = (), query: tuple[str, ...] = ()) -> None:
        self.method = method
        self.template = template   # path after /api, ``{sid}`` and other ``{name}`` parts
        self.stream = stream
        self.body = body           # keys copied from the client body (None = no body)
        self.query = query


#: the forwarded chat endpoints (design §5); a path not here is never forwarded
ROUTES: tuple[Route, ...] = (
    Route("POST", "chat/stream", stream=True, body=("message", "content", "session_id", "client_id")),
    Route("POST", "chat/answer-question", stream=True, body=("session_id", "tool_use_id", "answers")),
    Route("POST", "chat/cmd-permission-continue", stream=True,
          body=("session_id", "request_id", "decision", "feedback")),
    Route("DELETE", "chat/sessions/{sid}/pending/{client_id}"),
    Route("GET", "chat/sessions/{sid}"),
    Route("GET", "chat/sessions/{sid}/messages", query=("turns", "before_id", "limit")),
    Route("GET", "chat/sessions/{sid}/transcript", query=("since_rev",)),
    Route("GET", "chat/sessions/{sid}/context-usage"),
    Route("POST", "chat/stop/{sid}"),
    Route("POST", "chat/stop/{sid}/task/{task_id}"),
    Route("POST", "chat/compact/{sid}", body=("instructions",)),
    Route("POST", "chat/refresh/{sid}"),
    Route("GET", "chat/turn-diff", query=("git_root", "start", "end", "path")),
    Route("GET", "chat_threads/{sid}/tscc"),
    Route("GET", "chat/{sid}/system-prompt"),
    Route("GET", "chat/{sid}/recall"),
    Route("GET", "chat/{sid}/security-scan"),
    Route("GET", "canvas/session/{sid}", query=("parts",)),
    Route("GET", "canvas/changes/tree-diff", query=("root", "start", "end", "path")),
)


def _segments(template: str) -> list[str]:
    return template.split("/")


def match_route(method: str, path: str, sid: str) -> tuple[Route, str]:
    """The route for ``method`` + ``path`` (relative to /api) and the upstream path,
    with every ``{sid}`` equal to the session and every other parameter checked and
    quoted. Raises RemoteError(404) for anything else."""
    parts = path.split("/")
    if "" in parts:          # a leading, trailing or doubled "/": not a route's spelling
        raise RemoteError(404, "not found")
    for route in ROUTES:
        if route.method != method:
            continue
        tpl = _segments(route.template)
        if len(tpl) != len(parts):
            continue
        out: list[str] = []
        for want, got in zip(tpl, parts):
            if want.startswith("{") and want.endswith("}"):
                if not ID_RE.match(got) or (want == "{sid}" and got != sid):
                    break
                out.append(quote(got, safe=""))
            elif want != got:
                break
            else:
                out.append(want)
        else:
            return route, "/api/" + "/".join(out)
    raise RemoteError(404, "not found")


def build_query(route: Route, params: Mapping[str, str]) -> dict[str, str]:
    """The route's listed query keys only, each bounded."""
    out: dict[str, str] = {}
    for key in route.query:
        value = params.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > MAX_QUERY_VALUE:
            raise RemoteError(400, f"bad query value for {key}")
        out[key] = value
    return out


def _check_content(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        raise RemoteError(400, "content must be a list")
    out = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in _CONTENT_TYPES:
            raise RemoteError(400, "content may hold text, image and document blocks only")
        if block["type"] != "text":
            src = block.get("source")
            if not isinstance(src, dict) or src.get("type") != "base64":
                raise RemoteError(400, "an image or document block must carry base64 data")
        out.append(block)
    return out


def build_body(route: Route, body: Any, sid: str) -> Optional[dict[str, Any]]:
    """The upstream body: the route's keys copied from the client's body, nothing else.

    A route that carries ``session_id`` requires it to be the path's sid (a missing or
    null one would open a new session on the Hive, outside the dispatch log). The chat
    POSTs get ``agent_id`` fixed to the one dispatch uses."""
    if not route.body:
        return None
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise RemoteError(400, "the body must be a JSON object")
    out: dict[str, Any] = {}
    for key in route.body:
        if key in body and body[key] is not None:
            out[key] = body[key]
    if "session_id" in route.body:
        if out.get("session_id") != sid:
            raise RemoteError(400, "session_id must be the session of this path")
        out["agent_id"] = "default"
    if "content" in out:
        out["content"] = _check_content(out["content"])
    return out


#: every key a tab reads a session id from (sessionId on envelopes, session_id in
#: payloads such as session_start, newSessionId on a session rename)
SESSION_ID_KEYS = ("sessionId", "session_id", "newSessionId", "new_session_id")


def _sid_ok(ev: Mapping[str, Any], sid: str) -> bool:
    for key in SESSION_ID_KEYS:
        if key in ev and ev[key] != sid:
            return False
    return True


def clean_event(ev: Any, sid: str, *, post_stream: bool) -> Optional[dict[str, Any]]:
    """The event as the Host passes it on, or None to drop it.

    Dropped: a non-object, a type not in EVENT_TYPES, an event naming another session
    (on its envelope or in its payload). On a proxied POST stream the Hive's
    ``eventSeq`` is removed: the turn router keeps one count per session and the
    relay's republished copies carry this daemon's numbers."""
    if not isinstance(ev, dict) or ev.get("type") not in EVENT_TYPES:
        return None
    if not _sid_ok(ev, sid):
        return None
    out = dict(ev)
    if post_stream:
        out.pop("eventSeq", None)
    return out


class LineTooLong(Exception):
    pass


async def iter_sse_lines(chunks: AsyncIterator[bytes], cap: Optional[int] = None) -> AsyncIterator[str]:
    """Lines of an SSE byte stream; raises LineTooLong as soon as one line passes
    ``cap`` (default MAX_LINE_BYTES; it is never buffered whole)."""
    cap = MAX_LINE_BYTES if cap is None else cap
    buf = b""
    async for chunk in chunks:
        buf += chunk
        while True:
            nl = buf.find(b"\n")
            if nl < 0:
                if len(buf) > cap:
                    raise LineTooLong()
                break
            line, buf = buf[:nl], buf[nl + 1:]
            if len(line) > cap:
                raise LineTooLong()
            yield line.rstrip(b"\r").decode("utf-8", errors="replace")
    if buf:
        if len(buf) > cap:
            raise LineTooLong()
        yield buf.decode("utf-8", errors="replace")


def parse_data_line(line: str) -> Optional[Any]:
    """The JSON of an SSE ``data:`` line; None for comments, blanks and ``[DONE]``."""
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        return json.loads(payload)
    except (ValueError, RecursionError):   # malformed or nested past the parser's depth
        return None


async def read_capped(response: Any, cap: Optional[int] = None) -> bytes:
    """A streamed response's body, refused (RemoteError 502) as soon as it passes
    ``cap`` (default MAX_BODY_BYTES): a Hive's answer is never buffered whole first."""
    cap = MAX_BODY_BYTES if cap is None else cap
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise RemoteError(502, "the Hive's answer is too large")
    out = bytearray()
    async for chunk in response.aiter_bytes():
        out += chunk
        if len(out) > cap:
            raise RemoteError(502, "the Hive's answer is too large")
    return bytes(out)


def json_object(raw: bytes) -> dict[str, Any]:
    """A Hive's JSON answer that must be an object; RemoteError(502) otherwise."""
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise RemoteError(502, "the Hive's answer is not JSON")
    if not isinstance(value, dict):
        raise RemoteError(502, "the Hive's answer is not an object")
    return value


ALLOWED_RESPONSE_HEADERS = ("content-type", "etag", "x-has-more", "x-transcript-rev")


def filter_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() in ALLOWED_RESPONSE_HEADERS}


async def resolve_session(sid: str) -> dict[str, Any]:
    """The dispatch-log row of ``sid``: a session this Host dispatched to exactly one
    Hive, and not a session of this daemon. RemoteError(404) otherwise, (409) when a
    session of this daemon holds the same id."""
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise RemoteError(404, "not found")
    import asyncio
    rows = [r for r in await asyncio.to_thread(swarm_out._read_log) if r.get("session_id") == sid]
    hives = {r.get("hive_id") for r in rows}
    if len(rows) == 0 or len(hives) != 1 or not next(iter(hives)):
        raise RemoteError(404, "not found")
    if await _is_local_session(sid):
        # a local session holds the Hive session's id (made before a local send was
        # refused under it, chat.py): never addressed, but said, not "not found"
        raise RemoteError(409, COLLIDES)
    return rows[-1]


#: a discarded or expired run's session: nothing of it is forwarded (run-review §3.4)
CLOSED = "this run is closed: its session cannot be opened"
#: an accepted run's session: it can be read, nothing can be sent to it
READ_ONLY = "this run is accepted: its session is read-only"


def closed_for(row: dict[str, Any], write: bool) -> Optional[RemoteError]:
    """Why the proxy refuses a request for this dispatch's session, or None. A discarded
    or expired run (or one being discarded): every request (410). An accepted run (a pass
    verdict is enough: files or lessons may still be coming): every request that could
    change it (409); reads pass. Decided by core/swarm_out_settle.closed_state, the rule
    the person's commands use too."""
    from core import swarm_out_settle
    state = swarm_out_settle.closed_state(swarm_out_settle.load(str(row.get("dispatch_id") or "")))
    if state == "closed":
        return RemoteError(410, CLOSED)
    if state == "accepted" and write:
        return RemoteError(409, READ_ONLY)
    return None


#: why a run whose Hive session id is also a session of this machine cannot be opened
COLLIDES = "this run's Hive session id is also a session on this machine, so it cannot be opened here"


async def is_dispatched_hive_session(sid: str) -> bool:
    """``sid`` is a session this Host dispatched to a Hive (its dispatch log names it).
    Such an id is never a session of this machine: chat.py refuses to start one."""
    if not isinstance(sid, str) or not ID_RE.match(sid):
        return False
    import asyncio
    return any(r.get("session_id") == sid for r in await asyncio.to_thread(swarm_out._read_log))


async def _is_local_session(sid: str) -> bool:
    """A session of this daemon: a live unit or a stored session. The sid in the
    dispatch log was named by the Hive; it must never address a local session."""
    from core import session_registry
    router = session_registry.session_router
    if router is not None and router.get_unit(sid) is not None:
        return True
    from core.session_manager import session_manager
    return await session_manager.get_session(sid) is not None
