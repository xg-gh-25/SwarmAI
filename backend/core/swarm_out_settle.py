"""Accepting and settling a dispatched task, Host side (design
docs/design/2026-10-31-swarm-out-run-review.md §1, §3; the settle rules from
docs/design/2026-10-30-dispatch-center.md §6).

A task that went out under a Project Agent is followed to its end here, from what the
Host already keeps (the dispatch row, the run's result, the code it brought back, its
lessons) plus one small record per dispatch, ``state/swarm-out/settle/<dispatch>.json``,
that holds what was decided, by the person or by the agent that dispatched the run
(core/swarm_out_actor; ``commands`` keeps each of that agent's commands with its reason):

- ``verdict``: ``pass``, for a task whose deliverable is an answer or a document,
  bound to the result it judged (``ended_at``): a new turn on the Hive voids it. A task
  that delivers commits has no verdict of its own: landing them is the pass, "landed by
  hand" (``manual_landed_at``) too; when the dispatching agent landed them, its own
  judgment (its ``accept`` after the landing) completes the pass. Not good enough is not
  a verdict: the run is re-run with a correction (a new turn) or discarded.
- ``ask_person``: the dispatching agent handed its landed result to the person
  (core/swarm_out_record; design 2026-11-02-hive-run-decision-record): while it is
  open, or while the revert the person asked for is not done, the run does not settle;
  a verified revert settles it as ``reverted``.
- ``abandoned_at`` (+ ``discard_note``): the person discarded the task.
- ``settled``: when the conditions held, or it expired (``annotate``), and whether the
  Hive's workspace was released (``released``: True / False while the Hive could not be
  told, with ``tries`` and ``next_try_at`` for the backoff / None: no Hive to tell).

``derive(row, kept)`` gives the run's ``stage`` (running, needs_answer, awaiting,
stopped, accepted, closed: with ``why`` discarded / reverted / expired / hive_gone) and
the ``actions`` the person can take in it
(at most one primary; "open session" while it can open; "discard" last), and whether it
settles:

- the verdict is decided (pass, discarded, or the Hive is gone);
- the code is handled (landed in every repository that brought commits back, landed by
  hand, or none came back);
- the files are here: every returned file the release would remove (under
  ``Outputs/<id>/``) is placed in the Brain, or its Hive said it is gone (a file listed,
  then moved or deleted before its turn ended: nothing of it is left to remove), or none
  can come any more (the round's result is lost), or the dispatching agent settled the run
  without them (``settle_without_files``, a recorded decision with its reason);
- the lessons are in: the Hive's run record is closed (or the run has none) and every
  lesson it brought is queued for this machine's CDF; or nothing more is pulled (the
  Hive is stopped or gone, or no new entry for the records backstop). No one takes or
  declines lessons (CDF design D13: they go to the CDF as they arrive);
- its session is known to be stopped: never while it runs, and never while its Hive does
  not answer (``state`` unknown), unless a person discarded it.

A stopped round whose result can no longer come (``result_lost``: an Elastic Workers round
past its lifetime whose store was read whole after it could write nothing more, with no
final result: core/swarm_out_results ``round_end``) is ``stopped`` / ``no_result``, its
lessons count as in (none can follow), and its ``/runs`` row and the dispatching agent's
run summary (core/swarm_out_follow) carry a ``note`` saying what came back (``lost_note``;
the desktop panel shows the stage and the ``no_result`` why text, not the note): the
dispatching agent may discard it (core/swarm_out_actor), as nothing can arrive any more.

Settling tells the Hive to remove the task's workspace (``Hive/work/<id>/`` and
``Outputs/<id>/``), so code and files must have left it first: commits the Hive could
not seal into a bundle (``unsealed``), a repository not yet landed, and a file not
placed here hold the task back. Only discarding overrides that (the panel says what it
removes). No model decides anything here (P5): the verdict is given by the person or the
dispatching agent through the routes, the rest is counted.

- ``end``: how the run's session ended (``end_of``: its own final result, or proof that no
  result will come), written once by the first look that shows it; a later machine state
  (a Hive stopped now, a carrier past its lifetime) never changes it, and ``state_reason``
  stays the machine's state of the moment. A re-run's turn gets its own end.
- ``annotate(rows)``         — each row's stage, actions, settle and end; settles and
  expires what is due.
- ``set_verdict`` / ``set_manual_landed`` / ``discard`` / ``settle_without_files`` — the person's or the dispatching
  agent's actions (routes in routers/swarm_out; what an agent may not do with them is
  core/swarm_out_actor's).
- ``release_due`` — tell the Hives (``annotate`` hands each row out once per try, with backoff).

Every write is a read-modify-write of one record under that dispatch's lock
(``_update``): the runs are refreshed by the background loop and by the panel at once.
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

#: a result fetch that failed for good (core/swarm_out_results fetch_error)
NO_RESULT_ERRORS = ("no session", "session_gone", "http_404")
VERDICTS = ("pass",)
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
        raise SettleError("the verdict is pass (not good enough: re-run with a correction, or discard)")
    from core import swarm_run
    if swarm_run.is_child(row):
        # a Swarm Run's child: accepting it is its lead run taking the result
        set_handled(dispatch_id, note, ended_at)
        return {"handled": True}
    if (row.get("project_agent") or {}).get("returns") == "commits":
        # landing them is the pass; after the dispatching agent landed them, its own
        # judgment (this accept, kept as the run's decision by the route) closes the
        # funnel (core/swarm_out_record.judgment_pending)
        from core import swarm_out_record
        rec = load(dispatch_id)
        if swarm_out_record.judgment_pending(rec) and \
                _code(row, _current_result(dispatch_id), rec) == "landed":
            _not_settled(rec)
            return {"judged": True}
        raise SettleError("a task that delivers commits is accepted by landing them")
    if not row.get("session_id"):
        raise SettleError("this task has no session, so no result to judge: discard it instead")
    if not isinstance(ended_at, str) or not ended_at:
        raise SettleError("say which result the verdict is on (its ended_at)")
    current = _current_result(dispatch_id)
    if not _final(current) or current.get("ended_at") != ended_at:
        # a panel or a CLI read before the run moved on (a re-run, a new turn): a
        # verdict on an old result would hold every command back (closed_state)
        raise SettleError("the run's result changed since it was shown: reload it and judge the "
                          "result there is now")

    def change(rec):
        _not_settled(rec)
        rec["verdict"] = {"value": value, "note": " ".join((note or "").split())[:MAX_NOTE_CHARS],
                          "ended_at": ended_at, "at": _now()}
    return _update(dispatch_id, change)


def _current_result(dispatch_id: str) -> dict[str, Any]:
    """The run's kept result now (core/swarm_out_results), or {}."""
    from core import swarm_out_results
    res = (swarm_out_results.load(dispatch_id) or {}).get("result")
    return res if isinstance(res, dict) else {}


