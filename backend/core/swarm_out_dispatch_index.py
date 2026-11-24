"""This daemon's dispatch-log rows by the chat session that sent them, and which of them
already have a card in that session.

A dispatch card is drawn from the dispatch log row the daemon wrote (its ``dispatch_id``
and ``dispatched_from``), not from the text of the Bash command that sent it: a wrapper
script, a loop or an alias sends a task just as ``scripts.swarm_out dispatch`` does. The
streaming orchestrator asks this index after every Bash result which rows its session
sent while that call ran (core/streaming_orchestrator ``_bash_card_events``).

The daemon is the dispatch log's only writer (core/swarm_out ``_append``; the CLI and a
Swarm Run's ``pipeline-v1 child dispatch`` go through its HTTP route), so every row a
live chat session's Bash call can have sent is recorded here as it is written. A Bash
call never outlives the daemon that streams it, so a row from before a restart is never
inside a live call's window. A row is written once and never changed, so the few fields
kept here (id, sent time, title, lead run) cannot go stale; what a card shows is read
from the log row itself when it is drawn (core/swarm_out_card ``cards_of_claimed``).

Claimed keys, per session: a dispatch id (``so_…``) or a Swarm Run's id (``run_…``) that
has a card in the session, or is represented by one (a "not sure" card). Whoever draws a
card claims its key first, the literal dispatch path included, so one dispatch never
gets two cards, also when two parallel calls' windows both hold its row.

- ``record(row)`` — index a row just appended (one without ``dispatched_from`` is not)
- ``has(session_id)`` — whether the session sent anything since the daemon started: one
  dict lookup, no lock, no I/O (the streaming path's skip)
- ``claim(session_id, since, until, title=None)`` — the rows of the session sent within
  ``[since - SKEW_S, until]`` that nothing claimed yet, claimed now, after whatever an
  earlier draw left undrawn: ``(dispatch_ids, runs)`` in sent order, ``runs`` as
  ``(run_id, since)``. A Swarm Run child's row claims its own id and yields its run once
  (the run's card, never a card per child), with the ``since`` its first-children check
  takes
- ``mark(session_id, key)`` — claim one key the literal path drew; False when claimed
- ``undrawn(session_id, dispatch_ids, runs)`` — what a claim returned but could not be
  drawn (the log unreadable): it stays claimed, and the session's next ``claim`` returns
  it first, whatever that call's window
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, NamedTuple, Optional

#: the dispatch log's clock (``sent_at`` is cut to the second) vs the moment a call was
#: seen, as core/swarm_out_card takes it
SKEW_S = 5.0
#: rows are appended in the order they were made, give or take a thread hop: a scan from
#: the newest row back stops this far before the window
_REORDER_S = 60.0


class _Row(NamedTuple):
    dispatch_id: str
    sent_at: float
    title: str
    parent_run: Optional[str]


_LOCK = threading.Lock()
_BY_SESSION: dict[str, list[_Row]] = {}
_CLAIMED: dict[str, set[str]] = {}
#: per session: (dispatch ids, runs) claimed whose card could not be drawn yet
_UNDRAWN: dict[str, tuple[list[str], list[tuple[str, float]]]] = {}


def _epoch(value: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def record(row: dict) -> None:
    """Index *row*, a dispatch log row just written. A row no chat session sent
    (``dispatched_from`` None: the person's own dispatch from the panel) or without a
    readable ``sent_at`` is not indexed: no session's Bash call sent it."""
    sid, did, at = row.get("dispatched_from"), row.get("dispatch_id"), _epoch(row.get("sent_at"))
    if not isinstance(sid, str) or not sid or not isinstance(did, str) or at is None:
        return
    parent = row.get("parent_run")
    entry = _Row(did, at, str(row.get("title") or ""), parent if isinstance(parent, str) and parent else None)
    with _LOCK:
        _BY_SESSION.setdefault(sid, []).append(entry)


def has(session_id: Optional[str]) -> bool:
    """Whether *session_id* sent any dispatch this daemon recorded."""
    return bool(session_id) and session_id in _BY_SESSION


def claim(session_id: Optional[str], since: float, until: float,
          title: Optional[str] = None) -> tuple[list[str], list[tuple[str, float]]]:
    """Claim the session's unclaimed rows sent within ``[since - SKEW_S, until]`` (with
    *title*: only rows of that title) and return ``(dispatch_ids, runs)``, oldest first,
    after what an earlier draw left undrawn (``undrawn``; not with *title*): the dispatch
    ids of rows that are not a Swarm Run's child, and ``(run_id, since)`` for children
    whose run nothing claimed yet."""
    if not session_id:
        return [], []
    lo, stop = since - SKEW_S, since - SKEW_S - _REORDER_S
    with _LOCK:
        rows = _BY_SESSION.get(session_id)
        if not rows:
            return [], []
        ids: list[str] = []
        runs: list[tuple[str, float]] = []
        if title is None and session_id in _UNDRAWN:
            ids, runs = _UNDRAWN.pop(session_id)
        claimed = _CLAIMED.setdefault(session_id, set())
        found: list[_Row] = []
        for r in reversed(rows):
            if r.sent_at < stop:
                break
            if (lo <= r.sent_at <= until and r.dispatch_id not in claimed
                    and (title is None or r.title == title)):
                found.append(r)
        found.reverse()                       # append order, kept by the stable sort
        for r in sorted(found, key=lambda r: r.sent_at):
            claimed.add(r.dispatch_id)
            if r.parent_run is None:
                ids.append(r.dispatch_id)
            elif r.parent_run not in claimed:
                claimed.add(r.parent_run)
                runs.append((r.parent_run, since))
        return ids, runs


def mark(session_id: Optional[str], key: Optional[str]) -> bool:
    """Claim *key* (a dispatch id or a run id) for the session: True when it was not
    claimed yet (the card may be drawn), False when a card for it exists."""
    if not session_id or not key:
        return True
    with _LOCK:
        claimed = _CLAIMED.setdefault(session_id, set())
        if key in claimed:
            return False
        claimed.add(key)
        return True


def undrawn(session_id: Optional[str], dispatch_ids: list[str], runs: list[tuple[str, float]]) -> None:
    """Keep what a ``claim`` returned but could not be drawn: still claimed (no other
    call draws it), returned first by the session's next ``claim``."""
    if not session_id or not (dispatch_ids or runs):
        return
    with _LOCK:
        ids, rs = _UNDRAWN.get(session_id, ([], []))
        _UNDRAWN[session_id] = (list(dispatch_ids) + ids, list(runs) + rs)


def _reset() -> None:
    """Forget everything (tests)."""
    with _LOCK:
        _BY_SESSION.clear()
        _CLAIMED.clear()
        _UNDRAWN.clear()
