"""Who gave a command on a dispatched run, and what an agent may not do with it.

Design docs/design/2026-10-31-swarm-out-agent-owns-its-runs.md §2.1. Every run is
dispatched by an agent (the App has no dispatch), and the agent that dispatched it
answers for it: it may accept, re-send, stop, land and discard it, like the person, and
it answers its Hive's waits of the classes it answers for (``swarm_out answer``; design
2026-10-31-hive-approvals). Nothing on the path waits for the person (design
2026-11-01-brain-to-hive-zero-touch): a dispatched Hive session denies at once what
would be the person's to approve. A
command says who gave it (``by``: ``agent`` from the CLI, ``person`` from the App; a
request that says nothing is an agent's, so a bare call gets the agent's limits). For an
agent the routes refuse what is the person's to decide:

- any command from a session other than the one that dispatched the run, or without a
  reason (``why``);
- any command on a run no agent session is recorded as having dispatched (an older run);
- a command the panel does not offer the person on the run now (its ``/runs`` row's
  ``actions``): accept only while the run awaits it, on the final result the row shows
  (``ended_at``); stop only while it runs (``row_blocker``);
- "landed by hand" at all: it settles the run and releases its workspace with any
  commits not landed yet; an agent lands commits with ``return_land``;
- re-sending with a correction more than ``MAX_AGENT_RERUNS`` times (``reserve_rerun``
  counts it under the run's lock before it is sent): after that the agent takes the
  task over or discards it;
- discarding while something would be lost: commits not landed that are not on this
  host either (``return-prepare`` takes them into ``refs/hive/...``: once every returned
  tip is there, discarding loses nothing), a file not placed in the Brain, lessons not
  in, the run running, waiting or its Hive not answering, or its result not final
  (``discard_blocker``). A run whose Hive never started its session (``not_started``)
  has nothing to lose.

The agent lands commits that change the repository's own protected paths once it has
reviewed them (design 2026-11-02-hive-run-decision-record §2: the funnel lands on this
host or re-runs, never hands unlanded work to the person); the land entry keeps the
paths and its reason. Paths that make this machine run code on their own (git hooks,
filters, submodules, a coding tool's hooks: core/hive_return ``AUTORUN``) an agent never lands: it re-runs the task
asking the Hive to leave them out, or makes the change itself in its own session.

Every command, the agent's and the person's, is one entry of the run's decision record
(core/swarm_out_record: by, session, time, decision, reason, and the facts this host saw),
which the run card's Decisions tab shows. Like every "person only" in Swarm Out this is a
speed bump against the agent's own mistakes, not a boundary (ops-registry design §2.4):
the agent and the daemon share an OS user.

Public: ``Actor``, ``actor``, ``MAX_AGENT_RERUNS``, ``check``, ``row_blocker``,
``record``, ``record_wait_answer``, ``reserve_rerun``, ``release_rerun``, ``agent_reruns``,
``discard_blocker``.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

MAX_AGENT_RERUNS = 2
MAX_WHY_CHARS = 400
COMMANDS = ("accept", "manual_landed", "return_prepare", "return_land", "rerun", "stop", "discard")


class ActorError(ValueError):
    """The command is refused for this actor; the message is safe to show."""


@dataclass(frozen=True)
class Actor:
    by: str                      # "agent" | "person"
    session: Optional[str]       # the agent's app session id, when it gave one
    why: str

    @property
    def agent(self) -> bool:
        return self.by != "person"


def actor(by: Optional[str], session: Optional[str], why: Optional[str]) -> Actor:
    """The actor a request names. ``by`` other than ``person`` is an agent; a session
    id that is not one is dropped."""
    from core.app_session_id import valid_app_session_id
    sid = session if isinstance(session, str) and valid_app_session_id(session) else None
    clean = " ".join(str(why or "").split())[:MAX_WHY_CHARS]
    return Actor("person" if by == "person" else "agent", sid, clean)


def check(a: Actor, command: str, *, dispatch_id: str = "") -> None:
    """Raise ActorError when an agent may not give *command* now (module docstring);
    the discard check needs the run's row and is ``discard_blocker``."""
    if not a.agent:
        return
    if command not in COMMANDS:
        raise ActorError(f"unknown command {command!r}")
    if not a.why:
        raise ActorError("say why (--why): the person sees the reason for each command an agent gives")



#: which offered action of the run's row each command needs (core/swarm_out_settle
#: _actions): accept's variant says how accept works on the run now
_NEEDS = {"accept": ("accept", "verdict"), "return_prepare": ("accept", "land"),
          "return_land": ("accept", "land"),
          "rerun": ("rerun", None), "stop": ("stop", None), "discard": ("discard", None)}


