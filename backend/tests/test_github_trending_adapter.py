"""
Tests for GitHub Trending adapter (HTML scraping).

Tests:
- AC1: Adapter returns >=5 RawSignal objects with correct fields
- AC2: FeedType.GITHUB_TRENDING in models.py + ADAPTER_MAP
- AC3: config.yaml has github-trending feed
- AC4: Edge cases: HTML changes, empty page, rate limit
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobs.models import Feed, FeedType, RawSignal


# ── Fixtures ──────────────────────────────────────────────────────────

# Minimal GitHub Trending HTML with 3 repos
MOCK_TRENDING_HTML = """
<html><body>
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/mattpocock/skills" class="Link">
      <span class="text-normal">mattpocock / </span>skills
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 tmp-pr-4">Skills for Real Engineers.</p>
  <div class="f6 color-fg-muted mt-2">
    <span itemprop="programmingLanguage">Shell</span>
    <a href="/mattpocock/skills/stargazers" class="Link">30,745</a>
    <span class="d-inline-block float-sm-right">5,645 stars today</span>
  </div>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/gastownhall/beads" class="Link">
      <span class="text-normal">gastownhall / </span>beads
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 tmp-pr-4">Memory for coding agents</p>
  <div class="f6 color-fg-muted mt-2">
    <span itemprop="programmingLanguage">Go</span>
    <a href="/gastownhall/beads/stargazers" class="Link">22,232</a>
    <span class="d-inline-block float-sm-right">498 stars today</span>
  </div>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/deepseek-ai/DeepSeek-V3" class="Link">
      <span class="text-normal">deepseek-ai / </span>DeepSeek-V3
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 tmp-pr-4"></p>
  <div class="f6 color-fg-muted mt-2">
    <span itemprop="programmingLanguage">Python</span>
    <a href="/deepseek-ai/DeepSeek-V3/stargazers" class="Link">103,118</a>
    <span class="d-inline-block float-sm-right">81 stars today</span>
  </div>
</article>
</body></html>
"""


def _make_github_trending_feed(**overrides) -> Feed:
    """Create a github-trending feed config for testing."""
    defaults = {
        "id": "github-trending-test",
        "name": "GitHub Trending Test",
        "type": FeedType.GITHUB_TRENDING,
        "tier": "engineering",
        "config": {
            "spoken_language": "",
            "since": "daily",
            "top_n": 25,
        },
        "tags": ["github", "trending", "open-source"],
        "enabled": True,
    }
    defaults.update(overrides)
    return Feed(**defaults)


# ── AC1: Adapter returns valid RawSignal list ────────────────────────

class TestGitHubTrendingAdapterBasic:
    """AC1: fetch_github_trending returns >=5 RawSignals with correct fields."""

    def test_fetch_returns_raw_signals(self):
        """Adapter returns list of RawSignal from GitHub Trending page."""
        from jobs.adapters.github_trending import fetch_github_trending

        feed = _make_github_trending_feed()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = MOCK_TRENDING_HTML
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.github_trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_github_trending(feed)

        assert isinstance(signals, list)
        assert len(signals) == 3  # 3 repos in mock HTML
        assert all(isinstance(s, RawSignal) for s in signals)

    def test_signal_fields_correct(self):
        """Each signal has owner/name title, description, language, url, stars_today."""
        from jobs.adapters.github_trending import fetch_github_trending

        feed = _make_github_trending_feed()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = MOCK_TRENDING_HTML
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.github_trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_github_trending(feed)

        sig = signals[0]
        assert sig.feed_id == "github-trending-test"
        assert sig.title == "mattpocock/skills"
        assert "Skills for Real Engineers" in sig.summary
        assert sig.url == "https://github.com/mattpocock/skills"
        assert sig.source == "Shell"  # language as source
        assert "github" in sig.tags

    def test_stars_today_in_score(self):
        """stars_today stored as score for downstream ranking."""
        from jobs.adapters.github_trending import fetch_github_trending

        feed = _make_github_trending_feed()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = MOCK_TRENDING_HTML
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.github_trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_github_trending(feed)

        assert signals[0].score == 5645.0
        assert signals[1].score == 498.0

    def test_top_n_limits_results(self):
        """top_n config limits number of repos returned."""
        from jobs.adapters.github_trending import fetch_github_trending

        feed = _make_github_trending_feed(config={
            "spoken_language": "",
            "since": "daily",
            "top_n": 2,
        })

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = MOCK_TRENDING_HTML  # 3 repos
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.github_trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_github_trending(feed)

        assert len(signals) == 2

    def test_empty_description_handled(self):
        """Repo with no description doesn't crash."""
        from jobs.adapters.github_trending import fetch_github_trending

        feed = _make_github_trending_feed()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = MOCK_TRENDING_HTML
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.github_trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_github_trending(feed)

        # DeepSeek-V3 has empty description in mock
        deepseek = signals[2]
        assert deepseek.title == "deepseek-ai/DeepSeek-V3"
        assert deepseek.summary == ""  # empty but not None


