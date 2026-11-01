"""The Brains a Hive holds: the install endpoint the host pushes to, and the list the
host's console reads (design docs/design/2026-10-28-project-agent-flow.md §6).

Served only by a Hive (``SWARMAI_MODE=hive``); on a desktop both answer 404. Every
``/api/*`` request on a Hive already passes ``hive_auth`` (the Caddy credential,
checked again in the app).

``PUT /api/hive/brains/{brain}``: body = the package (``core.brain_sync.pack``), header
``X-Brain-Digest`` = its content digest. Checked before anything is written (name,
size before reading, members, digest); then installed and the domain skills
re-scanned and projected, while no turn holds the Brain and no new turn can take it
(``core.brain_use``). A Brain a turn holds answers 423 (try again when it finishes);
every other refusal keeps its own status and says why. ``GET /api/hive/brains``: for every Brain installed this
way, its recorded digest, revision and time, how many of its files changed on the
Hive since, and how many turns hold it now. Nothing else.

``PUT /api/hive/repos?url=&branch=&sha=``: keep the host's commit of a dispatched
task's repository (body = a git bundle from core/repo_ship) as
``Hive/incoming/<sha>.bundle`` (core/hive_repo_inbox). No git runs for this request; the
backend fetches it when it prepares the task's workspace (``POST /api/hive/work``).

``POST /api/hive/work`` / ``POST /api/hive/work/{id}/release``: prepare a dispatched
task's workspace before its session starts, and remove it when the Host settled the
task (core/hive_workspace).

``GET /api/hive/usage?after_id=``: this Hive's usage ledger rows after an id, the
accounting columns only (core/usage_accounting.rows_after), for the host's ledger.

``GET /api/hive/records/{session}`` and ``.../{seq}``: a dispatched session's run
record (core/hive_records), what the host takes back. Kept for a host that does not read
records yet: ``GET /api/hive/bundles/{session}/{key}`` (the newest sealed bundle of a
key, core/hive_bundle) and ``GET /api/hive/lessons/{session}`` (core/hive_lessons).
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import brain_sync, brain_use

logger = logging.getLogger(__name__)
router = APIRouter()



def _hive_only() -> None:
    if os.environ.get("SWARMAI_MODE") != "hive":
        raise HTTPException(status_code=404, detail="Not Found")


def _workspace() -> Path:
    from core.project_registry import get_swarmws
    return get_swarmws()


async def _read_body(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if not length or not length.isdigit():
        raise HTTPException(status_code=411, detail="Content-Length is required")
    if int(length) > brain_sync.MAX_PACKAGE_BYTES:
        raise HTTPException(status_code=413, detail="the package is larger than 20 MB")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > brain_sync.MAX_PACKAGE_BYTES:
            raise HTTPException(status_code=413, detail="the package is larger than 20 MB")
        chunks.append(chunk)
    return b"".join(chunks)


async def refresh_skills(workspace: Path) -> None:
    """Re-scan the domain skills and project them, as a workspace refresh does."""
    from core.projection_layer import refresh_workspace_skills
    await refresh_workspace_skills(workspace, why="hive brain install")


@router.put("/brains/{brain}")
async def put_brain(brain: str, request: Request) -> dict:
    _hive_only()
    digest = request.headers.get("x-brain-digest", "")   # unpack() checks it against the content
    ws = _workspace()
    try:
        _root, general = brain_sync.brain_root(ws, brain)
    except brain_sync.SyncError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    data = await _read_body(request)
    try:
        files = await asyncio.to_thread(brain_sync.unpack, data, general, digest)
    except brain_sync.SyncError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    if not brain_use.begin_install(brain):
        n = brain_use.in_use(brain)
        raise HTTPException(status_code=423, detail=(
            f"{n} turn(s) using {brain} are running; sync again when they finish" if n
            else f"{brain} is being installed already"))
    work = asyncio.ensure_future(asyncio.to_thread(brain_sync.install, ws, brain, files, digest))
    try:
        try:
            record = await asyncio.shield(work)
        except brain_sync.SyncError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        await refresh_skills(ws)          # before a turn can take the Brain again
    finally:
        # cancelled while the files are written: the Brain stays marked until the
        # thread is done, so no turn reads it half-written. A further cancel while
        # waiting must not end the wait early (that unmarked the Brain mid-write).
        while not work.done():
            try:
                await asyncio.wait({work})
            except asyncio.CancelledError:
                logger.info("hive brains: install of %s cancelled again; waiting for "
                            "its files to finish", brain)
        brain_use.end_install(brain)
    logger.info("hive brains: installed %s (%s, %d files)", brain, digest[:12], len(files))
    return {"brain": brain, "digest": record["digest"], "revision": record["revision"],
            "installed_at": record["installed_at"], "files": len(files)}


@router.put("/repos")
async def put_repo(request: Request, url: str, branch: str, sha: str) -> dict:
    """Keep a dispatched task's repository at the host's commit: body = a git bundle
    (core/repo_ship), stored as ``Hive/incoming/<sha>.bundle`` (core/hive_repo_inbox) for
    ``POST /work`` to fetch (core/hive_workspace). No git runs here. Hive only."""
    _hive_only()
    from core import hive_repo_inbox, repo_ship, task_repos
    length = request.headers.get("content-length")
    if length is None or not length.isdigit() or not int(length):
        raise HTTPException(status_code=411, detail="Content-Length is required")
    if int(length) > repo_ship.MAX_BUNDLE_BYTES:
        raise HTTPException(status_code=413, detail="the bundle is larger than a Hive takes")
    try:
        task_repos.parse([{"url": url, "ref": branch, "sha": sha}])
    except task_repos.TaskRepoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    ws = _workspace()
    try:
        inbox = await asyncio.to_thread(hive_repo_inbox.Inbox, ws, sha)
    except hive_repo_inbox.InboxError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > repo_ship.MAX_BUNDLE_BYTES:
                raise HTTPException(status_code=413, detail="the bundle is larger than a Hive takes")
            inbox.write(chunk)
        rel = await asyncio.to_thread(inbox.commit)
    except hive_repo_inbox.InboxError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None
    except BaseException:
        inbox.abort()
        raise
    logger.info("hive repos: kept %s for %s (%d bytes)", rel, branch, size)
    return {"bundle": rel, "sha": sha, "bytes": size}


class WorkBody(BaseModel):
    work_id: str = Field(max_length=40)
    repos: list[dict] = Field(default_factory=list, max_length=10)


def session_busy(session_id: str) -> bool:
    """A session of this daemon has a turn open (SessionRouter.is_busy); False when the
    router is not running here."""
    from core import session_registry
    r = session_registry.session_router
    return bool(r is not None and r.is_busy(session_id))


@router.post("/work")
async def prepare_work(body: WorkBody) -> dict:
    """Make a dispatched task's work order real before its session starts: each
    repository's worktree at its commit, read-only or on ``hive/<work_id>``, and
    ``Outputs/<work_id>/`` (core/hive_workspace). 422 with a fixed reason when it cannot
    be; nothing is left then. Hive only."""
    _hive_only()
    from core import hive_workspace
    try:
        order = await asyncio.to_thread(hive_workspace.prepare, _workspace(), body.work_id, body.repos)
    except hive_workspace.WorkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    logger.info("hive work: prepared %s (%d repositories)", body.work_id, len(order["repos"]))
    return order


@router.get("/work/{work_id}")
async def work_order(work_id: str) -> dict:
    """Whether the order exists, a session start took it, and the session it named
    (core/hive_workspace.order_state). Hive only."""
    _hive_only()
    from core import hive_workspace
    if not hive_workspace.WORK_ID_RE.fullmatch(work_id or ""):
        raise HTTPException(status_code=422, detail="not a work id")
    return await asyncio.to_thread(hive_workspace.order_state, _workspace(), work_id)


@router.post("/work/{work_id}/release")
async def release_work(work_id: str) -> dict:
    """The Host settled the task: remove its worktrees, ``Hive/work/<work_id>/`` and
    ``Outputs/<work_id>/`` (core/hive_workspace.release). 409 while its session runs.
    Hive only."""
    _hive_only()
    from core import hive_workspace
    try:
        done = await asyncio.to_thread(hive_workspace.release, _workspace(), work_id, session_busy)
    except hive_workspace.WorkError as exc:
        status = 409 if str(exc) == hive_workspace.ERR_BUSY else 422
        raise HTTPException(status_code=status, detail=str(exc)) from None
    return {"released": done}


@router.get("/bundles/{session_id}/{key}")
async def get_bundle(session_id: str, key: str):
    """A turn's commits of one declared repository (core/hive_bundle), for the host to
    take back. Hive only."""
    _hive_only()
    from fastapi.responses import StreamingResponse
    from core import hive_bundle
    try:
        fd, size = await asyncio.to_thread(hive_bundle.open_bundle, _workspace(), session_id, key)
    except OSError:
        raise HTTPException(status_code=404, detail="no such bundle") from None

    def chunks():
        try:
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    return
                yield b
        finally:
            os.close(fd)
    return StreamingResponse(chunks(), media_type="application/octet-stream",
                             headers={"Content-Length": str(size)})


@router.get("/usage")
async def get_usage(after_id: int = 0, limit: int = 1000) -> dict:
    """This Hive's usage ledger rows after ``after_id`` (core/usage_accounting), for the
    host's platform-wide ledger (core/usage_hive_sync). Hive only; read-only; the
    accounting columns only — no session content."""
    _hive_only()
    from core import usage_accounting
    return await asyncio.to_thread(usage_accounting.rows_after, max(0, after_id), max(1, min(limit, 2000)))


@router.get("/records/{session_id}")
async def get_record(session_id: str, after: int = 0) -> dict:
    """The entries of a session's run record after ``after`` (core/hive_records), for the
    host to take back. Hive only; read-only."""
    _hive_only()
    from core import hive_records
    try:
        d = hive_records.session_dir(_workspace(), session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="not a session id") from None
    if not (d / hive_records.MANIFEST).is_file():
        raise HTTPException(status_code=404, detail="no record for this session")
    return await asyncio.to_thread(hive_records.entries, _workspace(), session_id, max(0, after))


@router.get("/records/{session_id}/{seq}")
async def get_record_entry(session_id: str, seq: int):
    """One entry's file, opened without following a link. Hive only."""
    _hive_only()
    from fastapi.responses import StreamingResponse
    from core import hive_records
    try:
        fd, size = await asyncio.to_thread(hive_records.open_entry, _workspace(), session_id, seq)
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="no such entry") from None

    def chunks():
        try:
            while True:
                b = os.read(fd, 1 << 20)
                if not b:
                    return
                yield b
        finally:
            os.close(fd)
    return StreamingResponse(chunks(), media_type="application/octet-stream",
                             headers={"Content-Length": str(size)})


@router.get("/lessons/{session_id}")
async def get_lessons(session_id: str) -> dict:
    """The lessons this Hive's own CDF sources extracted for one dispatched session
    (core/hive_lessons.collect), for the host's judge. Hive only; read-only."""
    _hive_only()
    from core import hive_lessons
    try:
        return await asyncio.to_thread(hive_lessons.collect, session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="not a session id") from None


@router.get("/brains")
async def list_brains() -> dict:
    _hive_only()
    ws = _workspace()

    def _work() -> list[dict]:
        from core.project_agent import GENERAL_BRAIN
        names = [GENERAL_BRAIN]
        projects = ws / "Projects"
        if projects.is_dir():
            names += sorted(p.name for p in projects.iterdir()
                            if p.is_dir() and (p / brain_sync.STATE_FILE).is_file()
                            and brain_sync.BRAIN_NAME_RE.fullmatch(p.name))
        out = []
        for name in names:
            try:
                state = brain_sync.installed_state(ws, name)
            except (brain_sync.SyncError, OSError):
                state = None
            if state is not None:
                out.append(state)
        return out

    brains = await asyncio.to_thread(_work)
    for b in brains:
        b["running"] = brain_use.in_use(b["brain"])
    return {"brains": brains}
