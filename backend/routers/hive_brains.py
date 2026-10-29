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