def mark_code_landed(dispatch_id: str) -> bool:
    """A repository's commits landed on this machine (core/hive_return.land). Once every
    repository that brought commits back is landed or rejected (``_code``), the run is
    accepted from now on (``closed_state``), so no re-run or session write can add
    commits a release would then remove unlanded. While one still waits, nothing is
    written: re-run and discard stay open for it. True when the run is now accepted."""
    try:
        row = _row(dispatch_id)
    except SettleError:
        return False
    if _code(row, _current_result(dispatch_id), load(dispatch_id)) != "landed":
        return False

    def change(rec):
        if rec.get("code_landed_at"):
            return False
        rec["code_landed_at"] = _now()
    _update(dispatch_id, change)
    return True


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


def discard(dispatch_id: str, note: str = "") -> dict[str, Any]:
    """The person does not want the run: it is decided ("abandoned") and nothing else
    holds it back, so the next look settles it and tells its Hive to remove the
    workspace. ``note``: why, if they said (kept for the repeat-correction count)."""
    _row(dispatch_id)

    def change(rec):
        _not_settled(rec)
        if rec.get("abandoned_at"):
            raise SettleError("this run is already discarded")
        rec["abandoned_at"] = _now()
        clean = " ".join((note or "").split())[:MAX_NOTE_CHARS]
        if clean:
            rec["discard_note"] = clean
    return _update(dispatch_id, change)


def set_handled(dispatch_id: str, note: str, ended_at: Optional[str], *, by: str = "lead") -> dict[str, Any]:
    """A Swarm Run's child is handled by its lead run (design 2026-10-31-swarm-run §5.7):
    ``by="lead"``, the lead run took the result that ended at ``ended_at`` (its commits
    must be on this host first, in ``refs/hive``: nothing is lost when the workspace
    goes); ``by="run_end"``, the lead run ended and the child goes with it (the Conductor,
    core/swarm_run_conductor). Either way the child is accepted from now on: no re-run,
    no session write, and it settles once it has stopped and its lessons are in."""
    from core import swarm_out_actor, swarm_run
    row = _row(dispatch_id)
    if not swarm_run.is_child(row):
        raise SettleError("only a Swarm Run's child is handled by its lead run")
    if by == "lead":
        current = _current_result(dispatch_id)
        if not isinstance(ended_at, str) or not ended_at or not _final(current) \
                or current.get("ended_at") != ended_at:
            raise SettleError("the run's result changed since it was shown: reload it and take the "
                              "result there is now")
        if _brought_commits(dispatch_id) and not swarm_out_actor.commits_here(dispatch_id, current):
            raise SettleError("its commits are only on the Hive: take them back first "
                              "(`pipeline-v1 child merge`, or `swarm_out return-prepare`)")

    def change(rec):
        if rec.get("handled_at") or rec.get("abandoned_at") or rec.get("settled"):
            return False
        rec["handled_at"] = _now()
        rec["handled"] = {"by": by, "ended_at": ended_at,
                          "note": " ".join((note or "").split())[:MAX_NOTE_CHARS]}
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
        # the panel's result says ``available``; the kept one has the bundle's sha256
        if isinstance(b, dict) and (b.get("available") or b.get("sha256")) and isinstance(r.get("dir"), str):
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


#: reasons a listed file was not sealed after which waiting brings nothing back: ``gone``
#: (nothing of it is left) and ``hive`` (under the Hive's own ``Hive/`` area, never returned)
_NEVER_BACK = frozenset({"gone", "hive"})


def _skipped(rec: dict, ended_at: Optional[str]) -> frozenset:
    """The listed file paths the agent settled the run without (``settle_without_files``),
    for the result that ended at *ended_at* only: they count as never coming back. A newer
    result (a new turn) is judged afresh."""
    got = rec.get("files_skipped") if isinstance(rec.get("files_skipped"), dict) else {}
    if not ended_at or got.get("ended_at") != ended_at:
        return frozenset()
    return frozenset(x for x in got.get("paths") or [] if isinstance(x, str) and x)


def _files_here(dispatch_id: str, kept_result: dict, skipped: frozenset = frozenset()) -> bool:
    """Every returned file the release would remove is here (``_files_missing``)."""
    return not _files_missing(dispatch_id, kept_result, skipped)


