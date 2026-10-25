"""The order of a session's transcript rows around a user Stop.

A Stop writes three things in sequence: the running turn's held rows (flush), the
``turn_stop`` receipt, and, once the interrupt is done, the stopped turn's
``turn_changes`` / ``turn_deliverables``. A message the user sends meanwhile is
stored at once (persist-before-slot, session_router). Rows are ordered by
``created_at`` and a reload merges only consecutive assistant rows, so a user row
written between those steps puts the stopped turn's card under the next turn.

One asyncio lock per session fixes the order: the Stop holds it from the flush to the
products' write, and a user row is written under it. A user row written during a
Stop therefore comes after everything the Stop writes. The wait is bounded
(``USER_ROW_WAIT_S``): past it the row is written anyway, so a hung Stop can delay a
message's place in the transcript but never lose the message.

Public: ``stop_writes``, ``user_row``, ``forget``, ``USER_ROW_WAIT_S``.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)

#: How long a user row waits for a running Stop's writes (interrupt 5 s + recycle +
#: the products' finish budget 10 s fit well inside).
USER_ROW_WAIT_S = 30.0

_locks: dict[str, asyncio.Lock] = {}
#: How many stop_writes / user_row blocks use the session's lock (holding or waiting).
_users: dict[str, int] = {}
#: Sessions forgotten while their lock was in use: dropped when the last user leaves.
_forgotten: set[str] = set()


@asynccontextmanager
async def _use(session_id: str) -> AsyncIterator[asyncio.Lock]:
    """The session's lock, kept alive (not dropped by ``forget``) while in use."""
    lock = _locks.get(session_id)
    if lock is None:
        lock = _locks[session_id] = asyncio.Lock()
    _users[session_id] = _users.get(session_id, 0) + 1
    try:
        yield lock
    finally:
        _users[session_id] -= 1
        if not _users[session_id]:
            del _users[session_id]
            if session_id in _forgotten:
                _forgotten.discard(session_id)
                _locks.pop(session_id, None)


@asynccontextmanager
async def stop_writes(session_id: str) -> AsyncIterator[None]:
    """Held by a Stop from its first write to its last."""
    async with _use(session_id) as lock, lock:
        yield


@asynccontextmanager
async def user_row(session_id: str, wait_s: float = USER_ROW_WAIT_S) -> AsyncIterator[None]:
    """Held while a user row's ``created_at`` is chosen and the row is written."""
    async with _use(session_id) as lock:
        try:
            await asyncio.wait_for(lock.acquire(), wait_s)
        except asyncio.TimeoutError:
            logger.warning("transcript_order: Stop writes of %s still running after %.0fs; "
                           "writing the user row now", session_id, wait_s)
            yield
            return
        try:
            yield
        finally:
            lock.release()


def forget(session_id: str) -> None:
    """Drop the session's lock (LifecycleManager._release_session_state). A lock a
    Stop or a user row still holds or waits for is dropped when the last one leaves:
    a forced release during a Stop must not give the next user row a fresh lock."""
    if _users.get(session_id):
        _forgotten.add(session_id)
    else:
        _locks.pop(session_id, None)
