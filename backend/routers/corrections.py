"""Correction ledger routes (core/ingestion/correction_ledger; design
docs/design/2026-10-30-autonomy-tenets-gaps.md §2).

- ``GET  /api/ingestion/corrections/pending`` — corrections still waiting for the
  person's choice (a session's, a Brain's, or all of them), each with its classes.
- ``POST /api/ingestion/corrections/{id}/confirm`` — the person's choice. Like the
  redaction route it takes only a loopback request marked as a user action
  (``X-Swarm-User-Action: correction``): the class of a correction is the person's to
  give. This is not an authentication boundary (any local process of the same user can
  send the header; the local API has no per-request credential, known product-wide).
- ``GET  /api/ingestion/corrections/rate`` — the repeat-correction rate and its coverage.
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from core import executors
from core.ingestion import correction_ledger as ledger

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ingestion/corrections", tags=["ingestion"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Candidate(_Strict):
    key: str
    kind: Literal["entry", "golden", "ledger"]
    title: str


class PendingCorrection(_Strict):
    id: str
    at: str
    surface: str
    session_id: str | None
    client_id: str | None
    brain: str | None
    run_id: str | None
    text: str
    candidates: list[Candidate]
    status: str
    class_kind: str | None
    class_key: str | None


class PendingResponse(_Strict):
    corrections: list[PendingCorrection]


class ConfirmRequest(_Strict):
    choice: str = Field(min_length=1, max_length=2000)


class ConfirmResponse(_Strict):
    id: str
    status: str
    class_kind: str | None
    class_key: str | None
    repeat: bool


class RateResponse(_Strict):
    window_days: int
    brain: str | None
    total: int
    confirmed: int
    unconfirmed: int
    not_correction: int
    repeat: int
    by_kind: dict[str, int]
    rate: float | None
    small_sample: bool
    thresholds: dict[str, float]


@router.get("/pending", response_model=PendingResponse)
async def get_pending(session_id: str | None = Query(None, min_length=1),
                      brain: str | None = Query(None, min_length=1)) -> PendingResponse:
    rows = await executors.run_nav(lambda: ledger.pending(session_id, brain=brain))
    return PendingResponse(corrections=[PendingCorrection.model_validate(r) for r in rows])


@router.post("/{row_id}/confirm", response_model=ConfirmResponse)
async def confirm_correction(
    row_id: str,
    body: ConfirmRequest,
    request: Request,
    user_action: str | None = Header(default=None, alias="X-Swarm-User-Action"),
) -> ConfirmResponse:
    peer = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    if not loopback or user_action != "correction":
        raise HTTPException(status_code=403, detail="A correction's class is the person's choice")
    try:
        got: dict[str, Any] = await executors.run_in("io", lambda: ledger.confirm(row_id, body.choice))
    except ledger.LedgerError as exc:
        logger.info("correction confirm refused for %s: %s", row_id, exc)
        raise HTTPException(status_code=400, detail="This choice cannot be recorded") from exc
    return ConfirmResponse.model_validate(got)


@router.get("/rate", response_model=RateResponse)
async def get_rate(window: int = Query(28, ge=1, le=365),
                   brain: str | None = Query(None, min_length=1)) -> RateResponse:
    return RateResponse.model_validate(
        await executors.run_nav(lambda: ledger.rate(window, brain=brain)))
