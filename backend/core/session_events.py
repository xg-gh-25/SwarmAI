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
  turn in full (up to ``ACTIVE_TURN_LIMIT`` envelopes — at the limit the
  streaming deltas an ``assistant`` envelope already carries are dropped first —
  dropped at its ``turn_end``), so a running turn can always be replayed from its
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
  Then, for each cursor's session, a ``session_state`` snapshot (from the
  registered state provider, core/session_state — the same producer as the
  poll endpoint), so a (re)connected tab knows the session's state at once.
  Then live envelopes. An envelope that lands both in the buffer snapshot and the queue is
  yielded once (per-session high-water mark).
- A subscriber whose bounded queue overflows is closed (its iterator ends); the
  client reconnects with its cursors. Publishing never blocks and never fails
  because of a subscriber.
- ``epoch`` is new per process, so a cursor from before a daemon restart is never
  trusted.
- The session's turn in progress is also its "a turn is in progress" fact
  (docs/design/chat-turn-liveness.md S1): ``try_claim_turn`` claims it at a send's
  admission only when none is open or claimed (synchronous: one check-and-claim
  step), its ``turn_start`` is published later at the turn's first event, as
  before. ``open_turn_id`` / ``open_phase`` read the open turn, else the claim
  (preparing, then each ``turn_phase``; streaming from the open turn's first event
  not in ``WAIT_EVENT_TYPES``). ``set_activity`` / ``activity_of`` keep the
  activity core/session_state derives, with a seq that only grows (also across
  restarts and ``drop_session``).

The hub is process-local and in-memory by design: the DB stays the durable
record, this is only the live channel plus a short replay window.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from typing import Any, AsyncIterator, Mapping, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

#: cursor value meaning "join": replay the session's active turn, if any.
JOIN = -1

#: Events of a turn that do not mean the model is producing: they leave the turn's
#: phase where it is (docs/design/chat-turn-liveness.md S1). Any other event of the
#: open turn moves it to ``streaming``; a ``turn_phase`` sets its own phase.
WAIT_EVENT_TYPES = frozenset({
    "turn_start", "turn_phase", "session_start", "session_resuming", "queued",
    "heartbeat", "still_working", "reconnecting", "system_prompt_metadata",
    "mcp_health_warning", "error",
})

#: turn_start.source values (mirrored by desktop hooks/sessionTurnRouter TurnSource)
TURN_SOURCES = ("chat", "drain", "channel", "injected", "answer", "permission")

#: async (session_id) -> state dict | None, registered by the SessionRouter
_state_provider = None


def set_state_provider(fn) -> None:
    """Register what ``subscribe`` asks for each session's state snapshot."""
    global _state_provider
    _state_provider = fn


DEFAULT_BUFFER_SIZE = 4000
DEFAULT_QUEUE_SIZE = 8000
#: the active turn is kept whole up to this many envelopes (a known limit: a longer
#: turn replays its first part and then the ring's tail, with a gap in between)
ACTIVE_TURN_LIMIT = 20000
#: streaming previews whose content the turn's next ``assistant`` envelope carries
SUPERSEDED_BY_ASSISTANT = frozenset({"text_delta", "thinking_delta", "text_start",
                                     "thinking_start", "thinking_progress"})

#: limits for the client-supplied cursor map (untrusted query input)
_MAX_CURSOR_JSON = 65536
_MAX_CURSOR_SESSIONS = 64
_MAX_SESSION_ID_LEN = 200


