"""Feed Inbox sources: one kind per ADAPTER_MAP fetcher, read only, cursor by publication time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.inbox.models import SourceCategory, ThreadRef
from core.inbox.sources.base import SendFailed, SourceConfig
from core.inbox.sources.feeds import FEED_SOURCE_CLASSES, create
from jobs.handlers.signal_fetch import ADAPTER_MAP
from jobs.models import RawSignal

NOW = datetime.now(timezone.utc)
FEED = {"id": "hn-ai", "name": "Hacker News AI", "type": "hacker-news", "config": {"keywords": ["agents"]}}


def _signals(feed, max_age_hours):
    _signals.calls.append((feed.id, max_age_hours))
    return [
        RawSignal(feed_id=feed.id, title="New agent OS", url="https://news.example/1", summary="s1",
                  published=NOW - timedelta(minutes=30), source="HN"),
        RawSignal(feed_id=feed.id, title="Same story again", url="https://news.example/1", published=NOW - timedelta(minutes=30)),
        RawSignal(feed_id=feed.id, title="Old", url="https://news.example/2", published=NOW - timedelta(hours=5)),
        RawSignal(feed_id=feed.id, title="No link", url="", published=NOW),
    ]


_signals.calls = []


def _source(**options):
    cfg = SourceConfig(id="feed-hn", kind="feed:hacker-news", backend="feed", options={"feed": FEED, **options})
    src = create(cfg)
    src._fetcher = _signals
    return src


def test_every_adapter_map_fetcher_is_a_feed_kind():
    assert set(FEED_SOURCE_CLASSES) == {f"feed:{t.value}" for t in ADAPTER_MAP}
    for kind, cls in FEED_SOURCE_CLASSES.items():
        assert cls.category is SourceCategory.FEED
        assert cls.READ_TOOLS == frozenset({f"fetch:{kind.split(':', 1)[1]}"})


async def test_pull_wraps_the_fetcher_and_filters_by_cursor():
    _signals.calls.clear()
    result = await _source().pull((NOW - timedelta(hours=1)).isoformat())
    assert [m.external_id for m in result.messages] == ["https://news.example/1"]  # deduped, old and link-less dropped
    m = result.messages[0]
    assert m.title == "New agent OS" and m.author_name == "HN" and m.thread == ThreadRef("feed:hacker-news", {"url": m.url})
    assert _signals.calls == [("hn-ai", 2)]  # the window follows the cursor
    assert result.cursor == m.received_at.isoformat()


async def test_feeds_are_read_only():
    src = _source()
    assert not await src.can_send()
    with pytest.raises(SendFailed):
        await src.reply(ThreadRef("feed:hacker-news", {"url": "https://news.example/1"}), "hi")
    assert await src.find_sent(ThreadRef("feed:hacker-news", {"url": "u"}), NOW, "hi") is None
    assert (await src.thread(ThreadRef("feed:hacker-news", {"url": "u"}))).target is None


async def test_feed_of_another_type_is_refused():
    cfg = SourceConfig(id="f", kind="feed:rss", backend="feed", options={"feed": FEED})
    with pytest.raises(Exception, match="not rss"):
        await create(cfg).pull(None)


# The store keeps ONE source row for every enabled feed: id and kind "feed", with the feeds
# of config.yaml in options["feeds"] (core/inbox/store.open_source). It must resolve to a
# registered source that pulls each feed with its own cursor.

from jobs.models import FeedType  # noqa: E402
from core.inbox.sources.base import create_source  # noqa: E402
from core.inbox.sources.feeds import AllFeedsSource  # noqa: E402

RSS = {"id": "blog", "name": "A blog", "type": "rss", "config": {"url": "https://blog.example/feed"}}
COMMUNITY = {"id": "gh-tier1", "name": "Community tier 1", "type": "github-community", "config": {}}


def _rss(feed, max_age_hours):
    return [RawSignal(feed_id=feed.id, title="Post", url="https://blog.example/p1", published=NOW - timedelta(minutes=5))]


def _broken(feed, max_age_hours):
    raise RuntimeError("feed host down")


def _all(feeds, fetchers):
    return AllFeedsSource(SourceConfig(id="feed", kind="feed", backend="feed", options={"feeds": feeds}), fetchers=fetchers)


def test_the_store_feed_row_resolves_to_a_registered_source():
    src = create_source(SourceConfig(id="feed", kind="feed", backend="feed", options={"feeds": []}))
    assert isinstance(src, AllFeedsSource) and src.category is SourceCategory.FEED


@pytest.mark.asyncio
async def test_all_feeds_pull_each_feed_with_its_own_cursor_and_label():
    _signals.calls.clear()
    src = _all([FEED, RSS, COMMUNITY], {FeedType.HACKER_NEWS: _signals, FeedType.RSS: _rss})
    result = await src.pull(None)
    labels = {m.url: m.extra["source_label"] for m in result.messages}
    assert labels == {"https://news.example/1": "Hacker News AI", "https://news.example/2": "Hacker News AI",
                      "https://blog.example/p1": "A blog"}
    import json
    cursors = json.loads(result.cursor)
    assert set(cursors) == {"hn-ai", "blog"}          # the community feed has no fetcher: skipped
    again = await src.pull(result.cursor)             # each feed resumes from its own cursor
    assert again.messages == ()


@pytest.mark.asyncio
async def test_one_broken_feed_does_not_stop_the_others():
    src = _all([RSS, {**FEED, "id": "hn-broken"}], {FeedType.RSS: _rss, FeedType.HACKER_NEWS: _broken})
    result = await src.pull(None)
    assert [m.url for m in result.messages] == ["https://blog.example/p1"]
    import json
    assert set(json.loads(result.cursor)) == {"blog"}
