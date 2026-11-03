"""Swarm Out routes (Host only): list Hives, dispatch a task to one, list runs, answer questions.

Thin layer over ``core/swarm_out.py``. Every route refuses on a Hive (a Hive must not
dispatch to other Hives or read their credentials, same rule as ``routers/hive.py``).
Errors the person can act on come back as 400 with the module's safe message; anything
else is a generic 500 with no internal detail.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from core import swarm_out

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_desktop() -> None:
    if os.environ.get("SWARMAI_MODE") == "hive":
        raise HTTPException(status_code=403, detail="Swarm Out is not available on a Hive")


class DispatchBody(BaseModel):
    # None: the daemon picks one (swarm_out.pick_hive)
    hive: Optional[str] = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=swarm_out.MAX_TITLE_CHARS * 2)
    task: str = Field(min_length=1, max_length=swarm_out.MAX_TASK_CHARS)
    origin: str = Field(default="swarm-out", max_length=40)
    # the Brain whose Project Agent the task goes under ("General" = the Runner Agent),
    # the mode chosen from its definition, how this task runs (native or
    # pipeline:<profile>, core/task_spec.parse_execution) and why, and what done looks
    # like. The repositories come from the definition (core/task_spec), not the caller.
    brain: Optional[str] = Field(default=None, max_length=80)
    mode: Optional[str] = Field(default=None, max_length=80)
    execution: Optional[str] = Field(default=None, max_length=40)
    why: Optional[str] = Field(default=None, max_length=600)
    # the agent's reason the task needs a private network (it adds a refusal, §2.2)
    needs_private: Optional[str] = Field(default=None, max_length=600)
    # what comes back: answer / document / commits (core/task_spec.RETURNS)
    returns: Optional[str] = Field(default=None, max_length=20)
    acceptance: Optional[str] = Field(default=None, max_length=4000)
    # the revision /spec showed the person: a definition changed since is refused
    revision: Optional[str] = Field(default=None, max_length=64)
    # one or two sentences of what the task is, for the dispatch card
    brief: Optional[str] = Field(default=None, max_length=swarm_out.MAX_BRIEF_CHARS * 2)
    # the agent session that dispatched it (the CLI's SWARM_APP_SESSION_ID): it follows
    # the run (design 2026-10-31-swarm-out-agent-owns-its-runs §2.1)
    session: Optional[str] = Field(default=None, max_length=80)
    # where the task starts instead of each checkout's HEAD: ["<ref>"] or
    # ["<repo>=<ref>", ...] (core/task_spec.build; design 2026-10-31-swarm-run §5.1)
    base: Optional[list[str]] = Field(default=None, max_length=8)


class ActorBody(BaseModel):
    """Who gives a command on a run (core/swarm_out_actor): ``person`` from the App,
    anything else is an agent's (the CLI says ``agent``, with its session and why)."""
    by: Optional[str] = Field(default=None, max_length=10)
    session: Optional[str] = Field(default=None, max_length=80)
    why: Optional[str] = Field(default=None, max_length=1000)


class RouteBody(BaseModel):
    text: str = Field(min_length=1, max_length=swarm_out.MAX_TASK_CHARS)
    brain: Optional[str] = Field(default=None, max_length=80)
    # the deterministic signals of detect_active_project: an open file, the project
    # whose bound repository the calling session works in
    editor_file_path: Optional[str] = Field(default=None, max_length=1000)
    session_bound_project: Optional[str] = Field(default=None, max_length=80)


class SpecBody(BaseModel):
    brain: str = Field(min_length=1, max_length=80)
    task: str = Field(min_length=1, max_length=swarm_out.MAX_TASK_CHARS)
    acceptance: str = Field(default="", max_length=4000)
    mode: Optional[str] = Field(default=None, max_length=80)
    execution: Optional[str] = Field(default=None, max_length=40)
    why: Optional[str] = Field(default=None, max_length=600)
    returns: Optional[str] = Field(default=None, max_length=20)
    needs_private: Optional[str] = Field(default=None, max_length=600)
    base: Optional[list[str]] = Field(default=None, max_length=8)


