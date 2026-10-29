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
from pydantic import BaseModel, Field, field_validator

from core import swarm_out

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_desktop() -> None:
    if os.environ.get("SWARMAI_MODE") == "hive":
        raise HTTPException(status_code=403, detail="Swarm Out is not available on a Hive")


class DispatchBody(BaseModel):
    hive: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=swarm_out.MAX_TITLE_CHARS * 2)
    task: str = Field(min_length=1, max_length=swarm_out.MAX_TASK_CHARS)
    origin: str = Field(default="swarm-out", max_length=40)
    # the Brain whose Project Agent the task goes under ("General" = the Runner Agent),
    # the mode and profile chosen from its definition, and what done looks like. The
    # repositories come from the definition (core/task_spec), not from the caller.
    brain: Optional[str] = Field(default=None, max_length=80)
    mode: Optional[str] = Field(default=None, max_length=80)
    profile: Optional[str] = Field(default=None, max_length=20)
    acceptance: Optional[str] = Field(default=None, max_length=4000)
    # the revision /spec showed the person: a definition changed since is refused
    revision: Optional[str] = Field(default=None, max_length=64)


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
    profile: Optional[str] = Field(default=None, max_length=20)


class AnswerBody(BaseModel):
    answers: Optional[dict[str, str]] = None
    decision: Optional[str] = None
    # an approval names what the card showed (core.swarm_out.answer)
    shown_sha256: Optional[str] = Field(default=None, max_length=64)


async def _call(coro) -> Any:
    try:
        return await coro
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
    return await _call(swarm_out.dispatch_spec(body.hive, body.title, body.brain, body.task,
                                               body.acceptance or "", body.mode, body.profile,
                                               body.origin, seen_revision=body.revision))


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


class LessonsAcceptBody(BaseModel):
    #: the ``digests`` of the lessons state the person was shown: only those are taken
    digests: list[str] = Field(..., max_length=200)

    @field_validator("digests")
    @classmethod
    def _hex(cls, v: list[str]) -> list[str]:
        if not all(isinstance(x, str) and len(x) == 64 and all(c in "0123456789abcdef" for c in x) for x in v):
            raise ValueError("a digest is 64 lowercase hex")
        return v


@router.post("/runs/{dispatch_id}/lessons/accept")
async def lessons_accept(dispatch_id: str, body: LessonsAcceptBody) -> dict[str, Any]:
    """The person takes the run's lessons they were shown: queued for this machine's judge
    (core/hive_lessons.accept). Until then nothing a Hive learned reaches a Brain."""
    _require_desktop()
    from core import swarm_out_results
    try:
        return await swarm_out_results.accept_lessons(dispatch_id, list(body.digests))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except swarm_out.SwarmOutError as e:
        raise HTTPException(status_code=400, detail=str(e))


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


@router.post("/runs/{dispatch_id}/return/reject")
async def return_reject(dispatch_id: str, body: ReturnBody) -> dict[str, Any]:
    _require_desktop()
    import asyncio
    from core import hive_return
    return await _return(asyncio.to_thread, hive_return.reject, dispatch_id, body.key or "", body.tip or "")


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
                                    body.acceptance, body.mode, body.profile, "hive")
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


@router.get("/runs")
async def runs() -> dict[str, Any]:
    _require_desktop()
    return await _call(swarm_out.list_runs())


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
