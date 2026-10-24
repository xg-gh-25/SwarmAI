"""
Tests for Briefing Hub v2 — P1 (signal pipeline).

Tests the following acceptance criteria:
  AC1: Chinese signals in digest with source/lang fields
  AC9: Source language preserved (no translation)

Methodology: TDD RED→GREEN. (The P2 Welcome briefing API tests were deleted
with build_session_briefing_data — design docs/design/welcome-screen-v2.md.)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone



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