def _files_missing(dispatch_id: str, kept_result: dict, skipped: frozenset = frozenset()) -> list[str]:
    """The listed paths of the returned files that hold the run back (empty: every file
    the release would remove is here). A file listed under
    ``Outputs/<id>/`` (core/hive_workspace.outputs_rel; the release also removes
    ``Hive/work/<id>/``, and a path there with ``Outputs/<id>/`` in it is in scope too) is
    placed in the Brain, as core/hive_files.placed records it, unless its Hive's newest word
    is that it did not seal it for a reason that a later turn could change (a newer copy
    that may exist only there: it holds even when an older copy is placed). Two reasons
    hold nothing (``_NEVER_BACK``): ``gone`` (core/hive_files.not_sealed: it no longer
    existed when its turn ended, so the release removes nothing of it) and ``hive`` (it lies
    under the Hive's own ``Hive/`` area, which never comes back: no later pull can bring it,
    so holding would only keep the run open until its records expire; measured: a pipeline
    run wrote its scratch files into ``Hive/work/<id>/Outputs/<id>/`` and could be neither
    settled nor discarded). A file not placed and not reported holds. A file elsewhere on
    the Hive is not touched by the release, so it holds nothing back. A path in *skipped*
    (the agent's recorded ``settle_without_files``) holds nothing: it counts as never
    coming. Read from the placement and record, never from the ``local`` the result
    summary carries: a summary written before that field existed never has it, though its
    files are placed."""
    from core import hive_files, hive_workspace
    rel = hive_workspace.outputs_rel(dispatch_id).strip("/") + "/"
    here: set = set()
    for p in hive_files.placed(dispatch_id):
        if isinstance(p, dict) and isinstance(p.get("local"), str) and p["local"]:
            here.update(x for x in (p.get("listed"), p.get("path")) if isinstance(x, str))
    words: Optional[dict] = None
    missing: list[str] = []
    for f in ((kept_result.get("work") or {}).get("files") or []):
        if not isinstance(f, dict):
            continue
        names = [x for x in (f.get("abs"), f.get("path")) if isinstance(x, str) and x]
        if not any(("/" + n.lstrip("/")).find("/" + rel) >= 0 for n in names):
            continue                                   # not in the release's scope
        if skipped & set(names):
            continue                                   # settled without it: never coming
        if words is None:                              # the Hive's newest word, by listed path
            words = hive_files.not_sealed(dispatch_id)
        said = {words[n] for n in names if n in words}
        if said and not said <= _NEVER_BACK:           # a copy that may exist only on the Hive
            missing.append(names[0])
            continue
        if said or here & set(names):                  # gone or never returnable, or placed
            continue
        missing.append(names[0])
    return missing


#: listed paths one settle-without-files decision names in its record entry (all of them
#: are kept in ``files_skipped``)
SKIPPED_SHOWN = 20


def settle_without_files(dispatch_id: str, why: str, by: str = "agent", session: Optional[str] = None,
                         pulled: Optional[bool] = None, ended_at: Optional[str] = None) -> dict[str, Any]:
    """The dispatching agent (or the person) settles the run without the returned files
    that never came back: every file that holds it now (``_files_missing``) is kept in
    ``files_skipped`` and from then on counts as never coming, and the decision is one
    entry of the run's record with *why*, the paths and whether the pull tried just
    before reached its Hive (*pulled*). Only the files are released this way: a run
    whose files were its only blocker settles at the next look (its Hive removes the
    workspace and whatever of them is still there). Bound to the result that ended at
    *ended_at* (the one the caller judged; None: the current one): a newer result is
    refused, and the skip never covers a later result's files. ``{"skipped": [...]}``;
    an empty list when nothing holds any more (the pull brought them) writes nothing."""
    from core import swarm_out_record
    clean = " ".join(str(why or "").split())[:MAX_NOTE_CHARS]
    if not clean:
        raise SettleError("say why the run settles without its files")
    kept_res = _current_result(dispatch_id)
    now_at = kept_res.get("ended_at")
    if ended_at is not None and now_at != ended_at:
        raise SettleError("the run's result changed since it was judged: look at it again")
    out: dict[str, Any] = {"skipped": []}

    def change(rec):
        _not_settled(rec)
        missing = _files_missing(dispatch_id, kept_res, _skipped(rec, now_at))
        if not missing:
            return False
        out["skipped"] = missing
        rec["files_skipped"] = {"at": _now(), "by": by, "why": clean, "ended_at": now_at,
                                "paths": sorted(_skipped(rec, now_at) | set(missing))}
        rec.setdefault("commands", []).append(swarm_out_record.entry(
            by, "settle_without_files", clean, session, "settle_without_files",
            facts={"files": missing[:SKIPPED_SHOWN], "files_total": len(missing), "pulled": pulled}))
    _update(dispatch_id, change)
    return out


def closed_state(rec: dict, dispatch_id: Optional[str] = None) -> Optional[str]:
    """``closed`` (discarded, expired, its Hive gone), ``accepted`` (settled as passed,
    its commits landed here or by hand, or a pass verdict on the result there is now:
    files or lessons may still be coming), or None. One rule for everything that
    refuses a command or a write on a run: the Host proxy
    (core/swarm_out_remote.closed_for) and the person's commands (core/swarm_out). With
    *dispatch_id*, a pass verdict counts only on the run's current result (``derive``
    voids one a newer turn replaced); without it, any pass verdict counts."""
    settled = rec.get("settled") if isinstance(rec.get("settled"), dict) else None
    if rec.get("abandoned_at") or (settled and settled.get("outcome") in ("abandoned", "gone", "expired",
                                                                           "reverted")):
        return "closed"
    if settled or rec.get("manual_landed_at") or rec.get("code_landed_at") or rec.get("handled_at"):
        return "accepted"
    verdict = rec.get("verdict") if isinstance(rec.get("verdict"), dict) else None
    if verdict and verdict.get("value") == "pass" and (
            dispatch_id is None or verdict.get("ended_at") == _current_result(dispatch_id).get("ended_at")):
        return "accepted"
    return None


