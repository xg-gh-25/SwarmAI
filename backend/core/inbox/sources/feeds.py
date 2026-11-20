"""Feed sources (``kind="feed:<type>"``): the existing signal fetchers, read only.

Design: ``docs/design/2026-11-20-inbox.md`` §2 and §4. Each fetcher of
``jobs.handlers.signal_fetch.ADAPTER_MAP`` (RSS, Hacker News, GitHub releases and
trending, ...) becomes one source kind, ``feed:<FeedType value>``. The fetchers are called
unchanged (the old signal pipeline still uses them), off the event loop.

The feed's configuration stays in ``config.yaml`` (``feeds:``); a source names it with
``options["feed_id"]`` (read through ``jobs.config_io.read_config``) or carries it inline as
``options["feed"]``. Every item is a ``Message`` whose ``external_id`` is its URL (items
without a URL have no identity and are skipped); the cursor is the newest publication
time, and items not newer than it are dropped.

Feeds are read-only: ``can_send`` is false, ``reply`` raises ``SendFailed``, ``find_sent``
finds nothing, and ``thread`` has no reply target.

Key public symbols: ``FeedSource``, ``FEED_SOURCE_CLASSES``, ``create``.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, ClassVar, Optional

from core.inbox.models import Identity, Message, PullResult, SendReceipt, SourceCategory, Thread, ThreadRef
from core.inbox.sources.base import SendFailed, SourceConfig, register_source
from core.inbox.sources.common import SourceUnavailable, ToolGate, parse_time
from jobs.handlers.signal_fetch import ADAPTER_MAP
from jobs.models import Feed, FeedType

logger = logging.getLogger(__name__)

_DEFAULT_AGE_H = 48
_MAX_AGE_H = 24 * 7


class FeedSource:
    """One feed type. Subclasses (one per ``FeedType`` in ``ADAPTER_MAP``) set the class vars."""

    kind: ClassVar[str] = "feed"
    category: ClassVar[SourceCategory] = SourceCategory.FEED
    feed_type: ClassVar[FeedType]
    READ_TOOLS: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, config: SourceConfig, *, fetcher: Any = None) -> None:
        self.config = config
        self._gate = ToolGate(f"{self.kind}:{config.id}", self.READ_TOOLS)
        self._fetcher = fetcher

    @classmethod
    def tool_name(cls) -> str:
        return f"fetch:{cls.feed_type.value}"

    def _feed(self) -> Feed:
        opts = self.config.options or {}
        if isinstance(opts.get("feed"), dict):
            feed = Feed(**opts["feed"])
        else:
            feed_id = str(opts.get("feed_id") or "")
            if not feed_id:
                raise SourceUnavailable(f"{self.config.id}: options.feed_id is not set")
            from jobs.config_io import read_config

            entries = (read_config() or {}).get("feeds") or []
            entry = next((f for f in entries if isinstance(f, dict) and f.get("id") == feed_id), None)
            if entry is None:
                raise SourceUnavailable(f"{self.config.id}: no feed {feed_id!r} in config.yaml")
            feed = Feed(**entry)
        if feed.type != self.feed_type:
            raise SourceUnavailable(f"{self.config.id}: feed {feed.id!r} is {feed.type.value}, not {self.feed_type.value}")
        return feed

    async def whoami(self) -> Identity:
        return Identity(ids=frozenset(), display="")

    async def can_send(self) -> bool:
        return False

    async def pull(self, cursor: Optional[str]) -> PullResult:
        feed = await asyncio.to_thread(self._feed)
        since = parse_time(cursor) if cursor else None
        age_h = _DEFAULT_AGE_H
        if since is not None:
            hours = (datetime.now(timezone.utc) - since).total_seconds() / 3600 + 1
            age_h = int(min(max(hours, 1), _MAX_AGE_H))
        self._gate.check(self.tool_name())
        fetch = self._fetcher or ADAPTER_MAP[self.feed_type]
        signals = await asyncio.to_thread(fetch, feed, max_age_hours=age_h)
        now = datetime.now(timezone.utc)
        out: dict[str, Message] = {}
        newest = since
        skipped = 0
        for sig in signals or ():
            url = str(getattr(sig, "url", "") or "")
            if not url:
                skipped += 1
                continue
            published = parse_time(getattr(sig, "published", None))
            if since is not None and published is not None and published <= since:
                continue
            if published is not None and (newest is None or published > newest):
                newest = published
            out.setdefault(url, Message(
                external_id=url,
                thread=ThreadRef(self.kind, {"url": url}),
                author_id=str(getattr(sig, "source", "") or feed.name),
                author_name=str(getattr(sig, "source", "") or feed.name),
                received_at=published or now,
                text=str(getattr(sig, "summary", "") or ""),
                url=url,
                title=str(getattr(sig, "title", "") or ""),
                extra={"feed_id": feed.id, "source_label": feed.name,
                       "tags": list(getattr(sig, "tags", []) or []), "tier": str(feed.tier.value if hasattr(feed.tier, "value") else feed.tier)},
            ))
        if skipped:
            logger.info("feed source %s: %d item(s) without a URL skipped", self.config.id, skipped)
        cursor_out = (newest or since or now).isoformat()
        return PullResult(tuple(sorted(out.values(), key=lambda m: m.received_at)), cursor_out)

    async def thread(self, ref: ThreadRef) -> Thread:
        return Thread(ref=ref, messages=(), target=None)

    async def reply(self, ref: ThreadRef, text: str) -> SendReceipt:
        raise SendFailed("feeds are read-only")

    async def find_sent(self, ref: ThreadRef, after: datetime, text: str) -> Optional[SendReceipt]:
        return None

    async def close(self) -> None:
        return None


def _feed_class(feed_type: FeedType) -> type[FeedSource]:
    name = "FeedSource_" + feed_type.value.replace("-", "_")
    return type(name, (FeedSource,), {
        "kind": f"feed:{feed_type.value}",
        "feed_type": feed_type,
        "READ_TOOLS": frozenset({f"fetch:{feed_type.value}"}),
        "__doc__": f"The {feed_type.value} feed.",
    })


FEED_SOURCE_CLASSES: dict[str, type[FeedSource]] = {}
for _type in ADAPTER_MAP:
    _cls = _feed_class(_type)
    FEED_SOURCE_CLASSES[_cls.kind] = _cls


def create(config: SourceConfig) -> FeedSource:
    cls = FEED_SOURCE_CLASSES.get(config.kind)
    if cls is None:
        raise ValueError(f"no feed fetcher for {config.kind!r}")
    return cls(config)


for _kind in FEED_SOURCE_CLASSES:
    register_source(_kind, create)


class AllFeedsSource:
    """The one ``feed`` source the store keeps for every enabled feed of config.yaml
    (``options["feeds"]``, design §5): each feed is pulled through its type's source, with
    its own cursor (the cursor of this source is a JSON map ``{feed id: cursor}``). A feed
    whose type has no fetcher (the GitHub community subscriptions) is skipped; a feed that
    fails is logged and skipped, so one broken feed never stops the others."""

    kind: ClassVar[str] = "feed"
    category: ClassVar[SourceCategory] = SourceCategory.FEED
    READ_TOOLS: ClassVar[frozenset[str]] = frozenset(
        tool for cls in FEED_SOURCE_CLASSES.values() for tool in cls.READ_TOOLS)

    def __init__(self, config: SourceConfig, *, fetchers: Optional[dict[FeedType, Any]] = None) -> None:
        self.config = config
        self._fetchers = fetchers or {}

    def _one(self, feed: dict) -> Optional[FeedSource]:
        try:
            cls = FEED_SOURCE_CLASSES.get(f"feed:{FeedType(feed.get('type')).value}")
        except ValueError:
            cls = None
        if cls is None:
            return None
        return cls(SourceConfig(id=f"{self.config.id}:{feed.get('id')}", kind=cls.kind, backend="feed",
                                options={"feed": feed}), fetcher=self._fetchers.get(cls.feed_type))

    async def whoami(self) -> Identity:
        return Identity(ids=frozenset(), display="")

    async def can_send(self) -> bool:
        return False

    async def pull(self, cursor: Optional[str]) -> PullResult:
        try:
            cursors = json.loads(cursor) if cursor else {}
        except ValueError:
            cursors = {}
        if not isinstance(cursors, dict):
            cursors = {}
        out: list[Message] = []
        for feed in (self.config.options or {}).get("feeds") or ():
            if not isinstance(feed, dict) or not feed.get("id"):
                continue
            src = self._one(feed)
            if src is None:
                continue
            try:
                result = await src.pull(cursors.get(str(feed["id"])))
            except Exception as exc:  # noqa: BLE001 — one feed never stops the others
                logger.warning("feed %s skipped this pull: %s", feed.get("id"), type(exc).__name__)
                continue
            out.extend(result.messages)
            if result.cursor is not None:
                cursors[str(feed["id"])] = result.cursor
        return PullResult(tuple(sorted(out, key=lambda m: m.received_at)), json.dumps(cursors, sort_keys=True))

    async def thread(self, ref: ThreadRef) -> Thread:
        return Thread(ref=ref, messages=(), target=None)

    async def reply(self, ref: ThreadRef, text: str) -> SendReceipt:
        raise SendFailed("feeds are read-only")

    async def find_sent(self, ref: ThreadRef, after: datetime, text: str) -> Optional[SendReceipt]:
        return None

    async def close(self) -> None:
        return None


register_source(AllFeedsSource.kind, AllFeedsSource)
