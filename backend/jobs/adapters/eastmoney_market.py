"""
East Money (东方财富) Market Signal Adapter

Fetches trending stocks, hot concept sectors, and market movers from East Money.
All endpoints are public — no authentication, no WAF, no JS challenge required.

Data sources:
- Hot stocks: A-share top gainers/losers with price change percentage
- Concept sectors: trending thematic sectors (AI, robotics, chips, etc.)

Replaces xueqiu_market adapter which was blocked by Aliyun WAF.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .http_client import safe_client
from ..models import Feed, RawSignal

logger = logging.getLogger(__name__)

# East Money push2 API — public, JSON, no auth
EASTMONEY_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"

# Common token (public, not secret — used by all East Money web clients)
EASTMONEY_UT = "fa5fd1943c7b386f172d6893dbfba10b"

EASTMONEY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

# Market filters
FS_A_SHARES = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"  # All A-shares (SH+SZ)
FS_CONCEPTS = "m:90+t:3"  # Concept/thematic sectors

# Keywords to filter concept sectors for business relevance
DEFAULT_CONCEPT_KEYWORDS = [
    "AI", "人工智能", "大模型", "机器人", "芯片", "半导体",
    "云计算", "数据中心", "算力", "智能", "Agent", "6G",
    "自动驾驶", "物联网", "数字经济",
]


def fetch_eastmoney_market(feed: Feed, max_age_hours: int = 48) -> list[RawSignal]:
    """
    Fetch East Money market signals (hot stocks + concept sectors).

    Args:
        feed: Feed config. Config keys:
            - hot_stocks_count: int, max hot stocks to return (default 20)
            - concepts_count: int, max concept sectors to return (default 15)
            - concept_keywords: list[str], keywords to filter concepts (default: DEFAULT_CONCEPT_KEYWORDS)
            - include_losers: bool, also fetch top losers (default False)
        max_age_hours: Unused (market data is always "now"), kept for adapter interface

    Returns:
        List of RawSignal from hot stocks and concept sectors
    """
    hot_stocks_count = feed.config.get("hot_stocks_count", 20)
    concepts_count = feed.config.get("concepts_count", 15)
    concept_keywords = feed.config.get("concept_keywords", DEFAULT_CONCEPT_KEYWORDS)
    include_losers = feed.config.get("include_losers", False)

    signals: list[RawSignal] = []

    # 1. Top gainers
    try:
        gainers = _fetch_top_movers(feed.id, hot_stocks_count, descending=True)
        signals.extend(gainers)
    except Exception as e:
        logger.warning(f"East Money top gainers failed: {e}")

    # 2. Top losers (optional)
    if include_losers:
        try:
            losers = _fetch_top_movers(feed.id, hot_stocks_count, descending=False)
            signals.extend(losers)
        except Exception as e:
            logger.warning(f"East Money top losers failed: {e}")

    # 3. Hot concept sectors (filtered by keywords)
    try:
        concepts = _fetch_hot_concepts(feed.id, concepts_count, concept_keywords)
        signals.extend(concepts)
    except Exception as e:
        logger.warning(f"East Money concept sectors failed: {e}")

    logger.info(f"East Money market: {len(signals)} signals")
    return signals


def _fetch_top_movers(feed_id: str, count: int, descending: bool = True) -> list[RawSignal]:
    """Fetch top gaining or losing stocks."""
    signals: list[RawSignal] = []
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    params = {
        "pn": "1",
        "pz": str(min(count, 50)),
        "po": "1" if descending else "0",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",  # sort by 涨跌幅
        "fs": FS_A_SHARES,
        "fields": "f2,f3,f4,f12,f14",  # price, percent, change, code, name
        "ut": EASTMONEY_UT,
    }

    with safe_client(timeout=10, headers=EASTMONEY_HEADERS) as client:
        resp = client.get(EASTMONEY_CLIST_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    items = data.get("data", {}).get("diff", [])
    if not items:
        logger.warning("East Money top movers: no items in response")
        return []

    direction = "涨幅" if descending else "跌幅"
    for stock in items:
        code = stock.get("f12", "")
        name = stock.get("f14", "")
        percent = float(stock.get("f3") or 0.0)
        price = stock.get("f2") or "N/A"

        if not code or not name:
            continue

        # Score: volatility normalized (10%+ = 1.0 for individual stocks)
        score = min(abs(percent) / 10.0, 1.0)

        signals.append(RawSignal(
            feed_id=feed_id,
            title=f"{name} ({code}) {percent:+.2f}%",
            url=f"https://quote.eastmoney.com/{code}.html?d={today}",
            summary=f"现价:{price} {direction}:{percent:+.2f}%",
            published=datetime.now(timezone.utc),
            source="东方财富" + direction + "榜",
            tags=["eastmoney", "stock", "china", "market"],
            score=score,
        ))

    return signals


def _fetch_hot_concepts(
    feed_id: str, count: int, keywords: list[str]
) -> list[RawSignal]:
    """Fetch trending concept sectors, filtered by business-relevant keywords."""
    signals: list[RawSignal] = []
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    params = {
        "pn": "1",
        "pz": str(min(count * 3, 100)),  # fetch more to filter
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": FS_CONCEPTS,
        "fields": "f2,f3,f12,f14",  # price, percent, code, name
        "ut": EASTMONEY_UT,
    }

    with safe_client(timeout=10, headers=EASTMONEY_HEADERS) as client:
        resp = client.get(EASTMONEY_CLIST_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    items = data.get("data", {}).get("diff", [])
    if not items:
        logger.warning("East Money concepts: no items in response")
        return []

    # Filter by keywords (any keyword substring match in concept name)
    keywords_lower = [k.lower() for k in keywords]

    for concept in items:
        name = concept.get("f14", "")
        code = concept.get("f12", "")
        percent = float(concept.get("f3") or 0.0)

        if not name:
            continue

        # Check keyword relevance
        name_lower = name.lower()
        is_relevant = any(kw in name_lower for kw in keywords_lower)

        if not is_relevant:
            continue

        # Score: sector movement (3%+ = strong signal)
        score = min(abs(percent) / 3.0, 1.0)

        signals.append(RawSignal(
            feed_id=feed_id,
            title=f"📊 {name} {percent:+.2f}%",
            url=f"https://quote.eastmoney.com/bk/{code}.html?d={today}",
            summary=f"概念板块涨跌:{percent:+.2f}%",
            published=datetime.now(timezone.utc),
            source="东方财富概念板块",
            tags=["eastmoney", "concept", "china", "sector"],
            score=score,
        ))

        if len(signals) >= count:
            break

    return signals