#: the stages a person sees (design 2026-10-31-swarm-out-run-review §1.2). ``closed``: the
#: run ended without being accepted; ``why`` says how (``discarded``, ``reverted``,
#: ``expired``, ``hive_gone``). One stage, because no reader treats those endings
#: differently (design 2026-11-02-swarm-out-hive-deaths-diagnostics-states §3 S1).
STAGES = ("running", "needs_answer", "awaiting", "stopped", "accepted", "closed")
#: a settle outcome -> its stage (``reverted``: the person had what landed undone)
_OUTCOME_STAGE = {"passed": "accepted", "abandoned": "closed", "gone": "closed", "expired": "closed",
                  "reverted": "closed", "handled": "accepted"}
#: a settle outcome -> the ``why`` of its stage (stored outcomes keep their names);
#: ``swarm_run``: a child its lead run handled (design 2026-10-31-swarm-run §5.7)
_OUTCOME_WHY = {"abandoned": "discarded", "gone": "hive_gone", "expired": "expired", "reverted": "reverted",
                "handled": "swarm_run"}
#: a result that ended this way delivered what it set out to (anything else, the turn
#: limit included, stopped: design §3.2)
_DELIVERED = ("completed",)
#: a turn its Hive recorded as cut off by its daemon's death (core/hive_boot): stopped at
#: once, as an interrupted ``no_record`` turn is after INTERRUPTED_AFTER_S (an older Hive
#: records nothing). It is final, so it reaches ``stopped`` through ``_DELIVERED`` (its
#: ``why`` the Hive's word) and its returned commits go through the funnel like any
#: final result's; a Hive that is gone closes it the ``no_record`` way unless commits
#: of it can still land here or an ask holds it (``derive``).
_CUT_OFF = ("daemon_stopped",)


def _cut_off(res: Optional[dict]) -> bool:
    return isinstance(res, dict) and res.get("ended") in _CUT_OFF


def _act(key: str, primary: bool = False, variant: Optional[str] = None) -> dict[str, Any]:
    a: dict[str, Any] = {"key": key, "primary": primary}
    if variant:
        a["variant"] = variant
    return a


def _actions(stage: str, alive: bool, accept: Optional[str],
             reason: Optional[str] = None, ask: Optional[str] = None) -> list[dict[str, Any]]:
    """The commands a person can give in ``stage``, in their order (§1.2): at most one
    primary, "open session" while the session can be opened, "discard" last.
    ``alive``: the run's Hive session can still take a message; ``accept``: how accept
    works here (``land`` / ``manual`` / ``verdict``), None when it cannot; ``reason``:
    the run's ``state_reason``. Waiting on an answer, ``answer`` is a hint, not a
    command (the dispatching agent answers with ``swarm_out answer``, the person in the
    panel's wait card), and ``stop`` ends the waiting turn; after an answer the Hive
    refused, opening the session to answer there leads; after an answer sent and not yet
    taken there is nothing to do but wait.
    ``ask``: the state of the run's ask-person (core/swarm_out_record): open, the person
    keeps or reverts what landed; revert, the dispatching agent carries it out (its
    ``swarm_out reverted``), nothing to give here: what landed counts as accepted for
    every other command (core/swarm_out ``_not_closed``), so a revert the host cannot
    match exactly waits until the run's records expire. ``judge``: the dispatching agent landed the result and judges
    it: accept (its judgment) or hand it to the person (``swarm_out ask-person``); what
    landed is not re-run or discarded (the run counts as accepted for those:
    core/swarm_out ``_not_closed``)."""
    session = [_act("open_session")] if alive else []
    if stage == "awaiting" and ask == "open":
        return [_act("keep", True), _act("revert"), *session]
    if stage == "awaiting" and ask == "revert":
        return session
    if stage == "awaiting" and ask == "judge":
        return [_act("accept", True, "verdict"), *session]
    if stage == "running":
        if reason == "answer_sent":
            return [*session, _act("discard")]
        return [_act("stop", True), *session, _act("discard")]
    if stage == "needs_answer":
        # stop ends the turn that waits (then discard can follow): a run waiting on an
        # answer nobody will give is never left without a way out
        if reason == "answer_refused":
            return [*([_act("open_session", True)] if alive else []), _act("stop"), _act("discard")]
        return [_act("answer", False, "hint"), _act("stop"), *session, _act("discard")]
    if stage == "awaiting":
        head = [_act("accept", True, accept)] if accept else []
        rerun = [_act("rerun", not head)] if alive else []
        return [*head, *rerun, *session, _act("discard")]
    if stage == "stopped":
        return [*([_act("rerun", True)] if alive else []), *session, _act("discard")]
    if stage == "accepted":
        return [_act("open_session", variant="read_only")] if alive else []
    return []


#: how long a stopped run's result may say "no ending recorded" before the turn counts
#: as cut off (a Hive restart): the turn's end record can lag the session's stop by a
#: check or two
INTERRUPTED_AFTER_S = 300


def interrupted(row: dict, res: Optional[dict], kept: Optional[dict]) -> bool:
    """The run stopped mid-turn: its session is not running, and its result has said
    ``no_record`` (no end record for the last turn) for INTERRUPTED_AFTER_S
    (core/swarm_out_results records since when). A Hive that records the cut-off says
    ``daemon_stopped`` instead (``_CUT_OFF``)."""
    if row.get("state") != "stopped":
        return False
    if not isinstance(res, dict) or res.get("ended") != "no_record":
        return False
    # a record kept before the time was recorded: from its last check
    since = _iso((kept or {}).get("no_record_since") or (kept or {}).get("checked_at"))
    return since is not None and (datetime.now(timezone.utc) - since).total_seconds() >= INTERRUPTED_AFTER_S