# ── AC2: FeedType.GITHUB_TRENDING registered ─────────────────────────

class TestGitHubTrendingRegistration:
    """AC2: FeedType.GITHUB_TRENDING exists and is in ADAPTER_MAP."""

    def test_feedtype_github_trending_exists(self):
        """FeedType enum has GITHUB_TRENDING value."""
        assert hasattr(FeedType, "GITHUB_TRENDING")
        assert FeedType.GITHUB_TRENDING == "github-trending"

    def test_adapter_map_has_github_trending(self):
        """signal_fetch ADAPTER_MAP includes github_trending adapter."""
        from jobs.handlers.signal_fetch import ADAPTER_MAP

        assert FeedType.GITHUB_TRENDING in ADAPTER_MAP


# ── Edge cases ────────────────────────────────────────────────────────

class TestGitHubTrendingEdgeCases:
    """Edge cases: HTTP errors, empty page, HTML changes."""

    def test_http_error_returns_empty_list(self):
        """HTTP error → empty list, no crash."""
        from jobs.adapters.github_trending import fetch_github_trending

        feed = _make_github_trending_feed()

        with patch("jobs.adapters.github_trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.side_effect = Exception("403 Forbidden")
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_github_trending(feed)

        assert signals == []

    def test_no_box_rows_returns_empty_list(self):
        """Page with no article.Box-row → empty list."""
        from jobs.adapters.github_trending import fetch_github_trending

        feed = _make_github_trending_feed()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><div>No repos today</div></body></html>"
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.github_trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_github_trending(feed)

        assert signals == []

    def test_malformed_repo_link_skipped(self):
        """Article without a valid repo link is skipped."""
        from jobs.adapters.github_trending import fetch_github_trending

        feed = _make_github_trending_feed()

        bad_html = """
        <html><body>
        <article class="Box-row">
          <h2 class="h3 lh-condensed"><a href="/explore">Explore</a></h2>
        </article>
        <article class="Box-row">
          <h2 class="h3 lh-condensed">
            <a href="/mattpocock/skills" class="Link">
              <span class="text-normal">mattpocock / </span>skills
            </a>
          </h2>
          <p class="col-9 color-fg-muted my-1 tmp-pr-4">Description</p>
          <div class="f6 color-fg-muted mt-2">
            <span itemprop="programmingLanguage">Shell</span>
            <span>100 stars today</span>
          </div>
        </article>
        </body></html>
        """

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = bad_html
        mock_resp.raise_for_status = MagicMock()

        with patch("jobs.adapters.github_trending.safe_client") as mock_client_ctx:
            mock_client = MagicMock()
            mock_client.get.return_value = mock_resp
            mock_client_ctx.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_ctx.return_value.__exit__ = MagicMock(return_value=False)

            signals = fetch_github_trending(feed)

        # Only the valid repo, not the /explore link
        assert len(signals) == 1
        assert signals[0].title == "mattpocock/skills"
