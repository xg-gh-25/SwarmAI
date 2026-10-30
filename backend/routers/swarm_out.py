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
from pydantic import BaseModel, Field

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


class AnswerBody(BaseModel):
    answers: Optional[dict[str, str]] = None
    decision: Optional[str] = None
    # an approval names what the card showed (core.swarm_out.answer)
    shown_sha256: Optional[str] = Field(default=None, max_length=64)


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
                                               returns=body.returns, needs_private=body.needs_private or ""))


class ReturnBody(BaseModel):
    url: Optional[str] = Field(default=None, max_length=300)
    key: Optional[str] = Field(default=None, max_length=16)
    tip: Optional[str] = Field(default=None, max_length=64)
    approve_protected: bool = False


async def _return(fn, *args) -> dict[str, Any]:
    from core import hive_return
    try:
        res = fn(*args)
        if hasattr(res, "__await__"):
            return await res
        return res
    except hive_return.ReturnError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except swarm_out.SwarmOutError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("swarm_out return failed")
        raise HTTPException(status_code=500, detail="taking the commits back failed")
    finally:
        swarm_out.invalidate_snapshot()        # a landing changes the run's next step


@router.post("/runs/{dispatch_id}/return/prepare")
async def return_prepare(dispatch_id: str, body: ReturnBody) -> dict[str, Any]:
    """Take one declared repository's commits back from the Hive and check them
    (core/hive_return): nothing lands. The panel shows the result for review."""
    _require_desktop()
    from core import hive_return
    if not body.url:
        raise HTTPException(status_code=400, detail="name the repository")
    return await _return(hive_return.prepare, dispatch_id, body.url)


@router.post("/runs/{dispatch_id}/return/land")
async def return_land(dispatch_id: str, body: ReturnBody) -> dict[str, Any]:
    """Land the prepared commits the person approved (the panel's button)."""
    _require_desktop()
    import asyncio
    from core import hive_return
    return await _return(asyncio.to_thread, hive_return.land, dispatch_id, body.key or "", body.tip or "",
                         body.approve_protected)


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
                                    returns=body.returns, needs_private=body.needs_private or "")
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


class VerdictBody(BaseModel):
    value: str = Field(max_length=10)
    note: str = Field(default="", max_length=4000)
    # the result the card showed (its ended_at): a newer turn voids the verdict
    ended_at: Optional[str] = Field(default=None, max_length=40)


async def _settle(fn, *args) -> dict[str, Any]:
    import asyncio
    from core import swarm_out_settle
    try:
        return await asyncio.to_thread(fn, *args)
    except swarm_out_settle.SettleError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        swarm_out.invalidate_snapshot()


@router.post("/runs/{dispatch_id}/verdict")
async def run_verdict(dispatch_id: str, body: VerdictBody) -> dict[str, Any]:
    """The person's pass on an answer or a document (run-review §1.1). Person-only:
    core/run_owner_gate refuses it from a command, as for every command on a run."""
    _require_desktop()
    from core import swarm_out_settle
    return await _settle(swarm_out_settle.set_verdict, dispatch_id, body.value, body.note, body.ended_at)


@router.post("/runs/{dispatch_id}/manual-landed")
async def run_manual_landed(dispatch_id: str) -> dict[str, Any]:
    """The person landed the commits by hand: the code is handled and the task passes."""
    _require_desktop()
    from core import swarm_out_settle
    return await _settle(swarm_out_settle.set_manual_landed, dispatch_id)


class RerunBody(BaseModel):
    text: str = Field(min_length=1, max_length=swarm_out.MAX_TASK_CHARS)


class DiscardBody(BaseModel):
    note: str = Field(default="", max_length=4000)


@router.post("/runs/{dispatch_id}/rerun")
async def run_rerun(dispatch_id: str, body: RerunBody) -> dict[str, Any]:
    """Re-run with a correction: the text goes into the run's own Hive session."""
    _require_desktop()
    return await _call(swarm_out.rerun(dispatch_id, body.text))


@router.post("/runs/{dispatch_id}/stop")
async def run_stop(dispatch_id: str) -> dict[str, Any]:
    """Stop the run's current turn; its session and workspace stay."""
    _require_desktop()
    return await _call(swarm_out.stop_run(dispatch_id))


@router.post("/runs/{dispatch_id}/discard")
async def run_discard(dispatch_id: str, body: DiscardBody) -> dict[str, Any]:
    """Discard the run: stop it, close it, release its Hive workspace. Final."""
    _require_desktop()
    return await _call(swarm_out.discard_run(dispatch_id, body.note))


@router.get("/runs")
async def runs() -> dict[str, Any]:
    _require_desktop()
    return await _call(swarm_out.list_runs(swarm_out.SNAPSHOT_FRESH_S))


@router.get("/runs/{dispatch_id}/result")
async def run_result(dispatch_id: str) -> dict[str, Any]:
    """One run's whole kept result, with the Hive's reply (the panel's card)."""
    _require_desktop()
    return await _call(swarm_out.run_result(dispatch_id))


@router.post("/runs/{dispatch_id}/refresh")
async def refresh_result(dispatch_id: str) -> dict[str, Any]:
    """Check one run's Hive now."""
    _require_desktop()
    return await _call(swarm_out.refresh_result(dispatch_id))


@router.post("/asks/{hive_id}/{session_id}/{request_id}/answer")
async def answer(hive_id: str, session_id: str, request_id: str, body: AnswerBody) -> dict[str, Any]:
    _require_desktop()
    return await _call(swarm_out.answer(hive_id, session_id, request_id,
                                        answers=body.answers, decision=body.decision,
                                        shown_sha256=body.shown_sha256))