def derive(row: dict, kept: Optional[dict]) -> dict[str, Any]:
    """``{"stage", "actions", "why", "verdict", "code", "settles", "why_not", "settled",
    "result_lost", "note"}``
    for one row with its public ``result`` attached; ``kept`` is the run's kept record
    (core/swarm_out_results.load: its ``records`` and its whole ``result``), or None.

    ``stage`` and ``actions`` are what the panel shows and offers (design
    2026-10-31-swarm-out-run-review §1.2, §3.2); ``why`` says why a run stopped
    (``no_session`` / ``no_result`` / ``hive_stopped``: its Hive is stopped and its result
    was not fetched / how its result ended) or how a ``closed`` run ended
    (``discarded`` / ``reverted`` / ``expired`` / ``hive_gone``). The rest decides whether
    it settles: the verdict is decided (accepted, discarded, the Hive gone), the code is
    handled, the files the release would remove are here, the lessons are in, and its
    session is known to be stopped (a person's discard settles unless it runs). A run
    whose Hive does not answer (``state`` unknown) settles here only when a person
    discarded it: settling tells the Hive to release the workspace, and a turn or
    background work may still be going there (design
    2026-11-02-swarm-out-hive-deaths-diagnostics-states §3 S3)."""
    from core import hive_records, swarm_out_record, swarm_run
    rec = load(row["dispatch_id"])
    child = swarm_run.is_child(row)
    handled = child and bool(rec.get("handled_at"))
    # closed because its lead run ended: like a discard, a wait left open on its Hive
    # does not keep it from settling (nothing will answer it; core/swarm_run_conductor)
    run_end = handled and (rec.get("handled") or {}).get("by") == "run_end"
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
    # the funnel (core/swarm_out_record): an open ask waits on the person, a revert on
    # the dispatching agent; the agent's own landing waits on its judgment
    ask_rec = swarm_out_record.ask(rec)
    ask = ask_rec.get("state") if ask_rec else None
    judging = kind == "commits" and code == "landed" and swarm_out_record.judgment_pending(rec)
    # a Hive that is gone closes a result that may still change, and a cut-off turn
    # (``_CUT_OFF``) the same way when nothing it returned can still land here (commits
    # kept here, ``waiting``, or landed go through the funnel) and no ask holds it
    closes_gone = gone and (not _final(res) or (_cut_off(res) and code in ("none", "rejected", "unsealed")
                                                and ask is None))
    # the verdict: the person's on this result, the landing for commits, or discarded
    verdict: Optional[str] = None
    if abandoned:
        verdict = "abandoned"
    elif handled:
        verdict = "handled"
    elif ask == "reverted":
        verdict = "reverted"
    elif closes_gone:
        verdict = "gone"
    elif ask in ("open", "revert"):
        verdict = None
    elif kind == "commits" and code != "none":
        verdict = "pass" if code == "landed" and not judging else None
    else:
        v = rec.get("verdict") if isinstance(rec.get("verdict"), dict) else None
        if v and res and v.get("ended_at") == res.get("ended_at") and v.get("value") == "pass":
            verdict = "pass"                               # a newer turn voids it
    lessons = (res or {}).get("lessons")
    unqueued = isinstance(lessons, dict) and (lessons.get("pending") or 0) > 0
    # the round's result can no longer come: its store was read whole after its machine
    # could write nothing more (``result_lost``); nothing else will be pulled for it
    lost = result_lost(row, kept)
    # nothing more is pulled: the Hive is stopped or gone, the round's machine can write
    # nothing more, or the records backstop passed
    pulled_out = gone or bool(lost) or row.get("state_reason") == "hive_stopped" or (
        recs is not None and recs.get("closed") is not True
        and hive_records.backstop_passed(recs.get("last_at") or row.get("sent_at")))
    closed = recs.get("closed") if recs else None           # None: this run keeps no record
    lessons_done = abandoned or pulled_out or (closed is not False and not unqueued)
    # a handled child's commits are its lead run's material (in refs/hive, or no longer
    # wanted once the run ended): nothing here waits on landing them
    code_done = abandoned or gone or handled or code in ("none", "landed", "rejected")
    # the files: placed, or none can come any more (its Hive gone, the round's result
    # lost: its store was read whole), or the agent settled without them
    files_done = abandoned or gone or bool(lost) or _files_here(row["dispatch_id"], kept_res,
                                                                _skipped(rec, kept_res.get("ended_at")))
    decided = verdict in ("pass", "abandoned", "gone", "reverted", "handled")
    stopped_without_result = _no_result_ever(row, res)

    # the session can still take a message: named, its Hive here, not lost
    alive = bool(row.get("session_id")) and not gone and not stopped_without_result

    why: Optional[str] = None
    # the probe's view of the session (core/swarm_out.run_state); ``unknown``: its Hive did
    # not answer, so a turn or background work may still be going there
    state = row.get("state")
    settled = rec.get("settled") if isinstance(rec.get("settled"), dict) else None
    if settled:
        stage = _OUTCOME_STAGE.get(settled.get("outcome"), "accepted")
        why = _OUTCOME_WHY.get(settled.get("outcome"))
    elif abandoned:
        stage, why = "closed", "discarded"                 # settles at the next look
    elif handled:
        stage, why = "accepted", "swarm_run"               # settles once stopped, lessons in
    elif closes_gone:
        stage, why = "closed", "hive_gone"
    elif state == "waiting":
        stage = "needs_answer"
    elif not row.get("session_id"):
        stage, why = "stopped", "no_session"
    elif stopped_without_result or lost:
        stage, why = "stopped", "no_result"
    elif state == "stopped" and row.get("state_reason") == "hive_stopped" and not _final(res):
        # no session runs on a stopped Hive, and its result was not fetched before it
        # stopped: stopped, never "running" (the result is read once the Hive runs again)
        stage, why = "stopped", "hive_stopped"
    elif interrupted(row, res, kept):
        stage, why = "stopped", "no_record"                # cut off: re-run it
    elif state == "running" or res is None or not _final(res):
        stage = "running"
    elif verdict == "reverted":
        stage, why = "closed", "reverted"                  # settles at the next look
    elif ask in ("open", "revert") or judging:
        stage, why = "awaiting", {"open": "ask_person", "revert": "revert"}.get(ask or "", "judge")
    elif verdict == "pass":
        stage = "accepted"                                 # files or lessons still coming
    elif res.get("ended") not in _DELIVERED:
        stage, why = "stopped", str(res.get("ended") or "")[:30] or None
    else:
        stage = "awaiting"
    # how accept works: land the commits that came back (by hand when they cannot be
    # taken as they are), else the person's verdict on what came back; a task that
    # delivers commits and has none to land (none came, or an older record rejected
    # them all) cannot be accepted: re-run or discard
    if child:
        accept: Optional[str] = "child"                    # the lead run takes it (set_handled)
    elif judging:
        accept = "verdict"                                 # the agent's judgment of its landing
    elif code == "waiting":
        accept = "land"
    elif code == "unsealed":
        accept = "manual"
    elif kind == "commits":
        accept = None
    else:
        accept = "verdict"
    why_not = [w for w, ok in (("verdict", decided), ("code", code_done), ("files", files_done),
                               ("lessons", lessons_done)) if not ok]
    actions = _actions(stage, alive, accept, row.get("state_reason"),
                       ask or ("judge" if why == "judge" else None))
    out = {"stage": stage, "actions": _person_actions(actions) if child else actions,
           "why": why, "verdict": verdict, "code": code,
           # a discarded run (or a child closed by its run's end) settles though a wait
           # stays open on its Hive (a pipeline decision outlives the stopped chat):
           # nothing will answer it. Never while it
           # runs, and never while its Hive does not answer unless a person discarded it:
           # settling tells the Hive to release the workspace, which ends the session's
           # pipeline runs and removes what background work may still be writing
           "settles": not why_not and (state == "stopped" or (abandoned and state != "running")
                                       or (run_end and state == "waiting")),
           "why_not": why_not, "settled": settled,
           "result_lost": lost, "note": lost_note(row, lost) if lost else None}
    if child:
        # the lead run's commands on its child (core/swarm_out_actor reads these for it):
        # take it (accept, variant child), correct it (rerun), stop, discard
        out["lead_actions"] = actions
    out["funnel"] = swarm_out_record.funnel(row, rec, {**out, "files_done": files_done,
                                                       "result_final": _final(res)})
    out["person_decisions"] = swarm_out_record.person_decisions(rec, bool(row.get("dispatched_from")))
    return out


