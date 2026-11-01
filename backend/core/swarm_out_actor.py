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
- discarding while something would be lost: code not landed, a file not placed in the
  Brain, lessons not in, the run running, waiting or its Hive not answering, or its
  result not final (``discard_blocker``). A run whose Hive never started its session
  (``not_started``) has nothing to lose.

Landing commits that change the repository's protected paths is never an agent's,
whatever its request says (the land route ignores ``approve_protected`` from an agent):
those paths guard the agent itself or run code on this machine, so a Hive's change to
them stays the person's. The agent re-runs the task asking the Hive to leave them out,
or makes the change itself in its own session.

Every agent command is recorded in the run's settle record (``commands``: by, session,
time, command, reason), which the panel shows as "accepted by the agent: <reason>".
Like every "person only" in Swarm Out this is a speed bump against the agent's own
mistakes, not a boundary (ops-registry design §2.4): the agent and the daemon share an
OS user.

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
#: commands kept per run (oldest dropped)
MAX_COMMANDS = 50
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
    """Keep one line for an agent's command in the run's settle record. A person's
    commands are already what the settle record holds (verdict, discard, landing)."""
    if not a.agent:
        return
    from core import swarm_out_settle

    def change(rec: dict[str, Any]):
        cmds = rec.get("commands") if isinstance(rec.get("commands"), list) else []
        cmds.append({"by": "agent", "session": a.session, "command": command, "why": a.why,
                     "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        rec["commands"] = cmds[-MAX_COMMANDS:]
    swarm_out_settle._update(dispatch_id, change)


#: how much of what was asked a kept answer line holds (the card's text, Hive-written)
MAX_ASKED_CHARS = 400


def record_wait_answer(dispatch_id: str, session: Optional[str], request_id: str,
                       cls: Optional[str], pick: Any, why: str, asked: str = "") -> None:
    """Keep one line for a wait the dispatching agent answered itself (design
    2026-10-31-hive-approvals §2.3 H8): which wait, its class, what was asked, what it
    picked and why. ``host_answers`` reads them back for the run's autonomy tab, so the
    person can see afterwards what the agent decided for them; the run's summary counts
    them as handled by the host, never as the person stepping in."""
    from core import swarm_out_settle
    shown = pick if isinstance(pick, str) else json.dumps(pick, ensure_ascii=False)[:400]

    def change(rec: dict[str, Any]):
        cmds = rec.get("commands") if isinstance(rec.get("commands"), list) else []
        cmds.append({"by": "agent", "session": session, "command": "answer_wait",
                     "request_id": request_id, "class": cls, "pick": shown, "why": why,
                     "asked": str(asked or "")[:MAX_ASKED_CHARS],
                     "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        rec["commands"] = cmds[-MAX_COMMANDS:]
    try:
        swarm_out_settle._update(dispatch_id, change)
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
            for c in cmds if isinstance(c, dict) and c.get("command") == "answer_wait"]


def _counted(cmds: list) -> int:
    return sum(1 for c in cmds if isinstance(c, dict) and c.get("command") == "rerun"
               and c.get("failed") is not True)


def agent_reruns(dispatch_id: str) -> int:
    from core import swarm_out_settle
    try:
        rec = swarm_out_settle.load(dispatch_id)
    except swarm_out_settle.SettleError:
        return 0
    return _counted(rec.get("commands") if isinstance(rec.get("commands"), list) else [])


def reserve_rerun(dispatch_id: str, a: Actor) -> Optional[str]:
    """Count an agent's re-send before it is sent, under the run's lock, so two at once
    cannot both pass the limit. Returns the entry's time (``release_rerun`` marks it
    failed when the send fails), None for a person. Raises ActorError at the limit."""
    if not a.agent:
        return None
    from core import swarm_out_settle
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + f"#{time.monotonic_ns()}"

    def change(rec: dict[str, Any]):
        cmds = rec.get("commands") if isinstance(rec.get("commands"), list) else []
        if _counted(cmds) >= MAX_AGENT_RERUNS:
            raise ActorError(f"an agent has re-sent this run {MAX_AGENT_RERUNS} times already: take "
                             "the task over yourself, or discard the run saying why")
        cmds.append({"by": "agent", "session": a.session, "command": "rerun", "why": a.why, "at": at})
        rec["commands"] = cmds[-MAX_COMMANDS:]
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
_LOST = {"code": "commits that came back are not landed (land them first)",
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
    settle = row.get("settle") if isinstance(row.get("settle"), dict) else {}
    lost = [_LOST[w] for w in (settle.get("why_not") or []) if w in _LOST]
    if lost:
        return "; ".join(lost) + ": take them first"
    return None
