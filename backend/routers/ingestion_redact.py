"""User-only CDF redaction route.

The route accepts only a dashboard-marked loopback request.  The mutation itself
also requires actor="user", so CDF's own workers cannot invoke the primitive. This is
not an authentication boundary: like every local endpoint, any process of the same
user (the agent's Bash included) can send the header; the local API has no
per-request credential (known, product-wide).
"""
from __future__ import annotations

import ipaddress
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core import executors
from core.ingestion.governed_edit import redact

logger = logging.getLogger(__name__)
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
        logger.warning("CDF redact refused for %s: %s", body.anchor, exc)
        raise HTTPException(status_code=400, detail="This entry cannot be redacted") from exc
    return RedactResponse(revision=revision)
