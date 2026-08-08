"""Community API — the Community overlay's backend.

The Community overlay is SwarmAI's two-way membrane with the outside world.
Phase-1 (GET) projects on-disk data into the 3 tabs; Phase-2 (POST/PUT/DELETE
/feeds) makes Sources EDITABLE.

    GET    /api/community/feed        — recent Signals + Reports files (newest-first)
    GET    /api/community/sources     — configured feeds (id/name/type/tier/enabled/managed_by)
    GET    /api/community/engagement  — GitHub community engagement metrics (data-backed only)
    POST   /api/community/feeds       — add a source (managed_by:user)
    PUT    /api/community/feeds/{id}   — toggle enabled / change tier
    DELETE /api/community/feeds/{id}   — remove a source (idempotent)

Reads: pure parsers in core.community_data. Writes: ALL go through
jobs.config_io.mutate_config — the SINGLE serialization authority shared with
self_tune (R27), so a UI edit and a scheduled self_tune run can never clobber
each other's config.yaml write. Type/tier are validated against FeedType/TierType
BEFORE the write, so an invalid feed can never reach scheduler.load_feeds's
silent-skip path.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.community_data import aggregate_engagement, build_feed, parse_sources

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/community", tags=["community"])


def _config_path():
    from jobs.paths import CONFIG_FILE

    return CONFIG_FILE


def _knowledge_dir():
    from jobs.paths import SWARMWS

    return SWARMWS / "Knowledge"


def _engagement_dir():
    from jobs.paths import PROJECTS_DIR

    return PROJECTS_DIR / "GitHub_Community" / ".artifacts"


@router.get("/feed")
async def community_feed() -> dict:
    """Recent community signals + reports (files), newest-first.

    Each item: {path, category, name, mtime}. The frontend opens `path` in
    Canvas on click. Community-scoped: only Signals + Reports categories.
    """
    items = build_feed(_knowledge_dir())
    # `truncated` is honest: build_feed caps the payload, so a consumer can tell
    # "42 shown of exactly 42" from "100 shown, more on disk" (not a silent cut).
    from core.community_data import feed_cap

    return {"count": len(items), "items": items, "truncated": len(items) >= feed_cap()}


@router.get("/sources")
async def community_sources() -> dict:
    """Configured signal feeds (read-only view of config.yaml `feeds:`).

    Each source: {id, name, type, tier, enabled, managed_by, source_count, tags}.
    `managed_by` defaults to "manual" when absent. READ-ONLY in Phase-1 — no
    add/edit/delete (Phase-2). Empty list on a fresh install (no config.yaml).
    """
    sources = parse_sources(_config_path())
    return {"count": len(sources), "sources": sources}


@router.get("/engagement")
async def community_engagement() -> dict:
    """GitHub community engagement metrics (data-backed only).

    {comments_posted, replies_received, maintainer_replies, stars}. No fabricated
    quality score — there is no quality-score source on disk. Zeros when the
    GitHub_Community project has no engagement history yet.
    """
    return aggregate_engagement(_engagement_dir())


# ── Phase-2: Sources CRUD (writes config.yaml via the shared serialization authority) ──


class NewFeed(BaseModel):
    id: str
    name: str
    type: str
    tier: str = "engineering"
    config: dict = {}
    tags: list[str] = []


class FeedPatch(BaseModel):
    enabled: bool | None = None
    tier: str | None = None


def _valid_type(t: str) -> bool:
    from jobs.models import FeedType

    try:
        FeedType(t)
        return True
    except ValueError:
        return False


def _valid_tier(t: str) -> bool:
    from jobs.models import TierType

    try:
        TierType(t)
        return True
    except ValueError:
        return False


@router.post("/feeds")
async def add_feed(feed: NewFeed) -> dict:
    """Add a new signal source. Sets managed_by:user (protected from self_tune
    auto-disable). Validates type∈FeedType + tier∈TierType up front (422) so an
    invalid feed never reaches scheduler.load_feeds's silent-skip. Rejects a
    duplicate id (409) — config.yaml feeds is a list, two same-id entries would
    both load. Serialized with self_tune via the shared config lock.
    """
    if not feed.id.strip():
        raise HTTPException(status_code=422, detail="Feed id cannot be empty")
    if not _valid_type(feed.type):
        raise HTTPException(status_code=422, detail=f"Invalid feed type '{feed.type}'")
    if not _valid_tier(feed.tier):
        raise HTTPException(status_code=422, detail=f"Invalid tier '{feed.tier}'")

    from jobs.config_io import mutate_config

    class _Dup(Exception):
        pass

    def _mutator(config: dict) -> None:
        feeds = config.setdefault("feeds", [])
        if any(isinstance(f, dict) and f.get("id") == feed.id for f in feeds):
            raise _Dup()
        feeds.append({
            "id": feed.id, "name": feed.name, "type": feed.type, "tier": feed.tier,
            "config": feed.config, "tags": feed.tags, "enabled": True,
            "managed_by": "user",
        })

    try:
        mutate_config(_mutator)
    except _Dup:
        raise HTTPException(status_code=409, detail=f"Feed id '{feed.id}' already exists")
    return {"ok": True, "id": feed.id}


@router.put("/feeds/{feed_id}")
async def update_feed(feed_id: str, patch: FeedPatch) -> dict:
    """Toggle enabled and/or change tier of an existing feed. 404 if absent, 422 on
    an invalid tier. A user edit stamps managed_by:user (this feed is now
    user-owned). Serialized with self_tune via the shared config lock.
    """
    if patch.tier is not None and not _valid_tier(patch.tier):
        raise HTTPException(status_code=422, detail=f"Invalid tier '{patch.tier}'")

    from jobs.config_io import mutate_config

    class _NotFound(Exception):
        pass

    def _mutator(config: dict) -> None:
        for f in config.get("feeds", []):
            if isinstance(f, dict) and f.get("id") == feed_id:
                if patch.enabled is not None:
                    f["enabled"] = patch.enabled
                if patch.tier is not None:
                    f["tier"] = patch.tier
                f["managed_by"] = "user"  # a user edit takes ownership
                return
        raise _NotFound()

    try:
        mutate_config(_mutator)
    except _NotFound:
        raise HTTPException(status_code=404, detail=f"Feed '{feed_id}' not found")
    return {"ok": True, "id": feed_id}


@router.delete("/feeds/{feed_id}")
async def delete_feed(feed_id: str) -> dict:
    """Remove a source. IDEMPOTENT — deleting a missing id is a 200 no-op (not 500),
    so a double-click / retry is safe. Serialized with self_tune via the shared lock.
    """
    from jobs.config_io import mutate_config

    def _mutator(config: dict) -> bool:
        feeds = config.get("feeds", [])
        kept = [f for f in feeds if not (isinstance(f, dict) and f.get("id") == feed_id)]
        removed = len(kept) != len(feeds)
        config["feeds"] = kept
        return removed

    removed = mutate_config(_mutator)
    return {"ok": True, "id": feed_id, "removed": bool(removed)}
