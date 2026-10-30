"""Read-only HTTP routes for the Cognitive Distillation Flow dashboard.

- Every SQLite read runs through the dedicated navigation executor.
- Pydantic response models define every field consumed by the dashboard.
- Detail reads enqueue audit records without mutating the read-only database.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from config import get_swarmws
from core import executors
from core.ingestion import flow_view, queries

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])


class StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LandedRow(StrictResponse):
    store: str
    project: str | None
    path: str | None
    written: int
    reinforced: int


class MovedRow(StrictResponse):
    from_: str = Field(alias="from")
    to: str
    count: int

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AttentionRow(StrictResponse):
    code: str
    count: int | None = None
    example: str | None = None
    bytes: int | None = None
    since: str | None = None


class LandedResponse(StrictResponse):
    """What the Cognition flow view shows first (core/ingestion/flow_view.py)."""
    window: str
    since: str
    total: int
    totals: dict[str, int]
    groups: dict[str, int]
    reasons: dict[str, dict[str, int]]
    sources: dict[str, int]
    steps: dict[str, int]
    landed: list[LandedRow]
    moved: list[MovedRow]
    attention: list[AttentionRow]
    store_bytes: int
    last_cleanup_at: str | None


class UnitRow(StrictResponse):
    unit_id: str
    candidate_id: str
    created_at: str
    source: str
    source_ref: str
    kind: str
    type: str | None
    title: str
    text: str
    scope: str | None
    state: str
    reason: str | None
    group: str
    store: str | None
    project: str | None
    path: str | None
    section: str | None
    moved_from: str | None
    relation: str | None
    target: str | None
    basis: str | None


class UnitsResponse(StrictResponse):
    items: list[UnitRow]
    page: int
    page_size: int
    total: int
    has_next: bool


class FlowResponse(StrictResponse):
    window: str
    since: str
    total_units: int
    denominator_units: int
    written_rate: float
    reinforced_rate: float
    rejected_rate: float
    outcomes: dict[str, int]
    extraction_coverage: dict[str, dict[str, int]]
    link_status: dict[str, int]
    same_origin_reroute_count: int
    lanes: dict[str, int]
    sources: dict[str, int]
    states: dict[str, int]
    reasons: dict[str, int]


class ItemSummary(StrictResponse):
    id: str
    created_at: str
    source: str
    lane: str
    state: str
    unit_states: list[str]
    stores: list[str]
    reasons: list[str]
    preview: str


class ItemsResponse(StrictResponse):
    items: list[ItemSummary]
    page: int
    page_size: int
    total: int
    has_next: bool
    window_days: int


class ItemDetailResponse(StrictResponse):
    candidate: dict[str, Any]
    envelope: dict[str, Any]
    units: list[dict[str, Any]]
    writes: list[dict[str, Any]]
    relations: list[dict[str, Any]]
    detail_offset: int
    has_more: bool
    next_offset: int | None
    paged_fields: list[str]


class StatsResponse(StrictResponse):
    days: int
    rows: list[dict[str, Any]]


class BootstrapCounts(StrictResponse):
    terminal: int
    total: int


class BootstrapProgressResponse(StrictResponse):
    project: str
    terminal: int
    total: int
    pending: int
    s7: BootstrapCounts
    s4_backfill: BootstrapCounts


class HealthResponse(StrictResponse):
    lanes: dict[str, dict[str, Any]]
    events: list[dict[str, Any]]
    breakers: list[dict[str, Any]]
    last_cleanup_at: str | None
    inbox_count: int
    inbox_bytes: int
    memory_bytes: int
    evolution_bytes: int
    llm_by_source: list[dict[str, Any]]
    review: dict[str, Any]   # after-session review metrics (2026-10-29-cdf-session-review.md §12)


@router.get("/flow", response_model=FlowResponse)
async def get_flow(window: str = Query("24h", pattern="^(24h|7d|30d)$")) -> FlowResponse:
    return FlowResponse.model_validate(await executors.run_nav(queries.flow_overview, window))


@router.get("/landed", response_model=LandedResponse, response_model_by_alias=True)
async def get_landed(window: str = Query("24h", pattern="^(24h|7d|30d)$")) -> LandedResponse:
    return LandedResponse.model_validate(await executors.run_nav(flow_view.landed_overview, window))


@router.get("/units", response_model=UnitsResponse)
async def get_units(
    window: str = Query("24h", pattern="^(24h|7d|30d)$"),
    outcome: str = Query("all", pattern="^(all|new|adds|reinforced|not_kept|waiting)$"),
    place: str | None = Query(None, pattern="^(every_turn|project|general)$"),
    source: str | None = Query(None, max_length=40),
    store: str | None = Query(None, max_length=40),
    project: str | None = Query(None, max_length=200),
    path: str | None = Query(None, max_length=400),
    keyword: str | None = Query(None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=50),
) -> UnitsResponse:
    filters = {"window": window, "outcome": outcome, "place": place, "source": source,
               "store": store, "project": project, "path": path, "keyword": keyword,
               "page_size": page_size}
    return UnitsResponse.model_validate(await executors.run_nav(flow_view.list_units, filters, page))


@router.get("/items", response_model=ItemsResponse)
async def get_items(
    source: str | None = None,
    store: str | None = None,
    state: str | None = None,
    reason: str | None = None,
    keyword: str | None = None,
    time: int = Query(30, ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1),
) -> ItemsResponse:
    filters = {"source": source, "store": store, "state": state, "reason": reason,
               "keyword": keyword, "time": time, "page_size": page_size}
    return ItemsResponse.model_validate(await executors.run_nav(queries.list_items, filters, page))


@router.get("/items/{item_id}", response_model=ItemDetailResponse)
async def get_item(item_id: str, offset: int = Query(0, ge=0)) -> ItemDetailResponse:
    value = await executors.run_nav(queries.item_detail, item_id, get_swarmws(), offset)
    if value is None:
        raise HTTPException(status_code=404, detail="Ingestion candidate not found")
    return ItemDetailResponse.model_validate(value)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(range: int = Query(30, ge=1, le=365)) -> StatsResponse:
    return StatsResponse.model_validate(await executors.run_nav(queries.daily_stats, range))


@router.get("/bootstrap-progress", response_model=BootstrapProgressResponse)
async def get_bootstrap_progress(project: str = Query(..., min_length=1)) -> BootstrapProgressResponse:
    value = await executors.run_nav(queries.read_bootstrap_progress, project)
    return BootstrapProgressResponse.model_validate(value)


@router.get("/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    return HealthResponse.model_validate(await executors.run_nav(queries.health))
