"""Swarm Out remote session routes (Host only): a dispatched Hive session, opened in a tab.

``/api/swarm-out/remote/{sid}/...`` (design docs/design/2026-10-29-swarm-out-hive-
session-tab.md §6):

- ``POST /{sid}/relay`` ``{"client_id"}`` — start (or join) the relay that republishes
  the Hive session's events into this daemon's ``/api/chat/events``; answers the
  session's state entry. ``DELETE /{sid}/relay`` ``{"client_id"}`` — drop that hold.
- ``/{sid}/<chat path>`` — the chat endpoints of ``core/swarm_out_remote.ROUTES``,
  forwarded to the session's Hive with its stored credentials. The body is rebuilt
  from the route's key list, no client header goes upstream, a redirect becomes 502,
  only a few response headers come back, a Hive error is passed as its status with a
  generic message, SSE events are cleaned (``clean_event``).

Every route: desktop mode only, and a write (POST / DELETE) needs
``Content-Type: application/json`` and ``X-Swarm-User-Action`` (a cross-site page
cannot send that without a preflight the daemon's CORS refuses). Only a session in
this Host's dispatch log is reachable (``resolve_session``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from core import swarm_out, swarm_out_relay
from core.swarm_out_remote import (
    ID_RE, MAX_BODY_BYTES, LineTooLong, RemoteError, build_body, build_query, clean_event, closed_for,
    filter_headers, iter_sse_lines, match_route, parse_data_line, read_capped, resolve_session,
)

logger = logging.getLogger(__name__)

STREAM_READ_TIMEOUT_S = 60   # the Hive's chat streams send a heartbeat every 15 s


async def _guard(request: Request) -> None:
    if os.environ.get("SWARMAI_MODE") == "hive":
        raise HTTPException(status_code=403, detail="Swarm Out is not available on a Hive")
    if request.method in ("POST", "DELETE"):
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype != "application/json" or not request.headers.get("x-swarm-user-action"):
            raise HTTPException(status_code=415, detail="a JSON request from the app is required")


router = APIRouter(dependencies=[Depends(_guard)])


async def _json(request: Request) -> Any:
    raw = await request.body()
    if not raw:
        return None
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="request too large")
    try:
        return json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="the body must be JSON")


def _client_id(body: Any) -> str:
    cid = body.get("client_id") if isinstance(body, dict) else None
    if not isinstance(cid, str) or not ID_RE.match(cid):
        raise HTTPException(status_code=400, detail="client_id is required")
    return cid


async def _target(sid: str, write: bool) -> dict[str, Any]:
    """The running Hive of a session this Host dispatched, unless the run is closed to
    this request (core/swarm_out_remote.closed_for: discarded runs to every request,
    accepted runs to writes)."""
    try:
        row = await resolve_session(sid)
    except RemoteError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    closed = await asyncio.to_thread(closed_for, row, write)
    if closed is not None:
        raise HTTPException(status_code=closed.status, detail=str(closed))
    try:
        return await swarm_out._instance(row["hive_id"])
    except swarm_out.SwarmOutError:
        raise HTTPException(status_code=502, detail="the session's Hive is not reachable")


@router.post("/{sid}/relay")
async def start_relay(sid: str, request: Request) -> dict[str, Any]:
    client_id = _client_id(await _json(request))
    inst = await _target(sid, write=False)
    try:
        entry = await swarm_out_relay.manager().acquire(sid, client_id, inst)
    except RemoteError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="the session's Hive is not reachable")
    return {"session_id": sid, "state": entry}


@router.delete("/{sid}/relay")
async def stop_relay(sid: str, request: Request) -> dict[str, Any]:
    client_id = _client_id(await _json(request))
    if not ID_RE.match(sid):
        raise HTTPException(status_code=404, detail="not found")
    await swarm_out_relay.manager().release(sid, client_id)
    return {"session_id": sid, "released": True}


def _status_error(status: int) -> JSONResponse:
    """A Hive's non-2xx answer, without its body (it is the Hive's text)."""
    if 300 <= status < 400:
        return JSONResponse({"detail": "the Hive answered with a redirect"}, status_code=502)
    return JSONResponse({"detail": f"the Hive answered {status}"}, status_code=status)


@router.api_route("/{sid}/{path:path}", methods=["GET", "POST", "DELETE"])
async def proxy(sid: str, path: str, request: Request):
    try:
        route, upstream = match_route(request.method, path, sid)
        query = build_query(route, request.query_params)
        body = build_body(route, await _json(request), sid) if route.body else None
    except RemoteError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    inst = await _target(sid, write=request.method != "GET")

    if not route.stream:
        try:
            async with swarm_out._client(inst) as client:
                async with client.stream(request.method, upstream, params=query or None,
                                         json=body if route.body else None) as r:
                    if r.status_code >= 300:
                        return _status_error(r.status_code)
                    content = await read_capped(r, MAX_BODY_BYTES)
                    headers = filter_headers(r.headers)
                    status = r.status_code
        except RemoteError as e:
            return JSONResponse({"detail": str(e)}, status_code=e.status)
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="the session's Hive is not reachable")
        return Response(content=content, status_code=status, headers=headers)

    client = swarm_out._client(inst, timeout=httpx.Timeout(swarm_out.HTTP_TIMEOUT_S, read=STREAM_READ_TIMEOUT_S))
    try:
        upstream_req = client.build_request(request.method, upstream, json=body)
        r = await client.send(upstream_req, stream=True)
    except httpx.HTTPError:
        await client.aclose()
        raise HTTPException(status_code=502, detail="the session's Hive is not reachable")
    if r.status_code != 200:
        await r.aclose()
        await client.aclose()
        return _status_error(r.status_code)
    # what the person does in the run's Hive tab is the person's decision on the run,
    # one entry of its record (design 2026-11-02-hive-run-decision-record §3)
    _PERSON_STEP = {"chat/stream": ("said", "message"), "chat/answer-question": ("answer_wait", "answer"),
                    "chat/cmd-permission-continue": ("answer_wait", "approval")}
    step = _PERSON_STEP.get(route.template) if request.method == "POST" else None
    if step:
        import asyncio
        from core import swarm_out_record
        did = await asyncio.to_thread(swarm_out.dispatch_of_session, sid)
        if did:
            # one small entry per message: no facts (a chatty session must not grow the
            # record by a snapshot per message)
            await asyncio.to_thread(swarm_out_record.append_quietly, did, "person", step[0], "", None,
                                    step[0], {"kind": step[1]}, {})

    async def events() -> AsyncIterator[str]:
        try:
            async for line in iter_sse_lines(r.aiter_bytes()):
                if line.strip() == "data: [DONE]":
                    yield "data: [DONE]\n\n"
                    continue
                ev = parse_data_line(line)
                if ev is None:
                    continue
                clean = clean_event(ev, sid, post_stream=True)
                if clean is not None:
                    yield f"data: {json.dumps(clean)}\n\n"
        except LineTooLong:
            logger.warning("swarm_out_remote: a Hive line over the cap closed %s", sid[:8])
        except httpx.HTTPError as exc:
            logger.info("swarm_out_remote: upstream stream for %s ended (%s)", sid[:8], type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 — Hive data ends this stream, not the daemon
            logger.warning("swarm_out_remote: upstream stream for %s failed (%s)", sid[:8], type(exc).__name__)
        finally:
            await r.aclose()
            await client.aclose()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
