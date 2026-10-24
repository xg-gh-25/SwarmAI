"""CliStreamReader — the one consumer of a Claude CLI subprocess's SDK message stream.

Why it exists
-------------
The SDK reads the CLI's stdout in ONE loop that both answers control requests (hooks,
permissions, SDK MCP) and pushes every other frame into a 100-slot memory stream
(``claude_agent_sdk/_internal/query.py``). The daemon used to drain that stream only
inside a turn it started (``receive_response()`` per send). The CLI also starts turns
on its own — a background command or background subagent finished — and such a turn
filled the buffer, parked the SDK reader, and left the turn's first PreToolUse hook
unanswered until the user's next message. It was also invisible, and a CLI-initiated
text-only turn could leave a ResultMessage that ended the user's next turn early.

What it does
------------
One task per live client iterates ``client.receive_messages()`` from spawn to kill and
routes every frame into a ``TurnStream`` (an async iterator with the
``receive_response()`` contract: yields up to and including the turn's ResultMessage).

Turn ownership (verified against the real CLI, see the design doc §2):

- ``init`` opens a turn. If a claim is waiting (``claim()`` is called BEFORE the query
  is written) and no task notification arrived since it was registered, the turn is
  the claimant's — live, exactly as ``receive_response`` behaved.
- Otherwise the turn is a provisional *injected* turn (the CLI started it): its frames
  buffer in its stream and ``on_injected`` hands the stream to the session.
- With ``replay-user-messages`` on, the CLI echoes each user message we write, with our
  uuid, where it consumes it. An echo of a waiting claim inside an injected turn whose
  sink has not started TRANSFERS that turn (buffered frames included) to the claimant —
  the notification race, where the CLI folds our message into its own turn. If the sink
  already started, both streams receive the rest and end on the same ResultMessage.
- Echoes are consumed here, never forwarded.
- A ResultMessage closes the open turn; a claimant turn that never showed its echo is
  logged as ``turn_owner_mismatch``.

Quiescence (``quiescent``): the CLI may be written to only when its last
``session_state_changed`` was ``idle`` with no finished-task signal since (a
``task_notification`` or a terminal ``task_updated``), no turn open and no claim waiting. Without session-state frames (an older CLI) a ResultMessage
counts as idle. ``on_idle`` fires whenever the CLI reports idle. ``on_task(subtype, msg)`` gets every
``task_started`` / ``task_notification`` / ``task_updated`` frame, turn open or not.

Failure:

- The stream ENDS (EOF): the open turn and every waiting claim end as an ended stream
  (as ``receive_response`` did); the stream raises: they raise that exception. Either
  way the reader is ``dead``, every later ``claim()`` raises, ``on_error`` fires once,
  and a dead reader counts as quiescent with no background work: the session respawns
  on its next write instead of reporting a busy CLI forever.
- ``close()`` (our own teardown) ends them with ``TurnAbandoned``.
- With the CLI alive: an idle while a turn is still open ends it with
  ``TurnAbandoned``; an idle after a ``running`` that opened no turn ends the waiting
  claims with ``MessageNotConsumed`` (the message never reached the model). Never a hang.

Design: Knowledge/Designs/2026-10-20-continuous-cli-stream-reader-design.md (v2).
"""
from __future__ import annotations

import asyncio
import logging
import time
from core.awake_clock import awake_time  # liveness durations exclude sleep
from collections import OrderedDict
from typing import Any, Callable, Optional

from claude_agent_sdk import ResultMessage, SystemMessage, UserMessage

logger = logging.getLogger(__name__)

_END = object()

# Terminal task statuses (SDK types.TERMINAL_TASK_STATUSES plus the notification's
# mapped "stopped"); kept local so an SDK without the constant still imports.
_TERMINAL = frozenset({"completed", "failed", "killed", "stopped"})


def _patch_status(data: dict) -> Optional[str]:
    patch = data.get("patch")
    return patch.get("status") if isinstance(patch, dict) else None


_ABANDONED = "turn abandoned: "
_NOT_CONSUMED = _ABANDONED + "message not consumed: "


