"""
GitHub Trending Adapter

Fetches trending repositories from github.com/trending by scraping HTML.
GitHub has no official Trending API — HTML scraping is the standard approach.

Each article.Box-row contains one repo with: owner/name, description,
language, total stars, and stars today.
"""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone

from .http_client import safe_client
from ..models import Feed, RawSignal

logger = logging.getLogger(__name__)

GITHUB_TRENDING_URL = "https://github.com/trending"

GITHUB_HEADERS = {
    "User-Agent": "SwarmAI/1.x (+https://github.com/xg-gh-25/SwarmAI)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# Regex patterns for extracting data from each Box-row article
_RE_REPO_LINK = re.compile(r'href="/([^/]+/[^/"]+)"[^>]*class="Link"', re.DOTALL)
_RE_DESCRIPTION = re.compile(
    r'<p\s+class="col-9[^"]*"[^>]*>(.*?)</p>', re.DOTALL
)
_RE_LANGUAGE = re.compile(r'itemprop="programmingLanguage"[^>]*>([^<]+)')
_RE_STARS_TODAY = re.compile(r'([\d,]+)\s+stars?\s+today')
_RE_TOTAL_STARS = re.compile(r'/stargazers[^>]*>[^<]*?([\d,]+)')


def _parse_int(s: str) -> int:
    """Parse comma-separated integer string like '5,645' → 5645."""
    return int(s.replace(",", ""))


def fetch_github_trending(feed: Feed, max_age_hours: int = 48) -> list[RawSignal]:
    """
    Fetch trending repos from GitHub Trending page.

    Args:
        feed: Feed config. config keys:
            - spoken_language: language filter (e.g. "python"), "" for all
            - since: "daily" | "weekly" | "monthly"
            - top_n: max repos to return (default 25)
        max_age_hours: Unused (trending is always "now"), kept for interface

    Returns:
        List of RawSignal, one per trending repo
    """
    spoken_language = feed.config.get("spoken_language", "")
    since = feed.config.get("since", "daily")
    top_n = feed.config.get("top_n", 25)

    # Build URL with optional filters
    url = GITHUB_TRENDING_URL
    if spoken_language:
        url += f"/{spoken_language}"
    params = {}
    if since and since != "daily":
        params["since"] = since

    signals: list[RawSignal] = []

    try:
        with safe_client(timeout=15, headers=GITHUB_HEADERS) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "60"))
                logger.warning("GitHub rate limited, retry after %ds", retry_after)
                time.sleep(min(retry_after, 120))
                # Retry once
                resp = client.get(url, params=params, headers=GITHUB_HEADERS)
            resp.raise_for_status()
            html_text = resp.text
    except Exception as e:
        logger.error(f"GitHub Trending fetch failed: {e}")
        return []

    # Split by Box-row articles
    articles = re.split(r'<article\s+class="Box-row">', html_text)
    # First element is everything before the first article — skip it
    articles = articles[1:]

    if not articles:
        logger.warning(
            "GitHub Trending: no Box-row articles found in HTML — "
            "GitHub may have changed their markup. Verify regex patterns "
            "against current https://github.com/trending HTML structure."
        )
        return []

    for article_html in articles[:top_n]:
        try:
            # Extract repo owner/name from link
            repo_match = _RE_REPO_LINK.search(article_html)
            if not repo_match:
                continue

            repo_slug = repo_match.group(1).strip()
            # Skip non-repo links (e.g. /explore, /topics/...)
            if "/" not in repo_slug or repo_slug.count("/") != 1:
                continue

            # Extract description
            desc_match = _RE_DESCRIPTION.search(article_html)
            description = ""
            if desc_match:
                description = desc_match.group(1).strip()
                # Clean HTML entities
                description = re.sub(r'<[^>]+>', '', description).strip()
                description = html.unescape(description)

            # Extract language
            lang_match = _RE_LANGUAGE.search(article_html)
            language = lang_match.group(1).strip() if lang_match else ""

            # Extract stars today
            stars_match = _RE_STARS_TODAY.search(article_html)
            stars_today = _parse_int(stars_match.group(1)) if stars_match else 0

            # Extract total stars
            total_match = _RE_TOTAL_STARS.search(article_html)
            total_stars = _parse_int(total_match.group(1)) if total_match else 0

            signals.append(RawSignal(
                feed_id=feed.id,
                title=repo_slug,
                url=f"https://github.com/{repo_slug}",
                summary=description,
                published=datetime.now(timezone.utc),
                source=language,  # language as source for display
                tags=feed.tags + ([language.lower()] if language else []),
                # score = raw stars_today (NOT normalized). Downstream consumers
                # rank by this value within the github-trending feed only.
                # Cross-feed comparison requires normalization at the digest layer.
                score=float(stars_today),
            ))

        except Exception as e:
            logger.debug(f"GitHub Trending: failed to parse article: {e}")
            continue

    logger.info(f"GitHub Trending: {len(signals)} repos parsed")
    return signals
