"""Which turns on this Hive use which version of which Brain, and which Brain is being
installed (design docs/design/2026-10-28-project-agent-flow.md §6, "安装和 turn 互斥").

A turn reads the version of its Brain it started with until it ends; a new turn reads
the newest; a version no turn holds any more is removed. Kept in this process's event
loop (every call below runs on the loop thread; none awaits between its check and its
change, so each is atomic):

- **A turn** of a session under a Brain's Project Agent ``hold``\\ s the Brain's live
  version (``Projects/<brain>``, always the newest) from before its first event to its
  end (``SessionRouter._published_turn``). A turn that starts while the Brain is being
  installed waits for the install to finish, so it never reads a half-written Brain.
- **An install** (``routers/hive_brains.put_brain``) ``begin_install``\\ s: refused only
  while another install of the same Brain runs (two installs at once). When turns hold
  the live version (``needs_keep``), the install first has a copy of it kept
  (``core.brain_versions.keep``), ``retire``\\ s those turns onto the copy and has their
  sessions marked (``sync_disk``) before any file of the new version is written; then
  it writes the new version in place. No new turn takes the Brain until ``end_install``.
- **The end of a turn** (``release``) that held a kept version has the disk follow: its
  session's marker goes, and the version goes with its last turn. A turn waits for that
  cleanup of its Brain before it starts, so a session's next turn never reads the
  marker of its previous one. A cleanup that failed is made again before the next turn
  of that Brain starts; if it fails again and the session would still read an older
  version, that turn does not start (``BrainUseError``).

Which directory a turn's readers use is ``core.brain_versions.view``: the version its
session's marker names, else the stable path. The readers that resolve the Brain by
name follow it (``addressing.brain_root`` -- knowledge find / get / map and run
citations --, the DDD recall's ``recall_multi._load_ddd_docs_text``, the Brain gates,
the contract a run is created with). The skills projected into the workspace-wide
``.claude/skills`` and a plain file read of a ``Projects/<brain>/`` path follow the
newest version: the CLI has one skill set for every session, and the path names the
stable directory.

The registry counts turns, not sessions. A session's turn is bound to its session id
when the turn starts (``hold``) or, for a new session, when its id arrives
(``bind_session``); a turn without a known session is kept on its version here, but no
other process can be pointed at it. A turn whose Brain cannot be told (its session
record cannot be read, ``ANY``) holds the live version of every Brain: at an install of
any Brain it is kept on that Brain's version like that Brain's own turns. Between two
turns of a session the Brain can be updated: the next turn reads the new files under
its old pin, and the Brain gate hook refuses its gated tools ("the Brain's gates were
changed") when the gates differ -- the task is dispatched again rather than run under
rules it was not dispatched with.

Only a Hive installs Brains, so only a Hive registers turns (``brain_of_turn`` is None
elsewhere).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# a turn whose Brain cannot be told (its session record cannot be read) holds every
# Brain: an install keeps it on the version it started with
ANY = "*"


@dataclass
class _Kept:
    """A superseded version some turns still hold (``Hive/brains/<brain>/versions/<id>``)."""
    digest: Optional[str]
    keys: set[str] = field(default_factory=set)


_live: dict[str, set[str]] = {}            # Brain -> turns holding its live version
_any: set[str] = set()                     # turns holding every Brain's live version
_kept: dict[str, dict[str, _Kept]] = {}    # Brain -> version id -> kept version (oldest first)
_session: dict[str, str] = {}              # turn -> its session id
_order: dict[str, int] = {}                # turn -> when it took its Brain
_count = itertools.count()
_reserved: dict[str, str] = {}             # Brain -> id of the copy an install is making
_pending: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Future]] = {}
_installing: dict[str, asyncio.Event] = {}
_workspace: Optional[Path] = None          # where the kept versions are (set by retire)
_failed: set[str] = set()                  # Brains whose last disk update failed


def _kept_id(brain: str, key: str) -> Optional[str]:
    for vid, k in _kept.get(brain, {}).items():
        if key in k.keys:
            return vid
    return None


def _live_holders(brain: str) -> set[str]:
    """The turns reading *brain*'s live version: its own, and the ``ANY`` turns not
    already kept on one of its versions."""
    return set(_live.get(brain, ())) | {k for k in _any if _kept_id(brain, k) is None}


def in_use(brain: str) -> int:
    """How many turns hold a version of *brain* now."""
    return len(_live.get(brain, ())) + sum(len(k.keys) for k in _kept.get(brain, {}).values())


def versions(brain: str) -> list[dict]:
    """The kept versions of *brain*, oldest first: ``{digest, running}``."""
    return [{"digest": k.digest, "running": len(k.keys)} for k in _kept.get(brain, {}).values()]


def live_count(brain: str) -> int:
    """How many of *brain*'s own turns read its live version."""
    return len(_live.get(brain, ()))