class TurnAbandoned(Exception):
    """The turn could not complete while the CLI is ALIVE: it went idle before the
    turn's ResultMessage, or our own close() tore the reader down. A dead stream (EOF,
    transport error) is never a TurnAbandoned: it keeps its own error so the existing
    retry-with-resume classification applies (see ``CliStreamReader.dead``)."""

    def __init__(self, detail: str) -> None:
        super().__init__(_ABANDONED + detail)


class MessageNotConsumed(TurnAbandoned):
    """The CLI went idle without taking the claimed message: it never reached the model,
    so it can be delivered again (the session stores it as pending)."""

    def __init__(self, detail: str) -> None:
        Exception.__init__(self, _NOT_CONSUMED + detail)


class CliStreamEnded(Exception):
    """The CLI's message stream ended (the subprocess exited)."""


def is_turn_abandoned(err: Any) -> bool:
    """True for a TurnAbandoned, or the str() of one (a recovery path that passes the
    error on as text, e.g. a ``_fallthrough_error``)."""
    return isinstance(err, TurnAbandoned) or str(err).startswith(_ABANDONED)


def is_message_not_consumed(err: Any) -> bool:
    return isinstance(err, MessageNotConsumed) or str(err).startswith(_NOT_CONSUMED)


class TurnStream:
    """One turn's frames. Async iterator with the ``receive_response()`` contract."""

    def __init__(self, kind: str, uuid: Optional[str] = None) -> None:
        self.kind = kind
        self.uuid = uuid
        self.origin: Optional[dict] = None
        self.echo_seen = False
        self.transferred = False
        self.started = False
        self.closed = False
        self._q: asyncio.Queue = asyncio.Queue()
        self._notif_mark = 0
        # A waiting claim saw the CLI start running after it was registered, and no
        # turn opened since: an idle then means the CLI went quiet without it.
        self._running_seen = False
        # time.time() of this turn's first frame (set when the reader opens or hands
        # over the turn): a turn whose frames were buffered before its consumer
        # started is measured from when the CLI ran it, not from when it was read.
        self.opened_at: Optional[float] = None

    def _put(self, msg: Any) -> None:
        if not self.closed:
            self._q.put_nowait(msg)

    def _end(self, exc: Optional[BaseException] = None) -> None:
        if self.closed:
            return
        self.closed = True
        self._q.put_nowait(exc if exc is not None else _END)

    def _take_buffer(self) -> list:
        out = []
        while not self._q.empty():
            out.append(self._q.get_nowait())
        return out

    def __aiter__(self) -> "TurnStream":
        return self

    async def __anext__(self) -> Any:
        item = await self._q.get()
        if item is _END:
            self._q.put_nowait(_END)  # stay exhausted for any later read
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            self._q.put_nowait(item)
            raise item
        return item


