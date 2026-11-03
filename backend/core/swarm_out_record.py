"""The decision record of a dispatched run, Host side (design
docs/design/2026-11-02-hive-run-decision-record.md §1, §3).

A dispatched run goes through one funnel, the same for code and for other results:

1. **review** — the agent that dispatched it reads the result (``read``) and, for
   commits, takes them back (``return-prepare``);
2. **land** — the result is put on this host: commits merged into the local branch
   (``return-land``; the product places returned files in the Brain by itself);
   what cannot land is never the person's: the agent re-runs it, takes it over or
   discards it;
3. **judge** — once landed, the agent judges it (``accept``), or, when it cannot,
   hands it to the person with its judgment and why (``ask-person``); the person keeps
   it or reverts it, and the agent carries a revert out (``reverted``).

Every decision on the way is one entry in the run's settle record
(core/swarm_out_settle, key ``commands``, kept whole): who (``agent`` / ``person`` /
``product``), when, which ``decision``, ``why`` (the agent's or the person's own words)
and ``observed``: the facts this host saw at that moment (the result's ``ended_at``,
the checks, what landed, the files placed, the re-sends), computed here and never taken
from a request. Entries written before this record existed have only ``command``;
``decision_of`` reads them.

- ``append``            — add one entry (raises SettleError on a bad dispatch id)
- ``observed``          — the facts for a new entry
- ``entries``           — the entries, with their decision
- ``judgment_pending``  — the agent landed commits and has not judged them yet
- ``ask``               — the run's ask-person record (state open / keep / revert / reverted)
- ``person_decisions``  — how many decisions the person made (None: an older record)
- ``funnel``            — the gates passed and the current one, for the run card
- ``ask_person`` / ``answer_ask`` / ``reverted`` — the three steps of handing a landed
  result to the person
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: the decisions an entry can record
DECISIONS = ("dispatch", "answer_wait", "said", "review", "rerun", "stop", "land", "accept",
             "take_over", "discard", "escalate", "ask_person", "keep", "revert", "reverted",
             "place_files", "settle")
#: an entry written before ``decision`` existed: its command -> its decision
_COMMAND_DECISION = {"accept": "accept", "return_prepare": "review", "return_land": "land",
                     "manual_landed": "land", "rerun": "rerun", "stop": "stop",
                     "discard": "discard", "answer_wait": "answer_wait"}
#: what an entry may hold (``extra`` keys outside this are dropped)
_EXTRA = ("request_id", "class", "pick", "asked", "for_person", "commits", "failed", "kind")
MAX_WHY_CHARS = 2000
MAX_FIELD_CHARS = 2000
ASK_STATES = ("open", "keep", "revert", "reverted")


class RecordError(ValueError):
    """A step of the funnel is refused; the message is safe to show."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clean(text: Any, cap: int = MAX_FIELD_CHARS) -> str:
    return " ".join(str(text or "").split())[:cap]


def decision_of(entry: Any) -> Optional[str]:
    if not isinstance(entry, dict):
        return None
    d = entry.get("decision")
    if isinstance(d, str) and d:
        return d
    c = entry.get("command")
    return _COMMAND_DECISION.get(c, c if isinstance(c, str) else None)


def entries(rec: dict) -> list[dict]:
    """The record's entries, oldest first, each with its ``decision``."""
    out = []
    for e in rec.get("commands") or []:
        if isinstance(e, dict):
            out.append({**e, "decision": decision_of(e)})
    return out


def _new_format(rec: dict) -> bool:
    """The run's record is kept the funnel's way (it has an entry with a decision)."""
    return any(isinstance(e, dict) and e.get("decision") for e in rec.get("commands") or [])


# ── what the host sees ───────────────────────────────────────────────────────

def _landed(dispatch_id: str) -> list[dict]:
    """Each repository's landing on this host: the commit, the tip it took, the
    protected paths it changed (core/hive_return's state files)."""
    from core import hive_return, swarm_out
    out = []
    try:
        d = swarm_out._state_dir() / "returns" / dispatch_id
        files = sorted(d.glob("*.json")) if d.is_dir() else []
    except OSError:
        return out
    for f in files:
        try:
            st = hive_return.load(dispatch_id, f.stem) or {}
        except hive_return.ReturnError:
            continue
        if st.get("status") == "landed" and st.get("landed"):
            out.append({"url": st.get("url"), "commit": st.get("landed"), "tip": st.get("landed_tip"),
                        "protected": list(st.get("protected_files") or [])[:20]})
    return out


