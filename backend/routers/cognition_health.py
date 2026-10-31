"""The Health tab of the Needs You overlay (design docs/design/2026-10-31-cdf-health.md).

- ``GET /api/ingestion/cognition-health?window=7d|30d``: what the tab shows; reads only.
- ``POST /api/ingestion/cognition-health/recheck``: one pass in a background thread
  (``core.ingestion.health.start_recheck``); 409 while a pass runs. Needs a loopback
  request marked ``X-Swarm-User-Action: recheck`` (each pass spends model calls).
- ``PUT /api/ingestion/cognition-health/verdicts/{unit_id}``: the person's verdict on a
  sampled entry. Like the redaction route it needs a loopback request marked as a user
  action (``X-Swarm-User-Action: verdict``): the person's verdict is what calibrates the
  judge. This is not an authentication boundary (any local process can send the header).
"""
from __future__ import annotations

import ipaddress
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict

from core import executors
from core.ingestion import health

router = APIRouter(prefix="/api/ingestion/cognition-health", tags=["ingestion"])

Verdict = Literal["useful", "weak", "withdraw"]
Status = Literal["pass", "fail", "no_data"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WeekRate(Strict):
    week: str
    rate: float | None
    scored: int


class Sample(Strict):
    unit_id: str
    title: str
    project: str | None
    verdict: Verdict
    klass: str | None
    reason_en: str
    reason_zh: str
    user_verdict: Verdict | None
    written_at: str


class Destination(Strict):
    key: str
    group: Literal["every_turn", "on_demand", "other"]
    target: float
    scored: int
    useful: int
    weak: int
    withdraw: int
    rate: float | None
    written: int
    trend: list[WeekRate]
    samples: list[Sample]


class Value(Strict):
    rate: float | None
    target: float
    scored: int
    trend: list[WeekRate]
    destinations: list[Destination]


class Used(Strict):
    used_rate: float | None
    mature_written: int
    mature_used: int
    window_days: int


class Check(Strict):
    id: str
    status: Status
    value: dict[str, float | int | str] | None
    requirement: str


class ChecksPoint(Strict):
    at: str
    passed: int
    measured: int
    total: int


class Feature(Strict):
    feature: str
    usd: float
    calls: int
    avg_usd: float | None


class Day(Strict):
    day: str
    system_usd: float
    cdf_usd: float


class Cost(Strict):
    cdf_usd: float
    system_usd: float
    share: float | None
    unpriced_calls: int
    per_useful_usd: float | None
    features: list[Feature]
    days: list[Day]


class ReadingPart(Strict):
    seen: list[str]
    why: list[str]
    suggest: list[str]


class ReadingBasis(Strict):
    scored: int
    checks: int
    window: str


class Reading(Strict):
    en: ReadingPart
    zh: ReadingPart
    at: str
    input_hash: str
    model: str
    basis: ReadingBasis


class Agreement(Strict):
    judged_by_person: int
    agreed: int
    rate: float | None


class HealthOverview(Strict):
    window: str
    generated_at: str | None
    running: bool
    value: Value
    used: Used
    checks: list[Check]
    checks_trend: list[ChecksPoint]
    cost: Cost
    reading: Reading | None
    reading_failed: bool
    agreement: Agreement


class RecheckResponse(Strict):
    started: bool


class VerdictRequest(Strict):
    verdict: Verdict


class VerdictResponse(Strict):
    unit_id: str
    user_verdict: Verdict


@router.get("", response_model=HealthOverview)
async def get_cognition_health(window: str = Query("7d", pattern="^(7d|30d)$")) -> HealthOverview:
    return HealthOverview.model_validate(await executors.run_nav(health.overview, window))


def _user_action(request: Request, header: str | None, expected: str) -> None:
    """Refuse unless the request is a loopback one marked as this user action. A custom
    header also makes a browser preflight a cross-site request, which CORS then refuses."""
    peer = request.client.host if request.client else ""
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    if not loopback or header != expected:
        raise HTTPException(status_code=403, detail="This needs a local user action")


@router.post("/recheck", response_model=RecheckResponse)
async def recheck(
    request: Request,
    user_action: str | None = Header(default=None, alias="X-Swarm-User-Action"),
) -> RecheckResponse:
    _user_action(request, user_action, "recheck")
    if not await executors.run_nav(health.start_recheck):
        raise HTTPException(status_code=409, detail="A health check is already running")
    return RecheckResponse(started=True)


@router.put("/verdicts/{unit_id}", response_model=VerdictResponse)
async def set_verdict(
    body: VerdictRequest,
    request: Request,
    unit_id: str = Path(..., min_length=3, max_length=200),
    user_action: str | None = Header(default=None, alias="X-Swarm-User-Action"),
) -> VerdictResponse:
    _user_action(request, user_action, "verdict")
    if not await executors.run_nav(health.set_user_verdict, unit_id, body.verdict):
        raise HTTPException(status_code=404, detail="This entry was not sampled")
    return VerdictResponse(unit_id=unit_id, user_verdict=body.verdict)
