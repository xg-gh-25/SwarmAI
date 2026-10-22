"""The send ledger: one send is one message, however often it arrives.

Design: docs/design/chat-transcript-single-source.md S2. Every send that carries
a user message reserves its ``client_id`` in ``send_requests`` (schema v14)
before anything else happens — before a session is created, a unit spawned, or
a user row written. The same client_id arriving again (the desktop's reconnect
resend, a resend after SESSION_BUSY, a first send retried before its session
existed, a channel platform redelivering) finds the reservation and is answered
with an ``accepted`` receipt instead of running again.

What the ledger records is only what nothing else records: that the send was
taken, by which process, for which session, and its user row. Whether that row
is still queued is the row's own ``sent`` / ``pending_seq``; whether its turn is
running is the session's event hub. The receipt reads them, it does not copy.

- ``reserve(client_id, session_id)`` -> ``Reservation``: ``taken`` (go on) or
  the existing record. A reservation that never reached its user row — marked
  ``failed``, or left ``reserved`` by another process (a daemon that died) — is
  taken over. A ledger that cannot be read or written fails OPEN (the send runs,
  as before the ledger; logged): a broken ledger must not stop chat.
- ``persisted(client_id, user_message_id)`` / ``failed(client_id)``: the send's
  user row was written / the send ended before it was. ``release(client_id)``:
  the row was written but handed back (it could not be kept as pending) and
  removed — the client re-sends with the same client_id, which runs again.
- ``accepted(...)``: the receipt every taken send's POST gets once its user row
  is written; ``receipt(record)``: the same reply for a duplicate (both POST
  only, never published: they are not part of any turn).
- ``channel_client_id(...)``: a channel message's client_id from its platform
  message id (Slack ``ts`` …), so a redelivery is the same send.
- ``prune()``: requests older than the messages TTL (no row left to duplicate).

Public: ``Reservation``, ``reserve``, ``persisted``, ``failed``, ``release``, ``accepted``, ``receipt``,
``channel_client_id``, ``prune``, ``OWNER``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

#: this process; a reservation by another owner and without a user row was left
#: by a process that is gone (one daemon owns the DB at a time)
OWNER = uuid4().hex[:12]

RESERVED, PERSISTED, FAILED = "reserved", "persisted", "failed"


@dataclass(frozen=True)
class Reservation:
    taken: bool
    record: Optional[dict] = None   # the existing request when not taken


def _now() -> str:
    return datetime.now().isoformat()


async def reserve(client_id: str, session_id: str) -> Reservation:
    from database import db
    now = _now()
    try:
        if await db.reserve_send_request({
                "client_id": client_id, "session_id": session_id, "status": RESERVED,
                "owner": OWNER, "user_message_id": None, "created_at": now, "updated_at": now}):
            return Reservation(True)
        if await db.retake_send_request(client_id, OWNER, now):
            logger.info("send_requests.retaken client_id=%s", client_id)
            return Reservation(True)
        record = await db.get_send_request(client_id)
    except Exception as exc:  # noqa: BLE001 — fail open: the ledger never stops a send
        logger.error("send_requests.reserve_failed client_id=%s: %s — sending without it",
                     client_id, exc)
        return Reservation(True)
    if record is None:            # pruned between the two statements
        return Reservation(True)
    if record.get("session_id") != session_id:
        logger.warning("send_requests.duplicate_other_session client_id=%s asked=%s recorded=%s",
                       client_id, session_id, record.get("session_id"))
    logger.info("send_requests.duplicate client_id=%s session_id=%s status=%s",
                client_id, record.get("session_id"), record.get("status"))
    return Reservation(False, record)


async def _set(client_id: str, **fields) -> None:
    from database import db
    try:
        await db.update_send_request(client_id, _now(), **fields)
    except Exception as exc:  # noqa: BLE001
        logger.error("send_requests.update_failed client_id=%s %s: %s", client_id, fields, exc)


async def persisted(client_id: str, user_message_id: str) -> None:
    await _set(client_id, status=PERSISTED, user_message_id=user_message_id)


async def failed(client_id: str) -> None:
    await _set(client_id, status=FAILED)


async def release(client_id: str) -> None:
    """The send was handed back (its row removed): the same client_id runs again."""
    await _set(client_id, status=FAILED, user_message_id=None)


def accepted(client_id: Optional[str], session_id: str, user_message_id: str) -> dict:
    """The receipt of a send just taken: the message is the server's now."""
    return {"type": "accepted", "duplicate": False, "clientId": client_id,
            "sessionId": session_id, "status": PERSISTED,
            "userMessageId": user_message_id, "queued": False}


async def receipt(record: dict) -> dict:
    """``accepted`` for a send already taken: its session, its user row and whether
    that row is still waiting to be delivered (the row's own pending state)."""
    out = {"type": "accepted", "duplicate": True, "clientId": record.get("client_id"),
           "sessionId": record.get("session_id"), "status": record.get("status"),
           "userMessageId": record.get("user_message_id"), "queued": False}
    mid = record.get("user_message_id")
    if mid:
        try:
            from database import db
            row = await db.messages.get(mid)
            out["queued"] = bool(row) and row.get("sent") == 0
        except Exception as exc:  # noqa: BLE001 — a display aid
            logger.debug("send_requests.receipt_row_read_failed %s: %s", mid, exc)
    return out


def channel_client_id(channel_type: str, channel_id: str, chat_id: Optional[str],
                      external_message_id: Optional[str]) -> Optional[str]:
    """The client_id of a channel message: its platform message id, scoped by
    channel and chat (a Slack ``ts`` is unique within its channel only). None when
    the platform gave no id (then each delivery is its own send, as before)."""
    if not external_message_id:
        return None
    return f"channel:{channel_type or '?'}:{channel_id}:{chat_id or ''}:{external_message_id}"


async def prune() -> int:
    """Drop requests older than the messages TTL: their rows are gone, so there is
    nothing left for a late retry to duplicate."""
    from database import db
    from database.sqlite import SQLiteMessagesTable
    before = (datetime.now() - timedelta(seconds=SQLiteMessagesTable.TTL_SECONDS)).isoformat()
    return await db.prune_send_requests(before)