def observed(dispatch_id: str) -> dict[str, Any]:
    """The facts this host has about the run now. Never raises; a fact it cannot read
    is left out."""
    out: dict[str, Any] = {}
    try:
        from core import hive_files, swarm_out_results
        res = (swarm_out_results.load(dispatch_id) or {}).get("result")
        if isinstance(res, dict):
            out["ended_at"] = res.get("ended_at")
            out["ended"] = res.get("ended")
            checks = res.get("checks")
            if isinstance(checks, list):
                out["checks"] = {str(c.get("id")): c.get("status") for c in checks if isinstance(c, dict)}
            repos = [r for r in ((res.get("work") or {}).get("repos") or []) if isinstance(r, dict)]
            if repos:
                out["returned"] = [{"url": r.get("url"), "head": r.get("head"),
                                    "commits": r.get("commits_total")} for r in repos]
        landed = _landed(dispatch_id)
        if landed:
            out["landed"] = landed
        files = [p.get("local") for p in hive_files.placed(dispatch_id)
                 if isinstance(p, dict) and isinstance(p.get("local"), str) and p.get("local")]
        if files:
            out["files"] = files[:50]
        from core import swarm_out_settle
        rec = swarm_out_settle.load(dispatch_id)
        out["reruns"] = sum(1 for e in entries(rec) if e["decision"] == "rerun" and e.get("failed") is not True)
    except Exception as exc:  # noqa: BLE001 — an entry with fewer facts beats none
        logger.warning("swarm_out_record: facts of %s not read (%s)", dispatch_id, type(exc).__name__)
    return out


def entry(by: str, decision: str, why: str = "", session: Optional[str] = None,
          command: Optional[str] = None, extra: Optional[dict] = None,
          facts: Optional[dict] = None) -> dict[str, Any]:
    """One entry, as ``append`` writes it."""
    e: dict[str, Any] = {"at": _now(), "by": by if by in ("agent", "person", "product") else "agent",
                         "session": session, "decision": decision,
                         "why": _clean(why, MAX_WHY_CHARS), "observed": facts or {}}
    if command:
        e["command"] = command
    for k in _EXTRA:
        if extra and k in extra:
            e[k] = extra[k]
    return e


def append(dispatch_id: str, by: str, decision: str, why: str = "", session: Optional[str] = None,
           command: Optional[str] = None, extra: Optional[dict] = None,
           facts: Optional[dict] = None) -> dict[str, Any]:
    """Add one entry to the run's record. ``observed`` is computed here (``facts``
    only for a caller that already holds them, never from a request)."""
    from core import swarm_out_settle
    e = entry(by, decision, why, session, command, extra,
              facts if facts is not None else observed(dispatch_id))

    def change(rec: dict[str, Any]):
        cmds = rec.get("commands") if isinstance(rec.get("commands"), list) else []
        cmds.append(e)
        rec["commands"] = cmds
    swarm_out_settle._update(dispatch_id, change)
    return e


def append_quietly(dispatch_id: str, *args, **kwargs) -> None:
    """``append`` for a decision that already happened: a failure to keep the entry is
    logged, never raised."""
    try:
        append(dispatch_id, *args, **kwargs)
    except Exception:  # noqa: BLE001 — the decision happened; only its line is missing
        logger.warning("swarm_out_record: a %s on %s was not recorded", args[1] if len(args) > 1 else "?",
                       dispatch_id, exc_info=True)


# ── what follows from the entries ────────────────────────────────────────────

#: decisions that judge a landing (after the land they close the judgment)
_JUDGED = ("accept", "ask_person", "keep", "revert", "reverted", "discard", "take_over")


def judgment_pending(rec: dict) -> bool:
    """The agent landed commits (an entry of this record's format) and has not judged
    them since: the run does not settle until it does. A person's landing is the
    person's judgment."""
    pending = False
    for e in rec.get("commands") or []:
        if not isinstance(e, dict):
            continue
        d = decision_of(e)
        if d == "land":
            # only a landing kept the funnel's way holds a run (an older record's landing
            # was its pass)
            pending = e.get("by") == "agent" and bool(e.get("decision"))
        elif d in _JUDGED:
            pending = False
    return pending


def ask(rec: dict) -> Optional[dict]:
    a = rec.get("ask_person")
    return a if isinstance(a, dict) and a.get("state") in ASK_STATES else None


#: what the person does while a run works: steering it, not judging what it returned
_STEERING = ("said", "answer_wait")


