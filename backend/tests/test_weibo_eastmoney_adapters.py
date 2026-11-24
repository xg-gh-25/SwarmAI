"""
Tests for Weibo and East Money signal adapters.

Verifies:
- Output shape: returns list[RawSignal] with correct fields
- Keyword filtering: only the person's own keywords filter; none configured keeps every item
- Graceful failure: returns empty list on API error, never raises
- Metadata: East Money includes change_pct in score, concept filtering
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from core.inbox.feeds.weibo_trending import (
    fetch_weibo_trending,
    _fetch_hot_search,
    _parse_mblog,
)
from core.inbox.feeds.eastmoney_market import (
    fetch_eastmoney_market,
    _fetch_top_movers,
    _fetch_hot_concepts,
)
from jobs.models import Feed, FeedType, RawSignal


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def weibo_feed() -> Feed:
    return Feed(
        id="test-weibo",
        name="Test Weibo",
        type=FeedType.WEIBO_TRENDING,
        config={
            "keywords": ["AI", "云计算", "大模型"],
            "top_n": 30,
            "keyword_search": False,
        },
        tags=["test"],
    )


@pytest.fixture
def eastmoney_feed() -> Feed:
    return Feed(
        id="test-eastmoney",
        name="Test East Money",
        type=FeedType.EASTMONEY_MARKET,
        config={
            "hot_stocks_count": 5,
            "concepts_count": 10,
            "concept_keywords": ["AI", "机器人", "芯片"],
        },
        tags=["test"],
    )


# ── Weibo Hot Search Tests ────────────────────────────────────────────────

MOCK_WEIBO_HOT_RESPONSE = {
    "data": {
        "realtime": [
            {"word": "AI大模型突破", "raw_hot": 2500000, "category": "科技"},
            {"word": "某明星离婚", "raw_hot": 5000000, "category": "娱乐"},
            {"word": "云计算降价", "raw_hot": 800000, "category": "科技"},
            {"word": "吃瓜第一名", "raw_hot": 9000000, "category": "娱乐"},
            {"word": "AWS发布新服务", "raw_hot": 600000, "category": "科技"},
        ]
    }
}


@patch("core.inbox.feeds.weibo_trending.safe_client")
def test_weibo_hot_search_filters_by_keyword(mock_client, weibo_feed):
    """Only signals matching business keywords are returned."""
    mock_response = MagicMock()
    mock_response.json.return_value = MOCK_WEIBO_HOT_RESPONSE
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)

    signals = _fetch_hot_search("test-weibo", ["AI", "云计算", "AWS"], 30)

    # Should filter out entertainment (某明星离婚, 吃瓜第一名)
    assert len(signals) == 3
    titles = [s.title for s in signals]
    assert "AI大模型突破" in titles
    assert "云计算降价" in titles
    assert "AWS发布新服务" in titles
    assert "某明星离婚" not in titles
    assert "吃瓜第一名" not in titles


@patch("core.inbox.feeds.weibo_trending.safe_client")
def test_weibo_hot_search_output_shape(mock_client, weibo_feed):
    """Each signal has correct RawSignal fields."""
    mock_response = MagicMock()
    mock_response.json.return_value = MOCK_WEIBO_HOT_RESPONSE
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)

    signals = _fetch_hot_search("test-weibo", ["AI"], 30)

    assert len(signals) >= 1
    sig = signals[0]
    assert isinstance(sig, RawSignal)
    assert sig.feed_id == "test-weibo"
    assert sig.title == "AI大模型突破"
    assert "weibo.com" in sig.url
    assert sig.source == "Weibo hot search"
    assert 0.0 <= sig.score <= 1.0
    assert sig.published is not None
    assert "weibo" in sig.tags


@patch("core.inbox.feeds.weibo_trending.safe_client")
def test_weibo_graceful_failure_on_api_error(mock_client, weibo_feed):
    """Returns empty list on HTTP error, never raises."""
    mock_http = MagicMock()
    mock_http.get.side_effect = httpx.ConnectError("Connection refused")
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)

    # Should not raise — returns empty list
    signals = fetch_weibo_trending(weibo_feed)
    assert signals == []


def test_weibo_parse_mblog():
    """_parse_mblog correctly extracts fields from mblog dict."""
    mblog = {
        "text": "<a href='#'>AI</a>最新进展：大模型在企业中落地",
        "mid": "12345678",
        "user": {"id": "9999"},
        "reposts_count": 100,
        "comments_count": 50,
    }
    signal = _parse_mblog("test-feed", mblog, "AI")
    assert signal is not None
    assert "AI" in signal.title
    assert "大模型" in signal.title
    assert signal.url == "https://m.weibo.cn/detail/12345678"
    assert "Reposts: 100" in signal.summary
    assert "Comments: 50" in signal.summary
    assert signal.source == "Weibo search" and signal.title.startswith("[Weibo] ")
    assert signal.score == min(150.0 / 1000.0, 1.0)  # (100+50)/1000


def test_weibo_parse_mblog_empty_text():
    """Returns None for empty text."""
    assert _parse_mblog("test", {"text": ""}, "AI") is None
    assert _parse_mblog("test", {"text": "<br/>"}, "AI") is None


# ── East Money Hot Stocks Tests ──────────────────────────────────────────

MOCK_EASTMONEY_STOCKS_RESPONSE = {
    "rc": 0,
    "data": {
        "total": 5532,
        "diff": [
            {"f2": 18.22, "f3": 20.03, "f4": 3.04, "f12": "301151", "f14": "冠龙节能"},
            {"f2": 21.94, "f3": 20.02, "f4": 3.66, "f12": "300353", "f14": "东土科技"},
            {"f2": 40.30, "f3": 10.01, "f4": 6.72, "f12": "300626", "f14": "华瑞股份"},
        ]
    }
}

MOCK_EASTMONEY_CONCEPTS_RESPONSE = {
    "rc": 0,
    "data": {
        "total": 400,
        "diff": [
            {"f2": 1050.5, "f3": 4.37, "f12": "BK0891", "f14": "AI芯片"},
            {"f2": 980.3, "f3": 3.90, "f12": "BK0892", "f14": "机器人执行器"},
            {"f2": 750.1, "f3": 3.20, "f12": "BK0893", "f14": "裸眼3D"},
            {"f2": 620.0, "f3": 2.84, "f12": "BK0894", "f14": "云计算概念"},
            {"f2": 510.2, "f3": 2.10, "f12": "BK0895", "f14": "白酒概念"},
        ]
    }
}


@patch("core.inbox.feeds.eastmoney_market.safe_client")
def test_eastmoney_top_movers_output_shape(mock_client):
    """Hot stocks include name, code, change_pct in title/summary."""
    mock_response = MagicMock()
    mock_response.json.return_value = MOCK_EASTMONEY_STOCKS_RESPONSE
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)

    signals = _fetch_top_movers("test-eastmoney", 5, descending=True)

    assert len(signals) == 3
    sig = signals[0]
    assert isinstance(sig, RawSignal)
    assert "冠龙节能" in sig.title
    assert "301151" in sig.title
    assert "+20.03%" in sig.title
    assert "eastmoney.com" in sig.url
    assert "Change: +20.03%" in sig.summary
    assert sig.source == "East Money top gainers"
    assert "eastmoney" in sig.tags
    # Score = abs(20.03) / 10.0 = 2.003, capped at 1.0
    assert sig.score == 1.0


@patch("core.inbox.feeds.eastmoney_market.safe_client")
def test_eastmoney_top_movers_score_calculation(mock_client):
    """Score normalizes: 10%+ = 1.0, 5% = 0.5."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "rc": 0,
        "data": {"total": 1, "diff": [
            {"f2": 10.0, "f3": 5.0, "f4": 0.5, "f12": "000001", "f14": "平安银行"},
        ]}
    }
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)

    signals = _fetch_top_movers("test", 5)
    assert len(signals) == 1
    assert abs(signals[0].score - 0.5) < 0.01  # 5.0 / 10.0


