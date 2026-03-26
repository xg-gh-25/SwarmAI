"""
RSS/Atom Feed Adapter

Fetches and parses RSS/Atom feeds using httpx + xml.etree (stdlib).
No external dependencies beyond httpx.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .http_client import safe_get
from ..models import Feed, RawSignal

logger = logging.getLogger(__name__)

# Common Atom namespace
ATOM_NS = "{http://www.w3.org/2005/Atom}"


def fetch_rss(feed: Feed, max_age_hours: int = 48) -> list[RawSignal]:
    """
    Fetch signals from RSS/Atom feed URLs.

    Args:
        feed: Feed config with urls list in feed.config
        max_age_hours: Skip entries older than this

    Returns:
        List of RawSignal from all configured URLs
    """
    urls: list[str] = feed.config.get("urls", [])
    if not urls:
        logger.warning(f"RSS feed '{feed.id}' has no URLs configured")
        return []

    signals: list[RawSignal] = []
    cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)

    for url in urls:
        try:
            resp = safe_get(url, timeout=15, headers={
                "User-Agent": "SwarmSignalPipeline/1.0"
            })
            resp.raise_for_status()
            entries = _parse_feed(resp.text, url, feed, cutoff)
            signals.extend(entries)
        except Exception as e:
            logger.error(f"Error fetching RSS {url}: {e}")
            continue

    logger.info(f"RSS adapter '{feed.id}': fetched {len(signals)} signals from {len(urls)} feeds")
    return signals


def _parse_feed(xml_text: str, url: str, feed: Feed, cutoff: float) -> list[RawSignal]:
    """Parse RSS or Atom XML into RawSignal list."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(f"XML parse error for {url}: {e}")
        return []

    # Detect format and dispatch
    tag = root.tag.lower().split("}")[-1] if "}" in root.tag else root.tag.lower()

    if tag == "feed":  # Atom
        return _parse_atom(root, feed, cutoff)
    elif tag == "rss":
        channel = root.find("channel")
        if channel is not None:
            return _parse_rss_channel(channel, feed, cutoff)

    logger.warning(f"Unknown feed format for {url}: root tag = {root.tag}")
    return []


def _parse_atom(root: ET.Element, feed: Feed, cutoff: float) -> list[RawSignal]:
    """Parse Atom feed."""
    feed_title = _text(root, f"{ATOM_NS}title") or feed.name
    signals = []

    for entry in root.findall(f"{ATOM_NS}entry")[:10]:
        title = _text(entry, f"{ATOM_NS}title") or ""
        link_el = entry.find(f"{ATOM_NS}link[@rel='alternate']")
        if link_el is None:
            link_el = entry.find(f"{ATOM_NS}link")
        link = link_el.get("href", "") if link_el is not None else ""

        published = _parse_date(_text(entry, f"{ATOM_NS}published") or _text(entry, f"{ATOM_NS}updated"))
        if published and published.timestamp() < cutoff:
            continue

        summary = _clean_html(
            _text(entry, f"{ATOM_NS}summary") or _text(entry, f"{ATOM_NS}content") or ""
        )

        if not title or not link:
            continue

        signals.append(RawSignal(
            feed_id=feed.id,
            title=title.strip(),
            url=link.strip(),
            summary=summary[:500],
            published=published,
            source=feed_title,
            tags=feed.tags,
        ))

    return signals


def _parse_rss_channel(channel: ET.Element, feed: Feed, cutoff: float) -> list[RawSignal]:
    """Parse RSS 2.0 channel."""
    feed_title = _text(channel, "title") or feed.name
    signals = []

    for item in channel.findall("item")[:10]:
        title = _text(item, "title") or ""
        link = _text(item, "link") or ""

        published = _parse_date(_text(item, "pubDate"))
        if published and published.timestamp() < cutoff:
            continue

        summary = _clean_html(_text(item, "description") or "")

        if not title or not link:
            continue

        signals.append(RawSignal(
            feed_id=feed.id,
            title=title.strip(),
            url=link.strip(),
            summary=summary[:500],
            published=published,
            source=feed_title,
            tags=feed.tags,
        ))

    return signals


def _text(el: ET.Element, tag: str) -> str | None:
    """Get text content of a child element."""
    child = el.find(tag)
    return child.text if child is not None and child.text else None


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse RSS/Atom date string to datetime."""
    if not date_str:
        return None
    try:
        # RFC 2822 (RSS pubDate)
        return parsedate_to_datetime(date_str)
    except Exception:
        pass
    try:
        # ISO 8601 (Atom)
        from datetime import datetime as dt
        cleaned = date_str.replace("Z", "+00:00")
        return dt.fromisoformat(cleaned)
    except Exception:
        pass
    return None


def _clean_html(raw: str) -> str:
    """Strip HTML tags from text."""
    clean = re.sub(r"<[^>]+>", "", raw)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean
