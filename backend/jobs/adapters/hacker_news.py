"""
Hacker News Adapter

Fetches AI-filtered top stories from HN via Algolia API.
Free, no API key needed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .http_client import safe_client
from ..models import Feed, RawSignal

logger = logging.getLogger(__name__)

HN_SEARCH_API = "https://hn.algolia.com/api/v1/search"
HN_ITEM_URL = "https://news.ycombinator.com/item?id="


def fetch_hacker_news(feed: Feed, max_age_hours: int = 48) -> list[RawSignal]:
    """
    Fetch AI-relevant stories from Hacker News via Algolia search API.

    Searches for each keyword, filters by min_score, deduplicates by story ID.
    """
    keywords: list[str] = feed.config.get("keywords", ["AI", "LLM"])
    min_score: int = feed.config.get("min_score", 100)
    max_stories: int = feed.config.get("max_stories", 10)

    seen_ids: set[int] = set()
    signals: list[RawSignal] = []

    # Calculate time filter: last N hours
    seconds_ago = max_age_hours * 3600
    created_after = int(datetime.now(timezone.utc).timestamp() - seconds_ago)

    with safe_client(timeout=15) as client:
        for keyword in keywords:
            try:
                resp = client.get(HN_SEARCH_API, params={
                    "query": keyword,
                    "tags": "story",
                    "numericFilters": f"points>={min_score},created_at_i>={created_after}",
                    "hitsPerPage": max_stories,
                })
                resp.raise_for_status()
                data = resp.json()

                for hit in data.get("hits", []):
                    story_id = hit.get("objectID")
                    if not story_id or int(story_id) in seen_ids:
                        continue
                    seen_ids.add(int(story_id))

                    title = hit.get("title", "").strip()
                    url = hit.get("url") or f"{HN_ITEM_URL}{story_id}"
                    points = hit.get("points", 0)
                    num_comments = hit.get("num_comments", 0)
                    created_at = hit.get("created_at_i", 0)

                    if not title:
                        continue

                    published = datetime.fromtimestamp(created_at, tz=timezone.utc) if created_at else None

                    signals.append(RawSignal(
                        feed_id=feed.id,
                        title=title,
                        url=url,
                        summary=f"HN: {points} points, {num_comments} comments",
                        published=published,
                        source="Hacker News",
                        tags=feed.tags,
                    ))

            except Exception as e:
                logger.error(f"Error fetching HN for keyword '{keyword}': {e}")
                continue

    # Sort by score (embedded in summary, but we can sort by published recency)
    signals.sort(key=lambda s: s.published or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    signals = signals[:max_stories]

    logger.info(f"HN adapter '{feed.id}': fetched {len(signals)} stories")
    return signals
