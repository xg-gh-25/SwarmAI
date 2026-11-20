"""Inbox API (``/api/inbox``): the routes of ``schemas/inbox.py``.

Design: ``docs/design/2026-11-20-inbox.md`` §7-§8. The route list and the shapes are the
contract in ``schemas/inbox.py``; the domain work is in ``core/inbox/`` (store, runner,
send, caller).

- The three routes that send or unlock a send (``/items/{id}/send``,
  ``/sends/{id}/not-sent``, ``/sends/{id}/resend``) require ``X-Swarm-User-Action`` and
  pass the caller check (``core/inbox/caller.py``): a request from the backend itself or
  from any process it started is 403, as is one whose peer process cannot be found. The
  other POSTs that change Inbox state (``/redraft``, ``/action``, ``/refresh``,
  ``/sources/{id}/retry``) require the header too, so a web page cannot trigger them.
- 409 answers carry ``detail = {"code": ..., "message": ..., ...}``: ``target_changed``
  (with the fresh ``target``), ``redacted`` (with ``spans``), ``not_open``,
  ``cannot_send``, ``not_unknown``, ``not_failed``.
- Feed configuration moved here from ``/api/community/feeds`` unchanged in behaviour: same
  bodies, validation, status codes and the one write path ``jobs.config_io.mutate_config``.
  The community routes stay until the Community page is deleted.
- Blocking work (config.yaml reads and writes) runs on ``executors.run_nav``, the caller
  check on the Inbox pool (``store.run_blocking``); nothing blocks the event loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

from core import executors
from core.inbox import caller, drafts, runner, send, store
from core.inbox.models import ItemState, SendStatus
from schemas.inbox import (
    DraftOut,
    InboxCounts,
    ItemActionRequest,
    ItemOut,
    SendOut,
    SendRequest,
    SendsOut,
    SourceOut,
    TargetOut,
    TrendPoint,
    TrendSummaryOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inbox", tags=["inbox"])

SNOOZE_S = 3 * 3600
_UA = Header(default=None, alias="X-Swarm-User-Action")


def _require_app(user_action: Optional[str]) -> None:
    if not user_action:
        raise HTTPException(status_code=403, detail={"code": "forbidden",
                                                     "message": "X-Swarm-User-Action required"})


async def _require_desktop_ui(request: Request, user_action: Optional[str]) -> None:
    """Design §8 step 0: only the desktop UI may send."""
    _require_app(user_action)
    client = (request.client.host, request.client.port) if request.client else None
    server = request.scope.get("server")
    try:
        await store.run_blocking(caller.check_caller, client, tuple(server) if server else None)
    except caller.CallerRefused as exc:
        logger.warning("inbox: send refused, caller is not the desktop UI (%s)", exc.reason)
        raise HTTPException(status_code=403, detail={
            "code": "forbidden", "message": "only the desktop UI may send", "reason": exc.reason})


def _refused(exc: send.SendRefused) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail())


async def _item(item_id: str) -> dict:
    from database import db
    row = await db.inbox_get_item(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "no such item"})
    return row


# ── counts and items ─────────────────────────────────────────────────────────

@router.get("/counts", response_model=InboxCounts)
async def get_counts() -> InboxCounts:
    return InboxCounts(**await store.counts())


@router.get("/items", response_model=list[ItemOut])
async def list_items(lane: Literal["reply", "proposed", "aware", "trend"] = Query(...)) -> list[ItemOut]:
    from database import db
    await db.inbox_wake_snoozed(store.ts(store.now()))
    return [ItemOut(**store.item_out(r)) for r in await db.inbox_list_items(lane)]


@router.get("/items/{item_id}/target", response_model=TargetOut)
async def get_target(item_id: str) -> TargetOut:
    try:
        return TargetOut(**await send.target_for(item_id))
    except send.SendRefused as exc:
        raise _refused(exc)


_draft_locks: dict[tuple[int, str], asyncio.Lock] = {}


def _draft_out(row: dict) -> DraftOut:
    return DraftOut(id=row["id"], item_id=row["item_id"], text=row["text"], lang=row["lang"] or "",
                    note=row["note"] or "", created_at=row["created_at"])


async def _make_draft(item: dict) -> dict:
    """One draft from the thread fetched fresh. The only context is that thread and the
    person's own messages in it (design §6.3: never another thread's text)."""
    from database import db
    source_row = await db.inbox_get_source(item["source_id"])
    if source_row is None:
        raise HTTPException(status_code=409, detail={"code": "cannot_send",
                                                     "message": "the item's source is not configured"})
    try:
        source = await store.open_source(source_row)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail={"code": "source_unavailable",
                                                     "message": type(exc).__name__})
    try:
        me = store.identity_from_row(source_row) or await source.whoami()
        thread = await source.thread(store.ref_from_json(item["thread_ref"]))
        history = [m for m in thread.messages if me.is_me(m.author_id)]
        draft = await drafts.make_draft(thread, me, history)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — signed out, no model: the UI shows it
        logger.info("inbox: draft for %s failed: %s", item["id"], type(exc).__name__)
        raise HTTPException(status_code=503, detail={"code": "draft_unavailable",
                                                     "message": type(exc).__name__})
    finally:
        try:
            await source.close()
        except Exception:  # noqa: BLE001
            logger.debug("inbox: source close failed", exc_info=True)
    row = {"id": store.new_id("dr"), "item_id": item["id"], "text": draft.text,
           "lang": draft.lang or "", "note": draft.note or "", "model": "drafts.make_draft",
           "created_at": store.ts(store.now())}
    await db.inbox_insert_draft(row)
    return row


@router.get("/items/{item_id}/draft", response_model=DraftOut)
async def get_draft(item_id: str) -> DraftOut:
    """The latest draft; the first read makes one (one at a time per item)."""
    from database import db
    item = await _item(item_id)
    lock = _draft_locks.setdefault((id(asyncio.get_running_loop()), item_id), asyncio.Lock())
    async with lock:
        latest = await db.inbox_latest_draft(item_id)
        return _draft_out(latest or await _make_draft(item))


@router.post("/items/{item_id}/redraft", response_model=DraftOut)
async def redraft(item_id: str, user_action: Optional[str] = _UA) -> DraftOut:
    _require_app(user_action)
    return _draft_out(await _make_draft(await _item(item_id)))


@router.post("/items/{item_id}/send", response_model=SendOut)
async def send_reply(item_id: str, body: SendRequest, request: Request,
                     user_action: Optional[str] = _UA) -> SendOut:
    """The only route that sends (design §8)."""
    await _require_desktop_ui(request, user_action)
    try:
        return SendOut(**await send.send_item(item_id, body.text, body.target_sha, body.draft_id))
    except send.SendRefused as exc:
        raise _refused(exc)


@router.post("/items/{item_id}/action", response_model=ItemOut)
async def item_action(item_id: str, body: ItemActionRequest,
                      user_action: Optional[str] = _UA) -> ItemOut:
    from database import db
    _require_app(user_action)
    item = await _item(item_id)
    stamp = store.now()
    if item["state"] not in (ItemState.OPEN.value, ItemState.SNOOZED.value):
        raise HTTPException(status_code=409, detail={"code": "not_open",
                                                     "message": f"the item is {item['state']}"})
    if body.action == "later":
        fields = {"state": ItemState.SNOOZED.value,
                  "snooze_until": store.ts(stamp + timedelta(seconds=SNOOZE_S))}
    elif body.action == "dismiss":
        fields = {"state": ItemState.DISMISSED.value, "snooze_until": None}
        await db.inbox_insert_feedback({
            "id": store.new_id("fb"), "item_id": item_id, "source_id": item["source_id"],
            "action": "dismiss", "from_lane": item["lane"], "to_lane": None,
            "created_at": store.ts(stamp)})
    else:
        fields = {"state": ItemState.DONE.value, "snooze_until": None}
    if not await db.inbox_update_item(item_id, store.ts(stamp), expect_state=item["state"], **fields):
        raise HTTPException(status_code=409, detail={"code": "not_open",
                                                     "message": "the item changed meanwhile"})
    return ItemOut(**store.item_out(await db.inbox_get_item(item_id)))


# ── sends ────────────────────────────────────────────────────────────────────

@router.get("/sends", response_model=SendsOut)
async def list_sends(filter: Literal["all", "mine", "auto"] = "all") -> SendsOut:
    from database import db
    origin = {"all": None, "mine": "click", "auto": "auto"}[filter]
    need = await db.inbox_sends((SendStatus.FAILED.value, SendStatus.UNKNOWN.value), origin=origin,
                                open_items_only=True)
    sent = await db.inbox_sends((SendStatus.SENT.value,), origin=origin,
                                since=store.ts(store.now() - timedelta(days=30)))
    sources = {s["id"]: s for s in await db.inbox_list_sources()}
    counts = await db.inbox_send_counts(store.ts(store.now() - timedelta(days=7)))

    async def out(rows: list[dict]) -> list[SendOut]:
        return [SendOut(**await store.send_out(r, source=sources.get(r["source_id"], {})))
                for r in rows]

    return SendsOut(need_you=await out(need), sent=await out(sent),
                    total_7d=counts["sent"], auto_7d=counts["auto"])


@router.post("/sends/{send_id}/not-sent", response_model=SendOut)
async def mark_not_sent(send_id: str, request: Request, user_action: Optional[str] = _UA) -> SendOut:
    await _require_desktop_ui(request, user_action)
    try:
        return SendOut(**await send.not_sent(send_id))
    except send.SendRefused as exc:
        raise _refused(exc)


@router.post("/sends/{send_id}/resend", response_model=SendOut)
async def resend(send_id: str, request: Request, user_action: Optional[str] = _UA) -> SendOut:
    await _require_desktop_ui(request, user_action)
    try:
        return SendOut(**await send.resend(send_id))
    except send.SendRefused as exc:
        raise _refused(exc)


# ── trends, sources, refresh ─────────────────────────────────────────────────

def _enabled_feed_count() -> int:
    from jobs.config_io import read_config
    return sum(1 for f in read_config().get("feeds") or []
               if isinstance(f, dict) and f.get("enabled", True) is not False)


@router.get("/trends/summary", response_model=TrendSummaryOut)
async def trend_summary() -> TrendSummaryOut:
    from database import db
    latest = await db.inbox_latest_trend_summary()
    feeds = await executors.run_nav(_enabled_feed_count)
    watched = 0
    for s in await db.inbox_list_sources():
        repos = json.loads(s["config"] or "{}").get("watched_repos")
        if s["enabled"] and isinstance(repos, list):
            watched += len(repos)
    points = [TrendPoint(**p) for p in json.loads(latest["points"])] if latest else []
    return TrendSummaryOut(generated_at=latest["generated_at"] if latest else None,
                           points=points, feeds=feeds, watched_repos=watched)


async def _sources_out() -> list[SourceOut]:
    from database import db
    feeds = await store.sync_feed_source()
    return [SourceOut(**store.source_out(r, feeds=feeds)) for r in await db.inbox_list_sources()]


@router.get("/sources", response_model=list[SourceOut])
async def list_sources() -> list[SourceOut]:
    return await _sources_out()


@router.post("/sources/{source_id}/retry", response_model=SourceOut)
async def retry_source(source_id: str, user_action: Optional[str] = _UA) -> SourceOut:
    from database import db
    _require_app(user_action)
    if await db.inbox_get_source(source_id) is None:
        await store.sync_feed_source()
        if await db.inbox_get_source(source_id) is None:
            raise HTTPException(status_code=404, detail={"code": "not_found",
                                                         "message": "no such source"})
    await runner.pull_now([source_id])
    for out in await _sources_out():
        if out.id == source_id:
            return out
    raise HTTPException(status_code=404, detail={"code": "not_found", "message": "no such source"})


@router.post("/refresh", response_model=InboxCounts)
async def refresh(user_action: Optional[str] = _UA) -> InboxCounts:
    """Pull every enabled source now (the person's click: interval and window ignored)."""
    _require_app(user_action)
    await runner.pull_now()
    return InboxCounts(**await store.counts())


# ── feed configuration (moved from /api/community/feeds, unchanged in behaviour) ──

def _config_path():
    from jobs.paths import CONFIG_FILE
    return CONFIG_FILE


@router.get("/feeds")
async def list_feeds() -> dict:
    """Configured feeds (a view of config.yaml ``feeds:``), the shape of
    ``GET /api/community/sources``."""
    from core.community_data import parse_sources
    sources = await executors.run_nav(parse_sources, _config_path())
    return {"count": len(sources), "sources": sources}


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


class MemberBody(BaseModel):
    """One editable member of a feed (a URL / keyword / query / repo), in the body (member
    values contain slashes)."""
    value: str


class _InvalidMember(Exception):
    """A member value that fails its kind's format rule."""


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


def _member_key_for_type(feed_type: str) -> str | None:
    """The config key holding this feed type's editable string members (jobs.models
    MEMBER_KEY, keyed by the FeedType enum), or None."""
    from jobs.models import FeedType, MEMBER_KEY
    try:
        return MEMBER_KEY.get(FeedType(feed_type))
    except ValueError:
        return None


def _validate_member(key: str, value: str) -> None:
    """Per-kind validation (raise _InvalidMember -> 422): urls https-only and not a
    private/link-local/metadata IP literal; repos ``owner/repo``; logins a valid GitHub
    login; keywords free text."""
    if key == "urls":
        from urllib.parse import urlparse
        p = urlparse(value)
        if p.scheme != "https" or not p.netloc:
            raise _InvalidMember("must be an https:// URL")
        from jobs.adapters.http_client import is_blocked_ip_literal
        if is_blocked_ip_literal(p.hostname):
            raise _InvalidMember("must not be a private/link-local/metadata IP")
    elif key == "repos":
        parts = value.split("/")
        if len(parts) != 2 or not parts[0] or not parts[1] or any(c.isspace() for c in value):
            raise _InvalidMember("must be 'owner/repo'")
    elif key == "logins":
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}", value):
            raise _InvalidMember("must be a valid GitHub login (alphanumeric + hyphens, ≤39 chars)")


