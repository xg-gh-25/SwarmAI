"""Community API — the Community overlay's backend.

The Community overlay is SwarmAI's two-way membrane with the outside world.
GET endpoints project on-disk data into the 3 tabs; the /feeds endpoints make
Sources EDITABLE at both the FEED level and the MEMBER level.

    GET    /api/community/feed              — recent Signals + community Reports files (newest-first)
    GET    /api/community/sources           — configured feeds (id/name/type/tier/enabled/managed_by
                                              + members/member_count/member_kind/members_truncated)
    GET    /api/community/engagement        — GitHub community engagement metrics (data-backed only)
    POST   /api/community/feeds             — add a source (managed_by:user)
    PUT    /api/community/feeds/{id}         — toggle enabled / change tier
    DELETE /api/community/feeds/{id}         — remove a source (idempotent)
    POST   /api/community/feeds/{id}/members — add a member (url/keyword/repo…) to a feed
    DELETE /api/community/feeds/{id}/members — remove a member (idempotent)

Reads: pure parsers in core.community_data. Writes: ALL go through
jobs.config_io.mutate_config — the SINGLE serialization authority shared with
self_tune (R27), so a UI edit and a scheduled self_tune run can never clobber
each other's config.yaml write. Feed type/tier are validated against
FeedType/TierType, and a member value is validated per-KIND (url/repo shape),
so an invalid feed or member can never reach the adapter's silent-skip path.
"""

from __future__ import annotations

import asyncio
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
    # build_feed does rglob("*.md") + per-file open (blocking I/O) — off the event loop.
    items = await asyncio.to_thread(build_feed, _knowledge_dir())
    # `truncated` is honest: build_feed caps the payload, so a consumer can tell
    # "42 shown of exactly 42" from "100 shown, more on disk" (not a silent cut).
    from core.community_data import feed_cap

    return {"count": len(items), "items": items, "truncated": len(items) >= feed_cap()}


@router.get("/sources")
async def community_sources() -> dict:
    """Configured signal feeds (a view of config.yaml `feeds:`).

    Each source: {id, name, type, tier, enabled, managed_by, members, member_count,
    member_kind, members_truncated, tags}. `managed_by` defaults to "manual" when absent.
    `member_kind` is the config key holding this feed's editable string members (or None
    for a no-editable-member type); `member_count` is the accurate total; `members` is the
    capped list (`members_truncated` flags the cut). Editing is via the /feeds +
    /feeds/{id}/members endpoints. Empty list on a fresh install (no config.yaml).
    """
    # parse_sources reads + yaml-parses config.yaml (blocking) — off the event loop.
    sources = await asyncio.to_thread(parse_sources, _config_path())
    return {"count": len(sources), "sources": sources}


@router.get("/engagement")
async def community_engagement() -> dict:
    """GitHub community engagement metrics (data-backed only).

    {comments_posted, replies_received, maintainer_replies, stars}. No fabricated
    quality score — there is no quality-score source on disk. Zeros when the
    GitHub_Community project has no engagement history yet.
    """
    # aggregate_engagement reads JSONL files (blocking) — off the event loop.
    return await asyncio.to_thread(aggregate_engagement, _engagement_dir())


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

    # Apply the SAME url validation the member route enforces (_validate_member) —
    # add_feed used to write config verbatim, so an SSRF/private/metadata URL could
    # be persisted via the feed route while add_member rejected it (asymmetric write
    # paths, run_36d8ba1c). Validate config.urls HERE, before the lock: unlike
    # add_member (whose feed type is only knowable on-disk under the lock), the type
    # arrives in the request body, so we can fail-fast 422 without holding the config
    # lock. Only urls carry an SSRF surface — other config keys are left as-is
    # (scope = the asymmetry, not full-config validation). A non-list urls is skipped
    # (defensive: never crash on a malformed body).
    urls = feed.config.get("urls") if isinstance(feed.config, dict) else None
    if isinstance(urls, list):
        for u in urls:
            if not isinstance(u, str) or not u.strip():
                continue
            try:
                _validate_member("urls", u.strip())
            except _InvalidMember as e:
                raise HTTPException(status_code=422, detail=f"Invalid url '{u}': {e}")

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
        await asyncio.to_thread(mutate_config, _mutator)
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
        await asyncio.to_thread(mutate_config, _mutator)
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

    removed = await asyncio.to_thread(mutate_config, _mutator)
    return {"ok": True, "id": feed_id, "removed": bool(removed)}


# ── Member-level CRUD (edit a feed's internal urls/keywords/queries/repos/…) ──


class MemberBody(BaseModel):
    """A single editable member of a feed (a URL / keyword / query / repo). Carried in
    the request BODY (never a path param — member values contain slashes)."""
    value: str


def _member_key_for_type(feed_type: str) -> str | None:
    """The config key holding this feed type's editable string members, or None.
    Single source = jobs.models.MEMBER_KEY (imported directly — not via another module's
    private symbol). MEMBER_KEY is keyed by the FeedType ENUM, so the raw string MUST be
    converted (a bare MEMBER_KEY.get(raw_str) would return None for every feed). An
    unknown/invalid type → None (no editable members), never a crash."""
    from jobs.models import FeedType, MEMBER_KEY

    try:
        return MEMBER_KEY.get(FeedType(feed_type))
    except ValueError:
        return None


