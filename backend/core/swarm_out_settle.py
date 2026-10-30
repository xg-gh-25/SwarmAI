"""Accepting and settling a dispatched task (design
docs/design/2026-10-30-dispatch-center.md §4–§6, Host side).

A task that went out under a Project Agent is followed to its end here, from what the
Host already keeps (the dispatch row, the run's result, the code it brought back, its
lessons) plus one small record per dispatch, ``state/swarm-out/settle/<dispatch>.json``,
that holds only what the person decided:

- ``verdict``: ``pass`` / ``fail`` with a note, for a task whose deliverable is an
  answer or a document, bound to the result it judged (``ended_at``): a new turn on the
  Hive voids it. A task that delivers commits has no verdict of its own: landing them
  is the pass, rejecting them the fail, "landed by hand" (``manual_landed_at``) a pass.
- ``abandoned_at``: the person gave the task up (after a fail, or at any time).
- ``settled``: when the conditions held (``derive``) and whether the Hive's workspace
  was released (``released``: True / False while the Hive could not be told, with
  ``tries`` and ``next_try_at`` for the backoff / None: no Hive to tell).

``derive(row, kept)`` gives the run's single next step, in a fixed order (§3 of the
design: answer > running > accept > review code > none), and whether it settles:

- the verdict is decided (pass, abandoned, or the Hive is gone);
- the code is handled (landed in every repository that brought commits back, or all
  rejected, landed by hand, or none came back);
- the files are here: every returned file the release would remove (under
  ``Outputs/<id>/``) is placed in the Brain;
- the lessons are in: the Hive's run record is closed (or the run has none) and every
  lesson it brought is queued for this machine's CDF; or nothing more is pulled (the
  Hive is stopped or gone, or no new entry for the records backstop). No one takes or
  declines lessons (CDF design D13: they go to the CDF as they arrive).

Settling tells the Hive to remove the task's workspace (``Hive/work/<id>/`` and
``Outputs/<id>/``), so code and files must have left it first: commits the Hive could
not seal into a bundle (``unsealed``), a repository not yet landed or rejected, and a
file not placed here hold the task back. Only the person giving it up overrides that
(the card says what it removes). No model decides anything here (P5): the verdict is
the person's, the rest is counted.

- ``annotate(rows)``         — each row's ``next`` and ``settle``; settles what is due.
- ``set_verdict`` / ``set_manual_landed`` / ``abandon`` — the person's actions (routes
  in routers/swarm_out, person-only: core/run_owner_gate refuses them from a command).
- ``release_due`` — tell the Hives (``annotate`` hands each row out once per try, with backoff).

Every write is a read-modify-write of one record under that dispatch's lock
(``_update``): /runs is polled by more than one view at once.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

NEXT = ("answer", "running", "accept", "review_code")
#: a result fetch that failed for good (core/swarm_out_results fetch_error)
NO_RESULT_ERRORS = ("no session", "session_gone", "http_404")
VERDICTS = ("pass", "fail")
MAX_NOTE_CHARS = 2000
#: the person is reminded this many days before the Host's records of a run are swept
#: (hive_records.RETENTION_DAYS after their last change)
REMIND_DAYS = 3
#: a Hive that could not be told is told again after 30 s, doubling, at most an hour
RETRY_FIRST_S = 30
RETRY_MAX_S = 3600


class SettleError(ValueError):
    """The action cannot be taken; the message is safe to show."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _path(dispatch_id: str) -> Path:
    from core import swarm_out, swarm_out_results
    if not swarm_out_results.DISPATCH_ID_RE.fullmatch(dispatch_id or ""):
        raise SettleError("not a dispatch id")
    return swarm_out._state_dir() / "settle" / f"{dispatch_id}.json"


