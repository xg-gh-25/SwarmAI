"""FastAPI endpoint for authenticated CDF worker wakeups.

- ``wake_ingestion`` accepts no data and only signals the local owner event.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, Response

from core.ingestion.wake import authenticate, signal_local

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])


@router.post("/wake", status_code=204)
async def wake_ingestion(
    request: Request,
    x_swarm_wake: str | None = Header(default=None, alias="X-Swarm-Wake"),
) -> Response:
    """Signal the in-process worker after loopback and credential checks."""
    peer = request.client.host if request.client else None
    if not authenticate(peer, x_swarm_wake):
        raise HTTPException(status_code=403, detail="Forbidden")
    signal_local()
    return Response(status_code=204)