class AnswerBody(BaseModel):
    answers: Optional[dict[str, str]] = None
    decision: Optional[str] = None
    # an approval names what the card showed (core.swarm_out.answer)
    shown_sha256: Optional[str] = Field(default=None, max_length=64)
    # a pipeline decision is answered with one of its option ids
    option_id: Optional[str] = Field(default=None, max_length=200)


async def _call(coro) -> Any:
    try:
        return await coro
    except swarm_out.SwarmOutRefused as e:
        # refused at intake: nothing was sent (dispatch-center §2.3)
        raise HTTPException(status_code=422, detail={"message": str(e), "refused": True, "private": e.private})
    except swarm_out.SwarmOutMaybeSent as e:
        # the task may be running on the Hive: not a plain refusal (core/swarm_out_card)
        raise HTTPException(status_code=504, detail=str(e))
    except swarm_out.SwarmOutError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.exception("swarm_out route failed")
        raise HTTPException(status_code=500, detail="Swarm Out request failed")


@router.post("/dispatch")
async def dispatch(body: DispatchBody) -> dict[str, Any]:
    _require_desktop()
    if not body.brain:
        raise HTTPException(status_code=400, detail=(
            "say which Brain the task belongs to (route it first); General sends it to the "
            "Runner Agent"))
    if not body.revision:
        raise HTTPException(status_code=400, detail="pass the revision the spec showed (run spec first)")
    return await _call(swarm_out.dispatch_spec(body.hive or None, body.title, body.brain, body.task,
                                               body.acceptance or "", body.mode, body.execution,
                                               body.origin, seen_revision=body.revision,
                                               brief=body.brief or "", why=body.why or "",
                                               returns=body.returns, needs_private=body.needs_private or "",
                                               dispatched_from=body.session, base=body.base))


class ReturnBody(ActorBody):
    url: Optional[str] = Field(default=None, max_length=300)
    key: Optional[str] = Field(default=None, max_length=16)
    tip: Optional[str] = Field(default=None, max_length=64)
    summary: Optional[str] = Field(default=None, max_length=8000)


def _actor(body: "Optional[ActorBody]"):
    from core import swarm_out_actor
    return swarm_out_actor.actor(body.by if body else None, body.session if body else None,
                                 body.why if body else None)


async def _allowed(actor, command: str, dispatch_id: str,
                   ended_at: Optional[str] = None) -> None:
    """Refuse (403) what an agent may not do on this run now (core/swarm_out_actor): the
    request's own limits, then the run's row as /runs has it (which session dispatched
    it, what the panel offers on it, for a discard what would be lost)."""
    from core import swarm_out_actor
    try:
        swarm_out_actor.check(actor, command, dispatch_id=dispatch_id)
    except swarm_out_actor.ActorError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not actor.agent:
        return
    row = await _call(swarm_out.run_row(dispatch_id))
    why = swarm_out_actor.row_blocker(actor, command, row, ended_at)
    if why is None and command == "discard":
        import asyncio
        why = await asyncio.to_thread(swarm_out_actor.discard_blocker, row)
    if why:
        raise HTTPException(status_code=403, detail=f"an agent may not {command.replace('_', ' ')} this run now: {why}")


def _recorded(actor, command: str, dispatch_id: str) -> None:
    """Keep the command, the agent's or the person's, as one entry of the run's decision
    record (core/swarm_out_record, after it succeeded); a failure to keep it does not
    undo the command."""
    from core import swarm_out_actor
    try:
        swarm_out_actor.record(dispatch_id, actor, command)
    except Exception:  # noqa: BLE001 — the command happened; only its record is missing
        logger.warning("swarm_out: command not recorded for %s", dispatch_id, exc_info=True)


async def _return(fn, *args) -> dict[str, Any]:
    from core import hive_return
    try:
        res = fn(*args)
        if hasattr(res, "__await__"):
            res = await res
    except hive_return.ReturnError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except swarm_out.SwarmOutError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("swarm_out return failed")
        raise HTTPException(status_code=500, detail="taking the commits back failed")
    swarm_out._refresh_soon()                  # a landing changes the run: settle it now
    return res