def _no_result_ever(row: dict, res: Optional[dict]) -> bool:
    """No result will come: the run is stopped and none can be fetched for good (a
    passing fetch error, the Hive silent or unusable, may still bring it)."""
    return res is None and row.get("state") == "stopped" and (
        row.get("result_status") == "not_applicable"
        or (row.get("result_status") == "failed" and row.get("result_error") in NO_RESULT_ERRORS))


# ── how the run's session ended (``end``) ─────────────────────────────────────

#: ``end.reason``: how the run's session ended, from its own final result, or from what
#: proved that no result will come
END_REASONS = ("completed", "turn_limit", "stopped_by_agent", "stopped_by_person", "crashed",
               "carrier_expired", "hive_stopped", "lost")
#: a final result's ending -> its end reason (``stopped`` is named by who stopped it);
#: an ending missing here (paused, no_terminal, no_record, restarted, superseded) does
#: not say the session ended
_END_OF = {"completed": "completed", "turn_limit": "turn_limit", "error": "crashed",
           "crashed": "crashed", "gave_up": "crashed", "daemon_stopped": "hive_stopped"}
#: a stop or discard recorded this long after the result's end still stopped it (the
#: command is recorded after the Hive answered, and the clocks may differ)
STOP_SLACK_S = 120


def _stopped_by(rec: dict, ended_at: Any, kept: Optional[dict] = None) -> str:
    """Who stopped the turn: the last stop or discard command recorded during it (after
    the last re-send, core/swarm_out_results ``resent_at``, up to its end; ``commands``,
    core/swarm_out_record), the person's or an agent's; none recorded: the person, from
    the Hive's own page."""
    end = _iso(ended_at)
    sent = _iso((kept or {}).get("resent_at"))
    by = None
    for c in rec.get("commands") if isinstance(rec.get("commands"), list) else []:
        if not isinstance(c, dict) or c.get("command") not in ("stop", "discard"):
            continue
        at = _iso(c.get("at"))
        if end is not None and (at is None or (at - end).total_seconds() > STOP_SLACK_S):
            continue
        if sent is not None and at is not None and at < sent:
            continue                                   # a stop of an earlier turn
        by = c.get("by")
    return "stopped_by_person" if by in (None, "person") else "stopped_by_agent"


def _stamp(v: Any) -> str:
    t = _iso(v)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ") if t else _now()


