"""Inbox store: the domain side of the ``inbox_*`` tables (schema v20).

Design: ``docs/design/2026-11-20-inbox.md`` §5. ``database/sqlite.py`` holds the typed SQL;
this module maps rows to the shared models (``core/inbox/models.py``) and to the API shapes
(``schemas/inbox.py``), resolves which source object a row opens, and owns the Inbox's own
thread pool.

Rules this module keeps:

- **Identity comes from the source.** An item is one ``(source_id, external_id)``; the
  external id is the one the source assigned (or, for a submitted item, the one its
  producer gave). Nothing is keyed by text.
- **Private.** Items live only in ``data.db``. Nothing here writes a file, feeds a recall or
  Library index, or is listed in ``core/backup_manager.L2_TABLES``.
- **Feeds keep their configuration in ``config.yaml``.** There is one feed source row (id
  ``feed``): its cursor and health live in ``inbox_sources``; the feeds it pulls are read
  from ``Services/swarm-jobs/config.yaml`` ``feeds:`` (``jobs.config_io``) each time the
  source is opened, so a feed edit applies at the next pull.
- **Times** are UTC ISO-8601 strings of one width (``ts``), so a string comparison in SQL is
  a time comparison.

Key public symbols: ``ts`` / ``parse_ts`` / ``now``, ``run_blocking``, ``channel_label``,
``ensure_sources_loaded``, ``sync_feed_source``, ``open_source``, ``identity_from_row``,
``message_to_item_row``, ``item_message``, ``item_out``, ``send_out``, ``source_out``,
``counts``, ``FEED_SOURCE_ID``, ``DEFAULT_INTERVAL_S``.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import pkgutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, TypeVar
from uuid import uuid4

from core.inbox.models import (
    Identity,
    ItemState,
    Lane,
    Message,
    SourceCategory,
    SourceHealth,
    ThreadRef,
)
from core.inbox.sources import base as sources_base

logger = logging.getLogger(__name__)
T = TypeVar("T")

FEED_SOURCE_ID = "feed"
DEFAULT_INTERVAL_S = {SourceCategory.CONVERSATION.value: 900, SourceCategory.FEED.value: 21_600}
EXCERPT_CHARS = 2_000
LATE_AFTER = timedelta(days=2)

_CHANNEL_LABELS = {"slack": "Slack", "mail": "邮件", "github": "GitHub", "feed": "信息流"}

# The Inbox's own threads: the caller check (a psutil scan of the machine's TCP
# connections) and its file reads. Not the default pool and none of the pools the chat
# path waits on (core/executors: spawn, gate, subprocess, turn_*); two threads so a slow
# caller check never waits behind a config read.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="swarm-inbox")


async def run_blocking(fn: Callable[..., T], *args: Any) -> T:
    return await asyncio.get_running_loop().run_in_executor(_POOL, fn, *args)


# ── time ─────────────────────────────────────────────────────────────────────

def now() -> datetime:
    return datetime.now(timezone.utc)


def ts(dt: datetime) -> str:
    """UTC, microseconds, one width: ``2026-11-20T03:00:00.000000+00:00``."""
    if dt.tzinfo is None:
        dt = dt.astimezone()   # a naive time is local time
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.astimezone()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


# ── sources ──────────────────────────────────────────────────────────────────

def channel_label(kind: str) -> str:
    base = (kind or "").split(":", 1)[0]
    return _CHANNEL_LABELS.get(base, base or kind)


_loaded = False


def ensure_sources_loaded() -> None:
    """Import every module under ``core/inbox/sources`` once, so each registers its
    factory (``sources/base.register_source``). A module that fails to import is logged
    and skipped: the other sources still work."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    import core.inbox.sources as pkg
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name == "base" or info.name.startswith("_"):
            continue
        try:
            importlib.import_module(f"core.inbox.sources.{info.name}")
        except Exception as exc:  # noqa: BLE001 — one broken connector must not stop the rest
            logger.warning("inbox: source module %s failed to import: %s", info.name,
                           type(exc).__name__)


def _enabled_feeds() -> list[dict]:
    """The enabled feeds of config.yaml (blocking: a file read)."""
    from jobs.config_io import read_config
    feeds = read_config().get("feeds") or []
    return [f for f in feeds if isinstance(f, dict) and f.get("enabled", True) is not False]


