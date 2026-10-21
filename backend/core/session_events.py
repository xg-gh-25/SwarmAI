"""Per-session event log: every turn of a session publishes here, clients subscribe.

Why this exists. A chat turn used to belong to the HTTP request that started it:
its events went out only as the body of that POST's SSE response. A turn with no
such request (a drained pending message, a CLI-initiated turn after a background
task, a channel turn) reached the desktop only through polling and DB reloads, at
whole-message granularity, and a dropped request interrupted the turn. The session
now owns its event stream: ``SessionRouter._published_turn`` publishes every event
of every turn here, and ``GET /api/chat/events`` lets one desktop window subscribe
to all its sessions. Attaching or detaching a subscriber never touches a turn.

Contract:
- ``publish`` stamps a COPY of the event with ``sessionId``, ``turnId``,
  ``eventSeq`` (strictly increasing per session) and, when known,
  ``turnClientId`` (the persisted assistant row's client_id, so a tab that starts
  rendering a turn mid-way can reuse the row id). It keeps the last
  ``buffer_size`` envelopes per session for replay, and the session's ACTIVE
  turn in full (up to ``ACTIVE_TURN_LIMIT`` envelopes, dropped at its
  ``turn_end``), so a running turn can always be replayed from its
  ``turn_start``. Every buffered envelope is JSON-encodable: one that is not is
  replaced, same seq, by a ``SERIALIZATION_ERROR`` error envelope.
- ``subscribe(cursors, epoch)`` registers the subscriber FIRST, then yields an
  ``events_hello`` (current epoch + per-session seq), then for each cursor:
  the buffered envelopes after it, a ``resync`` when the cursor cannot be served
  (older than the buffer, ahead of the log, or from another epoch), or — for the
  ``JOIN`` cursor — the session's active turn from its ``turn_start``. A
  ``resync`` carries ``activeTurnId`` and an ``eventSeq`` just before that turn's
  start (the log head when no turn is active), and is followed by the active
  turn's full replay, so a client never has to infer that a running turn ended.
  Then live envelopes. An envelope that lands both in the buffer snapshot and the queue is
  yielded once (per-session high-water mark).
- A subscriber whose bounded queue overflows is closed (its iterator ends); the
  client reconnects with its cursors. Publishing never blocks and never fails
  because of a subscriber.
- ``epoch`` is new per process, so a cursor from before a daemon restart is never
  trusted.

The hub is process-local and in-memory by design: the DB stays the durable
record, this is only the live channel plus a short replay window.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import deque
from typing import Any, AsyncIterator, Mapping, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

#: cursor value meaning "join": replay the session's active turn, if any.
JOIN = -1

#: turn_start.source values (mirrored by desktop hooks/sessionTurnRouter TurnSource)
TURN_SOURCES = ("chat", "drain", "channel", "injected", "answer", "permission")

DEFAULT_BUFFER_SIZE = 4000
DEFAULT_QUEUE_SIZE = 8000
#: the active turn is kept whole up to this many envelopes (a known limit: a longer
#: turn replays its first part and then the ring's tail, with a gap in between)
ACTIVE_TURN_LIMIT = 20000

#: limits for the client-supplied cursor map (untrusted query input)
_MAX_CURSOR_JSON = 65536
_MAX_CURSOR_SESSIONS = 64
_MAX_SESSION_ID_LEN = 200


class _Log:
    __slots__ = ("seq", "buf", "active", "active_turn")

    def __init__(self, size: int) -> None:
        self.seq = 0
        self.buf: deque[dict] = deque(maxlen=size)
        # the active turn's envelopes from its turn_start; None between turns
        self.active: Optional[list[dict]] = None
        self.active_turn: Optional[str] = None

    @property
    def active_start(self) -> Optional[int]:
        return self.active[0]["eventSeq"] if self.active else None


class _Subscriber:
    __slots__ = ("queue", "max", "closed", "wake")

    def __init__(self, maxsize: int) -> None:
        self.queue: deque[dict] = deque()
        self.max = maxsize
        self.closed = False
        self.wake = asyncio.Event()

    def offer(self, env: dict) -> None:
        if self.closed:
            return
        if len(self.queue) >= self.max:
            self.closed = True
            self.queue.clear()
        else:
            self.queue.append(env)
        self.wake.set()


class SessionEventHub:
    """In-memory per-session event log with replay and fan-out (see module doc)."""

    def __init__(self, buffer_size: int = DEFAULT_BUFFER_SIZE,
                 queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self.epoch = uuid4().hex[:12]
        self._buffer_size = buffer_size
        self._queue_size = queue_size
        self._logs: dict[str, _Log] = {}
        self._subs: set[_Subscriber] = set()

    # ── publishing ───────────────────────────────────────────────────
    def publish(self, session_id: str, event: Mapping[str, Any], *, turn_id: str,
                turn_client_id: Optional[str] = None) -> dict:
        log = self._logs.get(session_id)
        if log is None:
            log = self._logs[session_id] = _Log(self._buffer_size)
        log.seq += 1
        env = dict(event)
        env["sessionId"] = session_id
        env["turnId"] = turn_id
        env["eventSeq"] = log.seq
        if turn_client_id:
            env["turnClientId"] = turn_client_id
        try:
            json.dumps(env)
        except (TypeError, ValueError) as exc:
            logger.warning("session_events.unserialisable type=%s: %s", env.get("type"), exc)
            env = {"type": "error", "code": "SERIALIZATION_ERROR",
                   "message": "An event could not be encoded and was skipped.",
                   "sessionId": session_id, "turnId": turn_id, "eventSeq": log.seq,
                   **({"turnClientId": turn_client_id} if turn_client_id else {})}
        etype = env.get("type")
        if etype == "turn_start":
            log.active = [env]
            log.active_turn = turn_id
        elif log.active is not None and turn_id == log.active_turn:
            if etype == "turn_end":
                log.active = None
                log.active_turn = None
            elif len(log.active) < ACTIVE_TURN_LIMIT:
                log.active.append(env)
        log.buf.append(env)
        for sub in tuple(self._subs):
            sub.offer(env)
        return env

    def current_seq(self, session_id: str) -> int:
        log = self._logs.get(session_id)
        return log.seq if log is not None else 0

    def drop_session(self, session_id: str) -> None:
        """Free a released session's replay buffer. The seq counter is kept: a
        tab may still hold a cursor for the session, and a counter that started
        over within one epoch would make its next events look like duplicates."""
        log = self._logs.get(session_id)
        if log is not None:
            log.buf.clear()
            log.active = None
            log.active_turn = None

    def subscriber_count(self) -> int:
        return len(self._subs)

    # ── subscribing ──────────────────────────────────────────────────
    async def subscribe(self, cursors: Mapping[str, int],
                        epoch: Optional[str]) -> AsyncIterator[dict]:
        sub = _Subscriber(self._queue_size)
        self._subs.add(sub)  # register BEFORE reading any buffer
        try:
            yield {
                "type": "events_hello",
                "epoch": self.epoch,
                "seqs": {sid: log.seq for sid, log in self._logs.items()},
            }
            high: dict[str, int] = {}
            same_epoch = epoch == self.epoch
            for sid, cursor in cursors.items():
                log = self._logs.get(sid)
                head = log.seq if log is not None else 0
                high[sid] = head
                if log is None:
                    # nothing was ever published here in this process
                    if cursor > 0 or (cursor > JOIN and not same_epoch):
                        yield {"type": "resync", "sessionId": sid, "eventSeq": 0, "activeTurnId": None}
                    continue
                if cursor == JOIN:
                    for env in self._active_replay(log):
                        yield env
                    continue
                oldest = log.buf[0]["eventSeq"] if log.buf else head + 1
                if not same_epoch or cursor > head or cursor < oldest - 1:
                    start = log.active_start
                    yield {"type": "resync", "sessionId": sid,
                           "eventSeq": (start - 1) if start is not None else head,
                           "activeTurnId": log.active_turn}
                    for env in self._active_replay(log):
                        yield env
                    continue
                for env in list(log.buf):
                    if env["eventSeq"] > cursor:
                        yield env
            while True:
                while sub.queue:
                    env = sub.queue.popleft()
                    sid = env["sessionId"]
                    if env["eventSeq"] <= high.get(sid, 0):
                        continue
                    high[sid] = env["eventSeq"]
                    yield env
                if sub.closed:
                    logger.info("session_events.subscriber_overflow — closing it")
                    return
                sub.wake.clear()
                if not sub.queue and not sub.closed:
                    await sub.wake.wait()
        finally:
            self._subs.discard(sub)


    @staticmethod
    def _active_replay(log: _Log) -> list[dict]:
        """The active turn from its turn_start (empty between turns); past
        ACTIVE_TURN_LIMIT, followed by the ring's envelopes after the kept part."""
        if not log.active:
            return []
        out = list(log.active)
        last = out[-1]["eventSeq"]
        out.extend(e for e in list(log.buf)
                   if e["eventSeq"] > last and e.get("turnId") == log.active_turn)
        return out


def parse_cursors(raw: Optional[str]) -> dict[str, int]:
    """Parse the client's ``{sessionId: eventSeq}`` JSON cursor map.

    Untrusted input: anything malformed yields ``{}`` (subscribe from now); at most
    ``_MAX_CURSOR_SESSIONS`` entries; session ids must be short strings; values are
    non-negative ints or ``JOIN`` (-1); other values are dropped.
    """
    if not raw or len(raw) > _MAX_CURSOR_JSON:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for sid, val in data.items():
        if len(out) >= _MAX_CURSOR_SESSIONS:
            break
        if not isinstance(sid, str) or not sid or len(sid) > _MAX_SESSION_ID_LEN:
            continue
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue
        if isinstance(val, float) and not math.isfinite(val):
            continue
        ival = int(val)
        if ival == JOIN or ival >= 0:
            out[sid] = ival
    return out


_hub: Optional[SessionEventHub] = None


def hub() -> SessionEventHub:
    """The process-wide hub."""
    global _hub
    if _hub is None:
        _hub = SessionEventHub()
    return _hub
