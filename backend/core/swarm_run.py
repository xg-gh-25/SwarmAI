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
- ``freeze(workspace, run_id, brain)``: what every child of one Swarm Run shares (design
  §5.1): the Brain package of the first child's dispatch (its bytes and digest), the
  Project Agent revision and the host's model, kept under ``state/swarm-run/<run>/``.
  Later children push those bytes and run on that model, whatever the host has learned
  since. ``held_hives(rows, brain, digest, run_id)``: the Hives where another running
  Swarm Run holds *brain* at a different digest (a dispatch does not replace it there).

Read-only and deterministic; nothing here asks a model.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

#: dispatches of one work package, corrections and replacements included (design §6.4)
MAX_WP_DISPATCHES = 3
#: dispatches of one Swarm Run in all: its plan's packages (child_cli.MAX_CHILDREN) and
#: their corrections; a work package renamed in the plan does not reset its count here
MAX_RUN_DISPATCHES = 40
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
#: guards _CACHE and _FREEZE_LOCKS only: held for dictionary reads and writes, never
#: while a file is read or a Brain packaged
_LOCK = threading.Lock()
#: held for a whole count, the dispatch log's read included (so two dispatches of one
#: package cannot both count the log before either row is written)
_DISPATCH_LOCK = threading.Lock()
#: guards _INFLIGHT only, never held over a read: ``release`` (called on the event loop)
#: takes nothing else
_FLIGHT_LOCK = threading.Lock()
#: (parent_run, wp) -> dispatches of it in flight (counted with the log's rows)
_INFLIGHT: dict[tuple[str, str], int] = {}
#: run_id -> the lock its freeze is made under (one Brain package per run)
_FREEZE_LOCKS: dict[str, threading.Lock] = {}


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
                         rows: "Iterable[dict] | Callable[[], Iterable[dict]]", *,
                         again: bool = False) -> Lead:
    """The lead run, when *session* may dispatch work package *wp* for it now; else
    ChildRefused naming the rule. *rows*: the dispatch log (every row so far), or the
    function that reads it (then read under the count's lock). *again*: a new attempt at
    a package that went out; without it a package that went out, or is going out now,
    is refused (a correction is said to be one). Counted: *wp*'s dispatches (at most
    MAX_WP_DISPATCHES) and the run's (at most MAX_RUN_DISPATCHES), in flight included;
    each one let through is ``release``d when it ends."""
    if not isinstance(wp, str) or not WP_RE.fullmatch(wp):
        raise ChildRefused("a child needs a work package id: letters, digits, - or _, at most 32")
    found = lead(parent_run)
    if found is None:
        raise ChildRefused(f"{str(parent_run)[:40]!r} is not a pipeline run on this machine")
    if not session or session != found.owner:
        raise ChildRefused(f"only the session that drives {found.run_id} dispatches its children")
    if found.ended:
        raise ChildRefused(f"{found.run_id} has ended ({found.status}); it dispatches nothing more")
    with _DISPATCH_LOCK:
        # in flight first, then the log: a dispatch that ends between the two reads is
        # counted twice (refused at worst), never not at all
        with _FLIGHT_LOCK:
            inflight = dict(_INFLIGHT)
        log = [r for r in (rows() if callable(rows) else rows)
               if isinstance(r, dict) and r.get("parent_run") == parent_run]
        mine = [r for r in log if r.get("wp") == wp]
        logged = len(mine)
        flying = inflight.get((parent_run, wp), 0)
        sent = logged + flying
        if not again and sent:
            raise ChildRefused(f"{wp} went out already: a new attempt at it is a correction "
                               "(`child rerun`, or `child dispatch --again`)")
        if again and flying:
            raise ChildRefused(f"{wp} is being dispatched now: wait for that dispatch to end")
        if again and not logged:
            raise ChildRefused(f"{wp} never went out: dispatch it, not again")
        if again and any(_open(r) for r in mine):
            raise ChildRefused(f"{wp} still has an open child: discard or take it first")
        if sent >= MAX_WP_DISPATCHES:
            raise ChildRefused(f"{wp} was dispatched {sent} times already (at most {MAX_WP_DISPATCHES}): "
                               "do it in the run yourself, or leave it out and say so in the delivery")
        total = len(log) + sum(n for (run, _), n in inflight.items() if run == parent_run)
        if total >= MAX_RUN_DISPATCHES:
            raise ChildRefused(f"{parent_run} has dispatched {total} children (at most {MAX_RUN_DISPATCHES}): "
                               "do the rest in the run yourself, or leave it out and say so in the delivery")
        with _FLIGHT_LOCK:
            _INFLIGHT[(parent_run, wp)] = _INFLIGHT.get((parent_run, wp), 0) + 1
    return found