def load(dispatch_id: str) -> dict[str, Any]:
    try:
        d = json.loads(_path(dispatch_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(dispatch_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(dispatch_id, threading.Lock())


def _update(dispatch_id: str, change: Callable[[dict[str, Any]], Optional[bool]]) -> dict[str, Any]:
    """Load the record, apply ``change`` (it may raise SettleError; returning False
    writes nothing) and write it back, all under the dispatch's lock."""
    p = _path(dispatch_id)
    with _lock(dispatch_id):
        rec = load(dispatch_id)
        if change(rec) is False:
            return rec
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / f".{p.name}.{secrets.token_hex(6)}.tmp"
        tmp.write_text(json.dumps(rec, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        return rec


def _not_settled(rec: dict) -> None:
    if rec.get("settled"):
        raise SettleError("this task is settled")


# ── what the person decides ──────────────────────────────────────────────────

def _row(dispatch_id: str) -> dict[str, Any]:
    from core import swarm_out
    try:
        return swarm_out._log_row_of(dispatch_id)
    except swarm_out.SwarmOutError as e:
        raise SettleError(str(e)) from None


def set_verdict(dispatch_id: str, value: str, note: str, ended_at: Optional[str]) -> dict[str, Any]:
    """The person's verdict on an answer or a document, on the result that ended at
    ``ended_at`` (the one the card showed)."""
    row = _row(dispatch_id)
    if value not in VERDICTS:
        raise SettleError("the verdict is pass or fail")
    if (row.get("project_agent") or {}).get("returns") == "commits":
        raise SettleError("a task that delivers commits is accepted by landing them (or rejecting them)")
    if not row.get("session_id"):
        raise SettleError("this task has no session, so no result to judge: give it up instead")
    if not isinstance(ended_at, str) or not ended_at:
        raise SettleError("say which result the verdict is on (its ended_at)")

    def change(rec):
        _not_settled(rec)
        rec["verdict"] = {"value": value, "note": " ".join((note or "").split())[:MAX_NOTE_CHARS],
                          "ended_at": ended_at, "at": _now()}
    return _update(dispatch_id, change)


def _brought_commits(dispatch_id: str) -> bool:
    from core import swarm_out_results
    res = (swarm_out_results.load(dispatch_id) or {}).get("result") or {}
    return any(isinstance(r, dict) and ((r.get("commits_total") or 0) > 0 or isinstance(r.get("bundle"), dict))
               for r in ((res.get("work") or {}).get("repos") or []))


def set_manual_landed(dispatch_id: str) -> dict[str, Any]:
    """The person landed the commits by hand (they did not merge as they were)."""
    row = _row(dispatch_id)
    returns = (row.get("project_agent") or {}).get("returns")
    if returns not in (None, "commits") or (returns is None and not _brought_commits(dispatch_id)):
        raise SettleError("this task delivers no commits")

    def change(rec):
        _not_settled(rec)
        rec["manual_landed_at"] = _now()
    return _update(dispatch_id, change)


def abandon(dispatch_id: str) -> dict[str, Any]:
    """The person gives the task up: its verdict is decided ("abandoned"), and nothing
    else holds it back."""
    _row(dispatch_id)

    def change(rec):
        _not_settled(rec)
        rec["abandoned_at"] = _now()
    return _update(dispatch_id, change)


# ── what follows from the records ────────────────────────────────────────────

def _code(row: dict, res: dict, rec: dict) -> str:
    """``none`` (no commits came back), ``landed`` (every repository that brought commits
    back is landed or rejected, one at least landed; or landed by hand), ``rejected``
    (all rejected), ``unsealed`` (a repository has commits the Hive did not seal into a
    bundle: they exist only in its workspace) or ``waiting``."""
    from core import hive_bundle, hive_return
    if rec.get("manual_landed_at"):
        return "landed"
    states = []
    for r in ((res.get("work") or {}).get("repos") or []):
        if not isinstance(r, dict):
            continue
        b = r.get("bundle")
        if isinstance(b, dict) and b.get("available") and isinstance(r.get("dir"), str):
            try:
                st = hive_return.load(row["dispatch_id"], hive_bundle.key(r["dir"])) or {}
            except hive_return.ReturnError:
                st = {}
            states.append(st.get("status"))
        elif (r.get("commits_total") or 0) > 0:
            states.append("unsealed")
    if not states:
        return "none"
    if "unsealed" in states:
        return "unsealed"
    if all(s == "rejected" for s in states):
        return "rejected"
    if all(s in ("landed", "rejected") for s in states):
        return "landed"
    return "waiting"


def _final(res: Optional[dict]) -> bool:
    from core.swarm_out_results import ENDED
    return isinstance(res, dict) and res.get("ended") in ENDED and res.get("provisional") is not True


def _files_here(dispatch_id: str, kept_result: dict) -> bool:
    """Every returned file the release would remove is here: one under ``Outputs/<id>/``
    (core/hive_workspace.outputs_rel, the only file directory the release removes) is
    placed in the Brain, as core/hive_files.placed records it. A file elsewhere on the
    Hive is not touched by the release, so it holds nothing back. Read from the
    placement record, never from the ``local`` the result summary carries: a summary
    written before that field existed never has it, though its files are placed."""
    from core import hive_files, hive_workspace
    rel = hive_workspace.outputs_rel(dispatch_id).strip("/") + "/"
    here: set = set()
    for p in hive_files.placed(dispatch_id):
        if isinstance(p, dict) and isinstance(p.get("local"), str) and p["local"]:
            here.update(x for x in (p.get("listed"), p.get("path")) if isinstance(x, str))
    for f in ((kept_result.get("work") or {}).get("files") or []):
        if not isinstance(f, dict):
            continue
        names = [x for x in (f.get("abs"), f.get("path")) if isinstance(x, str) and x]
        if not any(("/" + n.lstrip("/")).find("/" + rel) >= 0 for n in names):
            continue                                   # not in the release's scope
        if not here & set(names):                      # placed under the path it was listed by
            return False
    return True


def derive(row: dict, kept: Optional[dict]) -> dict[str, Any]:
    """``{"next", "verdict", "code", "settles", "why_not", "settled"}`` for one row with
    its public ``result`` attached; ``kept`` is the run's kept record
    (core/swarm_out_results.load: its ``records`` and its whole ``result``), or None."""
    from core import hive_records
    rec = load(row["dispatch_id"])
    kept = kept if isinstance(kept, dict) else {}
    recs = kept.get("records") if isinstance(kept.get("records"), dict) else None
    kept_res = kept.get("result") if isinstance(kept.get("result"), dict) else {}
    res = row.get("result") if isinstance(row.get("result"), dict) else None
    gone = row.get("state_reason") == "hive_gone"
    abandoned = bool(rec.get("abandoned_at"))
    returns = (row.get("project_agent") or {}).get("returns")
    code = _code(row, res or {}, rec) if res else "none"
    # a row dispatched before ``returns`` existed: commits if any came back, else an answer
    kind = returns or ("commits" if code != "none" else "answer")
    # the verdict: the person's on this result, the landing for commits, or given up
    verdict: Optional[str] = None
    if abandoned:
        verdict = "abandoned"
    elif gone and not _final(res):
        verdict = "gone"
    elif kind == "commits" and code != "none":
        verdict = {"landed": "pass", "rejected": "fail"}.get(code)
    else:
        v = rec.get("verdict") if isinstance(rec.get("verdict"), dict) else None
        if v and res and v.get("ended_at") == res.get("ended_at"):
            verdict = v.get("value")                       # a newer turn voids it
    lessons = (res or {}).get("lessons")
    unqueued = isinstance(lessons, dict) and (lessons.get("pending") or 0) > 0
    # nothing more is pulled: the Hive is stopped or gone, or the records backstop passed
    pulled_out = gone or row.get("state_reason") == "hive_stopped" or (
        recs is not None and recs.get("closed") is not True
        and hive_records.backstop_passed(recs.get("last_at") or row.get("sent_at")))
    closed = recs.get("closed") if recs else None           # None: this run keeps no record
    lessons_done = abandoned or pulled_out or (closed is not False and not unqueued)
    code_done = abandoned or gone or code in ("none", "landed", "rejected")
    files_done = abandoned or gone or _files_here(row["dispatch_id"], kept_res)
    decided = verdict in ("pass", "abandoned", "gone")
    # no result will come: none can be fetched for good (a passing fetch error, the Hive
    # silent or unusable, may still bring it)
    stopped_without_result = res is None and row.get("state") == "stopped" and (
        row.get("result_status") == "not_applicable"
        or (row.get("result_status") == "failed" and row.get("result_error") in NO_RESULT_ERRORS))
    if row.get("waiting"):
        nxt = "answer"
    elif not row.get("session_id") or (stopped_without_result and not gone):
        nxt = "accept"                     # no result will come: do it again, or give it up
    elif row.get("running") or (res is not None and not _final(res)) or (res is None and not gone):
        nxt = "running"
    elif kind == "commits" and code in ("waiting", "unsealed") and verdict is None:
        nxt = "review_code"                                # landing them is the acceptance
    elif verdict in (None, "fail"):
        # judge the answer or document; after a fail (or commits that never came, or
        # rejected): do it again, or give it up
        nxt = "accept"
    elif code in ("waiting", "unsealed"):
        nxt = "review_code"
    elif not files_done:
        nxt = "accept"                     # a returned file never got here: keep waiting, or give up
    else:
        nxt = None
    why_not = [w for w, ok in (("verdict", decided), ("code", code_done), ("files", files_done),
                               ("lessons", lessons_done)) if not ok]
    return {"next": nxt if not rec.get("settled") else None, "verdict": verdict, "code": code,
            "settles": not why_not and not row.get("waiting") and not row.get("running"),
            "why_not": why_not, "settled": rec.get("settled")}


def _iso(v: Any) -> Optional[datetime]:
    if not isinstance(v, str) or not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _idle_days(row: dict, kept: dict) -> Optional[float]:
    """Days since the run's records last changed: the newest of when it was sent, when
    its result ended and when its record took an entry (what the sweeps count from)."""
    recs = kept.get("records") if isinstance(kept.get("records"), dict) else {}
    res = row.get("result") if isinstance(row.get("result"), dict) else {}
    times = [t for t in (_iso(row.get("sent_at")), _iso(res.get("ended_at")), _iso(recs.get("last_at"))) if t]
    if not times:
        return None
    return (datetime.now(timezone.utc) - max(times)).total_seconds() / 86400


def _settle_now(r: dict, d: dict) -> Optional[dict]:
    """Write ``settled`` if no one did meanwhile; the record's ``settled``, or None when it
    was already settled."""
    outcome = {"pass": "passed", "abandoned": "abandoned", "gone": "gone"}.get(d["verdict"], "passed")
    gone = r.get("state_reason") == "hive_gone"
    out: dict = {}

    def change(rec):
        if rec.get("settled"):
            return False
        # a Hive that is gone has no workspace to release (None); else told (False until
        # it answers)
        rec["settled"] = out["s"] = {"at": _now(), "outcome": outcome, "verdict": d["verdict"],
                                     "code": d["code"], "released": None if gone else False,
                                     "tries": 0, "next_try_at": 0}
    _update(r["dispatch_id"], change)
    return out.get("s")


def annotate(rows: list[dict], can_tell: bool = True) -> list[dict]:
    """Each Project Agent row's ``next``, ``settle`` (verdict, code, why it is not settled,
    when it was) and ``cleanup_soon`` (unsettled, its records swept within REMIND_DAYS);
    settles what is due and returns the rows to tell their Hive about now (newly settled,
    and earlier ones whose retry is due; none while ``can_tell`` is False, a release still
    running: they wait for the next look). Blocking (file reads)."""
    from core import hive_records, swarm_out_results
    tell = []
    for r in rows:
        if not isinstance(r.get("project_agent"), dict) or not r.get("dispatch_id"):
            continue
        s = load(r["dispatch_id"]).get("settled")
        if isinstance(s, dict):
            # settled: nothing to derive again
            r["next"], r["cleanup_soon"] = None, False
            r["settle"] = {"verdict": s.get("verdict"), "code": s.get("code"), "why_not": [], "settled": s}
            if can_tell and _retry_due(r, s) and _claim(r["dispatch_id"]):
                tell.append(r)
            continue
        kept = swarm_out_results.load(r["dispatch_id"]) or {}
        d = derive(r, kept)
        idle = _idle_days(r, kept)
        r["next"] = d["next"]
        r["settle"] = {"verdict": d["verdict"], "code": d["code"], "why_not": d["why_not"], "settled": None}
        r["cleanup_soon"] = idle is not None and idle >= hive_records.RETENTION_DAYS - REMIND_DAYS
        if d["settles"]:
            s = _settle_now(r, d) or load(r["dispatch_id"]).get("settled")
            r["next"], r["cleanup_soon"] = None, False
            r["settle"]["settled"] = s
            if can_tell and isinstance(s, dict) and _retry_due(r, s) and _claim(r["dispatch_id"]):
                tell.append(r)
    return tell


def _retry_due(r: dict, s: dict) -> bool:
    """A settled row whose Hive is still to be told, and may be now: not gone or stopped
    (a stopped Hive is told once it runs again), and past its backoff."""
    return (s.get("released") is False and r.get("state_reason") not in ("hive_gone", "hive_stopped")
            and time.time() >= float(s.get("next_try_at") or 0))


#: a row handed out to be told is not handed out again for this long (the tell's own
#: timeout is well under it); its answer then sets the real next try
CLAIM_S = 120


def _claim(dispatch_id: str) -> bool:
    """Hand the row out to be told, once: two looks at the same moment tell it once."""
    got = {"ok": False}

    def change(rec):
        s = rec.get("settled")
        if not isinstance(s, dict) or s.get("released") is not False \
                or time.time() < float(s.get("next_try_at") or 0):
            return False
        s["next_try_at"] = time.time() + CLAIM_S
        got["ok"] = True
    _update(dispatch_id, change)
    return got["ok"]


def mark_released(dispatch_id: str, released: bool) -> None:
    """Record the Hive's answer. True stays True; a failure sets the next try (doubling
    from RETRY_FIRST_S up to RETRY_MAX_S)."""
    def change(rec):
        s = rec.get("settled")
        if not isinstance(s, dict) or s.get("released") is not False:
            return False
        if released:
            s["released"] = True
        else:
            s["tries"] = int(s.get("tries") or 0) + 1
            s["next_try_at"] = time.time() + min(RETRY_MAX_S, RETRY_FIRST_S * 2 ** (s["tries"] - 1))
    _update(dispatch_id, change)


async def release_due(rows: list[dict]) -> None:
    """Tell each row's Hive to remove the task's workspace (a Hive that refuses, a session
    still running there (409), or no answer: told again after the backoff; 404, no such
    order there: nothing to remove). Never raises."""
    for r in rows:
        try:
            await _tell(r)
        except Exception as exc:  # noqa: BLE001 — the next look tries again
            logger.warning("swarm_out_settle: telling %s failed (%s)", r.get("dispatch_id"), type(exc).__name__)


async def _tell(r: dict) -> None:
    import asyncio
    from core import swarm_out
    try:
        inst = await swarm_out._instance(r["hive_id"])
        async with swarm_out._client(inst, swarm_out.PROBE_TIMEOUT_S * 4) as c:
            resp = await c.post(f"/api/hive/work/{r['dispatch_id']}/release")
        ok = resp.status_code in (200, 404)
        why = f"http_{resp.status_code}"
    except Exception as exc:  # noqa: BLE001 — told again after the backoff
        ok, why = False, type(exc).__name__
    if not ok and not (((r.get("settle") or {}).get("settled") or {}).get("tries")):
        logger.info("swarm_out_settle: %s not released yet (%s), trying again later", r.get("dispatch_id"), why)
    await asyncio.to_thread(mark_released, r["dispatch_id"], ok)
