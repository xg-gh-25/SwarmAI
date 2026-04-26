"""
Tests for Briefing Hub v2 — P1 (signal pipeline) + P2 (API enrichment).

Tests the following acceptance criteria:
  AC1: Chinese signals in digest with source/lang fields
  AC2: Briefing API returns working/signals/hotNews/stocks/output/jobsSummary
  AC9: Source language preserved (no translation)

Methodology: TDD RED→GREEN. All tests must FAIL before implementation.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ── P1: Signal Pipeline Tests ────────────────────────────────────────


class TestDetectLang:
    """AC9: Language detection preserves source language."""

    def test_chinese_text_detected_as_zh(self):
        from jobs.handlers.signal_digest import _detect_lang
        assert _detect_lang("白宫记者晚宴枪击事件") == "zh"

    def test_english_text_detected_as_en(self):
        from jobs.handlers.signal_digest import _detect_lang
        assert _detect_lang("DeepSeek V4 — almost on the frontier") == "en"

    def test_mixed_text_mostly_chinese(self):
        from jobs.handlers.signal_digest import _detect_lang
        assert _detect_lang("36氪: AI智能基座发布 润芯微") == "zh"

    def test_mixed_text_mostly_english(self):
        from jobs.handlers.signal_digest import _detect_lang
        assert _detect_lang("Claude Code v4.6 quality reports update") == "en"

    def test_empty_string(self):
        from jobs.handlers.signal_digest import _detect_lang
        assert _detect_lang("") == "en"  # default


class TestSampleSignalsForDigest:
    """AC1: Per-tier quota sampling ensures Chinese signals are included."""

    def test_trending_signals_included(self):
        """Trending (chinese) signals must appear in sampled output."""
        from jobs.handlers.signal_digest import _sample_signals_for_digest
        from jobs.models import RawSignal

        signals = []
        # 40 engineering signals (would fill old :30 cap)
        for i in range(40):
            signals.append(RawSignal(
                feed_id="ai-engineering", title=f"Eng signal {i}",
                url=f"https://example.com/{i}", source="blog",
                tier="engineering", published=datetime.now(timezone.utc),
            ))
        # 10 trending signals
        for i in range(10):
            signals.append(RawSignal(
                feed_id="china-trending", title=f"热搜 {i}",
                url=f"https://weibo.com/{i}", source="微博",
                tier="aggregate", published=datetime.now(timezone.utc),
            ))

        sampled = _sample_signals_for_digest(signals, max_total=40)
        trending_titles = [s.title for s in sampled if s.feed_id == "china-trending"]
        assert len(trending_titles) > 0, "Trending signals must be included"

    def test_cn_ai_signals_included(self):
        """cn-ai (36kr, infoq) signals must appear in sampled output."""
        from jobs.handlers.signal_digest import _sample_signals_for_digest
        from jobs.models import RawSignal

        signals = []
        for i in range(40):
            signals.append(RawSignal(
                feed_id="ai-engineering", title=f"Eng {i}",
                url=f"https://example.com/{i}", source="blog",
                tier="engineering", published=datetime.now(timezone.utc),
            ))
        for i in range(5):
            signals.append(RawSignal(
                feed_id="cn-ai", title=f"36氪: AI新闻 {i}",
                url=f"https://36kr.com/{i}", source="36氪",
                tier="engineering", published=datetime.now(timezone.utc),
            ))

        sampled = _sample_signals_for_digest(signals, max_total=40)
        cn_titles = [s.title for s in sampled if s.feed_id == "cn-ai"]
        assert len(cn_titles) > 0, "cn-ai signals must be included"

    def test_respects_max_total(self):
        from jobs.handlers.signal_digest import _sample_signals_for_digest
        from jobs.models import RawSignal

        signals = [RawSignal(
            feed_id="ai-engineering", title=f"Signal {i}",
            url=f"https://example.com/{i}", source="blog",
            tier="engineering", published=datetime.now(timezone.utc),
        ) for i in range(100)]

        sampled = _sample_signals_for_digest(signals, max_total=40)
        assert len(sampled) <= 40


class TestScoredItemsHaveLangAndSource:
    """AC1: Scored items in signal_digest.json have source, tier, and lang fields."""

    def test_simple_scored_items_have_lang(self):
        from jobs.handlers.signal_digest import _simple_scored_items
        from jobs.models import RawSignal

        signals = [
            RawSignal(feed_id="cn-ai", title="36氪: AI基座发布",
                      url="https://36kr.com/1", source="36氪",
                      tier="engineering"),
            RawSignal(feed_id="ai-engineering", title="DeepSeek V4",
                      url="https://example.com/1", source="blog",
                      tier="engineering"),
        ]
        items = _simple_scored_items(signals)
        assert items[0].get("lang") == "zh"
        assert items[0].get("source") == "36氪"
        assert items[0].get("tier") == "engineering"
        assert items[1].get("lang") == "en"


# ── P2: Briefing API Tests ───────────────────────────────────────────


class TestBriefingAPIShape:
    """AC2: Briefing API returns all required sections."""

    def test_response_has_new_keys(self, tmp_path):
        """build_session_briefing_data must return working/signals/hotNews/stocks/output/jobsSummary."""
        from core.proactive_intelligence import build_session_briefing_data

        # Minimal workspace structure
        ctx = tmp_path / ".context"
        ctx.mkdir()
        (ctx / "MEMORY.md").write_text("## Open Threads\n_(None)_\n")

        result = build_session_briefing_data(str(tmp_path))

        # New keys must exist
        assert "working" in result, "Missing 'working' key"
        assert "signals" in result, "Missing 'signals' key"
        assert "hotNews" in result, "Missing 'hotNews' key"
        assert "stocks" in result, "Missing 'stocks' key"
        assert "output" in result, "Missing 'output' key"
        assert "jobsSummary" in result, "Missing 'jobsSummary' key"

    def test_output_has_subgroups(self, tmp_path):
        """output must contain builds, content, files sub-keys."""
        from core.proactive_intelligence import build_session_briefing_data

        ctx = tmp_path / ".context"
        ctx.mkdir()
        (ctx / "MEMORY.md").write_text("## Open Threads\n_(None)_\n")

        result = build_session_briefing_data(str(tmp_path))
        output = result.get("output", {})
        assert "builds" in output, "Missing output.builds"
        assert "content" in output, "Missing output.content"
        assert "files" in output, "Missing output.files"

    def test_jobs_summary_shape(self, tmp_path):
        """jobsSummary must have total/healthy/failed/disabled/jobs keys."""
        from core.proactive_intelligence import build_session_briefing_data

        ctx = tmp_path / ".context"
        ctx.mkdir()
        (ctx / "MEMORY.md").write_text("## Open Threads\n_(None)_\n")

        result = build_session_briefing_data(str(tmp_path))
        js = result.get("jobsSummary", {})
        for key in ["total", "healthy", "failed", "disabled", "jobs"]:
            assert key in js, f"Missing jobsSummary.{key}"


class TestExtractSignalsSplitByArea:
    """AC1+AC2: Signals split into signals (tech) vs hotNews (trending)."""

    def test_trending_goes_to_hot_news(self, tmp_path):
        """Items with feed_id 'china-trending' go to hotNews, not signals."""
        from core.proactive_intelligence import build_session_briefing_data

        # Setup workspace
        ctx = tmp_path / ".context"
        ctx.mkdir()
        (ctx / "MEMORY.md").write_text("## Open Threads\n_(None)_\n")

        # Write a signal_digest.json with both types
        sig_dir = tmp_path / "Services" / "signals"
        sig_dir.mkdir(parents=True)
        now = datetime.now(timezone.utc).isoformat()
        digest = {
            "generated_at": now,
            "signals_count": 4,
            "items": [
                {"title": "DeepSeek V4", "summary": "AI model", "source": "blog",
                 "url": "https://example.com/1", "relevance_score": 0.8,
                 "urgency": "medium", "fetched_at": now, "tier": "engineering",
                 "lang": "en", "feed_id": "ai-engineering"},
                {"title": "白宫枪击", "summary": "Top 1", "source": "微博",
                 "url": "https://weibo.com/1", "relevance_score": 0.5,
                 "urgency": "high", "fetched_at": now, "tier": "aggregate",
                 "lang": "zh", "feed_id": "china-trending",
                 "platform": "微博", "rank": 1, "region": "cn"},
                {"title": "36氪: AI基座", "summary": "Tech news", "source": "36氪",
                 "url": "https://36kr.com/1", "relevance_score": 0.6,
                 "urgency": "medium", "fetched_at": now, "tier": "engineering",
                 "lang": "zh", "feed_id": "cn-ai"},
                {"title": "Andreessen: AI", "summary": "HN discussion", "source": "HN",
                 "url": "https://news.ycombinator.com/1", "relevance_score": 0.5,
                 "urgency": "medium", "fetched_at": now, "tier": "aggregate",
                 "lang": "en", "feed_id": "hn-ai"},
            ],
        }
        (sig_dir / "signal_digest.json").write_text(json.dumps(digest))

        result = build_session_briefing_data(str(tmp_path))

        signals = result.get("signals", [])
        hot_news = result.get("hotNews", [])

        # Tech signals (engineering tier, non-trending feed_id)
        signal_titles = [s["title"] for s in signals]
        assert "DeepSeek V4" in signal_titles
        assert "36氪: AI基座" in signal_titles  # cn-ai is engineering tier

        # Trending goes to hotNews
        hot_titles = [h["title"] for h in hot_news]
        assert "白宫枪击" in hot_titles


class TestExtractStockItems:
    """AC2: Stocks extracted from report files."""

    def test_stock_reports_detected(self, tmp_path):
        from core.proactive_intelligence import build_session_briefing_data

        ctx = tmp_path / ".context"
        ctx.mkdir()
        (ctx / "MEMORY.md").write_text("## Open Threads\n_(None)_\n")

        # Create stock reports
        reports_dir = tmp_path / "Services" / "stock-analysis" / "reports"
        reports_dir.mkdir(parents=True)
        today = datetime.now().strftime("%Y-%m-%d")
        (reports_dir / f"{today}-515070-人工智能ETF.md").write_text("# Report")
        (reports_dir / f"{today}-513180-恒生科技ETF.md").write_text("# Report")

        result = build_session_briefing_data(str(tmp_path))
        stocks = result.get("stocks", [])
        assert len(stocks) >= 2
        tickers = [s["ticker"] for s in stocks]
        assert "515070" in tickers
        assert "513180" in tickers


class TestExtractBuilds:
    """AC2: Pipeline builds extracted from .artifacts/runs/."""

    def test_pipeline_reports_detected(self, tmp_path):
        from core.proactive_intelligence import build_session_briefing_data

        ctx = tmp_path / ".context"
        ctx.mkdir()
        (ctx / "MEMORY.md").write_text("## Open Threads\n_(None)_\n")

        # Create pipeline run with REPORT.md
        run_dir = tmp_path / "Projects" / "TestProj" / ".artifacts" / "runs" / "run_abc12345"
        run_dir.mkdir(parents=True)
        (run_dir / "REPORT.md").write_text(
            "# Autonomous Pipeline Report: Test Feature\n\n"
            "**Run ID:** run_abc12345 | **Project:** TestProj | **Profile:** full\n"
            "**Date:** 2026-04-26 | **Confidence:** 9/10\n\n"
            "## 1. Requirement\nTest feature requirement\n"
        )

        result = build_session_briefing_data(str(tmp_path))
        output = result.get("output", {})
        builds = output.get("builds", [])
        assert len(builds) >= 1
        assert builds[0]["runId"] == "run_abc12345"
        assert builds[0]["project"] == "TestProj"
        assert builds[0]["confidence"] == 9


class TestExtractContent:
    """AC2: Pollinate content extracted from content directories."""

    def test_pollinate_content_detected(self, tmp_path):
        from core.proactive_intelligence import build_session_briefing_data

        ctx = tmp_path / ".context"
        ctx.mkdir()
        (ctx / "MEMORY.md").write_text("## Open Threads\n_(None)_\n")

        # Create pollinate content
        content_dir = tmp_path / "Services" / "pollinate-studio" / "content" / "test-video"
        content_dir.mkdir(parents=True)
        (content_dir / "content_package.md").write_text(
            "# Test Video — AI Demo\n\nSome content\n"
        )
        video_dir = content_dir / "video"
        video_dir.mkdir()
        (video_dir / "audio.wav").write_bytes(b"fake")
        (video_dir / "audio.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello")

        result = build_session_briefing_data(str(tmp_path))
        output = result.get("output", {})
        content = output.get("content", [])
        assert len(content) >= 1
        assert content[0]["slug"] == "test-video"
        assert content[0]["type"] == "video"
