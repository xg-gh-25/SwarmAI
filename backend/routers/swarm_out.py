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
    hive: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=swarm_out.MAX_TITLE_CHARS * 2)
    task: str = Field(min_length=1, max_length=swarm_out.MAX_TASK_CHARS)
    origin: str = Field(default="swarm-out", max_length=40)


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
    return await _call(swarm_out.dispatch(body.hive, body.title, body.task, body.origin))


@router.get("/hives")
async def hives() -> dict[str, Any]:
    _require_desktop()
    return await _call(swarm_out.hives())


@router.get("/runs")
async def runs() -> dict[str, Any]:
    _require_desktop()
    return await _call(swarm_out.list_runs())


@router.post("/asks/{hive_id}/{session_id}/{request_id}/answer")
async def answer(hive_id: str, session_id: str, request_id: str, body: AnswerBody) -> dict[str, Any]:
    _require_desktop()
    return await _call(swarm_out.answer(hive_id, session_id, request_id,
                                        answers=body.answers, decision=body.decision,
                                        shown_sha256=body.shown_sha256))
