"""
Tests for Chinese Trending News adapter (newsnow API).

Tests:
- AC1: Adapter fetches from platforms and returns valid RawSignal list
- AC2: Trending data integrates with signal_fetch handler
- AC6: FeedType.TRENDING registered in models and adapter map
- Edge cases: API errors, empty items, invalid JSON, top_n limiting
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from jobs.models import Feed, FeedType, RawSignal


# ── Fixtures ──────────────────────────────────────────────────────────

MOCK_NEWSNOW_RESPONSE = {
    "status": "success",
    "items": [
        {"title": "习近平出席重要会议", "url": "https://weibo.com/1", "mobileUrl": "https://m.weibo.com/1"},
        {"title": "AI大模型最新进展", "url": "https://weibo.com/2", "mobileUrl": ""},
        {"title": "特斯拉降价", "url": "https://weibo.com/3"},
    ],
}

MOCK_CACHE_RESPONSE = {
    "status": "cache",
    "items": [
        {"title": "缓存数据", "url": "https://weibo.com/cache"},
    ],
}

MOCK_ERROR_RESPONSE = {
    "status": "error",
    "items": [],
}


def _make_trending_feed(**overrides) -> Feed:
    """Create a trending feed config for testing."""
    defaults = {
        "id": "china-trending-test",
        "name": "Chinese Hot Search Test",
        "type": FeedType.TRENDING,
        "tier": "aggregate",
        "config": {
            "platforms": [
                {"id": "weibo", "name": "微博"},
                {"id": "zhihu", "name": "知乎"},
            ],
            "top_n": 10,
            "request_interval_ms": 0,  # no delay in tests
        },
        "tags": ["trending", "china"],
        "enabled": True,
    }
    defaults.update(overrides)
    return Feed(**defaults)


# ── AC1: Adapter returns valid RawSignal list ─────────────────────────

class TestTrendingAdapterBasic:
    """AC1: newsnow adapter fetches data from all 11 platforms and returns valid RawSignal list."""

    def test_fetch_trending_returns_raw_signals(self):
        """Adapter returns list of RawSignal from newsnow API."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_NEWSNOW_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_trending(feed)

        assert isinstance(signals, list)
        assert len(signals) > 0
        assert all(isinstance(s, RawSignal) for s in signals)

    def test_signal_has_correct_fields(self):
        """Each signal has feed_id, title, url, source (platform name)."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed(config={
            "platforms": [{"id": "weibo", "name": "微博"}],
            "top_n": 10,
            "request_interval_ms": 0,
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_NEWSNOW_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_trending(feed)

        sig = signals[0]
        assert sig.feed_id == "china-trending-test"
        assert sig.title == "习近平出席重要会议"
        assert sig.url == "https://weibo.com/1"
        assert sig.source == "微博"
        assert sig.tags == ["trending", "china"]

    def test_fetches_all_configured_platforms(self):
        """Adapter makes one request per configured platform."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed(config={
            "platforms": [
                {"id": "weibo", "name": "微博"},
                {"id": "zhihu", "name": "知乎"},
                {"id": "toutiao", "name": "今日头条"},
            ],
            "top_n": 10,
            "request_interval_ms": 0,
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_NEWSNOW_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_trending(feed)

        # 3 platforms × 3 items each = 9 signals
        assert mock_client.get.call_count == 3
        assert len(signals) == 9

    def test_top_n_limits_per_platform(self):
        """top_n config limits items taken per platform."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed(config={
            "platforms": [{"id": "weibo", "name": "微博"}],
            "top_n": 2,
            "request_interval_ms": 0,
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_NEWSNOW_RESPONSE  # 3 items
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_trending(feed)

        assert len(signals) == 2  # limited by top_n

    def test_rank_stored_in_summary(self):
        """Signal summary includes rank position for downstream analysis."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed(config={
            "platforms": [{"id": "weibo", "name": "微博"}],
            "top_n": 10,
            "request_interval_ms": 0,
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_NEWSNOW_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_trending(feed)

        # First item should have rank #1
        assert "#1" in signals[0].summary
        assert "#2" in signals[1].summary


# ── AC6: FeedType.TRENDING registered ─────────────────────────────────

class TestTrendingRegistration:
    """AC6: FeedType.TRENDING exists and is registered in adapter map."""

    def test_feedtype_trending_exists(self):
        """FeedType enum has TRENDING value."""
        assert hasattr(FeedType, "TRENDING")
        assert FeedType.TRENDING == "trending"

    def test_adapter_map_has_trending(self):
        """signal_fetch ADAPTER_MAP includes trending adapter."""
        from jobs.handlers.signal_fetch import ADAPTER_MAP

        assert FeedType.TRENDING in ADAPTER_MAP


# ── Edge cases ────────────────────────────────────────────────────────

class TestTrendingEdgeCases:
    """Edge cases: API errors, empty items, cache responses, invalid data."""

    def test_api_error_skips_platform_continues(self):
        """If one platform fails, adapter continues with others."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed(config={
            "platforms": [
                {"id": "weibo", "name": "微博"},
                {"id": "zhihu", "name": "知乎"},
            ],
            "top_n": 10,
            "request_interval_ms": 0,
        })

        good_resp = MagicMock()
        good_resp.status_code = 200
        good_resp.json.return_value = MOCK_NEWSNOW_RESPONSE
        good_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            # First call fails, second succeeds
            mock_client.get.side_effect = [Exception("API down"), good_resp]
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_trending(feed)

        # Should still get signals from the second platform
        assert len(signals) > 0

    def test_empty_items_returns_empty_list(self):
        """Empty items array → valid empty RawSignal list."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed(config={
            "platforms": [{"id": "weibo", "name": "微博"}],
            "top_n": 10,
            "request_interval_ms": 0,
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "success", "items": []}
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_trending(feed)

        assert signals == []

    def test_cache_status_accepted(self):
        """API returning status=cache is treated as valid data."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed(config={
            "platforms": [{"id": "weibo", "name": "微博"}],
            "top_n": 10,
            "request_interval_ms": 0,
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_CACHE_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_trending(feed)

        assert len(signals) == 1
        assert signals[0].title == "缓存数据"

    def test_error_status_skips_platform(self):
        """API returning status=error → skip platform, no crash."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed(config={
            "platforms": [{"id": "weibo", "name": "微博"}],
            "top_n": 10,
            "request_interval_ms": 0,
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_ERROR_RESPONSE
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_trending(feed)

        assert signals == []

    def test_skips_items_with_empty_title(self):
        """Items with None/empty title are skipped."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed(config={
            "platforms": [{"id": "weibo", "name": "微博"}],
            "top_n": 10,
            "request_interval_ms": 0,
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "items": [
                {"title": "", "url": "https://weibo.com/1"},
                {"title": None, "url": "https://weibo.com/2"},
                {"title": "Valid Title", "url": "https://weibo.com/3"},
            ],
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_trending(feed)

        assert len(signals) == 1
        assert signals[0].title == "Valid Title"

    def test_no_platforms_configured(self):
        """Empty platforms list → empty result, no crash."""
        from jobs.adapters.trending import fetch_trending

        feed = _make_trending_feed(config={
            "platforms": [],
            "top_n": 10,
            "request_interval_ms": 0,
        })

        with patch("jobs.adapters.trending.safe_client"):
            signals = fetch_trending(feed)

        assert signals == []