def row_blocker(a: Actor, command: str, row: Optional[dict[str, Any]],
                ended_at: Optional[str] = None) -> Optional[str]:
    """Why an agent may not give *command* on the run whose ``/runs`` row is *row*, else
    None: another session dispatched it, or the panel does not offer the person that
    command on it now (module docstring)."""
    if not a.agent:
        return None
    if not isinstance(row, dict):
        return "the run is not in the list of runs now; try again in a moment"
    owner = row.get("dispatched_from")
    if not owner:
        # a run from before the dispatching session was recorded: no agent owns it
        return "no agent session is recorded as having dispatched this run: the person gives commands on it"
    if a.session != owner:
        return "another session dispatched this run: only that session (or the person) gives commands on it"
    if command == "manual_landed":
        # "landed by hand" settles the run and releases its workspace with whatever
        # commits are not landed yet: an agent lands commits with return_land instead
        return "an agent lands commits with return-land; saying they were landed by hand is the person's"
    key, variant = _NEEDS[command]
    offered = {x.get("key"): x.get("variant") for x in row.get("actions") or [] if isinstance(x, dict)}
    if key not in offered or (variant is not None and offered[key] != variant):
        stage = row.get("stage") or "?"
        return f"the run does not offer `{command}` now (stage {stage}); see its actions in `runs`"
    if command == "accept":
        from core.swarm_out_settle import _final
        res = row.get("result") if isinstance(row.get("result"), dict) else None
        if not _final(res) or not ended_at or res.get("ended_at") != ended_at:
            return "accept the run's final result as `runs` shows it now (its result's ended_at)"
    return None


def record(dispatch_id: str, a: Actor, command: str) -> None:
    """Keep one entry for a command, the agent's or the person's, in the run's decision
    record (core/swarm_out_record), with the facts this host sees now."""
    from core import swarm_out_record
    swarm_out_record.append(dispatch_id, a.by, swarm_out_record.decision_of({"command": command}) or command,
                            a.why, a.session if a.agent else None, command)


#: how much of what was asked a kept answer line holds (the card's text, Hive-written)
MAX_ASKED_CHARS = 400


def record_wait_answer(dispatch_id: str, session: Optional[str], request_id: str,
                       cls: Optional[str], pick: Any, why: str, asked: str = "") -> None:
    """Keep one line for a wait the dispatching agent answered itself (design
    2026-10-31-hive-approvals §2.3 H8): which wait, its class, what was asked, what it
    picked and why. ``host_answers`` reads them back for the run's autonomy tab, so the
    person can see afterwards what the agent decided for them; the run's summary counts
    them as handled by the host, never as the person stepping in."""
    from core import swarm_out_record, swarm_out_settle
    shown = pick if isinstance(pick, str) else json.dumps(pick, ensure_ascii=False)[:400]
    try:
        swarm_out_record.append(dispatch_id, "agent", "answer_wait", why, session, "answer_wait",
                                {"request_id": request_id, "class": cls, "pick": shown,
                                 "asked": str(asked or "")[:MAX_ASKED_CHARS]})
    except swarm_out_settle.SettleError:
        logging.getLogger(__name__).warning("swarm_out_actor: the answer on %s was not kept", dispatch_id)


def host_answers(dispatch_id: str) -> list[dict[str, Any]]:
    """The waits the dispatching agent answered on this run, oldest first: when, the
    wait's class, what was asked, what it picked and why (``record_wait_answer``)."""
    from core import swarm_out_settle
    try:
        rec = swarm_out_settle.load(dispatch_id)
    except swarm_out_settle.SettleError:
        return []
    cmds = rec.get("commands") if isinstance(rec.get("commands"), list) else []
    return [{k: c.get(k) for k in ("at", "request_id", "class", "asked", "pick", "why")}
            for c in cmds if isinstance(c, dict) and c.get("command") == "answer_wait"
            and c.get("by", "agent") == "agent"]


def _counted(cmds: list) -> int:
    return sum(1 for c in cmds if isinstance(c, dict) and c.get("command") == "rerun"
               and c.get("failed") is not True)


def agent_reruns(dispatch_id: str) -> int:
    from core import swarm_out_settle
    try:
        rec = swarm_out_settle.load(dispatch_id)
    except swarm_out_settle.SettleError:
        return 0
    cmds = rec.get("commands") if isinstance(rec.get("commands"), list) else []
    return _counted([c for c in cmds if isinstance(c, dict) and c.get("by", "agent") == "agent"])


