"""What one turn writes to the transcript — the single writer of a turn's rows.

Design: docs/design/chat-transcript-single-source.md §3 (S1, "the DB is the one
record"). Every router turn (run_conversation, continue_with_answer,
continue_with_cmd_permission, run_injected_turn) gets ONE ``TurnWriter``:

- ``write(event)``: called for every event of the turn BEFORE it is published;
  persists what the event contributes (``SessionRouter._persist_stream_event``:
  assistant rows, step results, the permission prompt, the turn's products) and
  returns the events to deliver before it. A write failure is logged and never
  stops the turn (the event still goes out; the DB layer logs the lost row).
- ``close()``: writes what the turn still holds — step results waiting to ride
  the next assistant row. ``SessionRouter._published_turn`` calls it in its
  ``finally``, before ``turn_end`` is published, however the turn ends (result,
  Stop, error, abort, cancellation at shutdown), so a tab that reloads on
  ``turn_end`` finds every block it was shown. Idempotent.
- ``begin(session_id)`` (``_published_turn``, at ``turn_start``): the writer is
  the session's running one — ``TurnWriter.running(session_id)`` — until
  ``close()``. One turn runs per session at a time; the registry is keyed by
  session and every entry is removed by its own writer's ``close()``.
- ``flush()``: writes what is held now and keeps the writer open — for a write
  into the running turn from outside it (``_persist_stop_receipt`` flushes
  ``running(sid)`` first), so the turn's rows keep the order the turn showed.

``write`` / ``flush`` / ``close`` are serialised by one lock: the Stop request
runs concurrently with the turn, and a flush between a write's row insert and
its clearing of the held results would write them twice.

Its writes are not reported to the transcript feed (core/transcript_feed): the
turn's own events carry them. Nothing else calls ``_persist_stream_event`` for a turn
(tests/test_turn_writer.py holds that). P2 assigns the transcript rev here.

Public: ``TurnWriter`` (``TurnWriter.running``).
"""
from __future__ import annotations

import asyncio
import logging
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
        self._closed = False
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
                return await self._router._persist_stream_event(
                    self._session_id, event, self._held, label=self._label,
                    client_id=self._row_key, files=files, changes=changes,
                ) or []
            except Exception as exc:  # noqa: BLE001 — persistence never wedges the turn
                logger.error("turn_writer.write_failed session_id=%s type=%s: %s",
                             self._session_id, event.get("type"), exc)
                return []
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
