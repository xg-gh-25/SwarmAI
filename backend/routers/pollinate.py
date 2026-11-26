"""Pollinate — router for the overlay's reads (sibling of routers/pipelines.py).

Read-only, all built by ``core/pollinate_works.py`` from the works' own files:
  GET /api/pollinate/works            — one row per work in <workspace>/.pollinate/works.json
  GET /api/pollinate/works/view?path= — one registered work's big card (404 for any other path)
  GET /api/pollinate/eval?days=15&tz= — the Eval tab: first-pass revisions, checks, guard
                                        (tz: the person's offset in minutes east of UTC)

Each handler does all its file reads in ONE sync call dispatched with
``executors.run_nav`` — never on the event loop (tests/test_router_async_blocking.py also
follows calls into same-module helpers).

/works: a missing registry is an empty list; a broken registry, entry, folder or work.json
is left out and counted in ``skipped`` — never a 500. The pre-v2 run gallery (/assets) is
gone with no backward compatibility (XG 2026-11-26).

Images are served by the existing GET /api/workspace/file/raw; the frontend points <img>
at it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from core import executors
from core import pollinate_works as works
from jobs.paths import SWARMWS
from schemas.pollinate import PollinateEval, PollinateWorksResponse, PollinateWorkView

router = APIRouter()


def _get_swarmws() -> Path:
    """Indirection so tests can monkeypatch the workspace root (mirrors pipelines)."""
    return SWARMWS


def _read_works() -> dict:
    return works.read_works(_get_swarmws())


def _work_view(path: str):
    return works.work_view(_get_swarmws(), path)


def _eval(days: int, tz: int) -> dict:
    return works.eval_summary(_get_swarmws(), days=days, tz_minutes=tz)


@router.get("/works", response_model=PollinateWorksResponse)
async def pollinate_works() -> dict:
    """The registered works, newest first. Always 200."""
    return await executors.run_nav(_read_works)


@router.get("/works/view", response_model=PollinateWorkView)
async def pollinate_work_view(path: str = Query(..., max_length=4096)) -> dict:
    """One registered work's big card. 404 when ``path`` is not a registered work's path."""
    view = await executors.run_nav(_work_view, path)
    if view is None:
        raise HTTPException(status_code=404, detail="not a registered work")
    return view


@router.get("/eval", response_model=PollinateEval)
async def pollinate_eval(days: int = Query(15, ge=1, le=90),
                         tz: int = Query(0, ge=-840, le=840, description="minutes east of UTC")) -> dict:
    """The Eval tab over the last ``days`` local days and the ``days`` before."""
    return await executors.run_nav(_eval, days, tz)
