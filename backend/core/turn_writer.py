"""What one turn writes to the transcript — the single writer of a turn's rows.

Design: docs/design/chat-transcript-single-source.md §3 (S1, "the DB is the one
record"). Every router turn (run_conversation, continue_with_answer,
continue_with_cmd_permission, run_injected_turn) gets ONE ``TurnWriter``:

- ``write(event)``: called for every event of the turn BEFORE it is published;
  persists what the event contributes (``SessionRouter._persist_stream_event``:
  assistant rows, step results, the permission prompt, the turn's products) and
  returns the events to deliver before it. A write failure is logged and never
  stops the turn (the event still goes out; the DB layer logs the lost row).
- A step's results are written as they arrive (appended to the turn's newest
  row, the one with their tool calls): a process killed right after a result
  loses nothing it showed.
- ``close()``: writes what the turn still holds (results whose immediate write
  failed). ``SessionRouter._published_turn`` calls it in its
  ``finally``, before ``turn_end`` is published, however the turn ends (result,
  Stop, error, abort, cancellation at shutdown), so a tab that reloads on
  ``turn_end`` finds every block it was shown. Idempotent.
- ``begin(session_id)`` (``_published_turn``, at ``turn_start``): the writer is
  the session's running one — ``TurnWriter.running(session_id)`` — until
  ``close()``. One turn runs per session at a time; the registry is keyed by
  session and every entry is removed by its own writer's ``close()``.
- ``record_outcome(block)``: the turn's ``turn_outcome`` block (core/turn_outcome),
  written by ``SessionRouter._published_turn`` before ``close()`` for a started turn.
- ``flush()``: writes what is held now and keeps the writer open — for a write
  into the running turn from outside it (``_persist_stop_receipt`` flushes
  ``running(sid)`` first), so the turn's rows keep the order the turn showed.

- A ``user_steered`` event (design chat-steer D4): the CLI read a steer here. The
  results held so far go onto the current row; the turn's later rows are keyed
  ``{steer_client_id}-asst`` (the steer's answer), the unit's ``_turn_row_key``
  follows (continuations and the Stop receipt write there too), and the event
  goes out with ``rowKey`` and the turn's ``segments`` (every segment key, in
  order). The steer's own row gets the writer's time here
  (``session_pending.restamp_steer``): the echo time comes from the reader, which
  can be ahead of this writer, and a reload orders rows by time. Not once a Stop
  started: the row stays before the receipt (chat-steer T29).

``write`` / ``flush`` / ``close`` are serialised by one lock: the Stop request
runs concurrently with the turn, and a flush between a write's row insert and
its clearing of the held results would write them twice.

Its writes are not reported to the transcript feed (core/transcript_feed): the
turn's own events carry them. Nothing else calls ``_persist_stream_event`` for a turn
(tests/test_turn_writer.py holds that). The transcript rev is assigned by the schema v15 triggers, not here.

Public: ``TurnWriter`` (``TurnWriter.running``).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover
    from .session_router import SessionRouter

from database.sqlite import TRANSCRIPT_FEED_SUPPRESSED

logger = logging.getLogger(__name__)

#: the running turn's writer, per session (see ``begin``)
_RUNNING: "dict[str, TurnWriter]" = {}


class TurnWriter:
    """The rows of one turn (see module doc)."""

    def __init__(self, router: "SessionRouter", session_id: Optional[str], *,
                 row_key: Optional[str], label: str = "") -> None:
        self._router = router
        self._session_id = session_id
        self._row_key = row_key
        self._label = label
        self._held: list[dict] = []   # step results waiting for the next assistant row
        self._fresh_segment = False   # a steer's segment with no assistant row yet
        self._closed = False
        self._outcome_recorded = False   # record_outcome writes one block per turn
        self._lock = asyncio.Lock()

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @staticmethod
    def running(session_id: Optional[str]) -> "Optional[TurnWriter]":
        """The writer of the session's running turn, if one runs."""
        return _RUNNING.get(session_id) if session_id else None

    def begin(self, session_id: str) -> None:
        """The turn started (``turn_start``): this is the session's running writer."""
        self._session_id = session_id
        if not self._closed:
            _RUNNING[session_id] = self

    async def write(self, event: dict, *, session_id: Optional[str] = None,
                    files: Optional[dict] = None, changes: Any = None) -> list[dict]:
        """Persist one event of the turn; returns the events to deliver before it.
        ``session_id``: the turn's session once known (a first send creates it)."""
        if session_id:
            self._session_id = session_id
        if self._closed or not self._session_id or not isinstance(event, dict):
            return []
        async with self._lock:
            token = TRANSCRIPT_FEED_SUPPRESSED.set(True)   # the turn's events carry these rows
            try:
                if event.get("type") == "user_steered":
                    await self._steer_segment(event)
                    return []
                if event.get("type") == "assistant":
                    self._fresh_segment = False
                out = await self._router._persist_stream_event(
                    self._session_id, event, self._held, label=self._label,
                    client_id=self._row_key, files=files, changes=changes,
                ) or []
                if event.get("type") == "tool_result" and self._held:
                    # durable now, not with the next row: a process killed between
                    # a step's result and the model's next message (SIGKILL, no
                    # finally) must not lose a result the user was shown
                    await self._write_results_now()
                return out
            except Exception as exc:  # noqa: BLE001 — persistence never wedges the turn
                logger.error("turn_writer.write_failed session_id=%s type=%s: %s",
                             self._session_id, event.get("type"), exc)
                return []
            finally:
                TRANSCRIPT_FEED_SUPPRESSED.reset(token)

    async def _steer_segment(self, event: dict) -> None:
        """The CLI read a steer at this point of the turn: start its segment."""
        if self._held:
            await self._write_results_now()    # onto the row that holds their calls
        cid = event.get("clientId")
        unit = self._router.get_unit(self._session_id) if hasattr(self._router, "get_unit") else None
        segments = list(getattr(unit, "_turn_segments", None) or ([self._row_key] if self._row_key else []))
        if cid and getattr(unit, "_stop_receipt_at", None) is None:
            await self._restamp_steer_row(cid)
        if cid:
            self._row_key = f"{cid}-asst"
            self._fresh_segment = True
            if self._row_key not in segments:
                segments.append(self._row_key)
            if unit is not None:
                unit._turn_row_key = cid
                unit._turn_segments = segments
        event["rowKey"] = self._row_key
        event["segments"] = segments

    async def _restamp_steer_row(self, client_id: str) -> None:
        from . import session_pending
        try:
            if not await session_pending.restamp_steer(self._session_id, client_id,
                                                       datetime.now().isoformat()):
                logger.warning("turn_writer.steer_row_missing session_id=%s client_id=%s",
                               self._session_id, client_id)
        except Exception as exc:  # noqa: BLE001 — the segment still starts
            logger.error("turn_writer.steer_restamp_failed session_id=%s client_id=%s: %s",
                         self._session_id, client_id, exc)

    async def _write_results_now(self) -> None:
        """The held step results onto the turn's newest row (the one holding their
        tool calls), else a row of their own."""
        held, self._held = list(self._held), []
        from database import db
        if self._row_key and await db.messages.append_to_turn_row(self._session_id, self._row_key, held):
            return
        if self._fresh_segment:
            # a result for a call made before the steer was read: the CLI echoed the
            # steer before every result of that step was back, which no experiment
            # showed (E2, E9). Observed, not repaired: it goes into a row of its own.
            logger.error("turn_writer.steer_result_after_segment session_id=%s row_key=%s",
                         self._session_id, self._row_key)
        await self._router._persist_assistant_blocks(
            self._session_id, held, None,
            label=f"{self._label or 'turn'}-results", client_id=self._row_key)

    async def record_outcome(self, block: dict) -> bool:
        """Write the turn's ``turn_outcome`` block (core/turn_outcome) onto its newest
        row, after what it still holds, else as a row of its own. Before ``close``;
        a closed writer writes nothing (False)."""
        async with self._lock:
            if self._closed or not self._session_id or self._outcome_recorded:
                return False                     # one outcome per turn
            await self._write_held()
            # marked before the write: a cancel can land after the commit, and a retry
            # must not add a second outcome (one lost is better than one doubled)
            self._outcome_recorded = True
            token = TRANSCRIPT_FEED_SUPPRESSED.set(True)
            try:
                from database import db
                if not (self._row_key and await db.messages.append_to_turn_row(
                        self._session_id, self._row_key, [block])):
                    await self._router._persist_assistant_blocks(
                        self._session_id, [block], None,
                        label=f"{self._label or 'turn'}-outcome", client_id=self._row_key)
                return True
            finally:
                TRANSCRIPT_FEED_SUPPRESSED.reset(token)

    async def flush(self) -> None:
        """Write the step results held now; the turn goes on."""
        async with self._lock:
            await self._write_held()

    async def close(self) -> None:
        """Write the step results still held (the turn ended without the assistant
        row or terminal event that carries them). Later writes are ignored."""
        try:
            async with self._lock:
                if self._closed:
                    return
                self._closed = True
                await self._write_held()
        finally:
            if self._session_id and _RUNNING.get(self._session_id) is self:
                del _RUNNING[self._session_id]

    async def _write_held(self) -> None:
        if not self._held or not self._session_id:
            return
        held, self._held = list(self._held), []
        token = TRANSCRIPT_FEED_SUPPRESSED.set(True)
        try:
            await self._router._persist_assistant_blocks(
                self._session_id, held, None,
                label=f"{self._label or 'turn'}-results", client_id=self._row_key,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("turn_writer.write_held_failed session_id=%s: %s", self._session_id, exc)
        finally:
            TRANSCRIPT_FEED_SUPPRESSED.reset(token)
