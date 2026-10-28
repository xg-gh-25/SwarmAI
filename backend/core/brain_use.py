"""Which turns on this Hive are using which Brain, and which Brain is being installed
(design docs/design/2026-10-28-project-agent-flow.md §6).

One reader/writer rule, kept in this process's event loop (every call below runs on
the loop thread; none awaits between its check and its change, so each is atomic):

- **A turn** of a session under a Brain's Project Agent ``hold``\\ s the Brain from
  before its first event to its end (``SessionRouter._published_turn``). A turn that
  starts while the Brain is being installed waits for the install to finish, so it
  never reads a half-written Brain.
- **An install** (``routers/hive_brains.put_brain``) ``begin_install``\\ s: refused when
  a turn holds the Brain or another install of it is running; then no new turn can
  take the Brain until ``end_install``.

The registry counts turns, not sessions. A turn holds its Brain from its first event,
so a turn queued for a CLI slot holds it too (an install then gets 423 until that turn
ends; the host syncs again later). A Brain whose turns overlap without a gap refuses
every install meanwhile: the console shows it as busy. Between two turns of a session the Brain can
be updated. The session's next turn then works on the new files under its old pin, and
the Brain gate hook refuses its gated tools ("the Brain's gates were changed") when the
gates differ — the task is dispatched again rather than run under rules it was not
dispatched with.

Only a Hive installs Brains, so only a Hive registers turns (``brain_of_turn`` is None
elsewhere).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# a turn whose Brain cannot be told (its session record cannot be read) holds every
# Brain: no install lands under it
ANY = "*"

_users: dict[str, set[str]] = {}
_installing: dict[str, asyncio.Event] = {}


def in_use(brain: str) -> int:
    """How many turns hold *brain* now."""
    return len(_users.get(brain, ()))


def begin_install(brain: str) -> bool:
    """Mark *brain* as being installed; False when a turn holds it or it already is."""
    if _users.get(brain) or _users.get(ANY) or brain in _installing:
        return False
    _installing[brain] = asyncio.Event()
    return True


def end_install(brain: str) -> None:
    ev = _installing.pop(brain, None)
    if ev is not None:
        ev.set()


async def hold(brain: str, key: str) -> None:
    """Take *brain* for the turn *key*, after any install of it (for ``ANY``: of any
    Brain) has finished."""
    while True:
        ev = next(iter(_installing.values()), None) if brain == ANY else _installing.get(brain)
        if ev is None:
            break
        await ev.wait()
    _users.setdefault(brain, set()).add(key)


def release(brain: str, key: str) -> None:
    keys = _users.get(brain)
    if keys is not None:
        keys.discard(key)
        if not keys:
            _users.pop(brain, None)


async def brain_of_turn(session_id: Optional[str], pin: Optional[dict]) -> Optional[str]:
    """The Brain a turn works under, on a Hive: the pin a new session starts with, else
    the one recorded on the session. None off a Hive and for a session under no Project
    Agent; ``ANY`` when the record cannot be read (it may be a Project Agent session:
    the turn holds every Brain rather than none)."""
    if os.environ.get("SWARMAI_MODE") != "hive":
        return None
    if isinstance(pin, dict) and isinstance(pin.get("brain"), str):
        return pin["brain"]
    if not session_id:
        return None
    try:
        from database import db
        row = await db.sessions.get(session_id)
    except Exception as exc:  # noqa: BLE001 — see the docstring
        logger.warning("brain use: session %s record unreadable: %s", session_id, exc)
        return ANY
    meta = (row or {}).get("metadata")
    from core.project_agent_session import PIN_KEY
    stored = meta.get(PIN_KEY) if isinstance(meta, dict) else None
    brain = stored.get("brain") if isinstance(stored, dict) else None
    return brain if isinstance(brain, str) and brain else None
