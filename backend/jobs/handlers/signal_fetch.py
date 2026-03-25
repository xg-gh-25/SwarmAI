"""
Signal Fetch Handler

Orchestrates all feed adapters: reads config, dispatches to the right adapter
by feed type, deduplicates results, and stores raw signals in scheduler state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..models import Feed, FeedType, JobResult, RawSignal, SchedulerState
from ..dedup import dedup_signals, trim_dedup_cache
from ..adapters.rss import fetch_rss
from ..adapters.github_releases import fetch_github_releases
from ..adapters.hacker_news import fetch_hacker_news
from ..adapters.web_search import fetch_web_search

logger = logging.getLogger(__name__)

# Adapter dispatch table
ADAPTER_MAP = {
    FeedType.RSS: fetch_rss,
    FeedType.GITHUB_RELEASES: fetch_github_releases,
    FeedType.HACKER_NEWS: fetch_hacker_news,
    FeedType.WEB_SEARCH: fetch_web_search,
}


def handle_signal_fetch(
    feeds: list[Feed],
    state: SchedulerState,
    max_age_hours: int = 48,
) -> JobResult:
    """
    Fetch signals from all enabled feeds, dedup, and buffer in state.

    Args:
        feeds: List of feed configs from config.yaml
        state: Mutable scheduler state (raw_signals and dedup_cache updated in place)
        max_age_hours: Skip signals older than this

    Returns:
        JobResult with summary of what was fetched
    """
    start = datetime.now(timezone.utc)
    all_raw: list[RawSignal] = []
    errors: list[str] = []
    feeds_processed = 0

    enabled_feeds = [f for f in feeds if f.enabled]
    logger.info(f"Signal fetch starting: {len(enabled_feeds)} enabled feeds")

    for feed in enabled_feeds:
        adapter = ADAPTER_MAP.get(feed.type)
        if not adapter:
            logger.warning(f"No adapter for feed type '{feed.type}' (feed: {feed.id})")
            continue

        try:
            signals = adapter(feed, max_age_hours=max_age_hours)
            all_raw.extend(signals)
            feeds_processed += 1
            logger.info(f"Feed '{feed.id}': {len(signals)} signals")
        except Exception as e:
            error_msg = f"Feed '{feed.id}' failed: {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    # Dedup against previously seen URLs
    unique, updated_cache = dedup_signals(all_raw, state.dedup_cache)
    state.dedup_cache = trim_dedup_cache(updated_cache)

    # Buffer unique signals for digest handler
    state.raw_signals.extend(unique)

    duration = (datetime.now(timezone.utc) - start).total_seconds()

    summary_parts = [
        f"Fetched {len(all_raw)} raw signals from {feeds_processed} feeds",
        f"{len(unique)} unique after dedup",
        f"{len(state.raw_signals)} total buffered",
    ]
    if errors:
        summary_parts.append(f"{len(errors)} errors: {'; '.join(errors[:3])}")

    summary = ". ".join(summary_parts)
    logger.info(summary)

    return JobResult(
        job_id="signal-fetch",
        timestamp=datetime.now(timezone.utc),
        status="success" if not errors else "partial",
        summary=summary,
        signals_count=len(unique),
        duration_seconds=duration,
        error="; ".join(errors) if errors else None,
    )
