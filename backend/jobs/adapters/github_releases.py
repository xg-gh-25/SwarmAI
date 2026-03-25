"""
GitHub Releases Adapter

Tracks releases for configured repositories via GitHub API.
Free, no API key needed (public repos, rate-limited to 60 req/hr unauthenticated).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .http_client import safe_client
from ..models import Feed, RawSignal

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


def fetch_github_releases(feed: Feed, max_age_hours: int = 48) -> list[RawSignal]:
    """
    Fetch latest releases for configured GitHub repos.

    Args:
        feed: Feed config with repos list and include_prereleases flag
        max_age_hours: Skip releases older than this

    Returns:
        List of RawSignal for new releases
    """
    repos: list[str] = feed.config.get("repos", [])
    include_pre = feed.config.get("include_prereleases", False)

    if not repos:
        logger.warning(f"GitHub feed '{feed.id}' has no repos configured")
        return []

    signals: list[RawSignal] = []
    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)

    with safe_client(timeout=15, headers={"Accept": "application/vnd.github.v3+json"}) as client:
        for repo in repos:
            try:
                resp = client.get(f"{GITHUB_API}/repos/{repo}/releases", params={"per_page": 3})

                if resp.status_code == 403:
                    logger.warning(f"GitHub rate limited for {repo}")
                    continue
                if resp.status_code == 404:
                    logger.warning(f"GitHub repo not found: {repo}")
                    continue

                resp.raise_for_status()
                releases = resp.json()

                for release in releases:
                    if release.get("draft"):
                        continue
                    if release.get("prerelease") and not include_pre:
                        continue

                    published_str = release.get("published_at", "")
                    if not published_str:
                        continue

                    published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
                    if published.timestamp() < cutoff:
                        continue

                    tag = release.get("tag_name", "")
                    name = release.get("name", tag)
                    body = release.get("body", "")[:500]
                    html_url = release.get("html_url", f"https://github.com/{repo}/releases")

                    signals.append(RawSignal(
                        feed_id=feed.id,
                        title=f"{repo} {name}",
                        url=html_url,
                        summary=_clean_release_body(body),
                        published=published,
                        source=f"GitHub: {repo}",
                        tags=feed.tags,
                    ))

            except Exception as e:
                logger.error(f"Error fetching GitHub releases for {repo}: {e}")
                continue

    logger.info(f"GitHub adapter '{feed.id}': fetched {len(signals)} releases from {len(repos)} repos")
    return signals


def _clean_release_body(body: str) -> str:
    """Extract first meaningful paragraph from release notes markdown."""
    import re
    # Remove markdown headers, links, badges
    clean = re.sub(r"!\[.*?\]\(.*?\)", "", body)
    clean = re.sub(r"#+\s+", "", clean)
    clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:300]