@router.post("/feeds")
async def add_feed(feed: NewFeed) -> dict:
    """Add a feed (managed_by:user). 422 on an empty id, an unknown type or tier, or an
    invalid url in config.urls; 409 on a duplicate id."""
    if not feed.id.strip():
        raise HTTPException(status_code=422, detail="Feed id cannot be empty")
    if not _valid_type(feed.type):
        raise HTTPException(status_code=422, detail=f"Invalid feed type '{feed.type}'")
    if not _valid_tier(feed.tier):
        raise HTTPException(status_code=422, detail=f"Invalid tier '{feed.tier}'")
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
            "config": feed.config, "tags": feed.tags, "enabled": True, "managed_by": "user",
        })

    try:
        await executors.run_nav(mutate_config, _mutator)
    except _Dup:
        raise HTTPException(status_code=409, detail=f"Feed id '{feed.id}' already exists")
    runner.wake()
    return {"ok": True, "id": feed.id}


@router.put("/feeds/{feed_id}")
async def update_feed(feed_id: str, patch: FeedPatch) -> dict:
    """Toggle enabled and/or change tier. 404 if absent, 422 on an invalid tier; the edit
    stamps managed_by:user."""
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
                f["managed_by"] = "user"
                return
        raise _NotFound()

    try:
        await executors.run_nav(mutate_config, _mutator)
    except _NotFound:
        raise HTTPException(status_code=404, detail=f"Feed '{feed_id}' not found")
    runner.wake()
    return {"ok": True, "id": feed_id}