@patch("core.inbox.feeds.eastmoney_market.safe_client")
def test_eastmoney_hot_concepts_filters_by_keyword(mock_client):
    """Only concept sectors matching keywords are returned."""
    mock_response = MagicMock()
    mock_response.json.return_value = MOCK_EASTMONEY_CONCEPTS_RESPONSE
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)

    signals = _fetch_hot_concepts("test-eastmoney", 15, ["AI", "机器人", "云计算"])

    # Should match: AI芯片, 机器人执行器, 云计算概念
    # Should NOT match: 裸眼3D, 白酒概念
    assert len(signals) == 3
    titles = [s.title for s in signals]
    assert any("AI芯片" in t for t in titles)
    assert any("机器人执行器" in t for t in titles)
    assert any("云计算概念" in t for t in titles)
    assert not any("裸眼3D" in t for t in titles)
    assert not any("白酒概念" in t for t in titles)


@patch("core.inbox.feeds.eastmoney_market.safe_client")
def test_eastmoney_hot_concepts_output_shape(mock_client):
    """Concept signals have correct fields."""
    mock_response = MagicMock()
    mock_response.json.return_value = MOCK_EASTMONEY_CONCEPTS_RESPONSE
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)

    signals = _fetch_hot_concepts("test-eastmoney", 15, ["AI"])

    assert len(signals) >= 1
    sig = signals[0]
    assert isinstance(sig, RawSignal)
    assert "AI芯片" in sig.title
    assert "eastmoney.com/bk/" in sig.url
    assert sig.source == "East Money concept sectors"
    assert "concept" in sig.tags
    # Score = abs(4.37) / 3.0 = 1.46, capped at 1.0
    assert sig.score == 1.0


@patch("core.inbox.feeds.eastmoney_market.safe_client")
def test_eastmoney_graceful_failure_on_api_error(mock_client, eastmoney_feed):
    """Returns empty list on HTTP error, never raises."""
    mock_http = MagicMock()
    mock_http.get.side_effect = httpx.ConnectError("Connection refused")
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)

    signals = fetch_eastmoney_market(eastmoney_feed)
    assert signals == []