def person_decisions(rec: dict, by_agent: bool) -> Optional[int]:
    """How often the person stepped into the run; None for a record kept before the
    funnel (count it the older way). ``by_agent``: an agent session dispatched the run
    (its row's ``dispatched_from``). There every decision of the person's counts (their
    commands, their answers, what they said in its Hive tab); on a run the person
    dispatched, only what they said or answered while it worked (judging their own run
    is not stepping in)."""
    if not _new_format(rec):
        return None
    return sum(1 for e in entries(rec) if e.get("by") == "person" and e.get("decision") != "dispatch"
               and (by_agent or e.get("decision") in _STEERING))


def funnel(row: dict, rec: dict, d: dict) -> dict[str, Any]:
    """The gates the run passed and the one it is at, for the card's line: ``review``,
    ``land`` (``ref``: the commit or the first file placed), ``judge``; ``state`` is
    ``done``, ``now`` or ``person`` (only an open ask); a discarded or expired run has
    no gate ``now``. ``d`` is derive's verdict."""
    es = entries(rec)
    res = row.get("result") if isinstance(row.get("result"), dict) else None
    final = bool(res) and d.get("result_final", True)
    commits = d.get("code") not in (None, "none")
    landed_facts = _landed(row["dispatch_id"]) if commits else []
    placed = []
    if not commits:
        try:
            from core import hive_files
            placed = [p.get("local") for p in hive_files.placed(row["dispatch_id"])
                      if isinstance(p, dict) and p.get("local")]
        except Exception as exc:  # noqa: BLE001 — the line shows without the placed file
            logger.warning("swarm_out_record: placed files of %s unreadable (%s)",
                           row.get("dispatch_id"), type(exc).__name__)
            placed = []

    def last(*decisions: str) -> Optional[dict]:
        for e in reversed(es):
            if e["decision"] in decisions:
                return e
        return None

    gates: list[dict[str, Any]] = []
    a = ask(rec)
    if not final or d.get("stage") in ("running", "needs_answer"):
        return {"gates": [], "current": None, "ask": a, "reruns": _reruns(es)}
    review = last("review", "land", "accept", "ask_person", "rerun", "discard", "take_over")
    landed = (commits and d.get("code") == "landed") or (not commits and (bool(placed) or d.get("files_done")))
    if not commits and placed:
        gates.append(_gate("land", "done", "product", None, placed[0]))
    gates.append(_gate("review", "done" if review or landed else "now",
                       (review or {}).get("by"), (review or {}).get("at")))
    if commits:
        le = last("land")
        if landed:
            gates.append(_gate("land", "done", (le or {}).get("by"), (le or {}).get("at"),
                               (landed_facts[0].get("commit") if landed_facts else None)))
        elif review:
            gates.append(_gate("land", "now", None, None))
    if landed or not commits:
        j = last("accept", "keep", "revert", "reverted", "ask_person")
        if a and a.get("state") == "open":
            gates.append(_gate("judge", "person", "agent", a.get("at")))
        elif a and a.get("state") == "revert":
            gates.append(_gate("judge", "now", "person", a.get("answered_at")))
        elif d.get("verdict") in ("pass", "reverted") and j:
            gates.append(_gate("judge", "done", j.get("by"), j.get("at")))
        elif d.get("verdict") == "pass":
            gates.append(_gate("judge", "done", None, None))
        elif d.get("stage") == "closed":
            # decided: discarded (by whoever did it) or gone; never "judging" any more
            j = last("discard")
            gates.append(_gate("judge", "done", (j or {}).get("by"), (j or {}).get("at")))
        elif review or landed:
            gates.append(_gate("judge", "now", None, None))
    current = next((g["key"] for g in gates if g["state"] != "done"), None)
    return {"gates": gates, "current": current, "ask": a, "reruns": _reruns(es)}


def _gate(key: str, state: str, by: Optional[str], at: Optional[str], ref: Optional[str] = None) -> dict:
    return {"key": key, "state": state, "by": by, "at": at, "ref": ref}


def _reruns(es: list[dict]) -> int:
    return sum(1 for e in es if e["decision"] == "rerun" and e.get("failed") is not True)


# ── handing a landed result to the person ───────────────────────────────────