def _validate_member(key: str, value: str) -> None:
    """#8 strict per-KIND validation — raise _InvalidMember (→422) for a value that would
    silently fail at fetch time. Keeps a dead subscription out of config.
      - urls (rss):            must parse as an http(s):// URL with a netloc
      - repos (github-*):      must be `owner/repo` — exactly one '/', both non-empty, no whitespace
      - keywords/concept_keywords/other: free-text — non-empty already guaranteed by the caller
    `value` is already .strip()'d and non-empty when this runs."""
    if key == "urls":
        from urllib.parse import urlparse

        p = urlparse(value)
        if p.scheme not in ("http", "https") or not p.netloc:
            raise _InvalidMember("must be an http(s):// URL")
        # SSRF hygiene (defense-in-depth — the RSS fetch already egress-guards, but a
        # dead/internal URL must not land in config.yaml). Reject a private/link-local/
        # loopback/metadata IP LITERAL. Use .hostname, never .netloc: .netloc keeps
        # :port/[ipv6]/user@ and would fail the IP parse → the SSRF form would SLIP.
        from jobs.adapters.http_client import is_blocked_ip_literal

        if is_blocked_ip_literal(p.hostname):
            raise _InvalidMember("must not be a private/link-local/metadata IP")
    elif key == "repos":
        parts = value.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1] or any(c.isspace() for c in value):
            raise _InvalidMember("must be 'owner/repo'")
    elif key == "logins":
        # GitHub username rules: alphanumeric or single interior hyphens, cannot
        # begin/end with a hyphen, max 39 chars. A malformed login silently returns
        # zero results from `gh search issues --author <login>` — reject at write
        # time so a dead person-subscription never lands in config. (R2 名人层)
        import re as _re

        if not _re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}", value):
            raise _InvalidMember("must be a valid GitHub login (alphanumeric + hyphens, ≤39 chars)")
    # keywords / concept_keywords: free-text, non-empty (caller-guaranteed) — no rule


class _InvalidMember(Exception):
    """A member value that fails its kind's format rule (#8)."""


@router.post("/feeds/{feed_id}/members")
async def add_member(feed_id: str, body: MemberBody) -> dict:
    """Add a string member (url/keyword/query/repo/…) to a feed's config. The target
    config key is derived from the feed's TYPE via MEMBER_KEY (the single source), so a
    feed type with no editable members (github-trending) is rejected 422 rather than
    silently writing an unused key. Empty value → 422, duplicate → 409, missing feed →
    404. A member edit stamps managed_by:user (protected from self_tune auto-disable).
    Serialized with self_tune + the feed-level endpoints via the shared config lock (R27).
    """
    value = body.value.strip()
    if not value:
        raise HTTPException(status_code=422, detail="Member value cannot be empty")

    from jobs.config_io import mutate_config

    class _NotFound(Exception):
        pass

    class _NoMemberType(Exception):
        pass

    class _Dup(Exception):
        pass

    def _mutator(config: dict) -> None:
        for f in config.get("feeds", []):
            if isinstance(f, dict) and f.get("id") == feed_id:
                # kind is only knowable HERE (the feed's type lives in config, read under
                # the lock) — so #8 per-kind validation happens inside the mutator, after
                # `key` is resolved and before the append (Gate-1: cannot validate by kind
                # before mutate_config since the request carries only the value).
                key = _member_key_for_type(f.get("type", ""))
                if key is None:
                    raise _NoMemberType()
                _validate_member(key, value)  # → _InvalidMember → 422
                cfg = f.get("config")
                if not isinstance(cfg, dict):
                    cfg = {}
                    f["config"] = cfg
                members = cfg.get(key)
                if not isinstance(members, list):
                    members = []
                    cfg[key] = members
                if value in members:
                    raise _Dup()
                members.append(value)
                f["managed_by"] = "user"
                return
        raise _NotFound()

    try:
        await asyncio.to_thread(mutate_config, _mutator)
    except _NotFound:
        raise HTTPException(status_code=404, detail=f"Feed '{feed_id}' not found")
    except _NoMemberType:
        raise HTTPException(status_code=422, detail=f"Feed '{feed_id}' has no editable members")
    except _InvalidMember as e:
        raise HTTPException(status_code=422, detail=f"Invalid member for '{feed_id}': {e}")
    except _Dup:
        raise HTTPException(status_code=409, detail=f"Member already exists in '{feed_id}'")
    return {"ok": True, "id": feed_id, "value": value}


@router.delete("/feeds/{feed_id}/members")
async def delete_member(feed_id: str, body: MemberBody) -> dict:
    """Remove a string member from a feed's config. IDEMPOTENT — removing an absent
    member (or a member from a no-member-type / missing feed) is a 200 no-op with
    removed=false, never a 500 (safe double-click). Stamps managed_by:user only when a
    real removal happens. Serialized with self_tune via the shared lock (R27).
    """
    value = body.value.strip()

    from jobs.config_io import mutate_config

    def _mutator(config: dict) -> bool:
        for f in config.get("feeds", []):
            if isinstance(f, dict) and f.get("id") == feed_id:
                key = _member_key_for_type(f.get("type", ""))
                if key is None:
                    return False
                cfg = f.get("config")
                if not isinstance(cfg, dict):
                    return False
                members = cfg.get(key)
                if not isinstance(members, list) or value not in members:
                    return False
                cfg[key] = [m for m in members if m != value]
                f["managed_by"] = "user"
                return True
        return False

    removed = await asyncio.to_thread(mutate_config, _mutator)
    return {"ok": True, "id": feed_id, "value": value, "removed": bool(removed)}
