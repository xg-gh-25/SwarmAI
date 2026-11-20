"""Feed fetchers: one ``fetch_x(feed, max_age_hours=...) -> list[RawSignal]`` per feed type.

Design: ``docs/design/2026-11-20-inbox.md`` §2 and §10. The Inbox feed source
(``core/inbox/sources/feeds.py``) turns each fetcher of ``ADAPTER_MAP`` into one source
kind ``feed:<FeedType value>``. Every outbound request goes through the SSRF-guarded
client in ``core/http_client.py``. The feeds themselves stay configured in
``Services/swarm-jobs/config.yaml`` (``feeds:``); ``config.py`` reads that list for the
Inbox's feed settings.

``FeedType.GITHUB_COMMUNITY`` and ``FeedType.GITHUB_PEOPLE`` have no fetcher on purpose:
those feeds are the subscription list of the GitHub community engine, which reads
config.yaml itself.

Key public symbols: ``ADAPTER_MAP``.
"""
from __future__ import annotations

from typing import Callable

from jobs.models import Feed, FeedType, RawSignal

from .eastmoney_market import fetch_eastmoney_market
from .github_releases import fetch_github_releases
from .github_trending import fetch_github_trending
from .hacker_news import fetch_hacker_news
from .rss import fetch_rss
from .trending import fetch_trending
from .web_search import fetch_web_search
from .weibo_trending import fetch_weibo_trending

Fetcher = Callable[..., "list[RawSignal]"]

ADAPTER_MAP: dict[FeedType, Fetcher] = {
    FeedType.RSS: fetch_rss,
    FeedType.GITHUB_RELEASES: fetch_github_releases,
    FeedType.HACKER_NEWS: fetch_hacker_news,
    FeedType.WEB_SEARCH: fetch_web_search,
    FeedType.TRENDING: fetch_trending,
    FeedType.GITHUB_TRENDING: fetch_github_trending,
    FeedType.WEIBO_TRENDING: fetch_weibo_trending,
    FeedType.EASTMONEY_MARKET: fetch_eastmoney_market,
}

__all__ = ["ADAPTER_MAP", "Feed", "Fetcher"]