@patch("core.inbox.feeds.eastmoney_market.safe_client")
def test_eastmoney_empty_response(mock_client, eastmoney_feed):
    """Handles empty response gracefully."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"rc": 0, "data": {"total": 0, "diff": []}}
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)

    signals = fetch_eastmoney_market(eastmoney_feed)
    assert signals == []


@patch("core.inbox.feeds.eastmoney_market.safe_client")
def test_eastmoney_skips_stocks_with_missing_fields(mock_client):
    """Stocks with empty name or code are skipped."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "rc": 0,
        "data": {"total": 2, "diff": [
            {"f2": 10.0, "f3": 5.0, "f4": 0.5, "f12": "", "f14": "无代码"},
            {"f2": 10.0, "f3": 5.0, "f4": 0.5, "f12": "000001", "f14": ""},
            {"f2": 10.0, "f3": 5.0, "f4": 0.5, "f12": "000002", "f14": "正常股票"},
        ]}
    }
    mock_response.raise_for_status = MagicMock()

    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)

    signals = _fetch_top_movers("test", 10)
    assert len(signals) == 1
    assert "正常股票" in signals[0].title


# ── Integration: FeedType + ADAPTER_MAP ───────────────────────────────────

def test_feedtype_enum_has_new_types():
    """FeedType enum includes WEIBO_TRENDING and EASTMONEY_MARKET."""
    assert FeedType.WEIBO_TRENDING == "weibo-trending"
    assert FeedType.EASTMONEY_MARKET == "eastmoney-market"


def test_adapter_map_routes_new_types():
    """ADAPTER_MAP dispatches to the correct functions."""
    from core.inbox.feeds import ADAPTER_MAP

    assert FeedType.WEIBO_TRENDING in ADAPTER_MAP
    assert FeedType.EASTMONEY_MARKET in ADAPTER_MAP
    assert ADAPTER_MAP[FeedType.WEIBO_TRENDING] is fetch_weibo_trending
    assert ADAPTER_MAP[FeedType.EASTMONEY_MARKET] is fetch_eastmoney_market


# ── No built-in keywords: a feed without the person's own keywords is not filtered ──

def _client(mock_client, payload):
    mock_response = MagicMock()
    mock_response.json.return_value = payload
    mock_response.raise_for_status = MagicMock()
    mock_http = MagicMock()
    mock_http.get.return_value = mock_response
    mock_client.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_client.return_value.__exit__ = MagicMock(return_value=False)
    return mock_http


def test_default_keywords_are_empty():
    from core.inbox.feeds import eastmoney_market, weibo_trending
    assert weibo_trending.DEFAULT_KEYWORDS == []
    assert eastmoney_market.DEFAULT_CONCEPT_KEYWORDS == []


@patch("core.inbox.feeds.weibo_trending.safe_client")
def test_no_keywords_keeps_every_item(mock_client):
    """Weibo: no keywords configured (or an empty list) keeps every hot-search item and
    runs no keyword search; the person's own keywords still filter."""
    http = _client(mock_client, MOCK_WEIBO_HOT_RESPONSE)
    for config in ({}, {"keywords": []}, {"keywords": [], "keyword_search": True}):
        feed = Feed(id="w", name="W", type=FeedType.WEIBO_TRENDING, config=config)
        http.get.reset_mock()
        signals = fetch_weibo_trending(feed)
        assert [s.title for s in signals] == [i["word"] for i in MOCK_WEIBO_HOT_RESPONSE["data"]["realtime"]]
        assert http.get.call_count == 1          # the hot search only
        assert all(s.source == "Weibo hot search" for s in signals)
    own = Feed(id="w", name="W", type=FeedType.WEIBO_TRENDING, config={"keywords": ["AWS"]})
    assert [s.title for s in fetch_weibo_trending(own)] == ["AWS发布新服务"]


@patch("core.inbox.feeds.eastmoney_market.safe_client")
def test_no_concept_keywords_keeps_the_top_concepts(mock_client):
    """East Money: no concept keywords keeps the top concept sectors up to the count;
    the person's own keywords still filter."""
    http = _client(mock_client, MOCK_EASTMONEY_CONCEPTS_RESPONSE)
    signals = _fetch_hot_concepts("e", 4, [])
    assert [s.title.split()[1] for s in signals] == ["AI芯片", "机器人执行器", "裸眼3D", "云计算概念"]
    assert http.get.call_args.kwargs["params"]["pz"] == "4"
    feed = Feed(id="e", name="E", type=FeedType.EASTMONEY_MARKET,
                config={"hot_stocks_count": 5, "concepts_count": 10})
    concepts = [s for s in fetch_eastmoney_market(feed) if "concept" in s.tags]
    assert len(concepts) == len(MOCK_EASTMONEY_CONCEPTS_RESPONSE["data"]["diff"])
    assert [s.title.split()[1] for s in _fetch_hot_concepts("e", 10, ["白酒"])] == ["白酒概念"]