@router.post("/runs/{dispatch_id}/return/prepare")
async def return_prepare(dispatch_id: str, body: ReturnBody) -> dict[str, Any]:
    """Take one declared repository's commits back from the Hive and check them
    (core/hive_return): nothing lands. The panel shows the result for review."""
    _require_desktop()
    from core import hive_return
    if not body.url:
        raise HTTPException(status_code=400, detail="name the repository")
    actor = _actor(body)
    await _allowed(actor, "return_prepare", dispatch_id)
    out = await _return(hive_return.prepare, dispatch_id, body.url)
    _recorded(actor, "return_prepare", dispatch_id)
    return out


@router.get("/runs/{dispatch_id}/return/file")
async def return_file(dispatch_id: str, url: str = "", path: str = "") -> dict[str, Any]:
    """Both versions of one file the prepared review lists (core/hive_return.file_versions):
    read from git's objects on this machine, nothing written. A path the review does not
    list is 400; commits not taken back for review yet are 409."""
    _require_desktop()
    import asyncio
    from core import hive_return
    if not url or len(url) > 300:
        raise HTTPException(status_code=400, detail="name the repository")
    try:
        return await asyncio.to_thread(hive_return.file_versions, dispatch_id, url, path)
    except hive_return.ReturnPathError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except hive_return.ReturnError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except swarm_out.SwarmOutError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("swarm_out return file failed")
        raise HTTPException(status_code=500, detail="reading the file's versions failed")


@router.post("/runs/{dispatch_id}/return/land")
async def return_land(dispatch_id: str, body: ReturnBody) -> dict[str, Any]:
    """Land the prepared commits: the panel's button, or the dispatching agent's command.
    The agent lands the repository's protected paths it reviewed (design
    2026-11-02-hive-run-decision-record §2: the funnel lands or re-runs, it never hands
    unlanded work to the person; the land entry keeps the paths and the reason), but
    never the git hooks and filters that run code on this machine (``allow_git_exec``
    False: it re-runs without them, or makes the change itself)."""
    _require_desktop()
    import asyncio
    from core import hive_return
    actor = _actor(body)
    await _allowed(actor, "return_land", dispatch_id)
    out = await _return(asyncio.to_thread, hive_return.land, dispatch_id, body.key or "", body.tip or "",
                        not actor.agent, body.summary)
    _recorded(actor, "return_land", dispatch_id)
    return out


@router.post("/route")
async def route(body: RouteBody) -> dict[str, Any]:
    """Which Brain a task belongs to (core.task_spec.route)."""
    _require_desktop()
    import asyncio
    from core import task_spec
    from core.project_registry import get_swarmws
    try:
        return await asyncio.to_thread(task_spec.route, get_swarmws(), body.text, body.brain,
                                       body.editor_file_path, body.session_bound_project)
    except task_spec.SpecError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/spec")
async def spec(body: SpecBody) -> dict[str, Any]:
    """The Task Spec the dispatch would send, its admission and the project section:
    what the confirmation shows. Nothing is sent."""
    _require_desktop()
    import asyncio
    from core import task_spec
    from core.project_registry import get_swarmws
    try:
        s = await asyncio.to_thread(task_spec.build, get_swarmws(), body.brain, body.task,
                                    body.acceptance, body.mode, body.execution, body.why or "", "hive",
                                    returns=body.returns, needs_private=body.needs_private or "",
                                    base=body.base)
    except task_spec.SpecError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"spec": {k: v for k, v in s.items() if k != "task"},
            "refusals": task_spec.admit(s), "segment": task_spec.segment(s)}


@router.get("/hives")
async def hives() -> dict[str, Any]:
    _require_desktop()
    return await _call(swarm_out.hives())


@router.get("/fleet")
async def fleet() -> dict[str, Any]:
    _require_desktop()
    return await _call(swarm_out.fleet())


class VerdictBody(ActorBody):
    value: str = Field(max_length=10)
    note: str = Field(default="", max_length=4000)
    # the result the card showed (its ended_at): a newer turn voids the verdict
    ended_at: Optional[str] = Field(default=None, max_length=40)