async def sync_feed_source() -> int:
    """Keep the one feed source row in step with config.yaml: enabled while at least one
    feed is enabled. Returns the number of enabled feeds."""
    from database import db
    feeds = await run_blocking(_enabled_feeds)
    row = await db.inbox_get_source(FEED_SOURCE_ID)
    stamp = ts(now())
    if row is None:
        if not feeds:
            return 0
        await db.inbox_put_source({
            "id": FEED_SOURCE_ID, "kind": "feed", "backend": "feed",
            "category": SourceCategory.FEED.value, "config": "{}", "enabled": 1,
            "interval_s": DEFAULT_INTERVAL_S[SourceCategory.FEED.value], "cursor": None,
            "me": None, "can_send": 0, "health": SourceHealth.OK.value, "fix": "",
            "last_pull_at": None, "last_attempt_at": None, "last_error": "",
            "created_at": stamp, "updated_at": stamp})
    elif bool(row["enabled"]) != bool(feeds):
        await db.inbox_update_source(FEED_SOURCE_ID, stamp, enabled=1 if feeds else 0)
    return len(feeds)


async def open_source(row: dict) -> sources_base.Source:
    """The source object for an ``inbox_sources`` row. Raises ``SourceNotFound`` when no
    connector of that kind is registered."""
    ensure_sources_loaded()
    options = json.loads(row.get("config") or "{}")
    if row["id"] == FEED_SOURCE_ID:
        options = {**options, "feeds": await run_blocking(_enabled_feeds)}
    return sources_base.create_source(sources_base.SourceConfig(
        id=row["id"], kind=row["kind"], backend=row["backend"], options=options))


def identity_to_json(me: Identity) -> str:
    return json.dumps({"ids": sorted(me.ids), "display": me.display}, ensure_ascii=False)


def identity_from_row(row: dict) -> Optional[Identity]:
    try:
        data = json.loads(row.get("me") or "null")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return Identity(ids=frozenset(str(i) for i in data.get("ids") or []),
                    display=str(data.get("display") or ""))


def ref_to_json(ref: ThreadRef) -> str:
    return json.dumps({"source_kind": ref.source_kind, "data": ref.data},
                      sort_keys=True, ensure_ascii=False)


def ref_from_json(value: str) -> ThreadRef:
    data = json.loads(value)
    return ThreadRef(source_kind=str(data["source_kind"]), data=dict(data.get("data") or {}))


# ── items ────────────────────────────────────────────────────────────────────

def _short(value: Any, limit: int = 200) -> str:
    return str(value or "")[:limit]


def message_to_item_row(source_id: str, msg: Message, lane: Lane, floor: Optional[Lane],
                        *, origin: str = "pull", state: ItemState = ItemState.OPEN,
                        reason: str = "", producer: Optional[str] = None,
                        triaged: bool = False) -> dict:
    """A new ``inbox_items`` row for one message. Only facts the UI shows are kept: the
    excerpt is the first 2,000 characters (drafts and sends fetch the thread again)."""
    stamp = ts(now())
    meta = {"is_direct": msg.is_direct, "mentions_me": msg.mentions_me, "is_bot": msg.is_bot,
            "is_bulk": msg.is_bulk, "watched": msg.watched,
            "where": _short(msg.extra.get("where")), "author_role": _short(msg.extra.get("author_role"))}
    if producer:
        meta["producer"] = producer
    return {
        "id": new_id("it"), "source_id": source_id, "external_id": msg.external_id,
        "source_kind": msg.thread.source_kind, "thread_key": msg.thread.key(),
        "thread_ref": ref_to_json(msg.thread), "origin": origin,
        "author": _short(msg.author_name or msg.author_id), "author_id": _short(msg.author_id),
        "received_at": ts(msg.received_at), "title": _short(msg.title, 500),
        "excerpt": (msg.text or "")[:EXCERPT_CHARS], "url": _short(msg.url, 2_000),
        "meta": json.dumps(meta, ensure_ascii=False), "lane": lane.value,
        "rule_lane": lane.value, "floor": floor.value if floor else None, "priority": 3,
        "reason": reason, "state": state.value, "snooze_until": None,
        "triaged_at": stamp if triaged else None, "answered_at": None,
        "created_at": stamp, "updated_at": stamp,
    }


def item_message(row: dict) -> Message:
    """The stored part of an item as a ``Message`` (the excerpt, not the full text)."""
    meta = json.loads(row.get("meta") or "{}")
    return Message(
        external_id=row["external_id"], thread=ref_from_json(row["thread_ref"]),
        author_id=row["author_id"], author_name=row["author"],
        received_at=parse_ts(row["received_at"]) or now(), text=row["excerpt"],
        url=row["url"], title=row["title"], is_direct=bool(meta.get("is_direct")),
        mentions_me=bool(meta.get("mentions_me")), is_bot=bool(meta.get("is_bot")),
        is_bulk=bool(meta.get("is_bulk")), watched=bool(meta.get("watched")))


def _where(row: dict, meta: dict) -> str:
    if meta.get("where"):
        return str(meta["where"])
    label = channel_label(row["source_kind"])
    if meta.get("is_direct"):
        return f"{label} 私信"
    if meta.get("mentions_me"):
        return f"{label} · @你"
    return label