def begin_install(brain: str) -> bool:
    """Mark *brain* as being installed; False when another install of it runs."""
    if brain in _installing:
        return False
    _installing[brain] = asyncio.Event()
    return True


def end_install(brain: str) -> None:
    ev = _installing.pop(brain, None)
    if ev is not None:
        ev.set()


def needs_keep(brain: str) -> bool:
    """Whether an install of *brain* would change files turns are reading."""
    return bool(_live_holders(brain))


def reserve(brain: str) -> str:
    """The id of the copy an install of *brain* makes now; reconcile leaves it alone."""
    vid = secrets.token_hex(8)
    _reserved[brain] = vid
    return vid


def unreserve(brain: str, vid: str, workspace: Optional[Path] = None) -> None:
    """The install that reserved *vid* ended without moving turns onto it: the copy,
    if it was made, is removed (with *workspace*)."""
    global _workspace
    if _reserved.get(brain) != vid:
        return
    _reserved.pop(brain, None)
    if workspace is not None:
        _workspace = Path(workspace)
        _schedule(brain)


def retire(brain: str, vid: str, digest: Optional[str], workspace: Path) -> int:
    """Move the turns reading *brain*'s live version onto the kept copy *vid*; how many
    were moved (0: the copy is not needed and the next ``sync_disk`` removes it)."""
    global _workspace
    _workspace = Path(workspace)
    unreserve(brain, vid)
    keys = _live_holders(brain)
    if keys:
        _kept.setdefault(brain, {})[vid] = _Kept(digest, keys)
        _live.pop(brain, None)
    return len(keys)


def unretire(brain: str, vid: str) -> None:
    """Undo ``retire`` of *vid* (its session markers could not be written): its turns
    read the live version again, and an install must keep it for them first."""
    kept = _kept.get(brain, {}).pop(vid, None)
    if not _kept.get(brain):
        _kept.pop(brain, None)
    if kept is not None:
        for key in kept.keys:
            if key not in _any:
                _live.setdefault(brain, set()).add(key)
    _schedule(brain)


class BrainUseError(RuntimeError):
    """A turn cannot start: the version it would read cannot be made the newest."""


async def _still_marked(brain: str, session_id: Optional[str]) -> bool:
    """Whether the disk still points *session_id*'s reads of *brain* at a kept version
    (only asked after a failed update; read on the brain_versions executor)."""
    if not session_id or _workspace is None:
        return False
    from core import brain_versions
    nowhere = Path("")
    return await run_disk(brain_versions.view, _workspace, brain, nowhere, session_id) != nowhere


def _turn_brains(key: str) -> set[str]:
    """The Brains whose kept versions *key* holds."""
    return {b for b, vs in _kept.items() if any(key in k.keys for k in vs.values())}


def _session_brains(sid: str) -> set[str]:
    """The Brains with a kept version a turn of session *sid* holds."""
    return {b for b, vs in _kept.items()
            if any(_session.get(key) == sid for k in vs.values() for key in k.keys)}


def _disk_plan(brain: str) -> tuple[dict[str, str], set[str]]:
    """({session: version id}, {version ids to keep}) for *brain*: the marker of a
    session is the version its newest turn holds, none when that turn reads the live
    version."""
    ids = set(_kept.get(brain, {}))
    if brain in _reserved:
        ids.add(_reserved[brain])
    holders = _live.get(brain, set()) | _any | {key for k in _kept.get(brain, {}).values() for key in k.keys}
    newest: dict[str, str] = {}
    for key in holders:
        sid = _session.get(key)
        if sid and (sid not in newest or _order.get(key, -1) > _order.get(newest[sid], -1)):
            newest[sid] = key
    markers = {}
    for sid, key in newest.items():
        vid = _kept_id(brain, key)
        if vid is not None:
            markers[sid] = vid
    return markers, ids