async def _settle(fn, *args) -> dict[str, Any]:
    import asyncio
    from core import swarm_out_settle
    try:
        out = await asyncio.to_thread(fn, *args)
    except swarm_out_settle.SettleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    swarm_out._refresh_soon()                  # settled now, not at the next look
    return out


@router.post("/runs/{dispatch_id}/verdict")
async def run_verdict(dispatch_id: str, body: VerdictBody) -> dict[str, Any]:
    """The pass on an answer or a document (run-review §1.1), the person's or the
    dispatching agent's (core/swarm_out_actor); on commits the dispatching agent landed,
    its judgment of them (design 2026-11-02-hive-run-decision-record §1, gate 3): that
    entry is what closes the funnel, so a failure to keep it is the command's failure."""
    _require_desktop()
    import asyncio
    from core import swarm_out_actor, swarm_out_settle
    actor = _actor(body)
    await _allowed(actor, "accept", dispatch_id, ended_at=body.ended_at)
    out = await _settle(swarm_out_settle.set_verdict, dispatch_id, body.value, body.note or actor.why,
                        body.ended_at)
    if isinstance(out, dict) and out.get("judged") is True:
        try:                                   # the judgment of a landing: the entry is the decision
            await asyncio.to_thread(swarm_out_actor.record, dispatch_id, actor, "accept")
        except swarm_out_settle.SettleError as e:
            raise HTTPException(status_code=400, detail=str(e))
        swarm_out._refresh_soon()
        return {"judged": True}
    _recorded(actor, "accept", dispatch_id)
    return out


@router.post("/runs/{dispatch_id}/manual-landed")
async def run_manual_landed(dispatch_id: str, body: Optional[ActorBody] = None) -> dict[str, Any]:
    """The commits were landed by hand: the code is handled and the task passes."""
    _require_desktop()
    from core import swarm_out_settle
    actor = _actor(body)
    await _allowed(actor, "manual_landed", dispatch_id)
    out = await _settle(swarm_out_settle.set_manual_landed, dispatch_id)
    _recorded(actor, "manual_landed", dispatch_id)
    return out


class RerunBody(ActorBody):
    text: str = Field(min_length=1, max_length=swarm_out.MAX_TASK_CHARS)


class DiscardBody(ActorBody):
    note: str = Field(default="", max_length=4000)


@router.post("/runs/{dispatch_id}/rerun")
async def run_rerun(dispatch_id: str, body: RerunBody) -> dict[str, Any]:
    """Re-run with a correction: the text goes into the run's own Hive session."""
    _require_desktop()
    from core import swarm_out_actor
    actor = _actor(body)
    await _allowed(actor, "rerun", dispatch_id)
    try:
        # an agent's re-send is counted before it is sent (two at once cannot both pass)
        reserved = swarm_out_actor.reserve_rerun(dispatch_id, actor)
    except swarm_out_actor.ActorError as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        out = await _call(swarm_out.rerun(dispatch_id, body.text))
        if not actor.agent:
            _recorded(actor, "rerun", dispatch_id)   # an agent's re-send is its reservation
        return out
    except HTTPException as e:
        # refused before anything was sent: it does not count. A 504 (it may have
        # reached the Hive) and a cancelled request keep counting.
        if e.status_code in (400, 403, 422):
            try:
                swarm_out_actor.release_rerun(dispatch_id, reserved)
            except Exception:  # noqa: BLE001 — the command's own error is the answer
                logger.warning("swarm_out: a re-send reservation was not released", exc_info=True)
        raise


@router.post("/runs/{dispatch_id}/stop")
async def run_stop(dispatch_id: str, body: Optional[ActorBody] = None) -> dict[str, Any]:
    """Stop the run's current turn; its session and workspace stay."""
    _require_desktop()
    actor = _actor(body)
    await _allowed(actor, "stop", dispatch_id)
    out = await _call(swarm_out.stop_run(dispatch_id))
    _recorded(actor, "stop", dispatch_id)
    return out


@router.post("/runs/{dispatch_id}/discard")
async def run_discard(dispatch_id: str, body: DiscardBody) -> dict[str, Any]:
    """Discard the run: stop it, close it, release its Hive workspace. Final. An agent
    may discard only when nothing would be lost (core/swarm_out_actor.discard_blocker)."""
    _require_desktop()
    actor = _actor(body)
    await _allowed(actor, "discard", dispatch_id)
    out = await _call(swarm_out.discard_run(dispatch_id, body.note or actor.why))
    _recorded(actor, "discard", dispatch_id)
    return out


class CardBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)


@router.post("/runs/{dispatch_id}/card")
async def wait_card(dispatch_id: str, body: CardBody) -> dict[str, Any]:
    """The AskUserQuestion input that asks the person one of the run's waits in the
    chat (core/swarm_out_wait_card). The person's pick is forwarded by the daemon."""
    _require_desktop()
    from core import swarm_out_wait_card
    try:
        return await swarm_out_wait_card.card(dispatch_id, body.request_id)
    except (swarm_out_wait_card.CardError, swarm_out.SwarmOutError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("swarm_out card failed")
        raise HTTPException(status_code=500, detail="Swarm Out request failed")


class WatchBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=20)
    since: Optional[str] = Field(default=None, max_length=8000)
    session: Optional[str] = Field(default=None, max_length=80)
    wait_s: Optional[float] = Field(default=None, ge=0)


@router.post("/watch")
async def watch(body: WatchBody) -> dict[str, Any]:
    """Wait until one of the runs changes, or ``wait_s`` (at most WATCH_MAX_S) passes
    (core/swarm_out_follow). Read only: Host fields, no text a Hive wrote."""
    _require_desktop()
    from core import swarm_out_follow
    wait = min(body.wait_s if body.wait_s is not None else swarm_out_follow.WATCH_MAX_S,
               swarm_out_follow.WATCH_MAX_S)
    return await _call(swarm_out_follow.watch(body.ids, body.since, body.session, wait))


@router.get("/runs")
async def runs() -> dict[str, Any]:
    _require_desktop()
    return await _call(swarm_out.list_runs(swarm_out.SNAPSHOT_FRESH_S))


class AskPersonBody(BaseModel):
    """The dispatching agent hands its landed result to the person (the CLI's
    ``ask-person``): what they decide, its own judgment, why it cannot decide."""
    session: Optional[str] = Field(default=None, max_length=80)
    decide: str = Field(min_length=1, max_length=2000)
    judgment: str = Field(min_length=1, max_length=2000)
    why: str = Field(min_length=1, max_length=2000)


class AskAnswerBody(BaseModel):
    pick: str = Field(max_length=10)
    note: str = Field(default="", max_length=4000)


class RevertedBody(BaseModel):
    session: Optional[str] = Field(default=None, max_length=80)
    commits: list[str] = Field(default_factory=list, max_length=20)
    why: str = Field(min_length=1, max_length=2000)


async def _record_step(fn, *args) -> dict[str, Any]:
    import asyncio
    from core import swarm_out_record
    try:
        out = await asyncio.to_thread(fn, *args)
    except swarm_out_record.RecordError as e:
        raise HTTPException(status_code=409, detail=str(e))
    swarm_out._refresh_soon()
    return out


@router.post("/runs/{dispatch_id}/ask-person")
async def run_ask_person(dispatch_id: str, body: AskPersonBody) -> dict[str, Any]:
    """Hand a landed result the dispatching agent cannot judge to the person (design
    2026-11-02-hive-run-decision-record §1): only a result on this host; refused with
    nothing written otherwise."""
    _require_desktop()
    from core import swarm_out_record
    row = await _call(swarm_out.run_row(dispatch_id))
    return await _record_step(swarm_out_record.ask_person, dispatch_id, row, body.session,
                              body.decide, body.judgment, body.why)


@router.post("/runs/{dispatch_id}/ask-person/answer")
async def run_ask_answer(dispatch_id: str, body: AskAnswerBody) -> dict[str, Any]:
    """The person keeps what landed, or has it reverted (the person's only: ops registry)."""
    _require_desktop()
    from core import swarm_out_record
    return await _record_step(swarm_out_record.answer_ask, dispatch_id, body.pick, body.note)


