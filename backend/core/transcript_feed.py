"""The transcript feed: every change to a session's record reaches its subscribers.

Design: docs/design/chat-transcript-single-source.md S1 ("the DB is the one
record; the screen is a snapshot plus the changes after it"). A turn's content
reaches a tab as the turn's events. Everything else written to a transcript —
a user message, a permission decision, a Stop receipt, a background agent's
final status, a compaction, a question's answer, a removed row — used to reach
another tab only at its next reload. The DB layer now reports each committed
change of that kind (``database.sqlite.TRANSCRIPT_LISTENERS``; a turn's own rows,
written through ``core/turn_writer``, are not reported), and this module
publishes it on the session's event hub, outside any turn:

- ``{"type": "transcript_row", "op": "insert"|"update"|"upsert", "row": <history
  row>, "rev": n, "pending": bool}`` — the row as the history endpoint returns it
  (a compaction as its synthetic ``compact-<id>`` system row, ``upsert``). An
  ``update`` changes state on blocks a tab already shows (a decision, an answer,
  an agent's status); it never adds the row's other blocks again;
- ``{"type": "transcript_delete", "rowId": id, "rev": n}``.

``rev`` is the transcript revision of the change (schema v15): a tab whose
snapshot was read at rev R ignores a change with rev <= R, and keeps the newest
rev per row.

Public: ``install`` (idempotent; session_router imports this module).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_installed = False


def _publish(change: dict) -> None:
    from . import session_events
    sid = change.get("session_id")
    if not sid:
        return
    kind = change.get("kind")
    if kind == "delete":
        env = {"type": "transcript_delete", "rowId": change.get("row_id"), "rev": change.get("rev", 0)}
    elif kind == "compaction":
        from .compact_summary import history_message
        env = {"type": "transcript_row", "row": history_message(sid, change["row"]),
               "rev": change.get("rev", 0), "pending": False, "op": "upsert"}
    else:
        env = {"type": "transcript_row", "row": change["row"], "rev": change.get("rev", 0),
               "pending": bool(change.get("pending")), "op": change.get("op", "update")}
    session_events.hub().publish(sid, env, turn_id="")


def install() -> None:
    global _installed
    if _installed:
        return
    from database.sqlite import TRANSCRIPT_LISTENERS
    TRANSCRIPT_LISTENERS.append(_publish)
    _installed = True


install()
