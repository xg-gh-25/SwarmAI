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

- any command from a session other than the one that answers for the run, or without a
  reason (``why``), except ``adopt``: a session of the same person takes over a run whose
  owner session no longer exists (``adopt_blocker``; never a Swarm Run's child, never
  another person's run). The person's New session in the Swarm Out tab moves that tab's
  open runs to the new session (``hand_over``);
- ``settle_without_files`` on a run held back by anything but returned files that are not
  here (``files_blocker``): the files it skips count as never coming;
- any command on a run no agent session is recorded as having dispatched (an older run);
- a command the panel does not offer the person on the run now (its ``/runs`` row's
  ``actions``): accept only while the run awaits it, on the final result the row shows
  (``ended_at``); stop only while it runs (``row_blocker``);
- "landed by hand" (``manual_landed``, the person's button) at all: it settles the run
  and releases its workspace with any commits not landed yet, on the person's word. An
  agent lands commits with ``return_land``; when that answers a conflict it lands them
  itself and records it with ``landed_by_hand``, which git here checks first (the named
  commits on the prepared branch, after the base, carrying the run's change to the
  returned files:
  core/swarm_out_settle.set_landed_by_hand), only where the run offers landing and never
  over a check that errored (``land_blocker``), as for ``return_land``;
- re-sending with a correction more than ``MAX_AGENT_RERUNS`` times (``reserve_rerun``
  counts it under the run's lock before it is sent): after that the agent takes the
  task over or discards it;
- landing a run's commits when a check the Brain declared for it errored on its last
  run (``land_blocker``): it re-runs the task with the failure to fix or takes it over.
  A check that failed on the run's base too (``pre_existing``) does not stop it;
- discarding while something would be lost: commits not landed that are not on this
  host either (``return-prepare`` takes them into ``refs/hive/...``: once every tip the
  result names now is there, discarding loses nothing; when it refused after it fetched the tip, the
  verified bundle it kept and that tip hold them here, ``commits_here``), a file not
  placed in the Brain, lessons not in, the run running, waiting or its Hive not answering, or its result not final
  (``discard_blocker``). A run whose Hive never started its session (``not_started``)
  has nothing to lose, and neither has one whose result can no longer come (its settle's
  ``result_lost``: an Elastic Workers round read whole after its machine could write
  nothing more, core/swarm_out_settle): what came back is here, nothing else can.

The agent lands commits that change the repository's own protected paths once it has
reviewed them (design 2026-11-02-hive-run-decision-record §2: the funnel lands on this
host or re-runs, never hands unlanded work to the person); the land entry keeps the
paths and its reason. The person-only paths (core/hive_return ``PERSON_ONLY``: git hooks,
filters and submodules, which make this machine run code on their own, and CI pipelines and
coding-agent configuration such as ``.github/workflows/``, ``.claude/``, ``.mcp.json`` and
AGENTS.md / CLAUDE.md at any depth) an agent never lands: it re-runs the task asking the
Hive to leave them out, or makes the change itself in its own session; the person may land
them. A repository's ``[hive_return]`` adds protected paths, it never removes these.

Every command, the agent's and the person's, is one entry of the run's decision record
(core/swarm_out_record: by, session, time, decision, reason, and the facts this host saw),
which the run card's Decisions tab shows. Like every "person only" in Swarm Out this is a
speed bump against the agent's own mistakes, not a boundary (ops-registry design §2.4):
the agent and the daemon share an OS user.

Public: ``Actor``, ``actor``, ``MAX_AGENT_RERUNS``, ``check``, ``row_blocker``,
``record``, ``record_wait_answer``, ``reserve_rerun``, ``release_rerun``, ``agent_reruns``,
``discard_blocker``, ``land_blocker``, ``files_blocker``, ``adopt_blocker``, ``adopt``,
``hand_over``.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

MAX_AGENT_RERUNS = 2
#: commands no one gives on a Swarm Run's child (design 2026-10-31-swarm-run §5.3)
CHILD_REFUSED = frozenset({"return_land", "manual_landed", "landed_by_hand"})
#: commands on a child that are its lead run's only (§5.9: the person stops or discards)
CHILD_LEAD_ONLY = frozenset({"accept", "rerun"})
#: what the lead run no longer does on a child once it has ended
CHILD_AFTER_END = frozenset({"accept", "rerun", "return_prepare"})
MAX_WHY_CHARS = 400
COMMANDS = ("accept", "manual_landed", "return_prepare", "return_land", "landed_by_hand", "rerun", "stop",
            "discard", "settle_without_files", "adopt")


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
          "return_land": ("accept", "land"), "landed_by_hand": ("accept", "land"),
          "rerun": ("rerun", None), "stop": ("stop", None), "discard": ("discard", None)}


def row_blocker(a: Actor, command: str, row: Optional[dict[str, Any]],
                ended_at: Optional[str] = None) -> Optional[str]:
    """Why an agent may not give *command* on the run whose ``/runs`` row is *row*, else
    None: another session answers for it, or the panel does not offer the person that
    command on it now (module docstring)."""
    from core import swarm_run
    if command in CHILD_REFUSED and swarm_run.is_child(row):
        # a Swarm Run's child is material for its lead run, never landed here on its own
        # (design 2026-10-31-swarm-run §6 point 2): the lead run lands its own change
        return ("this run is a child of Swarm Run " + str(row.get("parent_run"))
                + ": its commits are material for that run, not landed on their own")
    if command in CHILD_LEAD_ONLY and swarm_run.is_child(row) and not a.agent:
        return ("this run is a child of Swarm Run " + str(row.get("parent_run"))
                + ": its lead run takes or corrects it; stop or discard it here, and the lead run is woken")
    if not a.agent:
        return None
    if not isinstance(row, dict):
        return "the run is not in the list of runs now; try again in a moment"
    if command == "adopt":
        return adopt_blocker(a, row)         # given by a session that does not answer for it yet
    owner = swarm_run.owner_of(row)
    if not owner and swarm_run.is_child(row):
        return ("the Swarm Run this run belongs to cannot be read: nobody gives commands on it now "
                "(the Conductor closes it if the run stays unreadable)")
    if not owner:
        # a run from before the dispatching session was recorded: no agent owns it
        return "no agent session is recorded as having dispatched this run: the person gives commands on it"
    if a.session != owner:
        return "another session answers for this run: only that session (or the person) gives commands on it"
    if command in CHILD_AFTER_END and swarm_run.is_child(row):
        found = swarm_run.lead(row.get("parent_run"))
        if found is not None and found.ended:
            # the run has its delivery: what its children did no longer goes into it, and
            # the Conductor closes them (core/swarm_run_conductor)
            return (f"its Swarm Run {found.run_id} has ended ({found.status}): its children are "
                    "closed with it; stop or discard this one")
    if command == "manual_landed":
        # the person's "landed by hand" settles the run and releases its workspace with
        # whatever commits are not landed yet, unchecked: an agent records its own hand
        # landing with landed_by_hand, which git checks first
        return ("an agent lands commits with return-land, and records a landing it made by hand with "
                "`swarm_out landed-by-hand` (git checks the commits); the manual-landed button is the person's")
    if command == "settle_without_files":
        return files_blocker(row)
    key, variant = _NEEDS[command]
    listed = row.get("actions")
    if swarm_run.is_child(row):
        # the lead run's commands on its child: taking it is ``accept`` variant ``child``
        # (core/swarm_out_settle.set_handled)
        if command == "return_prepare":
            return None                  # only into refs/hive here: the take needs them first
        listed = row.get("lead_actions")
        variant = "child" if command == "accept" else variant
    offered = {x.get("key"): x.get("variant") for x in listed or [] if isinstance(x, dict)}
    if key not in offered or (variant is not None and offered[key] != variant):
        stage = row.get("stage") or "?"
        return f"the run does not offer `{command}` now (stage {stage}); see its actions in `runs`"
    if command == "accept":
        from core.swarm_out_settle import _final
        res = row.get("result") if isinstance(row.get("result"), dict) else None
        if not _final(res) or not ended_at or res.get("ended_at") != ended_at:
            return "accept the run's final result as `runs` shows it now (its result's ended_at)"
    return None


def files_blocker(row: Optional[dict[str, Any]]) -> Optional[str]:
    """Why the run may not be settled without its files now, else None: only a run whose
    one remaining blocker is returned files that are not here (its ``/runs`` row's
    ``settle.why_not`` is exactly ``files``), the person's command or the agent's alike."""
    if not isinstance(row, dict):
        return "the run is not in the list of runs now; try again in a moment"
    settle = row.get("settle") if isinstance(row.get("settle"), dict) else {}
    if settle.get("settled"):
        return "the run is settled"
    why_not = list(settle.get("why_not") or [])
    if why_not != ["files"]:
        return ("settling without files is for a run held back only by returned files that are "
                "not here; this one waits on: " + (", ".join(why_not) or "nothing (it settles by itself)"))
    return None


def adopt_blocker(a: Actor, row: Optional[dict[str, Any]]) -> Optional[str]:
    """Why session *a* may not take this run over now, else None. Only an agent session,
    of the person the run is (its ``dispatched_person``; none recorded: the install's
    owner), on a run that is not a Swarm Run's child (its lead run's owner answers for
    it) and not settled, whose current owner session no longer exists (core/swarm_run
    ``owner_of``; ``attention_authority.session_confirmed``)."""
    from core import attention_authority, swarm_run
    if not a.agent:
        return "the person hands a session's runs to a new one with New session in the Swarm Out tab"
    if not a.session:
        return "this command has no session id: run it as one plain command in your Bash tool"
    if not isinstance(row, dict):
        return "the run is not in the list of runs now; try again in a moment"
    if swarm_run.is_child(row):
        return ("this run is a child of Swarm Run " + str(row.get("parent_run"))
                + ": whoever drives that run answers for it; take the lead run over instead")
    if ((row.get("settle") or {}) if isinstance(row.get("settle"), dict) else {}).get("settled"):
        return "the run is settled: there is nothing left to answer for"
    owner = swarm_run.owner_of(row)
    if owner == a.session:
        return "this session already answers for the run"
    if owner and attention_authority.session_confirmed(owner):
        return ("the session that answers for this run still exists: it gives the commands on it "
                "(the person can open it from history)")
    exists, person = attention_authority.session_person(a.session)
    if not exists:
        return "this session is not in the session table yet; try again after its first turn"
    if run_person(row) != person:
        return "this run is another person's (or whose it is was not known when it was sent): only their own sessions take it over"
    return None


#: the ``dispatched_person`` of a run whose dispatching session could not be read when it
#: was sent: no session matches it, so nobody adopts it (core/swarm_out ``_person_of``)
UNKNOWN_PERSON = "?unknown"


def run_person(row: dict[str, Any]) -> str:
    """The person a run is: its ``dispatched_person``; none recorded is the install's
    owner ("": no session had a user id before the field existed, and the owner's still
    have none)."""
    return str(row.get("dispatched_person") or "")


def adopt(dispatch_id: str, session: str, previous: Optional[str], by: str, why: str) -> None:
    """*session* answers for the run from now on (``swarm_run.set_owner``), kept as one
    ``adopt`` entry of its decision record (who, why, the session it came from). The
    caller checked who may (``adopt_blocker``, ``hand_over``)."""
    from core import swarm_out_record, swarm_run
    if not session or session == "None":
        raise ActorError("no session to hand the run to")
    swarm_run.set_owner(dispatch_id, session, previous, by, swarm_out_record._now())
    swarm_out_record.append_quietly(dispatch_id, by, "adopt", why, session if by == "agent" else None,
                                    "adopt", facts={**swarm_out_record.observed(dispatch_id),
                                                    "from_session": previous})


#: the reason a hand-over records on each run it moves
HAND_OVER_WHY = "the person started a new session in the Swarm Out tab"


def hand_over(from_session: Optional[str], to_session: Optional[str],
              rows: list[dict[str, Any]]) -> list[str]:
    """The person started a new session in the Swarm Out tab: every open run the old
    session answers for (not settled, not a Swarm Run's child) answers to the new one, of
    the same person. Returns the dispatch ids moved. Raises ActorError on a bad pair."""
    from core import attention_authority, swarm_out_settle, swarm_run
    from core.app_session_id import valid_app_session_id
    if not (valid_app_session_id(from_session) and valid_app_session_id(to_session)) \
            or from_session == to_session:
        raise ActorError("name the old session and the new one")
    exists, person = attention_authority.session_person(str(to_session))
    if not exists:
        raise ActorError("the new session is not in the session table yet")
    _old, old_person = attention_authority.session_person(str(from_session))
    if _old and old_person != person:
        raise ActorError("the two sessions are different people's")
    moved = []
    for r in rows:
        did = r.get("dispatch_id") if isinstance(r, dict) else None
        if not isinstance(did, str) or swarm_run.is_child(r) or swarm_run.owner_of(r) != from_session:
            continue
        if run_person(r) != person:
            continue                         # never across people
        try:
            if swarm_out_settle.load(did).get("settled"):
                continue
        except swarm_out_settle.SettleError:
            continue
        try:
            adopt(did, str(to_session), str(from_session), "person", HAND_OVER_WHY)
        except swarm_run.OwnerChanged:
            continue                         # another session took it over meanwhile
        moved.append(did)
    return moved


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


def _lessons_due() -> str:
    """When a refused discard can be tried again for lessons: a Hive sends a session's
    lessons with its ``closed`` record once it has reviewed the session
    (core/hive_records.seal_end), and that review waits for the session to be quiet
    (core/ingestion/s2_jobs). Without this, a refusal minutes after the run ended reads
    as a run that can never be discarded."""
    from core.ingestion import s2_jobs
    quiet = int(s2_jobs.REVIEW_DELAY.total_seconds() // 60) + 2     # + the idle hook's 120 s
    most = int(s2_jobs.MAX_WAIT.total_seconds() // 3600)
    return (f" (its Hive sends them when it reviews the session, about {quiet} minutes after "
            f"the session's last turn and at most {most} hours later; `watch` reports when "
            "they arrive, then discard again)")


def land_blocker(dispatch_id: str) -> Optional[str]:
    """Why an agent may not land this run's commits now, else None: a check the Brain
    declared for the run whose latest run on the result errored (core/swarm_out_results
    ``checks``: the Hive's own exit code). A change whose own check last failed is not
    landed by an agent: it re-runs the task with the failure to fix, takes it over, or
    the person lands it. ``pre_existing`` does not refuse: the check failed on the
    commit the task started from too (the Hive ran it there, scripts/task_checks), so the
    failure is not the change's; the card still shows it. A check that failed on the
    change and passed on the base, or has no base result, is ``errored`` and refuses.
    ``not_seen`` and ``unknown`` do not refuse: the Host found no
    call that runs the check, which is not "it did not run" (a check run through a script
    is not seen). A result this host cannot read refuses: the agent does not land what
    it cannot see the checks of; it waits for the next refresh, or the person lands it."""
    from core import swarm_out_results
    unknown = ("the run's check results cannot be read on this host now: an agent does not land "
               "without them; try again after the next refresh, or leave it for the person to land")
    try:
        kept = swarm_out_results.load(dispatch_id)
    except Exception as exc:  # noqa: BLE001 -- not knowing the checks is not "no check failed"
        logging.getLogger(__name__).warning("swarm_out_actor: result of %s unreadable: %s", dispatch_id, exc)
        return unknown
    res = kept.get("result") if isinstance(kept, dict) else None
    if not isinstance(res, dict):
        return unknown
    checks = res.get("checks")
    failed = [str(c.get("id")) for c in checks or [] if isinstance(c, dict)
              and swarm_out_results.check_status(c.get("status")) == "errored"]
    if not failed:
        return None
    return ("the run's own check errored on its last run (" + ", ".join(failed[:5])
            + "): an agent does not land a change whose check failed; re-run it with the failure "
            "to fix (`swarm_out rerun`), take it over, or leave it for the person to land")


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
    if row.get("state") != "stopped":
        # running, waiting, or unknown: its Hive did not answer the probe, so a turn may be
        # running there (read it with `swarm_out diagnose`)
        return ("the run is still running or waiting, or its Hive is not answering: stop it first, "
                "or wait (`swarm_out diagnose` says why its Hive does not answer)")
    res = row.get("result")
    settle = row.get("settle") if isinstance(row.get("settle"), dict) else {}
    if not _final(res if isinstance(res, dict) else None) and not settle.get("result_lost"):
        return "its final result is not here yet"
    funnel = row.get("funnel") if isinstance(row.get("funnel"), dict) else {}
    if isinstance(funnel.get("ask"), dict) and funnel["ask"].get("state") == "revert":
        return ("the person asked for a revert: undo what landed and report it with "
                "`swarm_out reverted`; when it cannot match exactly, say so in the session "
                "(the run stays open until its records expire)")
    why_not = list(settle.get("why_not") or [])
    if "code" in why_not and commits_here(str(row.get("dispatch_id") or ""), res):
        why_not.remove("code")       # every returned tip is in refs/hive here: nothing is lost
    taken = [_LOST[w] for w in why_not if w in _LOST and w != "lessons"]   # the agent takes these
    lost = ["; ".join(taken) + ": take them first"] if taken else []
    if "files" in why_not:
        # files that can no longer come: decide the run, then settle it without them
        lost.append("files that cannot come any more: once the run is decided, `settle-without-files` "
                    "pulls once more and settles it without the rest")
    if "lessons" in why_not:
        lost.append(_LOST["lessons"] + _lessons_due())                      # these come by themselves
    return "; ".join(lost) or None


def commits_here(dispatch_id: str, res: Any) -> bool:
    """Every repository that brought commits back has them on this host: the commits the
    run's result names now were taken back (core/hive_return.prepare, ``_taken_back``) and
    its ``refs/hive/<dispatch>/<key>`` ref still names the tip, or taking them back was
    refused after their tip was fetched and the bundle the result names is kept here,
    verifies in the checkout and has its tip there (core/hive_return.kept_here). A
    repository whose commits were never sealed into a bundle, whose bundle never reached
    this host, or whose take-back is of an earlier turn than the result's, has them only
    on the Hive."""
    from core import hive_return
    repos = [r for r in (((res or {}).get("work") or {}).get("repos") or []) if isinstance(r, dict)]
    checked = 0
    for r in repos:
        b = r.get("bundle")
        has_bundle = isinstance(b, dict) and (b.get("available") or b.get("sha256")) and isinstance(r.get("dir"), str)
        if not has_bundle:
            if (r.get("commits_total") or 0) > 0:
                return False
            continue
        if not (_taken_back(dispatch_id, r["dir"]) or hive_return.kept_here(dispatch_id, r["dir"])):
            return False
        checked += 1
    return checked > 0          # nothing to check is not proof the commits are here


def _taken_back(dispatch_id: str, rdir: str) -> bool:
    """The repository's take-back is ``prepared`` or ``landed``, it is of the commits the
    run's kept result names now, and its ref still names the tip it recorded. The state
    does not say which turn's bundle it took, so the Hive commit it took (``hive_tip``
    when its base was mapped and ``tip`` is the replayed copy, else ``tip``; ``land``
    keeps both) is compared with the head the result names for the repository: a re-run
    whose later turn sealed new commits that nobody took back is not "here" from the
    earlier turn's take-back. The result is the one ``prepare`` takes the bundle from
    (core/swarm_out_results), not the caller's copy, so states written before this
    check count the same way."""
    from core import hive_bundle, hive_return, swarm_out_results
    import subprocess
    try:
        st = hive_return.load(dispatch_id, hive_bundle.key(rdir)) or {}
    except hive_return.ReturnError:
        return False
    if st.get("status") not in ("prepared", "landed") or not st.get("repo") or not st.get("ref"):
        return False
    took = st.get("hive_tip") or st.get("tip")
    if not isinstance(took, str) or not took or took != _result_head(swarm_out_results.load(dispatch_id), rdir):
        return False                 # an earlier turn's commits, or the result names none
    try:
        out = subprocess.run(["git", "-C", str(st["repo"]), "rev-parse", "--verify", "--quiet",
                              f"{st['ref']}^{{commit}}"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip() == st.get("tip")


def _result_head(rec: Any, rdir: str) -> Optional[str]:
    """The head commit the kept result *rec* names for the repository declared as *rdir*,
    else None (no result, no such repository, or more than one entry for it)."""
    res = rec.get("result") if isinstance(rec, dict) else None
    work = res.get("work") if isinstance(res, dict) else None
    repos = work.get("repos") if isinstance(work, dict) else None
    heads = [r.get("head") for r in repos or [] if isinstance(r, dict) and r.get("dir") == rdir] \
        if isinstance(repos, list) else []
    return heads[0] if len(heads) == 1 and isinstance(heads[0], str) and heads[0] else None
