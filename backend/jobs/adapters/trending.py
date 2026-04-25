"""
Chinese Trending News Adapter

Fetches hot-search data from 11 Chinese platforms via the newsnow public API.
Free, no API key needed. Each platform is a separate GET request.

Platforms: weibo, zhihu, toutiao, baidu, douyin, bilibili-hot-search,
           wallstreetcn-hot, thepaper, cls-hot, ifeng, tieba

API: GET https://newsnow.busiyi.world/api/s?id={platform_id}&latest
Response: {"status": "success"|"cache", "items": [{"title": "...", "url": "...", "mobileUrl": "..."}]}
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .http_client import safe_client
from ..models import Feed, RawSignal

logger = logging.getLogger(__name__)

NEWSNOW_API = "https://newsnow.busiyi.world/api/s"

# Browser-like headers required by newsnow API (403 without them)
NEWSNOW_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://newsnow.busiyi.world/",
}


def fetch_trending(feed: Feed, max_age_hours: int = 48) -> list[RawSignal]:
    """
    Fetch hot-search signals from Chinese platforms via newsnow API.

    Args:
        feed: Feed config with platforms list in feed.config
        max_age_hours: Unused (trending data is always "now"), kept for adapter interface

    Returns:
        List of RawSignal from all configured platforms
    """
    platforms: list[dict] = feed.config.get("platforms", [])
    top_n: int = feed.config.get("top_n", 10)
    interval_ms: int = feed.config.get("request_interval_ms", 500)

    if not platforms:
        logger.warning(f"Trending feed '{feed.id}' has no platforms configured")
        return []

    signals: list[RawSignal] = []

    with safe_client(timeout=15, headers=NEWSNOW_HEADERS) as client:
        for i, platform in enumerate(platforms):
            platform_id = platform.get("id", "")
            platform_name = platform.get("name", platform_id)

            if not platform_id:
                continue

            try:
                url = f"{NEWSNOW_API}?id={platform_id}&latest"
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()

                status = data.get("status", "")
                if status not in ("success", "cache"):
                    logger.warning(
                        f"Trending '{platform_id}': unexpected status '{status}', skipping"
                    )
                    continue

                items = data.get("items", [])
                for rank, item in enumerate(items[:top_n], 1):
                    title = item.get("title")
                    if not title or not str(title).strip():
                        continue

                    title = str(title).strip()
                    url = item.get("url", "")
                    mobile_url = item.get("mobileUrl", "")

                    signals.append(RawSignal(
                        feed_id=feed.id,
                        title=title,
                        url=url or mobile_url,
                        summary=f"Top {rank} on {platform_name}",
                        published=datetime.now(timezone.utc),
                        source=platform_name,
                        tags=feed.tags,
                    ))

                logger.info(
                    f"Trending '{platform_id}' ({platform_name}): "
                    f"{min(len(items), top_n)} items ({status})"
                )

            except Exception as e:
                logger.error(f"Trending '{platform_id}' failed: {e}")
                continue

            # Rate limiting between platforms (skip after last).
            # This runs in a thread pool worker (job executor), not the event loop,
            # so time.sleep is safe — it only blocks the worker thread.
            if i < len(platforms) - 1 and interval_ms > 0:
                time.sleep(interval_ms / 1000)

    logger.info(f"Trending feed '{feed.id}': {len(signals)} total signals")
    return signals