def _schedule(brain: str) -> Optional[asyncio.Future]:
    """Have the disk of *brain*'s kept versions follow the registry, on the
    ``brain_versions`` executor (one thread: copies and cleanups run in order). None
    off a running loop or before this process kept any version."""
    if _workspace is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    from core import brain_versions, executors
    markers, ids = _disk_plan(brain)
    fut = loop.run_in_executor(executors.get_pool("brain_versions"), brain_versions.reconcile,
                               _workspace, brain, markers, ids)
    _pending[brain] = (loop, fut)

    def done(f: asyncio.Future, b: str = brain) -> None:
        if _pending.get(b, (None, None))[1] is f:
            _pending.pop(b, None)
        if not f.cancelled() and f.exception() is not None:
            # made again before the next turn of the Brain starts (hold): a marker left
            # behind would point that turn at an older version
            _failed.add(b)
            logger.warning("brain use: the kept versions of %s could not be updated: %s", b, f.exception())
        elif not f.cancelled() and _pending.get(b) is None:
            _failed.discard(b)
    fut.add_done_callback(done)
    return fut


async def sync_disk(brain: str) -> None:
    """``_schedule`` and wait for it (an install, before it writes the new version)."""
    fut = _schedule(brain)
    if fut is not None:
        await asyncio.shield(fut)
        if fut.exception() is not None:
            raise fut.exception()


async def run_disk(fn, *args):
    """Run a ``core.brain_versions`` call on its executor."""
    from core import executors
    return await executors.run_in("brain_versions", fn, *args)


def _waiting_for(brain: str) -> list[asyncio.Future]:
    """The cleanups of this loop a turn of *brain* waits for (``ANY``: of every Brain)."""
    loop = asyncio.get_running_loop()
    entries = list(_pending.values()) if brain == ANY else [p for p in [_pending.get(brain)] if p]
    return [f for lp, f in entries if lp is loop and not f.done()]


async def hold(brain: str, key: str, session_id: Optional[str] = None) -> None:
    """Take *brain*'s live version for the turn *key* of *session_id*, after any install
    of it (for ``ANY``: of any Brain) and any cleanup of its kept versions has finished.
    Before it returns, the disk names no older version for the session: an earlier turn
    of the session still on a kept version has its marker replaced, and a cleanup that
    failed is made again (``_failed``), both awaited. When that update fails again and
    the session would still read a kept version, it raises ``BrainUseError`` and holds
    nothing; a cancel while it waits holds nothing either."""
    while True:
        ev = next(iter(_installing.values()), None) if brain == ANY else _installing.get(brain)
        if ev is not None:
            await ev.wait()
            continue
        pending = _waiting_for(brain)
        if pending:
            await asyncio.wait(pending)
            continue
        break
    _order[key] = next(_count)
    if session_id:
        _session[key] = session_id
    if brain == ANY:
        _any.add(key)
    else:
        _live.setdefault(brain, set()).add(key)
    redo = set(_failed) if brain == ANY else ({brain} & _failed)
    if session_id:
        redo |= _session_brains(session_id)
    futs = {b: f for b, f in ((b, _schedule(b)) for b in sorted(redo)) if f is not None}
    if futs:
        try:
            await asyncio.wait(list(futs.values()))
            stale = [b for b, f in futs.items()
                     if (f.cancelled() or f.exception() is not None) and await _still_marked(b, session_id)]
        except BaseException:
            release(brain, key)            # cancelled before the turn started: not held
            raise
        if stale:
            # the update failed again and the session would read an older version than
            # the newest: the turn does not start rather than run on it
            release(brain, key)
            raise BrainUseError(f"the versions of {', '.join(stale)} on this Hive cannot be updated "
                                "(a disk error); try again")


def bind_session(key: str, session_id: str) -> None:
    """The session id of the turn *key*, known only once a new session started."""
    if not session_id or _session.get(key) == session_id or key not in _order:
        return
    _session[key] = session_id
    for b in _turn_brains(key) | _session_brains(session_id):
        _schedule(b)


def release(brain: str, key: str) -> None:
    touched = _turn_brains(key)
    sid = _session.get(key)
    if sid:
        touched |= _session_brains(sid)
    for b in list(_kept):
        for vid in list(_kept[b]):
            kept = _kept[b][vid]
            kept.keys.discard(key)
            if not kept.keys:
                del _kept[b][vid]
        if not _kept[b]:
            del _kept[b]
    if brain == ANY:
        _any.discard(key)
    keys = _live.get(brain)
    if keys is not None:
        keys.discard(key)
        if not keys:
            _live.pop(brain, None)
    _session.pop(key, None)
    _order.pop(key, None)
    for b in touched:
        _schedule(b)


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
