"""Shared signal denoising + ranking — the SINGLE source of truth.

Both the Welcome Page signals card (``core.proactive_intelligence.
build_session_briefing_data``) and the Slack "Signal Digest" notification
(``jobs.executor._format_signal_digest_message``) read the same
``Services/signals/signal_digest.json`` and MUST apply identical denoising so
they never drift apart again (they did: the Slack path used to re-sort by the
capped ``relevance_score`` with no feed exclusion / no per-feed cap / no 48h
cutoff / no china-trending split — producing junk and an unfriendly structure).

This module owns the ONLY copy of the denoising constants and the pure
``select_signals`` function. Callers keep their own DISPLAY shaping (Welcome
card fields vs Slack mrkdwn) — shared denoising, separate presentation.

Design notes:
  * PURE — no I/O. Each caller reads the JSON and passes ``items`` in.
  * Sorts ``signals`` by the uncapped ``final_score`` (the write-time ranking
    key stamped by ``jobs.handlers.signal_digest``). Because the digest JSON is
    already ``final_score``-sorted at write-time, this sort is idempotent for
    the Welcome path (output stays byte-identical) while it FIXES the Slack path
    (which previously re-sorted by the capped ``relevance_score``).
  * Does NOT cap the returned lists to a display size — each caller slices to
    its own budget (Welcome 8 signals / 10 hot_news; Slack ``max_items``).
  * Does NOT mutate the input list.
"""

from __future__ import annotations

from datetime import datetime, timezone

# ── Denoising constants — THE single source of truth (do NOT duplicate) ──────

# china-trending is a mass-audience hot-search aggregate (weibo/zhihu/douyin…) —
# it is NOT an AI/tech signal, so it is routed to a separate hot_news bucket
# instead of competing in the Signals card.
TRENDING_FEEDS: frozenset[str] = frozenset({"china-trending"})

# Feeds with no home on the Signals surface at all — eastmoney-market is stock
# gainers, pure noise in an AI/tech signal feed. Dropped entirely.
SIGNALS_EXCLUDED_FEEDS: frozenset[str] = frozenset({"eastmoney-market"})

# Per-feed cap inside Signals: the reference-commits stream
# (hermes-agent/openclaw commits) is high-volume and would otherwise fill the
# top slots, crowding out higher-tier frontier/leaders/research items. Cap it so
# a handful surface without flooding.
SIGNALS_PER_FEED_CAP: dict[str, int] = {"reference-commits": 3}

# Freshness window: signals older than this are dropped (stale ≠ signal).
FRESHNESS_CUTOFF_H: int = 48

# ── Display source labels — shared by BOTH surfaces to prevent label drift ───
# For github/commit feeds the raw `source` field is a programming language
# ("python", "go") rather than a meaningful source name; render a readable feed
# label instead. This map + the readable_source() helper live here (not in each
# caller) so a label edit can never silently diverge the Welcome card and the
# Slack digest (drift risk flagged by REVIEW + Gate-2 in run_44342b40).
_FEED_SOURCE_LABELS: dict[str, str] = {
    "frontier-labs": "Frontier Labs",
    "ai-leaders": "AI Leaders",
    "ai-engineering": "AI Engineering",
    "ai-newsletters": "Newsletter",
    "tool-releases": "Tool Release",
    "github-trending": "GitHub Trending",
    "reference-commits": "Repo Update",
}
_LANG_SOURCE_FEEDS: frozenset[str] = frozenset({"github-trending", "reference-commits"})


def readable_source(feed_id: str, raw_source: str) -> str:
    """The human-readable source label for a signal.

    For lang-source feeds (github/commits) the raw `source` is a programming
    language — substitute the feed's readable label. Every other feed uses its
    raw source verbatim. Single source of truth for both the Welcome card and
    the Slack digest.
    """
    if feed_id in _LANG_SOURCE_FEEDS:
        return _FEED_SOURCE_LABELS.get(feed_id, raw_source)
    return raw_source


def _rank_key(item: dict) -> float:
    """Sort key: the uncapped multi-dim final_score (write-time ranking).

    Falls back to relevance_score, then 0 — never crashes on an older digest
    item that predates final_score. Coerces defensively: signal_digest.json is a
    serialization boundary (could be hand-edited/corrupted), and this function
    now feeds BOTH the Welcome card and the Slack job, so a non-numeric score
    must degrade to 0 rather than crash the sort (O023).
    """
    val = item.get("final_score", item.get("relevance_score", 0))
    return val if isinstance(val, (int, float)) else 0


def _is_fresh(item: dict, cutoff_ts: float) -> bool:
    """True if the item was fetched within the freshness window.

    Matches the Welcome path's original semantics EXACTLY: a missing/empty/
    unparseable ``fetched_at`` is treated as NOT fresh (dropped). This is
    deliberate — an item with no timestamp cannot be proven fresh.
    """
    fetched = item.get("fetched_at", "")
    if not isinstance(fetched, str) or not fetched:
        return False
    try:
        dt_val = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return dt_val.timestamp() >= cutoff_ts


def select_signals(items: list[dict], now: datetime | None = None) -> dict:
    """Denoise + rank a raw signal_digest ``items`` list.

    Returns ``{"signals": [...], "hot_news": [...]}`` of RAW item dicts (no
    display cap, no field renaming — callers shape + slice for their surface).

    Pipeline (order matches the original Welcome inline logic so its output is
    preserved byte-for-byte):
      1. 48h freshness cutoff  (applied BEFORE the split → both buckets fresh)
      2. drop ``SIGNALS_EXCLUDED_FEEDS``
      3. split ``TRENDING_FEEDS`` items into ``hot_news``
      4. per-feed cap on the remaining signals (``SIGNALS_PER_FEED_CAP``)
      5. sort ``signals`` by ``final_score`` desc (stable; idempotent for the
         already-sorted write-time JSON)

    The per-feed cap is applied over the input in its existing order (which is
    final_score-sorted at write-time), so a subsequent stable sort keeps the
    same survivors the original interleaved loop selected.
    """
    ref = now or datetime.now(timezone.utc)
    cutoff_ts = ref.timestamp() - FRESHNESS_CUTOFF_H * 3600

    signals: list[dict] = []
    hot_news: list[dict] = []
    per_feed_count: dict[str, int] = {}

    for sig in items:
        if not _is_fresh(sig, cutoff_ts):
            continue
        feed_id = sig.get("feed_id", "")

        # (2) drop feeds with no home on the signals surface
        if feed_id in SIGNALS_EXCLUDED_FEEDS:
            continue

        # (3) trending → hot_news
        if feed_id in TRENDING_FEEDS:
            hot_news.append(sig)
            continue

        # (4) per-feed cap on the rest
        cap = SIGNALS_PER_FEED_CAP.get(feed_id)
        if cap is not None:
            if per_feed_count.get(feed_id, 0) >= cap:
                continue
            per_feed_count[feed_id] = per_feed_count.get(feed_id, 0) + 1

        signals.append(sig)

    # (5) stable sort by final_score desc — idempotent for write-time-sorted JSON
    signals.sort(key=_rank_key, reverse=True)

    return {"signals": signals, "hot_news": hot_news}
