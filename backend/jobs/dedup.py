"""
Swarm Job System — Signal Deduplication

URL-based dedup with title similarity fallback.
Maintains a rolling window of seen URLs in state.json.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from .models import RawSignal


def dedup_signals(
    new_signals: list[RawSignal],
    seen_urls: list[str],
    title_threshold: float = 0.85,
) -> tuple[list[RawSignal], list[str]]:
    """
    Remove duplicate signals.

    Returns:
        (unique_signals, updated_seen_urls)
    """
    unique: list[RawSignal] = []
    seen_titles: list[str] = []
    updated_urls = list(seen_urls)

    for signal in new_signals:
        # 1. Exact URL match
        if signal.url in updated_urls:
            continue

        # 2. Title similarity (catches reposts / mirrors)
        is_dup = False
        for seen_title in seen_titles:
            ratio = SequenceMatcher(None, signal.title.lower(), seen_title.lower()).ratio()
            if ratio >= title_threshold:
                is_dup = True
                break

        if not is_dup:
            # Also check against titles we'd derive from existing URLs
            # (we don't store titles in dedup cache, so this only catches
            #  within-batch duplicates)
            unique.append(signal)
            seen_titles.append(signal.title)
            updated_urls.append(signal.url)

    return unique, updated_urls


def trim_dedup_cache(urls: list[str], max_size: int = 500) -> list[str]:
    """Keep dedup cache bounded. Oldest entries dropped first."""
    if len(urls) > max_size:
        return urls[-max_size:]
    return urls
