"""
Weibo Trending Signal Adapter

Fetches Weibo hot search (热搜) and optional keyword-matched posts.
All endpoints are public — no authentication required.

Data sources:
- Hot search API: top trending topics with heat score
- Keyword search (optional): posts matching configured keywords

Rate limiting: ~30 req/min without auth. Our usage: 1 call/day for hot
search + optional 5 keyword searches = well within limits.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

from .http_client import safe_client
from ..models import Feed, RawSignal

logger = logging.getLogger(__name__)

# Public Weibo endpoints (no auth required)
WEIBO_HOT_SEARCH_URL = "https://weibo.com/ajax/side/hotSearch"
WEIBO_TOPIC_SEARCH_URL = "https://m.weibo.cn/api/container/getIndex"

# Browser-like headers (Weibo blocks empty UA)
WEIBO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://weibo.com/",
}

# Default keywords relevant to cloud/AI business
DEFAULT_KEYWORDS = [
    "云计算", "AI", "人工智能", "大模型", "AWS", "亚马逊云",
    "数字化转型", "AIGC", "智能体", "Agent",
]


def fetch_weibo_trending(feed: Feed, max_age_hours: int = 48) -> list[RawSignal]:
    """
    Fetch Weibo hot search + optional keyword signals.

    Args:
        feed: Feed config. Config keys:
            - keyword_search: bool, whether to also search by keyword (default False)
            - keywords: list[str], keywords to search (default: DEFAULT_KEYWORDS)
            - top_n: int, max hot search items to scan (default 30)
        max_age_hours: Unused (trending is always "now"), kept for adapter interface

    Returns:
        List of RawSignal, filtered to business-relevant topics
    """
    keywords = feed.config.get("keywords", DEFAULT_KEYWORDS)
    top_n = feed.config.get("top_n", 30)
    keyword_search = feed.config.get("keyword_search", False)

    signals: list[RawSignal] = []

    # 1. Hot search (top trending topics)
    try:
        hot_signals = _fetch_hot_search(feed.id, keywords, top_n)
        signals.extend(hot_signals)
    except Exception as e:
        logger.warning(f"Weibo hot search failed: {e}")

    # 2. Optional keyword search (uses same keywords list, capped at 5)
    if keyword_search:
        seen_urls: set[str] = {s.url for s in signals}  # dedup against hot search results
        for keyword in keywords[:5]:  # Cap at 5 to stay within rate limits
            try:
                keyword_signals = _search_keyword(feed.id, keyword)
                for sig in keyword_signals:
                    if sig.url not in seen_urls:
                        seen_urls.add(sig.url)
                        signals.append(sig)
            except Exception as e:
                logger.warning(f"Weibo keyword search '{keyword}' failed: {e}")
                continue

    logger.info(f"Weibo trending: {len(signals)} signals (hot + keyword)")
    return signals


def _fetch_hot_search(feed_id: str, keywords: list[str], top_n: int) -> list[RawSignal]:
    """Fetch hot search and filter by keywords."""
    signals: list[RawSignal] = []

    with safe_client(timeout=15, headers=WEIBO_HEADERS) as client:
        resp = client.get(WEIBO_HOT_SEARCH_URL)
        resp.raise_for_status()
        data = resp.json()

    hot_list = data.get("data", {}).get("realtime", [])
    if not hot_list:
        logger.warning("Weibo hot search: no 'realtime' data in response")
        return []

    for item in hot_list[:top_n]:
        word = item.get("word", "")
        if not word:
            continue

        # Keyword filter: only keep items matching business-relevant keywords
        if not any(kw.lower() in word.lower() for kw in keywords):
            continue

        raw_hot = item.get("raw_hot", 0)
        # Normalize heat to 0-1 range (1M+ heat = 1.0)
        score = min(float(raw_hot) / 1_000_000.0, 1.0) if raw_hot else 0.0

        signals.append(RawSignal(
            feed_id=feed_id,
            title=word,
            url=f"https://s.weibo.com/weibo?q=%23{quote(word)}%23",
            summary=f"微博热搜 热度:{raw_hot:,}",
            published=datetime.now(timezone.utc),
            source="微博热搜",
            tags=["weibo", "hot-search", "china"],
            score=score,
        ))

    return signals


def _search_keyword(feed_id: str, keyword: str) -> list[RawSignal]:
    """Search Weibo for posts matching a keyword (top 3 results)."""
    signals: list[RawSignal] = []

    # containerid for search: 100103type=1&q=KEYWORD
    container_id = f"100103type=1&q={keyword}"
    params = {"containerid": container_id, "page_type": "searchall"}

    with safe_client(timeout=15, headers=WEIBO_HEADERS) as client:
        resp = client.get(WEIBO_TOPIC_SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    cards = data.get("data", {}).get("cards", [])
    post_count = 0

    for card in cards:
        if post_count >= 3:
            break

        # Card group contains actual posts
        card_group = card.get("card_group", [])
        if not card_group:
            # Single card with mblog
            mblog = card.get("mblog")
            if mblog:
                signal = _parse_mblog(feed_id, mblog, keyword)
                if signal:
                    signals.append(signal)
                    post_count += 1
            continue

        for sub_card in card_group:
            if post_count >= 3:
                break
            mblog = sub_card.get("mblog")
            if mblog:
                signal = _parse_mblog(feed_id, mblog, keyword)
                if signal:
                    signals.append(signal)
                    post_count += 1

    return signals


def _parse_mblog(feed_id: str, mblog: dict, keyword: str) -> RawSignal | None:
    """Parse a Weibo mblog object into a RawSignal."""
    text = mblog.get("text", "")
    if not text:
        return None

    # Strip HTML tags from text
    clean_text = re.sub(r"<[^>]+>", "", text).strip()
    if not clean_text:
        return None

    mid = mblog.get("mid", "")
    uid = mblog.get("user", {}).get("id", "")
    reposts = mblog.get("reposts_count", 0)
    comments = mblog.get("comments_count", 0)

    # Engagement-based score (normalize: 1000+ engagement = 1.0)
    engagement = reposts + comments
    score = min(float(engagement) / 1000.0, 1.0)

    return RawSignal(
        feed_id=feed_id,
        title=f"[微博] {clean_text[:80]}",
        url=f"https://m.weibo.cn/detail/{mid}" if mid else f"https://weibo.com/u/{uid}",
        summary=f"转发:{reposts} 评论:{comments} 关键词:{keyword}",
        published=datetime.now(timezone.utc),
        source="微博搜索",
        tags=["weibo", "search", "china", keyword],
        score=score,
    )