class _Log:
    __slots__ = ("seq", "buf", "active", "active_turn", "warned", "retry_seq",
                 "phase", "claim", "claim_phase", "claim_task", "waiters", "activity", "activity_seq")

    def __init__(self, size: int) -> None:
        self.warned = False   # the active turn's over-limit warning was logged
        self.retry_seq = 0    # no compaction of the active turn before this seq
        self.seq = 0
        self.buf: deque[dict] = deque(maxlen=size)
        # the active turn's envelopes from its turn_start; None between turns
        self.active: Optional[list[dict]] = None
        self.active_turn: Optional[str] = None
        # the active turn's phase (docs/design/chat-turn-liveness.md S1): preparing
        # from its turn_start, then each turn_phase, streaming from its first event
        # that is not a wait; None between turns
        self.phase: Optional[str] = None
        # the turn admitted but not yet started (docs/design/chat-turn-liveness.md
        # S1, SessionEventHub.try_claim_turn) and its phase; None when none
        self.claim: Optional[str] = None
        self.claim_phase: Optional[str] = None
        # the task that claimed it: a claim whose task has ended is no claim, so a
        # claim can never outlive the request that made it (v4 RC-A backstop)
        self.claim_task: Optional["asyncio.Task"] = None
        # a message was queued while the session was busy: the next end of a turn
        # or claim must kick the drain (docs/design/chat-turn-liveness.md S1 v4)
        self.waiters: bool = False
        # the session's activity as last derived (core/session_state.refresh_activity)
        # and its seq (SessionEventHub.set_activity)
        self.activity: str = "idle"
        self.activity_seq: int = 0

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
        self._activity_clock = 0
        # sessions whose events a relay republishes from a Hive (core/swarm_out_relay):
        # their activity seq comes from the Hive's clock, so local activity writes skip them
        self._relayed: set[str] = set()

    # ── relayed sessions (docs/design/2026-10-29-swarm-out-hive-session-tab.md §3) ──
    def mark_relayed(self, session_id: str) -> None:
        self._relayed.add(session_id)

    def unmark_relayed(self, session_id: str) -> None:
        self._relayed.discard(session_id)

    def is_relayed(self, session_id: str) -> bool:
        return session_id in self._relayed

    def reset_relayed(self, session_id: str) -> None:
        """Forget a relayed session's buffer and turn in progress, keeping its seq.

        A relay that stops (or whose Hive restarted) mid-turn would otherwise leave
        an active turn here that no turn_end ever closes: a later JOIN would replay
        it and ``open_turn_id`` would never clear. The seq is kept for the same
        reason as in ``drop_session``. Unlike ``drop_session`` it does not touch the
        activity (that is the Hive's, not this process's clock)."""
        log = self._logs.get(session_id)
        if log is None:
            return
        log.buf.clear()
        log.active = None
        log.active_turn = None
        log.phase = None
        log.claim = None
        log.claim_phase = None
        log.claim_task = None
        log.waiters = False

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
            log.warned = False
            log.retry_seq = 0
            # a claimed turn keeps the phase it reached before its turn_start
            log.phase = (log.claim_phase if log.claim == turn_id and log.claim_phase
                         else "preparing")
        elif log.active is not None and turn_id == log.active_turn:
            if etype == "turn_end":
                log.active = None
                log.active_turn = None
                log.phase = None
                if log.claim == turn_id:
                    log.claim = None
                    log.claim_phase = None
                    log.claim_task = None
            else:
                if etype == "turn_phase":
                    log.phase = env.get("phase") or log.phase
                elif etype not in WAIT_EVENT_TYPES:
                    log.phase = "streaming"
                if len(log.active) < ACTIVE_TURN_LIMIT or self._compact_active(log):
                    log.active.append(env)
        log.buf.append(env)
        for sub in tuple(self._subs):
            sub.offer(env)
        return env

    def current_seq(self, session_id: str) -> int:
        log = self._logs.get(session_id)
        return log.seq if log is not None else 0

    # ── the open turn and the session's activity (chat-turn-liveness S1) ─────
    def try_claim_turn(self, session_id: str, turn_id: str) -> bool:
        """Claim the session's turn for ``turn_id`` at its admission, unless a turn is
        open or claimed. Synchronous (no await): the check and the claim are one
        step, so two sends on the loop can never both get it. The claim is the turn
        from admission on (the session's activity reads it); its ``turn_start`` is
        published later, at the turn's first event, as before."""
        log = self._logs.get(session_id)
        if log is None:
            log = self._logs[session_id] = _Log(self._buffer_size)
        if log.active is not None or self._live_claim(log) is not None:
            return False
        log.claim = turn_id
        log.claim_phase = "preparing"
        try:
            log.claim_task = asyncio.current_task()
        except RuntimeError:
            log.claim_task = None   # no running loop (a sync test)
        return True

    @staticmethod
    def _live_claim(log: "_Log") -> Optional[str]:
        """The log's claim, dropped first when the task that made it has ended
        (its finally never ran: a generator nobody closed)."""
        if log.claim is not None and log.claim_task is not None and log.claim_task.done():
            logger.warning("session_events.stale_claim_dropped turn=%s", log.claim)
            log.claim = None
            log.claim_phase = None
            log.claim_task = None
        return log.claim

    def note_phase(self, session_id: str, turn_id: str, phase: Optional[str]) -> bool:
        """A claimed turn's phase before its turn_start (not published). True when
        it changed."""
        log = self._logs.get(session_id)
        if log is None or log.claim != turn_id or not phase or log.claim_phase == phase:
            return False
        log.claim_phase = phase
        return True

    def note_waiter(self, session_id: str) -> None:
        """A message of the session was queued because it was busy: whatever holds
        it kicks the drain when it ends (``take_waiters``)."""
        log = self._logs.get(session_id)
        if log is None:
            log = self._logs[session_id] = _Log(self._buffer_size)
        log.waiters = True

    def take_waiters(self, session_id: str) -> bool:
        """True (once) when messages were queued while the session was busy."""
        log = self._logs.get(session_id)
        if log is None or not log.waiters:
            return False
        log.waiters = False
        return True

    def release_claim(self, session_id: str, turn_id: str) -> None:
        """The claimed turn ended without ever starting (queued, rejected)."""
        log = self._logs.get(session_id)
        if log is not None and log.claim == turn_id:
            log.claim = None
            log.claim_phase = None
            log.claim_task = None

    def open_turn_id(self, session_id: str) -> Optional[str]:
        """The session's turn in progress: the open one, else the claimed one."""
        log = self._logs.get(session_id)
        if log is None:
            return None
        return log.active_turn if log.active is not None else self._live_claim(log)

    def open_phase(self, session_id: str) -> Optional[str]:
        """The phase of the session's turn in progress, None when there is none."""
        log = self._logs.get(session_id)
        if log is None:
            return None
        if log.active is not None:
            return log.phase
        return log.claim_phase if self._live_claim(log) is not None else None

    def activity_of(self, session_id: str) -> "tuple[str, int]":
        log = self._logs.get(session_id)
        return (log.activity, log.activity_seq) if log is not None else ("idle", 0)

    def set_activity(self, session_id: str, activity: str) -> Optional[int]:
        """Record the session's activity; its new seq when it changed, else None.

        The seq is microseconds since the epoch, bumped past the last one handed
        out: strictly increasing per session within a process AND across a daemon
        restart (a tab holding the old process's seq must accept the new one's),
        and it survives ``drop_session``. A relayed session is left alone: its
        activity seq is the Hive's (``mark_relayed``)."""
        if session_id in self._relayed:
            return None
        log = self._logs.get(session_id)
        if log is None:
            log = self._logs[session_id] = _Log(self._buffer_size)
        if log.activity == activity and log.activity_seq:
            return None
        self._activity_clock = max(self._activity_clock + 1, time.time_ns() // 1000)
        log.activity = activity
        log.activity_seq = self._activity_clock
        return log.activity_seq

    def drop_session(self, session_id: str) -> None:
        """Free a released session's replay buffer. The seq counter is kept: a
        tab may still hold a cursor for the session, and a counter that started
        over within one epoch would make its next events look like duplicates.
        A relayed session is not this process's to release (``reset_relayed``)."""
        if session_id in self._relayed:
            return
        log = self._logs.get(session_id)
        if log is not None:
            log.buf.clear()
            log.active = None
            log.active_turn = None
            log.phase = None
            log.claim = None
            log.claim_phase = None
            log.claim_task = None
            log.waiters = False
            # the session is gone: it is idle now (new seq, so a tab never keeps an
            # old "streaming"; the seq itself only grows)
            self.set_activity(session_id, "idle")

    def subscriber_count(self) -> int:
        return len(self._subs)

    # ── subscribing ──────────────────────────────────────────────────
    async def subscribe(self, cursors: Mapping[str, int],
                        epoch: Optional[str], *, only: bool = False) -> AsyncIterator[dict]:
        """``only``: deliver the sessions named in ``cursors`` and no others (a relay
        that watches one session of this daemon need not download all of them)."""
        sub = _Subscriber(self._queue_size)
        self._subs.add(sub)  # register BEFORE reading any buffer
        try:
            yield {
                "type": "events_hello",
                "epoch": self.epoch,
                "seqs": {sid: log.seq for sid, log in self._logs.items()
                         if not only or sid in cursors},
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
            provider = _state_provider
            if provider is not None:
                for sid in cursors:
                    try:
                        state = await provider(sid)
                    except Exception as exc:  # noqa: BLE001 — a display aid
                        logger.debug("session_events.state_snapshot_failed %s: %s", sid, exc)
                        continue
                    if state is not None:
                        # not buffered: a snapshot, at the seq the tab now holds
                        yield {"type": "session_state", "sessionId": sid, "turnId": "",
                               "eventSeq": high.get(sid, 0), "state": state, "snapshot": True}
            while True:
                while sub.queue:
                    env = sub.queue.popleft()
                    sid = env["sessionId"]
                    if only and sid not in cursors:
                        continue
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
    def _compact_active(log: _Log) -> bool:
        """At ACTIVE_TURN_LIMIT: drop the streaming deltas an ``assistant``
        envelope already carries (every delta before the turn's latest
        ``assistant``), so a very long turn still replays whole. True when room
        was made. A replay without them displays the same (checked on the
        liveReload fixture with every such delta removed: 80/80)."""
        active = log.active or []
        if log.seq < log.retry_seq:
            return False         # the last try freed too little: wait, no rescan per event
        last = max((i for i, e in enumerate(active) if e.get("type") == "assistant"), default=-1)
        if last < 0:
            kept = active
        else:
            kept = [e for i, e in enumerate(active)
                    if i == 0 or i > last or e.get("type") not in SUPERSEDED_BY_ASSISTANT]
        # a compaction must free a tenth of the limit; otherwise the next try waits
        # that many events, so the O(n) scan never runs once per event
        if len(kept) > ACTIVE_TURN_LIMIT * 0.9:
            log.retry_seq = log.seq + max(1, int(ACTIVE_TURN_LIMIT * 0.1))
            if not log.warned:
                logger.warning("session_events.active_turn_over_limit turn=%s: a replay "
                               "of it may miss part of its middle (the record has it)",
                               log.active_turn)
                log.warned = True
            return False
        log.active = kept
        return True

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