class CliStreamReader:
    """Consumes one client's message stream for the client's whole life."""

    def __init__(
        self,
        client: Any,
        session_id: str,
        *,
        on_injected: Optional[Callable[[TurnStream], None]] = None,
        on_idle: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[], None]] = None,
        on_task: Optional[Callable[[str, Any], None]] = None,
        on_bg_frame: Optional[Callable[[str, Any], None]] = None,
    ) -> None:
        self._client = client
        self._on_task = on_task
        self._on_bg_frame = on_bg_frame
        self._session_id = session_id
        self._on_injected = on_injected
        self._on_idle = on_idle
        self._on_error = on_error
        self._closed_by_us = False
        self._task: Optional[asyncio.Task] = None
        self._open: Optional[TurnStream] = None
        self._merged: Optional[TurnStream] = None  # a claimant sharing a started injected turn
        self._claims: "OrderedDict[str, TurnStream]" = OrderedDict()
        self._notif_count = 0
        self._notif_at_idle = 0
        self._state: Optional[str] = None  # last session_state_changed value
        self._state_seen = False
        self._idle = True  # nothing has run yet
        self._bg: set[str] = set()
        self._bg_frames = 0  # background sub-agent frames seen with no turn open
        self.error: Optional[BaseException] = None

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._run())

    def close(self) -> None:
        """Stop reading and fail every open stream (the client is being torn down)."""
        self._closed_by_us = True
        if self.error is None:
            self.error = TurnAbandoned(f"stream reader closed (session {self._session_id})")
        self._fail_all(self.error)
        if self._task is not None and not self._task.done():
            self._task.cancel()

    # ── public state ───────────────────────────────────────────────────

    @property
    def dead(self) -> bool:
        """The stream failed or ended on its own (not our close()): the subprocess is
        gone or unreadable. Nothing will run on it; the session must respawn."""
        return self.error is not None and not self._closed_by_us

    @property
    def notif_count(self) -> int:
        """Finished-task signals seen so far (a gate records it; see ``claim``)."""
        return self._notif_count

    @property
    def quiescent(self) -> bool:
        if self.dead:
            return True  # nothing runs on a dead stream; the next write respawns
        return (
            self.error is None
            and self._idle
            and self._notif_count == self._notif_at_idle
            and self._open is None
            and not self._claims
        )

    @property
    def background_tasks(self) -> frozenset:
        return frozenset(self._bg)

    def claim(self, uuid: str, kind: str = "human",
              notif_mark: Optional[int] = None) -> TurnStream:
        """Register the next turn's owner. Call BEFORE writing the query.

        *notif_mark*: the finished-task count the caller saw when it decided the CLI
        was quiescent (its gate). A notification after that point means the next
        turn may be the CLI's own: it goes to the sink, and the echo transfer gives it
        to this claim if the CLI folded the message in. Default: the current count."""
        if self.error is not None:
            raise self.error
        ts = TurnStream(kind, uuid)
        ts._notif_mark = self._notif_count if notif_mark is None else notif_mark
        self._claims[uuid] = ts
        return ts

    def drop_claim(self, uuid: str) -> None:
        """Withdraw a claim whose message was never written (the write failed)."""
        ts = self._claims.pop(uuid, None)
        if ts is not None:
            ts._end(TurnAbandoned("the user message was not written"))

    def start_sink(self, ts: TurnStream) -> bool:
        """Mark an injected stream as consumed by the session's sink. False when it was
        transferred to a claimant (or already ended without frames to give)."""
        if ts.transferred or ts.started:
            return False
        ts.started = True
        return True

    # ── reading ────────────────────────────────────────────────────────

    async def _run(self) -> None:
        try:
            async for msg in self._client.receive_messages():
                try:
                    self._route(msg)
                except Exception:  # noqa: BLE001 — one bad frame must not stop reading
                    logger.exception("cli_stream_reader.route_failed session_id=%s", self._session_id)
            ended = self.error is None
            if ended:
                self.error = CliStreamEnded(f"CLI stream ended (session {self._session_id})")
            # EOF: the open turn and waiting claims end as an ended stream, exactly as
            # receive_response did, so the orchestrator's end-of-stream handling (zombie
            # detection -> retry with --resume) runs; later claims raise the error.
            self._fail_all(None if ended else self.error)
        except asyncio.CancelledError:
            if self.error is None:
                self.error = TurnAbandoned(f"stream reader cancelled (session {self._session_id})")
            self._fail_all(self.error)
            raise
        except Exception as exc:  # noqa: BLE001 — surfaced to the turn that waits for it
            self.error = exc
            # A CLI we SIGKILLed ourselves (release, refresh, recycle, reap) ends its
            # stream with exit -9: expected, INFO. Any other end stays WARNING.
            ours = False
            if "-9" in str(exc):
                try:
                    from core import kill_audit
                    ours = kill_audit.killed_session_recently(self._session_id)
                except Exception:  # noqa: BLE001
                    logger.debug("kill audit unreadable", exc_info=True)
            (logger.info if ours else logger.warning)(
                "cli_stream_reader.stream_error session_id=%s: %s: %s%s",
                self._session_id, type(exc).__name__, exc, " (killed by SwarmAI)" if ours else "")
            self._fail_all(exc)
        finally:
            if self.dead and self._on_error is not None:
                try:
                    self._on_error()
                except Exception:  # noqa: BLE001
                    logger.exception("cli_stream_reader.on_error_failed session_id=%s", self._session_id)

    def _fail_all(self, exc: Optional[BaseException]) -> None:
        """End every open and waiting stream: with *exc*, or as a plain end of stream.
        The background-task set goes with the stream (the tasks cannot report back)."""
        for ts in (self._open, self._merged, *self._claims.values()):
            if ts is not None:
                ts._end(exc)
        self._open = None
        self._merged = None
        self._claims.clear()
        self._bg.clear()

    def _deliver(self, msg: Any) -> None:
        if self._open is not None:
            self._open._put(msg)
        if self._merged is not None:
            self._merged._put(msg)

    def _open_turn(self, msg: Any) -> None:
        # A turn opened: a running the waiting claims saw is explained by it, so an
        # idle after it does not mean the CLI skipped them (it may take one next).
        for waiting in self._claims.values():
            waiting._running_seen = False
        claim = next(iter(self._claims.values()), None)
        if claim is not None and claim._notif_mark == self._notif_count:
            self._claims.pop(claim.uuid, None)
            self._open = claim
        else:
            ts = TurnStream("injected")
            self._open = ts
            if self._on_injected is not None:
                try:
                    self._on_injected(ts)
                except Exception:  # noqa: BLE001
                    logger.exception("cli_stream_reader.on_injected_failed session_id=%s", self._session_id)
            else:
                logger.warning("cli_stream_reader.injected_turn_unhandled session_id=%s", self._session_id)
        self._idle = False
        self._open.opened_at = awake_time()
        self._open._put(msg)

    def _on_echo(self, uuid: str) -> None:
        """A user message we wrote was consumed by the CLI in the open turn."""
        cur = self._open
        if cur is not None and cur.uuid == uuid:
            cur.echo_seen = True
            return
        claim = self._claims.pop(uuid, None)
        if claim is None:
            return
        claim.echo_seen = True
        if cur is None:
            # an echo outside any turn (should not happen): the claimant owns what follows
            claim.opened_at = awake_time()
            self._open = claim
            return
        claim.opened_at = cur.opened_at
        if cur.kind == "injected" and not cur.started:
            for item in cur._take_buffer():
                claim._put(item)
            cur.transferred = True
            cur._end()
            self._open = claim
            logger.info("cli_stream_reader.claim_transferred session_id=%s uuid=%s",
                        self._session_id, uuid)
            return
        self._merged = claim
        logger.warning("cli_stream_reader.merged_into_injected session_id=%s uuid=%s open_kind=%s",
                       self._session_id, uuid, cur.kind)

    def _route(self, msg: Any) -> None:
        parent = getattr(msg, "parent_tool_use_id", None)
        if parent and self._on_bg_frame is not None and not isinstance(msg, SystemMessage):
            # a sub-agent's own frame: the session learns a background agent is
            # working (its progress), with or without a turn open; routing below is
            # unchanged (docs/design/2026-10-24-bg-agent-stop-livereload.md D1)
            try:
                self._on_bg_frame(parent, msg)
            except Exception:  # noqa: BLE001 — a display aid never stops reading
                logger.exception("cli_stream_reader.on_bg_frame_failed session_id=%s", self._session_id)
        if isinstance(msg, SystemMessage):
            sub = msg.subtype
            data = msg.data if isinstance(getattr(msg, "data", None), dict) else {}
            if sub == "session_state_changed":
                self._state_seen = True
                self._state = data.get("state")
                if self._state == "running":
                    for ts in self._claims.values():
                        ts._running_seen = True
                if self._state == "idle":
                    self._abandon_unconsumed_claims()
                    if self._open is not None:
                        logger.error("cli_stream_reader.idle_with_open_turn session_id=%s kind=%s",
                                     self._session_id, self._open.kind)
                        self._open._end(TurnAbandoned("the CLI went idle before the turn's result"))
                        if self._merged is not None:
                            self._merged._end(TurnAbandoned("the CLI went idle before the turn's result"))
                        self._open = None
                        self._merged = None
                    self._mark_idle()
                else:
                    self._idle = False
                return
            if sub in ("task_started", "task_notification", "task_updated"):
                self._task_event(sub, msg)
            if sub == "task_started":
                tid = data.get("task_id")
                if tid:
                    self._bg.add(tid)
            elif sub == "task_notification" or (
                    sub == "task_updated" and _patch_status(data) in _TERMINAL):
                # A finished background task: the CLI is about to start a turn for it.
                # The terminal task_updated is the earliest sign; a non-terminal one
                # (running / paused progress) does not end quiescence.
                self._notif_count += 1
                self._bg.discard(data.get("task_id"))
            if sub == "init" and self._open is None:
                self._open_turn(msg)
                return
            if self._open is not None:
                self._deliver(msg)
            return
        if isinstance(msg, UserMessage) and msg.uuid and (
                msg.uuid in self._claims
                or (self._open is not None and msg.uuid == self._open.uuid)
                or (self._merged is not None and msg.uuid == self._merged.uuid)):
            self._on_echo(msg.uuid)
            return
        if self._open is None:
            if isinstance(msg, ResultMessage):
                # A result with no turn open (e.g. the output of an interrupt sent
                # to an idle CLI) ends nothing and must not open a turn: as a
                # claimant's first frame it would end that user turn at once.
                logger.warning("cli_stream_reader.stray_result session_id=%s subtype=%s",
                               self._session_id, getattr(msg, "subtype", None))
                if not self._state_seen:
                    self._mark_idle()
                return
            if getattr(msg, "parent_tool_use_id", None):
                # A background sub-agent's own progress (its Agent was called in a
                # turn that has ended): it belongs to that Agent, not to a new turn.
                # Opening one here showed an empty, endlessly streaming reply and
                # gave a waiting user claim a turn that was not its own. The CLI
                # opens its own turn (init) when the task finishes and it answers.
                self._bg_frames += 1
                if self._bg_frames == 1 or self._bg_frames % 50 == 0:
                    logger.info("cli_stream_reader.background_subagent_frames session_id=%s count=%d",
                                self._session_id, self._bg_frames)
                return
            self._open_turn(msg)
            return
        self._deliver(msg)
        if isinstance(msg, ResultMessage):
            self._close_turn(msg)

    def _task_event(self, sub: str, msg: Any) -> None:
        """Hand a task frame to the session (``on_task``), with or without a turn
        open: a background agent's start and end arrive between turns too."""
        if self._on_task is None:
            return
        try:
            self._on_task(sub, msg)
        except Exception:  # noqa: BLE001 — a display aid never stops reading
            logger.exception("cli_stream_reader.on_task_failed session_id=%s", self._session_id)

    def _abandon_unconsumed_claims(self) -> None:
        """On idle: end every waiting claim the CLI ran past without consuming.

        Only claims that saw a ``running`` after they were registered: an idle
        already in flight when a claim was written (a leak continuation written
        right after a result) precedes the CLI reading that message and must not
        abandon it."""
        for uuid, ts in list(self._claims.items()):
            if ts._running_seen:
                del self._claims[uuid]
                logger.error("cli_stream_reader.claim_not_consumed session_id=%s uuid=%s kind=%s",
                             self._session_id, uuid, ts.kind)
                ts._end(MessageNotConsumed("the CLI went idle without taking the message"))

    def _close_turn(self, result: ResultMessage) -> None:
        for ts in (self._open, self._merged):
            if ts is None:
                continue
            ts.origin = getattr(result, "origin", None)
            if ts.kind != "injected" and not ts.echo_seen:
                logger.error(
                    "cli_stream_reader.turn_owner_mismatch session_id=%s kind=%s uuid=%s origin=%s",
                    self._session_id, ts.kind, ts.uuid, ts.origin)
            ts._end()
        self._open = None
        self._merged = None
        if not self._state_seen:
            self._mark_idle()

    def _mark_idle(self) -> None:
        self._idle = True
        self._notif_at_idle = self._notif_count
        if self._on_idle is not None:
            try:
                self._on_idle()
            except Exception:  # noqa: BLE001
                logger.exception("cli_stream_reader.on_idle_failed session_id=%s", self._session_id)