def end_of(row: dict, rec: dict, kept: Optional[dict]) -> Optional[dict]:
    """``{"reason", "at"}``: how the run's session ended, or None while nothing proves it
    did. From the session's own final result (``at`` its ``ended_at``); else from proof
    that no result will come: the round's store read whole after its machine could write
    no more (``result_lost``: ``carrier_expired`` when its lifetime ended it), its Hive
    gone, or no result for good (``lost``). A Hive that is only stopped, a carrier past
    its lifetime whose store was not read yet, or a turn whose ending has not been
    recorded for a while (``interrupted``, a timer), is not an end: the result may still
    come and say how it ended (a turn the Hive's stop cut off says ``daemon_stopped``)."""
    res = row.get("result") if isinstance(row.get("result"), dict) else None
    if _final(res):
        ended = res.get("ended")
        reason = _stopped_by(rec, res.get("ended_at"), kept) if ended == "stopped" else _END_OF.get(ended)
        if reason:
            return {"reason": reason, "at": _stamp(res.get("ended_at"))}
    lost = result_lost(row, kept)
    if lost:
        return {"reason": "carrier_expired" if row.get("state_reason") == "carrier_expired" else "lost",
                "at": _stamp(lost.get("checked_at"))}
    if row.get("state_reason") == "hive_gone" or _no_result_ever(row, res):
        return {"reason": "lost", "at": _now()}
    return None


def _of_earlier_turn(end: Any, kept: Optional[dict]) -> bool:
    """The end is of a turn before the last re-send (core/swarm_out_results ``resent_at``):
    a correction went into the same session after it."""
    sent = _iso((kept or {}).get("resent_at"))
    at = _iso(end.get("at")) if isinstance(end, dict) else None
    return sent is not None and at is not None and at < sent


def _note_end(row: dict, rec: dict, kept: Optional[dict]) -> Optional[dict]:
    """The run's ``end`` as ``/runs`` shows it, written first if due: once, the first
    look that proves how the session ended writes it and no later look changes it (a
    later machine state, ``hive_stopped`` or ``carrier_expired``, never replaces it). A
    re-run's turn gets its own: an end older than the re-send is not shown and the new
    turn's end replaces it. A record that cannot be written: the row lists without one,
    and the next look writes it."""
    cur = rec.get("end") if isinstance(rec.get("end"), dict) else None
    if cur and not _of_earlier_turn(cur, kept):
        return cur
    end = end_of(row, rec, kept)
    if end is None or _of_earlier_turn(end, kept):
        return None
    out: dict[str, Any] = {}

    def change(r: dict):
        have = r.get("end") if isinstance(r.get("end"), dict) else None
        if have and not _of_earlier_turn(have, kept):
            out["end"] = have                      # another look wrote it first
            return False
        r["end"] = out["end"] = end
    try:
        _update(row["dispatch_id"], change)
    except (OSError, SettleError) as e:
        logger.warning("swarm_out_settle: the end of %s was not kept (%s)", row.get("dispatch_id"), type(e).__name__)
        return None
    return out.get("end")


def result_lost(row: dict, kept: Optional[dict]) -> Optional[dict]:
    """``{"why", "checked_at"}`` when the run's stopped current round will never bring a
    final result: the kept record's ``round_end`` is of that round (its number and
    session; core/swarm_out_results.round_end_of) and the result is not final. None
    otherwise: a result that did come, a later round, or a run not stopped."""
    from core import swarm_out_results
    res = row.get("result") if isinstance(row.get("result"), dict) else None
    if row.get("state") != "stopped" or _final(res):
        return None
    mark = swarm_out_results.round_end_of(row, kept)
    if not mark:
        return None
    return {"why": str(row.get("state_reason") or "ended")[:40], "checked_at": mark.get("checked_at")}


#: files a card names by path before it only counts the rest
NOTE_FILES = 8
_END_WORDS = {"carrier_expired": "reached the Elastic Workers session limit",
              "carrier_replaced": "was replaced", "closed": "closed", "released": "was released",
              "idle": "ended"}


#: a returned path is the worker's text: shown on one line, quoted, cut to this length
NOTE_PATH_MAX = 120


def _shown(path: str, cap: int = NOTE_PATH_MAX) -> str:
    """A path as a card names it: control and format characters dropped, whitespace
    collapsed (a path with a newline never starts a line of its own), in backticks
    (none inside), at most *cap* characters."""
    import unicodedata
    clean = "".join(c for c in path if unicodedata.category(c)[0] != "C" or c in " \t\n")
    clean = " ".join(clean.split()).replace("`", "'")
    return "`" + (clean[:cap - 1] + "…" if len(clean) > cap else clean) + "`"


def lost_note(row: dict, lost: dict) -> str:
    """The ``note`` of a run whose result can no longer come (its ``/runs`` row and the
    agent's run summary; the desktop panel shows only the stage and why): how its session
    ended, that nothing more can arrive, and what did come back (the files placed in the
    Brain, files that came back but could not be placed, commit bundles with no result to
    land them by). Blocking (small file reads)."""
    from core import hive_files, hive_records
    did = str(row.get("dispatch_id") or "")
    how = _END_WORDS.get(lost.get("why") or "", f"ended ({lost.get('why')})")
    placed = [p for p in hive_files.placed(did) if isinstance(p, dict)]
    here = sorted({p["local"] for p in placed if isinstance(p.get("local"), str) and p["local"]})
    unplaced = sum(1 for p in placed if not p.get("local"))
    try:
        bundles = sum(1 for e in hive_records.local_entries(did) if e.get("kind") == "bundle")
    except (OSError, ValueError):
        bundles = 0
    parts = []
    if here:
        named = ", ".join(_shown(x) for x in here[:NOTE_FILES]) \
            + (f" and {len(here) - NOTE_FILES} more" if len(here) > NOTE_FILES else "")
        parts.append(f"{len(here)} file{'s' if len(here) != 1 else ''} placed in the Brain ({named})")
    if unplaced:
        parts.append(f"{unplaced} file{'s' if unplaced != 1 else ''} that could not be placed")
    if bundles:
        one = bundles == 1
        parts.append(f"{bundles} commit bundle{'' if one else 's'} with no result naming "
                     f"{'its' if one else 'their'} repository, so not landed (kept on this host with "
                     f"the run's records, {_shown(str(hive_records.local_dir(did)), 200)}, until they expire)")
    came = "; ".join(parts) if parts else "nothing"
    return (f"The worker's session {how} before its final result came back. Its result store was "
            f"read after the session could write nothing more, so nothing more will arrive. "
            f"What came back: {came}. Discard it, or re-run it to finish the work.")


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