def item_out(row: dict) -> dict:
    """An item as ``schemas.inbox.ItemOut`` fields."""
    meta = json.loads(row.get("meta") or "{}")
    received = parse_ts(row["received_at"]) or now()
    ask = row["reason"] or row["title"] or " ".join((row["excerpt"] or "").split())[:120]
    return {
        "id": row["id"], "lane": row["lane"], "state": row["state"],
        "source_id": row["source_id"], "source_kind": row["source_kind"],
        "channel_label": channel_label(row["source_kind"]), "author": row["author"],
        "author_role": str(meta.get("author_role") or ""), "where": _where(row, meta),
        "received_at": row["received_at"], "late": now() - received > LATE_AFTER,
        "ask": ask, "ask_sub": row["title"] if row["reason"] else "",
        "excerpt": row["excerpt"], "url": row["url"], "priority": int(row["priority"]),
        "producer": meta.get("producer"),
    }


# ── sends ────────────────────────────────────────────────────────────────────

_UNHEALTHY = {SourceHealth.AUTH_FAILED.value, SourceHealth.ERROR.value, SourceHealth.READ_ONLY.value}


def split_origin(origin: str) -> tuple[str, Optional[str]]:
    """``click`` -> (click, None); ``auto:<producer>`` -> (auto, producer)."""
    kind, _, producer = (origin or "").partition(":")
    return ("auto", producer or None) if kind == "auto" else ("click", None)


async def send_out(row: dict, *, item: Optional[dict] = None,
                   source: Optional[dict] = None) -> dict:
    """A send as ``schemas.inbox.SendOut`` fields."""
    from database import db
    item = item or await db.inbox_get_item(row["item_id"]) or {}
    source = source if source is not None else (await db.inbox_get_source(row["source_id"]) or {})
    origin, producer = split_origin(row["origin"])
    target = json.loads(row.get("target") or "{}")
    kind = item.get("source_kind") or source.get("kind") or ""
    reply_item = None
    if row["status"] == "sent" and item:
        reply_item = await db.inbox_later_item(row["source_id"], item["thread_key"],
                                               row["sent_at"] or row["created_at"])
    return {
        "id": row["id"], "item_id": row["item_id"], "origin": origin, "producer": producer,
        "status": row["status"], "to_label": str(target.get("label") or ""),
        "source_kind": kind, "channel_label": channel_label(kind), "text": row["text"],
        "url": row["url"] or "", "created_at": row["created_at"], "sent_at": row["sent_at"],
        "error": row["error"] or "",
        "blocked_by_source": row["status"] == "failed" and (
            not source or source.get("health") in _UNHEALTHY or not source.get("can_send")),
        "has_reply": reply_item is not None, "reply_item_id": reply_item,
    }


# ── sources out, counts ──────────────────────────────────────────────────────

def source_out(row: dict, *, feeds: Optional[int] = None) -> dict:
    """A source as ``schemas.inbox.SourceOut`` fields."""
    me = identity_from_row(row)
    identity = (me.display or (sorted(me.ids)[0] if me.ids else "")) if me else ""
    options = json.loads(row.get("config") or "{}")
    if row["id"] == FEED_SOURCE_ID:
        detail = f"{feeds} 个 feed" if feeds is not None else ""
    else:
        parts = []
        for key, word in (("watched_channels", "频道"), ("watched_repos", "仓库")):
            if isinstance(options.get(key), list) and options[key]:
                parts.append(f"关注 {len(options[key])} 个{word}")
        detail = " · ".join(parts)
    return {
        "id": row["id"], "kind": row["kind"], "channel_label": channel_label(row["kind"]),
        "backend": row["backend"], "identity": identity, "interval_s": int(row["interval_s"]),
        "health": row["health"], "fix": row["fix"] or "", "last_pull_at": row["last_pull_at"],
        "last_error": row["last_error"] or "", "can_send": bool(row["can_send"]),
        "detail": detail,
    }


async def counts() -> dict:
    """``schemas.inbox.InboxCounts`` fields. Snoozed items due again are woken first."""
    from database import db
    current = now()
    await db.inbox_wake_snoozed(ts(current))
    items = await db.inbox_item_counts(ts(current - timedelta(hours=24)))
    sends = await db.inbox_send_counts(ts(current - timedelta(days=7)))
    sources = await db.inbox_list_sources()
    pulls = [s["last_pull_at"] for s in sources if s["last_pull_at"]]
    return {
        "reply": items.get(Lane.REPLY.value, 0), "proposed": items.get(Lane.PROPOSED.value, 0),
        "aware": items.get(Lane.AWARE.value, 0), "trend": items.get(Lane.TREND.value, 0),
        "sends_need_you": sends["need_you"], "skipped": items.get("skipped", 0),
        "unhealthy_sources": [s["id"] for s in sources if s["enabled"]
                              and s["health"] in (SourceHealth.AUTH_FAILED.value,
                                                  SourceHealth.ERROR.value)],
        "updated_at": max(pulls) if pulls else None,
    }
