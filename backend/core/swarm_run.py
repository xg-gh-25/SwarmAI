"""The Host side of a Swarm Run (design docs/design/2026-10-31-swarm-run.md; how it is
built: docs/design/2026-11-03-swarm-run-impl.md).

A Swarm Run is a pipeline run (the *lead run*) that hands parts of its work to Hives as
*child* runs. A child is an ordinary Swarm Out dispatch whose record also names the lead
run (``parent_run``), its project and the work package it carries (``wp``). Everything
else about it (the task spec, admission, the Hive, the result, the decision record) is
the standard one. What this module decides is who answers for a child and whether a
child may be dispatched:

- ``lead(run_id)``: the lead run's project, current owner session and status, read from
  its audit log (re-read only when the log changed). None when it is not a readable v1 run.
- ``owner_of(row)``: the session that answers for a dispatched run. A child: the lead
  run's current owner (the run may have been taken over since the child went out;
  responsibility follows the run, design §5.6). Any other run: the session that
  dispatched it (``dispatched_from``). Every check of "is this run yours" reads this.
- ``check_child_dispatch(parent_run, wp, session, rows)``: refuses a child dispatch that
  is not the lead run's owner's, for a run that has ended, or that would send the same
  work package a fourth time (design §6 point 4: at most ``MAX_WP_DISPATCHES``). A
  dispatch that passes holds a place until ``release(parent_run, wp)``: its row is
  written only when the dispatch ends (minutes later), so two at once cannot both count
  the same free place.
- ``is_child(row)``: whether a dispatched run belongs to a Swarm Run.

Read-only and deterministic; nothing here asks a model.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

#: dispatches of one work package, corrections and replacements included (design §6.4)
MAX_WP_DISPATCHES = 3
RUN_ID_RE = re.compile(r"run_[0-9a-f]{8,32}")
WP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
TERMINAL = frozenset({"completed", "abandoned", "failed"})


class ChildRefused(ValueError):
    """A child dispatch the Host does not send; the message says why."""


@dataclass(frozen=True)
class Lead:
    run_id: str
    project: str
    owner: str
    status: str
    stage: str

    @property
    def ended(self) -> bool:
        return self.status in TERMINAL


_CACHE: dict[str, tuple[tuple, Lead]] = {}
_LOCK = threading.Lock()
#: (parent_run, wp) -> dispatches of it in flight (counted with the log's rows)
_INFLIGHT: dict[tuple[str, str], int] = {}


def _stamp(run_id: str) -> Optional[tuple]:
    from config import get_app_data_dir
    from core.run_protocol.store import AuditPaths
    from pathlib import Path
    paths = AuditPaths(Path(get_app_data_dir()) / "state" / "pipeline_agent_audit", run_id)
    try:
        st = paths.events.stat()
        ct = paths.contract.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, ct.st_mtime_ns)


def lead(run_id: Optional[str]) -> Optional[Lead]:
    """The lead run *run_id* as its audit log says now; None when there is no such v1 run
    (or its log cannot be read: then nobody answers for its children)."""
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        return None
    stamp = _stamp(run_id)
    if stamp is None:
        return None
    with _LOCK:
        hit = _CACHE.get(run_id)
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        from core.pipeline_human import load_engine
        engine = load_engine(run_id)
        if engine is None:
            return None
        state = engine.projection()
        found = Lead(run_id=run_id, project=engine.project, owner=engine.owner_session(),
                     status=str(state.get("status") or ""), stage=str(state.get("stage") or ""))
    except Exception as exc:                      # a damaged or foreign log answers for nothing
        logger.warning("swarm_run: lead run %s unreadable (%s)", run_id, type(exc).__name__)
        return None
    with _LOCK:
        _CACHE[run_id] = (stamp, found)
    return found


def is_child(row: Any) -> bool:
    return isinstance(row, dict) and bool(row.get("parent_run"))


def owner_of(row: Any) -> Optional[str]:
    """The session that answers for this dispatched run (see the module doc)."""
    if not isinstance(row, dict):
        return None
    if row.get("parent_run"):
        found = lead(row.get("parent_run"))
        return found.owner if found else None
    owner = row.get("dispatched_from")
    return owner if isinstance(owner, str) and owner else None


def check_child_dispatch(parent_run: str, wp: str, session: Optional[str],
                         rows: Iterable[dict]) -> Lead:
    """The lead run, when *session* may dispatch work package *wp* for it now; else
    ChildRefused naming the rule. *rows*: the dispatch log (every row so far)."""
    if not isinstance(wp, str) or not WP_RE.fullmatch(wp):
        raise ChildRefused("a child needs a work package id: letters, digits, - or _, at most 32")
    found = lead(parent_run)
    if found is None:
        raise ChildRefused(f"{str(parent_run)[:40]!r} is not a pipeline run on this machine")
    if not session or session != found.owner:
        raise ChildRefused(f"only the session that drives {found.run_id} dispatches its children")
    if found.ended:
        raise ChildRefused(f"{found.run_id} has ended ({found.status}); it dispatches nothing more")
    logged = sum(1 for r in rows if isinstance(r, dict) and r.get("parent_run") == parent_run
                 and r.get("wp") == wp)
    with _LOCK:
        sent = logged + _INFLIGHT.get((parent_run, wp), 0)
        if sent >= MAX_WP_DISPATCHES:
            raise ChildRefused(f"{wp} was dispatched {sent} times already (at most {MAX_WP_DISPATCHES}): "
                               "do it in the run yourself, or leave it out and say so in the delivery")
        _INFLIGHT[(parent_run, wp)] = _INFLIGHT.get((parent_run, wp), 0) + 1
    return found


def release(parent_run: str, wp: str) -> None:
    """The dispatch that ``check_child_dispatch`` let through has ended (its row, if one
    was written, is in the log now)."""
    with _LOCK:
        n = _INFLIGHT.get((parent_run, wp), 0) - 1
        if n > 0:
            _INFLIGHT[(parent_run, wp)] = n
        else:
            _INFLIGHT.pop((parent_run, wp), None)


def children(rows: Iterable[dict], run_id: str) -> list[dict]:
    """The dispatched children of *run_id*, oldest first."""
    return [r for r in rows if isinstance(r, dict) and r.get("parent_run") == run_id]
