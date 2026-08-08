"""Community API — read-only endpoints behind the Community overlay (Phase-1).

The Community overlay is SwarmAI's two-way membrane with the outside world.
Phase-1 is strictly READ-ONLY: three GET endpoints project existing on-disk
data (feed config, signal/report files, engagement logs) into the overlay's
three tabs. Zero mutation — Phase-2 (a future run) adds the /sources CRUD +
self_tune coexistence writes.

    GET /api/community/feed        — recent Signals + Reports files (newest-first)
    GET /api/community/sources     — configured feeds (id/name/type/tier/enabled/managed_by)
    GET /api/community/engagement  — GitHub community engagement metrics (data-backed only)

The parsing/aggregation logic lives in core.community_data (pure, unit-tested
against tmp fixtures); this router only resolves the live workspace paths (via
the jobs/paths.py SSOT) and returns the shapes.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

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