@router.post("/runs/{dispatch_id}/reverted")
async def run_reverted(dispatch_id: str, body: RevertedBody) -> dict[str, Any]:
    """The dispatching agent carried out the person's revert: checked here before the
    run settles."""
    _require_desktop()
    from core import swarm_out_record
    row = await _call(swarm_out.run_row(dispatch_id))
    return await _record_step(swarm_out_record.reverted, dispatch_id, row, body.session,
                              body.commits, body.why)


@router.get("/runs/{dispatch_id}/result")
async def run_result(dispatch_id: str) -> dict[str, Any]:
    """One run's whole kept result, with the Hive's reply (the panel's card)."""
    _require_desktop()
    return await _call(swarm_out.run_result(dispatch_id))


@router.get("/runs/{dispatch_id}/diagnostics")
async def run_diagnostics(dispatch_id: str) -> dict[str, Any]:
    """Why the run's Hive session stopped, read from its Hive with a fixed read-only
    script over SSM (core/hive_diagnostics): kernel OOM kills, the service's stops, its
    memory, the session's error lines, and a deterministic list of findings."""
    _require_desktop()
    from core import hive_diagnostics

    async def read() -> dict[str, Any]:
        # a session id the read cannot use is a 400 with its reason; ``_call`` turns any
        # other exception into a generic 500
        try:
            return await hive_diagnostics.read(dispatch_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
    return await _call(read())


@router.post("/runs/{dispatch_id}/refresh")
async def refresh_result(dispatch_id: str) -> dict[str, Any]:
    """Check one run's Hive now."""
    _require_desktop()
    return await _call(swarm_out.refresh_result(dispatch_id))


class HostAnswerBody(BaseModel):
    """The dispatching agent's own answer to one of its run's waits (the CLI's
    ``answer``; design 2026-10-31-hive-approvals): the card's question text → one of its
    option labels, and why."""
    session: Optional[str] = Field(default=None, max_length=80)
    why: str = Field(min_length=1, max_length=1000)
    # a card with one question: the label picked; with more, card question → label
    pick: Optional[str] = Field(default=None, max_length=600)
    answers: Optional[dict[str, str]] = Field(default=None, max_length=4)

    @model_validator(mode="after")
    def _one_answer(self) -> "HostAnswerBody":
        if (self.pick is None) == (self.answers is None):
            raise ValueError("give exactly one of pick and answers")
        return self


class EscalateBody(BaseModel):
    session: Optional[str] = Field(default=None, max_length=80)
    why: str = Field(min_length=1, max_length=1000)


@router.post("/runs/{dispatch_id}/waits/{request_id}/answer-host")
async def wait_answer_host(dispatch_id: str, request_id: str, body: HostAnswerBody) -> dict[str, Any]:
    """The dispatching agent answers a wait it answers for (core/swarm_out_wait_card
    ``answer_as_host``); recorded on the Hive as the host's answer, never the person's."""
    _require_desktop()
    from core import swarm_out_wait_card
    try:
        return await swarm_out_wait_card.answer_as_host(body.session, dispatch_id, request_id,
                                                        body.answers, body.why, pick=body.pick)
    except swarm_out_wait_card.CardError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except Exception:
        logger.exception("swarm_out host answer failed")
        raise HTTPException(status_code=500, detail="Swarm Out request failed")


@router.post("/runs/{dispatch_id}/waits/{request_id}/escalate")
async def wait_escalate(dispatch_id: str, request_id: str, body: EscalateBody) -> dict[str, Any]:
    """The dispatching agent hands a wait to the person (Needs You, with its reason)."""
    _require_desktop()
    from core import swarm_out_wait_card
    try:
        return await swarm_out_wait_card.escalate(body.session, dispatch_id, request_id, body.why)
    except swarm_out_wait_card.CardError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except Exception:
        logger.exception("swarm_out escalate failed")
        raise HTTPException(status_code=500, detail="Swarm Out request failed")


@router.post("/asks/{hive_id}/{session_id}/{request_id}/answer")
async def answer(hive_id: str, session_id: str, request_id: str, body: AnswerBody) -> dict[str, Any]:
    _require_desktop()
    return await _call(swarm_out.answer(hive_id, session_id, request_id,
                                        answers=body.answers, decision=body.decision,
                                        shown_sha256=body.shown_sha256,
                                        option_id=body.option_id))
