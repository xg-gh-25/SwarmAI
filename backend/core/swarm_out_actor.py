"""Who gave a command on a dispatched run, and what an agent may not do with it.

Design docs/design/2026-10-31-swarm-out-agent-owns-its-runs.md §2.1. Every run is
dispatched by an agent (the App has no dispatch), and the agent that dispatched it
answers for it: it may accept, re-send, stop, land and discard it, like the person. A
command says who gave it (``by``: ``agent`` from the CLI, ``person`` from the App; a
request that says nothing is an agent's, so a bare call gets the agent's limits). For an
agent the routes refuse what is the person's to decide:

- landing commits that change the repository's protected paths (``approve_protected``);
- re-sending with a correction more than ``MAX_AGENT_RERUNS`` times;
- discarding while something would be lost: code not landed, a file not placed in the
  Brain, lessons not in, the run still running or waiting, or its result not final
  (``discard_blocker``, from the run's own ``settle`` as ``/runs`` computes it);
- any command without a reason (``why``).

Every agent command is recorded in the run's settle record (``commands``: by, session,
time, command, reason), which the panel shows as "accepted by the agent: <reason>".
Like every "person only" in Swarm Out this is a speed bump against the agent's own
mistakes, not a boundary (ops-registry design §2.4): the agent and the daemon share an
OS user.

Public: ``Actor``, ``actor``, ``MAX_AGENT_RERUNS``, ``check``, ``record``,
``agent_reruns``, ``discard_blocker``.
"""
from __future__ import annotations

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


def check(a: Actor, command: str, *, dispatch_id: str = "",
          approve_protected: bool = False) -> None:
    """Raise ActorError when an agent may not give *command* now (module docstring);
    the discard check needs the run's row and is ``discard_blocker``."""
    if not a.agent:
        return
    if command not in COMMANDS:
        raise ActorError(f"unknown command {command!r}")
    if not a.why:
        raise ActorError("say why (--why): the person sees the reason for each command an agent gives")
    if command == "return_land" and approve_protected:
        raise ActorError("these commits change paths the repository protects: the person approves "
                         "landing them (ask them, or they land it from the Swarm Out panel)")
    if command == "rerun" and agent_reruns(dispatch_id) >= MAX_AGENT_RERUNS:
        raise ActorError(f"an agent has re-sent this run {MAX_AGENT_RERUNS} times already: ask the "
                         "person whether to try again, change the task, or discard it")


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


def agent_reruns(dispatch_id: str) -> int:
    from core import swarm_out_settle
    try:
        rec = swarm_out_settle.load(dispatch_id)
    except swarm_out_settle.SettleError:
        return 0
    cmds = rec.get("commands") if isinstance(rec.get("commands"), list) else []
    return sum(1 for c in cmds if isinstance(c, dict) and c.get("command") == "rerun")


#: what each ``settle.why_not`` item means for a discard
_LOST = {"code": "commits that came back are not landed (land them or ask the person)",
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
    if row.get("running") or row.get("waiting"):
        return "the run is still running or waiting: stop it first, or ask the person"
    res = row.get("result")
    if not _final(res if isinstance(res, dict) else None):
        return "its final result is not here yet"
    settle = row.get("settle") if isinstance(row.get("settle"), dict) else {}
    lost = [_LOST[w] for w in (settle.get("why_not") or []) if w in _LOST]
    if lost:
        return "; ".join(lost) + ": ask the person before discarding"
    return None