def _on_host(row: dict) -> Optional[str]:
    """Why the run's result is not on this host yet, else None (code: every repository
    landed; anything else: a final result with every file the release would remove
    placed in the Brain)."""
    from core.swarm_out_settle import _final
    res = row.get("result") if isinstance(row.get("result"), dict) else None
    settle = row.get("settle") if isinstance(row.get("settle"), dict) else {}
    if settle.get("settled"):
        return "the run is settled"
    if not _final(res):
        return "its final result is not here yet"
    code = settle.get("code")
    if code not in (None, "none"):
        if code != "landed":
            return "its commits are not landed on this host (land them, or re-run / take over / discard)"
    elif "files" in (settle.get("why_not") or []):
        return "its files are not placed in the Brain yet"
    return None


def ask_person(dispatch_id: str, row: Optional[dict], session: Optional[str], decide: str,
               judgment: str, why: str) -> dict[str, Any]:
    """The dispatching agent hands its landed result to the person: what they decide
    (``decide``), the agent's own judgment, and why the agent cannot decide. Refused,
    with nothing written, when the result is not on this host, the session is not the
    one that dispatched the run, an ask is already open, or a field is missing."""
    from core import swarm_out_settle
    if not isinstance(row, dict):
        raise RecordError("the run is not in the list of runs now; try again in a moment")
    from core import swarm_run
    if swarm_run.is_child(row):
        # a child's result is material for its lead run: the person sees the lead run's
        # own landed result, never a child's (design 2026-10-31-swarm-run §5.3)
        raise RecordError("this run is a child of a Swarm Run: its lead run judges it, and hands "
                          "the person only its own landed result")
    if not session or swarm_run.owner_of(row) != session:
        raise RecordError("only the session that dispatched this run hands it to the person")
    decide, judgment, why = _clean(decide), _clean(judgment), _clean(why)
    if not decide or not judgment or not why:
        raise RecordError("say what the person decides (--decide), your judgment (--judgment) "
                          "and why you cannot decide it (--why)")
    blocker = _on_host(row)
    if blocker:
        raise RecordError(f"only a result that is on this host goes to the person: {blocker}")
    facts = observed(dispatch_id)
    landed = {"commits": [x.get("commit") for x in facts.get("landed") or []],
              "files": facts.get("files") or []}
    for_person = {"decide": decide, "judgment": judgment, "why": why, "landed": landed}
    e = entry("agent", "ask_person", why, session, "ask_person", {"for_person": for_person}, facts)

    def change(rec: dict[str, Any]):
        swarm_out_settle._not_settled(rec)
        if ask(rec) and ask(rec).get("state") in ("open", "revert"):
            raise swarm_out_settle.SettleError("this run already waits on the person")
        rec["ask_person"] = {"state": "open", "at": e["at"], "session": session, **for_person}
        rec.setdefault("commands", []).append(e)
    try:
        swarm_out_settle._update(dispatch_id, change)
    except swarm_out_settle.SettleError as exc:
        raise RecordError(str(exc)) from None
    return {"asked": True, "ask_person": {**for_person, "state": "open"}}


def answer_ask(dispatch_id: str, pick: str, note: str = "") -> dict[str, Any]:
    """The person's answer to an open ask: ``keep`` (the run passes) or ``revert`` (the
    agent undoes the landing and reports it with ``reverted``)."""
    from core import swarm_out_settle
    if pick not in ("keep", "revert"):
        raise RecordError("answer keep or revert")
    note = _clean(note)
    facts = observed(dispatch_id)
    e = entry("person", pick, note, None, None, None, facts)

    def change(rec: dict[str, Any]):
        swarm_out_settle._not_settled(rec)
        a = ask(rec)
        if not a or a.get("state") != "open":
            raise swarm_out_settle.SettleError("this run is not waiting on you")
        a["state"], a["answered_at"], a["note"] = pick, e["at"], note
        if pick == "keep" and facts.get("ended_at") and not facts.get("landed"):
            # an answer or a document passes on the result the person kept
            rec["verdict"] = {"value": "pass", "note": note, "ended_at": facts["ended_at"], "at": e["at"]}
        rec.setdefault("commands", []).append(e)
    try:
        swarm_out_settle._update(dispatch_id, change)
    except swarm_out_settle.SettleError as exc:
        raise RecordError(str(exc)) from None
    return {"answered": pick}