def _person_actions(actions: list[dict]) -> list[dict]:
    """What the person is offered on a Swarm Run's child: taking it and correcting it are
    the lead run's (design 2026-10-31-swarm-run §5.9, I12); stop, discard and opening the
    session stay theirs."""
    return [a for a in actions if a.get("key") not in ("accept", "rerun")]


def _settle_now(r: dict, d: dict) -> Optional[dict]:
    """Write ``settled`` if no one did meanwhile; the record's ``settled``, or None when it
    was already settled."""
    from core import swarm_out_record
    outcome = {"pass": "passed", "abandoned": "abandoned", "gone": "gone",
               "expired": "expired", "reverted": "reverted", "handled": "handled"}.get(d["verdict"], "passed")
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
        # the product's own decision, one line of the run's record
        rec.setdefault("commands", []).append(swarm_out_record.entry(
            "product", "settle", outcome, facts={"outcome": outcome, "code": d["code"]}))
    _update(r["dispatch_id"], change)
    return out.get("s")


def _show_settled(r: dict, s: dict) -> None:
    """A settled row's stage and commands: an accepted run's session opens read-only; a
    closed one's does not open (§3.4), and its ``why`` says how it ended."""
    stage = _OUTCOME_STAGE.get(s.get("outcome"), "accepted")
    alive = bool(r.get("session_id")) and r.get("state_reason") != "hive_gone"
    r["stage"], r["actions"], r["why"] = stage, _actions(stage, alive, None), _OUTCOME_WHY.get(s.get("outcome"))
    r["cleanup_soon"] = False
    r["settle"] = {"code": s.get("code"), "why_not": [], "settled": s}
    from core import swarm_out_record
    rec = load(r["dispatch_id"])
    r["funnel"] = swarm_out_record.funnel(r, rec, {"stage": stage, "verdict": s.get("verdict"),
                                                   "code": s.get("code"), "files_done": True,
                                                   "result_final": True})
    r["person_decisions"] = swarm_out_record.person_decisions(rec, bool(r.get("dispatched_from")))


def annotate(rows: list[dict], can_tell: bool = True) -> list[dict]:
    """Each Project Agent row's ``stage``, ``actions`` and ``why`` (what the panel shows
    and offers, ``derive``), ``settle`` (verdict, code, why it is not settled, when it
    was) and ``cleanup_soon`` (unsettled, its records swept within REMIND_DAYS). Settles
    what is due, and expires (outcome ``expired``) an unsettled run nothing changed in
    for hive_records.RETENTION_DAYS: its records go then. Returns the rows to tell their
    Hive about now (newly settled, and earlier ones whose retry is due; none while
    ``can_tell`` is False, a release still running: they wait for the next look).
    Blocking (file reads)."""
    from core import hive_records, swarm_out_results
    tell = []
    for r in rows:
        if not isinstance(r.get("project_agent"), dict) or not r.get("dispatch_id"):
            continue
        rec = load(r["dispatch_id"])
        s = rec.get("settled")
        if not isinstance(s, dict):
            kept = swarm_out_results.load(r["dispatch_id"]) or {}
            r["end"] = _note_end(r, rec, kept)
            d = derive(r, kept)
            idle = _idle_days(r, kept)
            # a run asking a question nobody answered expires too (the Hive sweeps it
            # then), and so does one whose Hive no longer answers; one still running does not
            expire = idle is not None and idle >= hive_records.RETENTION_DAYS and r.get("state") != "running"
            if not d["settles"] and not expire:
                r["stage"], r["actions"], r["why"] = d["stage"], d["actions"], d["why"]
                if "lead_actions" in d:
                    r["lead_actions"] = d["lead_actions"]
                r["settle"] = {"code": d["code"], "why_not": d["why_not"], "settled": None,
                               "result_lost": d["result_lost"]}
                r["note"] = d["note"]
                r["funnel"], r["person_decisions"] = d["funnel"], d["person_decisions"]
                r["cleanup_soon"] = idle is not None and idle >= hive_records.RETENTION_DAYS - REMIND_DAYS
                continue
            s = _settle_now(r, d if d["settles"] else {**d, "verdict": "expired"}) \
                or load(r["dispatch_id"]).get("settled")
            if not isinstance(s, dict):
                continue
        else:
            # settled: nothing runs any more; an older record may still take its end
            r["end"] = _note_end(r, rec, None)
        _show_settled(r, s)
        if can_tell and _retry_due(r, s) and _claim(r["dispatch_id"]):
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
    """Release the run's workspace through its carrier (``Carrier.release`` of the
    current round; a row written before rounds: the round its Hive and session name)."""
    import asyncio
    from core.carriers import registry
    try:
        handle = registry.handle_of(r)
        await registry.carrier(handle.carrier).release(handle, r["dispatch_id"])
        ok, why = True, "released"
    except Exception as exc:  # noqa: BLE001 — told again after the backoff
        ok, why = False, str(exc)[:200] or type(exc).__name__
    if not ok and not (((r.get("settle") or {}).get("settled") or {}).get("tries")):
        logger.info("swarm_out_settle: %s not released yet (%s), trying again later", r.get("dispatch_id"), why)
    await asyncio.to_thread(mark_released, r["dispatch_id"], ok)
