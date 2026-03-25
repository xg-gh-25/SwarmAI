"""
Web Search Adapter (Tavily API)

DISABLED — requires paid Tavily API key.
Stub implementation that returns empty results with a clear message.

To enable:
1. Get API key from https://tavily.com
2. Set TAVILY_API_KEY environment variable
3. Set feed.enabled = true in config.yaml
"""

from __future__ import annotations

import logging
import os

from ..models import Feed, RawSignal

logger = logging.getLogger(__name__)


def fetch_web_search(feed: Feed, max_age_hours: int = 48) -> list[RawSignal]:
    """
    Fetch signals via Tavily web search API.
    Currently disabled — returns empty list with warning.
    """
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        logger.info(
            f"Web search feed '{feed.id}' skipped — TAVILY_API_KEY not set. "
            "This is a paid API. Set the key when ready."
        )
        return []

    # TODO: Implement Tavily search when API key is available
    # queries = feed.config.get("queries", [])
    # max_results = feed.config.get("max_results_per_query", 5)
    # ...

    logger.warning(f"Web search feed '{feed.id}' — Tavily integration not yet implemented")
    return []
