"""User-only CDF redaction route.

The route accepts only a dashboard-marked loopback request.  The mutation itself
also requires actor="user", so background workers cannot invoke the primitive.
"""
from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core import executors
from core.ingestion.governed_edit import redact

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])


class RedactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor: str = Field(min_length=3)
    reason: str = Field(min_length=1, max_length=500)


class RedactResponse(BaseModel):
    revision: str


@router.post("/redact", response_model=RedactResponse)
async def redact_ingestion_unit(
    body: RedactRequest,
    request: Request,
    user_action: str | None = Header(default=None, alias="X-Swarm-User-Action"),
) -> RedactResponse:
    """Apply a tombstone only for an explicit local dashboard action."""
    peer = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    if not loopback or user_action != "redact":
        raise HTTPException(status_code=403, detail="Redaction requires a local user action")
    try:
        revision = await executors.run_in(
            "io", lambda: redact(body.anchor, body.reason, actor="user"),
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedactResponse(revision=revision)