def reserve_rerun(dispatch_id: str, a: Actor) -> Optional[str]:
    """Count an agent's re-send before it is sent, under the run's lock, so two at once
    cannot both pass the limit. Returns the entry's time (``release_rerun`` marks it
    failed when the send fails), None for a person. Raises ActorError at the limit."""
    if not a.agent:
        return None
    from core import swarm_out_record, swarm_out_settle
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + f"#{time.monotonic_ns()}"
    e = swarm_out_record.entry("agent", "rerun", a.why, a.session, "rerun",
                               facts=swarm_out_record.observed(dispatch_id))
    e["at"] = at

    def change(rec: dict[str, Any]):
        cmds = rec.get("commands") if isinstance(rec.get("commands"), list) else []
        if _counted([c for c in cmds if isinstance(c, dict) and c.get("by", "agent") == "agent"]) \
                >= MAX_AGENT_RERUNS:
            raise ActorError(f"an agent has re-sent this run {MAX_AGENT_RERUNS} times already: take "
                             "the task over yourself, or discard the run saying why")
        cmds.append(e)
        rec["commands"] = cmds
    try:
        swarm_out_settle._update(dispatch_id, change)
    except swarm_out_settle.SettleError as e:
        raise ActorError(str(e)) from None
    return at


def release_rerun(dispatch_id: str, at: Optional[str]) -> None:
    """The reserved re-send was not sent: it no longer counts."""
    if not at:
        return
    from core import swarm_out_settle

    def change(rec: dict[str, Any]):
        for c in rec.get("commands") or []:
            if isinstance(c, dict) and c.get("at") == at and c.get("command") == "rerun":
                c["failed"] = True
    swarm_out_settle._update(dispatch_id, change)


#: what each ``settle.why_not`` item means for a discard
_LOST = {"code": "commits that came back are not landed or taken back here (return-prepare them first)",
         "files": "files that came back are not placed in the Brain yet",
         "lessons": "the run's lessons are not in yet"}


def discard_blocker(row: Optional[dict[str, Any]]) -> Optional[str]:
    """Why an agent may not discard this run now, from its ``/runs`` row, else None.
    Discarding removes the run's Hive workspace (``Hive/work/<id>``, ``Outputs/<id>``):
    nothing that exists only there may be lost, and a run still working or waiting is
    not the agent's to end that way."""
    from core.swarm_out_settle import _final
    if not isinstance(row, dict):
        return "the run is not in the list of runs now; try again in a moment"
    if row.get("state_reason") == "not_started":
        return None                    # its Hive never started the session: nothing came back
    if row.get("running") is not False or row.get("waiting") or row.get("state") == "unknown":
        # running None: its Hive did not answer the probe, so a turn may be running there
        return "the run is still running or waiting, or its Hive is not answering: stop it first, or wait"
    res = row.get("result")
    if not _final(res if isinstance(res, dict) else None):
        return "its final result is not here yet"
    funnel = row.get("funnel") if isinstance(row.get("funnel"), dict) else {}
    if isinstance(funnel.get("ask"), dict) and funnel["ask"].get("state") == "revert":
        return ("the person asked for a revert: undo what landed and report it with "
                "`swarm_out reverted`; when it cannot match exactly, say so in the session "
                "(the run stays open until its records expire)")
    settle = row.get("settle") if isinstance(row.get("settle"), dict) else {}
    why_not = list(settle.get("why_not") or [])
    if "code" in why_not and commits_here(str(row.get("dispatch_id") or ""), res):
        why_not.remove("code")       # every returned tip is in refs/hive here: nothing is lost
    lost = [_LOST[w] for w in why_not if w in _LOST]
    if lost:
        return "; ".join(lost) + ": take them first"
    return None


def commits_here(dispatch_id: str, res: Any) -> bool:
    """Every repository that brought commits back has them on this host: its bundle was
    taken back (core/hive_return.prepare) and its ``refs/hive/<dispatch>/<key>`` ref
    still names the tip. A repository whose commits were never sealed into a bundle
    has them only on the Hive."""
    from core import hive_bundle, hive_return
    import subprocess
    repos = [r for r in (((res or {}).get("work") or {}).get("repos") or []) if isinstance(r, dict)]
    checked = 0
    for r in repos:
        b = r.get("bundle")
        has_bundle = isinstance(b, dict) and (b.get("available") or b.get("sha256")) and isinstance(r.get("dir"), str)
        if not has_bundle:
            if (r.get("commits_total") or 0) > 0:
                return False
            continue
        try:
            st = hive_return.load(dispatch_id, hive_bundle.key(r["dir"])) or {}
        except hive_return.ReturnError:
            return False
        if st.get("status") not in ("prepared", "landed") or not st.get("repo") or not st.get("ref"):
            return False
        try:
            out = subprocess.run(["git", "-C", str(st["repo"]), "rev-parse", "--verify", "--quiet",
                                  f"{st['ref']}^{{commit}}"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return False
        if out.returncode != 0 or out.stdout.strip() != st.get("tip"):
            return False
        checked += 1
    return checked > 0          # nothing to check is not proof the commits are here