def _open(row: dict) -> bool:
    """Nothing has closed this dispatch yet (settled, handled by its run, discarded)."""
    from core import swarm_out_settle
    try:
        rec = swarm_out_settle.load(str(row.get("dispatch_id") or ""))
    except Exception as exc:  # noqa: BLE001 — a record that cannot be read is not known closed
        logger.warning("swarm_run: settle record of %s unreadable (%s)", row.get("dispatch_id"),
                       type(exc).__name__)
        return True
    return not (rec.get("settled") or rec.get("handled_at") or rec.get("abandoned_at"))


def release(parent_run: str, wp: str) -> None:
    """The dispatch that ``check_child_dispatch`` let through has ended (its row, if one
    was written, is in the log now)."""
    with _FLIGHT_LOCK:
        n = _INFLIGHT.get((parent_run, wp), 0) - 1
        if n > 0:
            _INFLIGHT[(parent_run, wp)] = n
        else:
            _INFLIGHT.pop((parent_run, wp), None)


def _freeze_dir(run_id: str):
    from pathlib import Path
    from core import swarm_out
    if not RUN_ID_RE.fullmatch(run_id or ""):
        raise ChildRefused("not a run id")
    return Path(swarm_out._state_dir()).parent / "swarm-run" / run_id


def frozen(run_id: str) -> Optional[dict]:
    """The freeze of *run_id* (``{brain, digest, files, revision, model, at}``), or None."""
    import json
    try:
        data = json.loads((_freeze_dir(run_id) / "freeze.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, ChildRefused):
        return None
    return data if isinstance(data, dict) else None


def frozen_package(run_id: str) -> Optional[tuple[bytes, str, int]]:
    f = frozen(run_id)
    if not f:
        return None
    try:
        data = (_freeze_dir(run_id) / "brain.pkg").read_bytes()
    except OSError:
        return None
    return data, f["digest"], int(f.get("files") or 0)


def freeze(workspace, run_id: str, brain: str, revision: str) -> dict:
    """The run's freeze, made now if it has none (see the module doc). Raises
    ChildRefused when the Brain cannot be packaged or is not the run's."""
    import json
    import os
    import time
    _freeze_dir(run_id)                       # a bad run id is refused before a lock is made
    with _LOCK:
        run_lock = _FREEZE_LOCKS.setdefault(run_id, threading.Lock())
    # the run's own lock: two first children of one run make one package; lead() and
    # other runs' freezes do not wait for the packaging
    with run_lock:
        have = frozen(run_id)
        if have:
            if have.get("brain") != brain:
                raise ChildRefused(f"{run_id}'s children go under {have.get('brain')}")
            return have
        from core import brain_sync_host, swarm_out_model
        try:
            data, digest, n = brain_sync_host._package(workspace, brain, brain in brain_sync_host.DEFAULT_BRAINS)
        except Exception as exc:  # noqa: BLE001 — named to the lead run
            raise ChildRefused(f"{brain} cannot be packaged for the Swarm Run: {exc}") from None
        rec = {"brain": brain, "digest": digest, "files": n, "revision": revision,
               "model": swarm_out_model.host_model() or "", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        d = _freeze_dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "brain.pkg.tmp").write_bytes(data)
        os.replace(d / "brain.pkg.tmp", d / "brain.pkg")
        (d / "freeze.json.tmp").write_text(json.dumps(rec), encoding="utf-8")
        os.replace(d / "freeze.json.tmp", d / "freeze.json")
        return rec


def drop_freeze(run_id: str) -> None:
    """Remove an ended run's freeze (its children no longer need the package)."""
    import shutil
    try:
        shutil.rmtree(_freeze_dir(run_id), ignore_errors=True)
    except ChildRefused:
        pass
    # the run's freeze lock stays: a freeze still being made holds it, and a new one
    # would let a second packaging run beside it (one lock per run costs nothing)


def held_hives(rows: Iterable[dict], brain: str, digest: Optional[str], run_id: Optional[str]) -> set[str]:
    """Hive ids where another Swarm Run that has not ended holds *brain* frozen at a digest
    other than *digest* (design §5.1, I15): a dispatch does not put another version of
    the Brain there while that run's children use it."""
    out: set[str] = set()
    for r in rows:
        if not is_child(r) or r.get("project") != brain or r.get("parent_run") == run_id:
            continue
        f = frozen(str(r.get("parent_run")))
        found = lead(r.get("parent_run"))
        if not f or found is None or found.ended or f.get("digest") == digest:
            continue
        if r.get("hive_id"):
            out.add(str(r["hive_id"]))
    return out


def child_report(run_id: str, recorded: Iterable[dict]) -> list[dict]:
    """For a lead run's REPORT and REFLECT (design §5.8, §7 point 9, §16): each child of
    the run in Swarm Out's dispatch log (the truth of what went out: a dispatch whose
    ``child_dispatched`` event was never written is listed too), with what the run's log
    adds (``again_of``), how it ended in Swarm Out and what the lead run did with it (its
    decision record's ``by: agent`` entries, design 2026-11-02-hive-run-decision-record
    §3). *recorded*: the ``child_dispatched`` payloads. Read from files; never asks a Hive."""
    from core import swarm_out, swarm_out_settle
    events = {str(c.get("dispatch_id")): c for c in recorded if isinstance(c, dict) and c.get("dispatch_id")}
    try:
        logged = children(swarm_out._read_log(), run_id)
    except (OSError, ValueError):
        logged = []                    # the run's own log is all there is to say then
    items, seen = [], set()
    for r in logged:
        did = str(r.get("dispatch_id") or "")
        if not did or did in seen:
            continue
        seen.add(did)
        e = events.get(did) or {}
        items.append({"dispatch_id": did, "wp": r.get("wp") or e.get("wp"),
                      "hive": r.get("hive") or e.get("hive"), "again_of": e.get("again_of")})
    items += [e for did, e in events.items() if did not in seen]
    out = []
    for c in items:
        did = str(c.get("dispatch_id") or "")
        if not did:
            continue
        try:
            rec = swarm_out_settle.load(did)
        except Exception as exc:  # noqa: BLE001 — a child whose record is gone is listed as such
            logger.warning("swarm_run: settle record of %s unreadable for REPORT (%s)", did, type(exc).__name__)
            rec = {}
        settled = rec.get("settled") if isinstance(rec.get("settled"), dict) else {}
        outcome = settled.get("outcome") or ("handled" if rec.get("handled_at") else
                                             "discarded" if rec.get("abandoned_at") else "open")
        lead_did = [{"decision": e.get("decision"), "why": e.get("why")}
                    for e in rec.get("commands") or []
                    if isinstance(e, dict) and e.get("by") == "agent" and e.get("decision") != "dispatch"]
        out.append({"dispatch_id": did, "wp": c.get("wp"), "hive": c.get("hive"),
                    "again_of": c.get("again_of"), "outcome": outcome, "lead_run": lead_did})
    return out


def open_children(run_id: str) -> list[dict]:
    """The children of *run_id* nothing has closed yet (read from the dispatch log and the
    settle records; for ``next``)."""
    from core import swarm_out, swarm_out_settle
    out = []
    for r in children(swarm_out._read_log(), run_id):
        rec = swarm_out_settle.load(str(r.get("dispatch_id")))
        if not (rec.get("handled_at") or rec.get("abandoned_at") or rec.get("settled")):
            out.append(r)
    return out


def view(run_id: str) -> Optional[dict]:
    """What the Swarm Run card shows above its children (design §5.9): the lead run's
    title, project, profile and status, its plan's packages not dispatched yet, and the
    children's spend (each child's own, by dispatch id: never added twice, I10). The
    children's rows themselves are Swarm Out's ``/runs`` (filtered by ``parent_run``).
    None when *run_id* is not a readable v1 run."""
    import json
    found = lead(run_id)
    if found is None:
        return None
    from core import swarm_out, swarm_out_results
    from core.pipeline_human import load_engine
    engine = load_engine(run_id)
    if engine is None:
        return None                   # gone since its log was read
    try:
        title = engine._run_title()
    except Exception as exc:  # noqa: BLE001 — the card names the run by its id then
        logger.debug("swarm_run: %s has no readable title (%s)", run_id, type(exc).__name__)
        title = None
    try:
        plan = json.loads((engine.run_dir / "children.json").read_text(encoding="utf-8")).get("children") or []
    except (OSError, ValueError, AttributeError):
        plan = []
    kids = children(swarm_out._read_log(), run_id)
    sent = {r.get("wp") for r in kids}
    spend = swarm_out_results.spend_of([str(r.get("dispatch_id")) for r in kids])
    cost = sum(float(v.get("cost_usd") or 0) for v in spend.values())
    return {"run_id": run_id, "project": found.project, "title": title or run_id,
            "profile": engine.contract.get("profile"), "status": found.status, "stage": found.stage,
            "children": len(kids), "planned": len(plan),
            "not_dispatched": [{"wp": c.get("wp"), "title": c.get("title"), "after": c.get("after") or []}
                               for c in plan if isinstance(c, dict) and c.get("wp") not in sent],
            "children_cost_usd": round(cost, 4) if spend else None}


def children(rows: Iterable[dict], run_id: str) -> list[dict]:
    """The dispatched children of *run_id*, oldest first."""
    return [r for r in rows if isinstance(r, dict) and r.get("parent_run") == run_id]