@router.delete("/feeds/{feed_id}")
async def delete_feed(feed_id: str) -> dict:
    """Remove a feed. Idempotent: a missing id is a 200 with removed=false."""
    from jobs.config_io import mutate_config

    def _mutator(config: dict) -> bool:
        feeds = config.get("feeds", [])
        kept = [f for f in feeds if not (isinstance(f, dict) and f.get("id") == feed_id)]
        removed = len(kept) != len(feeds)
        config["feeds"] = kept
        return removed

    removed = await executors.run_nav(mutate_config, _mutator)
    runner.wake()
    return {"ok": True, "id": feed_id, "removed": bool(removed)}


@router.post("/feeds/{feed_id}/members")
async def add_member(feed_id: str, body: MemberBody) -> dict:
    """Add a string member to a feed. 422 empty / no editable members / invalid for its
    kind, 409 duplicate, 404 missing feed; stamps managed_by:user."""
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
                key = _member_key_for_type(f.get("type", ""))
                if key is None:
                    raise _NoMemberType()
                _validate_member(key, value)
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
        await executors.run_nav(mutate_config, _mutator)
    except _NotFound:
        raise HTTPException(status_code=404, detail=f"Feed '{feed_id}' not found")
    except _NoMemberType:
        raise HTTPException(status_code=422, detail=f"Feed '{feed_id}' has no editable members")
    except _InvalidMember as e:
        raise HTTPException(status_code=422, detail=f"Invalid member for '{feed_id}': {e}")
    except _Dup:
        raise HTTPException(status_code=409, detail=f"Member already exists in '{feed_id}'")
    runner.wake()
    return {"ok": True, "id": feed_id, "value": value}


@router.delete("/feeds/{feed_id}/members")
async def delete_member(feed_id: str, body: MemberBody) -> dict:
    """Remove a string member. Idempotent: an absent member is a 200 with removed=false."""
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

    removed = await executors.run_nav(mutate_config, _mutator)
    runner.wake()
    return {"ok": True, "id": feed_id, "value": value, "removed": bool(removed)}