def reverted(dispatch_id: str, row: Optional[dict], session: Optional[str], commits: list[str],
             why: str) -> dict[str, Any]:
    """The dispatching agent carried out the person's revert: for commits, each named
    commit is on the landed branch and undoes exactly one landed commit, and every landed
    commit is undone (core/hive_return.verify_revert); for files, none placed is left at
    its place. Refused, with nothing written, otherwise."""
    from core import hive_files, hive_return, swarm_out_settle
    from core.project_registry import get_swarmws
    from pathlib import Path
    if not isinstance(row, dict):
        raise RecordError("the run is not in the list of runs now; try again in a moment")
    from core import swarm_run
    if not session or swarm_run.owner_of(row) != session:
        raise RecordError("only the session that dispatched this run reports its revert")
    why = _clean(why)
    if not why:
        raise RecordError("say what you did (--why)")
    a = ask(swarm_out_settle.load(dispatch_id))
    if not a or a.get("state") != "revert":
        raise RecordError("the person has not asked for a revert on this run")
    landed = _landed(dispatch_id)
    if landed:
        try:
            hive_return.verify_revert(dispatch_id, list(commits or []))
        except hive_return.ReturnError as exc:
            # a revert after a conflict cannot match exactly: the run stays open until
            # its records expire
            raise RecordError(f"{exc}; when your revert needed a conflict resolved and cannot "
                              "match, say so in the session (the run stays open until its "
                              "records expire)") from None
    else:
        ws = Path(get_swarmws())
        left = [p.get("local") for p in hive_files.placed(dispatch_id)
                if isinstance(p, dict) and p.get("local") and (ws / p["local"]).exists()]
        if left:
            raise RecordError("these files are still in the Brain: " + ", ".join(left[:5]))
    facts = observed(dispatch_id)
    e = entry("agent", "reverted", why, session, "reverted", {"commits": list(commits or [])[:20]}, facts)

    def change(rec: dict[str, Any]):
        swarm_out_settle._not_settled(rec)
        cur = ask(rec)
        if not cur or cur.get("state") != "revert":
            raise swarm_out_settle.SettleError("the person has not asked for a revert on this run")
        cur["state"], cur["reverted_at"] = "reverted", e["at"]
        rec.setdefault("commands", []).append(e)
    try:
        swarm_out_settle._update(dispatch_id, change)
    except swarm_out_settle.SettleError as exc:
        raise RecordError(str(exc)) from None
    return {"reverted": True}


# ── who needs to act ─────────────────────────────────────────────────────────

def owner_alive(row: dict) -> bool:
    """The run has a session that answers for it and still exists: the one that
    dispatched it, or for a Swarm Run's child its lead run's owner (core/swarm_run)."""
    from core import swarm_run
    owner = swarm_run.owner_of(row)
    if not owner:
        return False
    from core import attention_authority
    return attention_authority.session_confirmed(owner)


def needs_person(row: dict, asks: list[dict]) -> Optional[bool]:
    """Whether the person has something to do on the run: None for a run no live session
    answers for (the older rules apply); else only an open ask, or a wait on its Hive
    that is the person's (not the agent's class, or escalated). A Swarm Run's child never
    falls back to the older rules: its lead run takes it or the Conductor closes it, so
    only a wait that is the person's is the person's, whether its lead session lives or not."""
    from core import swarm_run
    if not swarm_run.is_child(row) and not owner_alive(row):
        return None
    a = (row.get("funnel") or {}).get("ask")
    if isinstance(a, dict) and a.get("state") == "open":
        return True
    from core import approval_class, swarm_out_wait_card
    for w in asks:
        if not isinstance(w, dict) or w.get("session_id") != row.get("session_id"):
            continue
        if w.get("approval_class") not in approval_class.HOST_CLASSES:
            return True
        if swarm_out_wait_card.escalation(str(row.get("dispatch_id") or ""), str(w.get("request_id") or "")):
            return True
    return False


def actionable(row: dict, asks: list[dict]) -> bool:
    """The dispatching agent has something to do on the run now: a result to review,
    land or judge, a stopped or expiring run, a wait of its own class, a revert to carry
    out. An open ask (the person's) and a settled or running run are not."""
    if (row.get("settle") or {}).get("settled"):
        return False
    a = (row.get("funnel") or {}).get("ask")
    if isinstance(a, dict) and a.get("state") == "revert":
        return True
    if isinstance(a, dict) and a.get("state") == "open":
        return False
    if row.get("stage") in ("awaiting", "stopped") or row.get("cleanup_soon"):
        return True
    if row.get("stage") == "needs_answer":
        from core import approval_class, swarm_out_wait_card
        for w in asks:
            if isinstance(w, dict) and w.get("session_id") == row.get("session_id") \
                    and w.get("approval_class") in approval_class.HOST_CLASSES \
                    and not swarm_out_wait_card.escalation(str(row.get("dispatch_id") or ""),
                                                           str(w.get("request_id") or "")):
                return True
    return False
