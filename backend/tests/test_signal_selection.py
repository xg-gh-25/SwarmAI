"""Tests for the shared signal denoising+ranking function (run_44342b40).

The Slack "Signal Digest" and the Welcome Page signals card must apply the SAME
denoising (feed exclusion / trending split / per-feed cap / 48h freshness /
final_score sort). This module tests the single source of truth,
``backend/jobs/signal_selection.py::select_signals``, plus the two callers'
convergence onto it.

Methodology: TDD RED->GREEN. Each behavioral assertion is written to FAIL against
the pre-refactor state (no shared function; Slack sorts by capped relevance_score
with no filters) and pass after convergence.

Covers:
  AC1  single source — the 4 denoising constants live ONLY in signal_selection.py
  AC2  both callers migrated — Welcome + Slack import & call select_signals
  AC3  Welcome byte-identical — build_session_briefing_data signals unchanged
  AC4  Slack denoise — 0 eastmoney, <=3 reference-commits, final_score order
  AC5  Slack render — flat (no per-feed headers), zh items grouped, no dropped items
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone


# ─────────────────────────── fixtures ───────────────────────────

def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _fresh() -> str:
    return _iso(datetime.now(timezone.utc))


def _stale() -> str:
    return _iso(datetime.now(timezone.utc) - timedelta(hours=72))


def _mixed_items() -> list[dict]:
    """A realistic multi-feed digest: eastmoney junk, a commit flood, frontier,
    a china-trending item, a stale item, and a zh AI signal."""
    now = _fresh()
    items: list[dict] = []
    # 10 reference-commits, pre-sorted by final_score desc (as write-time does)
    for i in range(10):
        items.append({
            "title": f"feat(moa): commit {i}", "summary": "repo update",
            "source": "python", "url": f"https://github.com/x/{i}",
            "relevance_score": 0.9, "final_score": 2.0 - i * 0.01,
            "urgency": "high", "tier": "engineering",
            "lang": "en", "feed_id": "reference-commits", "fetched_at": now,
        })
    # 2 lower-final_score frontier items (must survive the commit cap)
    for i in range(2):
        items.append({
            "title": f"Frontier release {i}", "summary": "model launch",
            "source": "frontier-labs", "url": f"https://openai.com/{i}",
            "relevance_score": 0.7, "final_score": 1.0 - i * 0.01,
            "urgency": "high", "tier": "frontier",
            "lang": "en", "feed_id": "frontier-labs", "fetched_at": now,
        })
    # eastmoney junk — must be dropped entirely
    items.append({
        "title": "惠城环保 +20%", "summary": "stock gainer", "source": "东方财富",
        "url": "https://eastmoney.com/1", "relevance_score": 0.95,
        "final_score": 2.5, "urgency": "medium", "tier": "engineering",
        "lang": "zh", "feed_id": "eastmoney-market", "fetched_at": now,
    })
    # china-trending — must route to hot_news, NOT signals
    items.append({
        "title": "微博热搜第一", "summary": "trending", "source": "weibo",
        "url": "https://weibo.com/1", "relevance_score": 0.6,
        "final_score": 0.8, "urgency": "low", "tier": "aggregate",
        "lang": "zh", "feed_id": "china-trending", "platform": "weibo",
        "rank": 1, "region": "cn", "fetched_at": now,
    })
    # stale AI signal — must be dropped by 48h cutoff
    items.append({
        "title": "Old news", "summary": "stale", "source": "blog",
        "url": "https://example.com/old", "relevance_score": 0.99,
        "final_score": 3.0, "urgency": "high", "tier": "frontier",
        "lang": "en", "feed_id": "ai-engineering", "fetched_at": _stale(),
    })
    # a fresh zh AI signal (NOT trending) — stays in signals, zh group in Slack
    items.append({
        "title": "国产大模型发布", "summary": "中文 AI 信号", "source": "机器之心",
        "url": "https://jiqizhixin.com/1", "relevance_score": 0.85,
        "final_score": 1.5, "urgency": "high", "tier": "leaders",
        "lang": "zh", "feed_id": "ai-leaders", "fetched_at": now,
    })
    return items


# ─────────────────────────── AC1: single source ───────────────────────────

class TestSingleSourceConstants:
    """The denoising constants must live ONLY in signal_selection.py."""

    _CONST_NAMES = [
        "TRENDING_FEEDS", "SIGNALS_EXCLUDED_FEEDS",
        "SIGNALS_PER_FEED_CAP", "FRESHNESS_CUTOFF_H",
    ]

    def test_constants_defined_in_signal_selection(self):
        from jobs import signal_selection as ss
        for name in self._CONST_NAMES:
            assert hasattr(ss, name), f"{name} must be defined in signal_selection.py"

    def test_no_duplicate_literal_in_other_modules(self):
        """executor.py and proactive_intelligence.py must NOT re-declare the
        denoising sets/dict as literals — they import from signal_selection."""
        import jobs.executor as ex
        import core.proactive_intelligence as pi

        for mod in (ex, pi):
            src = inspect.getsource(mod)
            # The eastmoney/china-trending/reference-commits literals are the
            # denoising values; a frozenset/dict literal of them in these
            # modules = the re-drift this fix eliminates.
            assert 'frozenset({"eastmoney-market"})' not in src, \
                f"{mod.__name__} re-declares SIGNALS_EXCLUDED_FEEDS literal"
            assert 'frozenset({"china-trending"})' not in src, \
                f"{mod.__name__} re-declares TRENDING_FEEDS literal"
            assert '"reference-commits": 3' not in src, \
                f"{mod.__name__} re-declares SIGNALS_PER_FEED_CAP literal"

    def test_no_duplicate_label_map_in_other_modules(self):
        """The DISPLAY source-label map must also live only in signal_selection —
        both callers use readable_source() (run_cda1e759, closes the last drift
        risk from run_44342b40's REVIEW+Gate-2)."""
        import jobs.executor as ex
        import core.proactive_intelligence as pi

        for mod in (ex, pi):
            src = inspect.getsource(mod)
            assert "_FEED_SOURCE_LABELS = {" not in src, \
                f"{mod.__name__} re-declares the display label map literal"
            assert '"reference-commits": "Repo Update"' not in src, \
                f"{mod.__name__} still has an inline label-map entry"


class TestReadableSource:
    """readable_source() is the single source for display source labels."""

    def test_lang_source_feed_uses_label(self):
        from jobs.signal_selection import readable_source
        # github/commit feeds: raw source is a language → readable label
        assert readable_source("reference-commits", "python") == "Repo Update"
        assert readable_source("github-trending", "go") == "GitHub Trending"

    def test_non_lang_feed_uses_raw_source(self):
        from jobs.signal_selection import readable_source
        assert readable_source("ai-leaders", "机器之心") == "机器之心"
        assert readable_source("frontier-labs", "OpenAI") == "OpenAI"

    def test_matches_old_inline_logic(self):
        """Behavior-preserving: reproduces the exact expression both callers had
        inline — .get(feed_id, raw) if feed_id in LANG_SOURCE_FEEDS else raw."""
        from jobs.signal_selection import (
            readable_source, _FEED_SOURCE_LABELS, _LANG_SOURCE_FEEDS,
        )
        cases = [
            ("reference-commits", "python"), ("github-trending", "rust"),
            ("frontier-labs", "OpenAI"), ("ai-leaders", "机器之心"),
            ("unknown-feed", "whatever"), ("reference-commits", ""),
        ]
        for feed_id, raw in cases:
            old = (_FEED_SOURCE_LABELS.get(feed_id, raw)
                   if feed_id in _LANG_SOURCE_FEEDS else raw)
            assert readable_source(feed_id, raw) == old


# ─────────────────────────── select_signals behavior ───────────────────────────

class TestSelectSignalsCore:

    def test_eastmoney_excluded(self):
        from jobs.signal_selection import select_signals
        out = select_signals(_mixed_items())
        feeds = {s["feed_id"] for s in out["signals"]}
        assert "eastmoney-market" not in feeds

    def test_china_trending_routed_to_hot_news(self):
        from jobs.signal_selection import select_signals
        out = select_signals(_mixed_items())
        sig_feeds = {s["feed_id"] for s in out["signals"]}
        hot_feeds = {s["feed_id"] for s in out["hot_news"]}
        assert "china-trending" not in sig_feeds
        assert "china-trending" in hot_feeds

    def test_reference_commits_capped_at_3(self):
        from jobs.signal_selection import select_signals
        out = select_signals(_mixed_items())
        ref = sum(1 for s in out["signals"] if s["feed_id"] == "reference-commits")
        assert ref <= 3, f"reference-commits must be capped at <=3, got {ref}"

    def test_stale_dropped_from_both(self):
        from jobs.signal_selection import select_signals
        out = select_signals(_mixed_items())
        titles = {s["title"] for s in out["signals"]} | {s["title"] for s in out["hot_news"]}
        assert "Old news" not in titles, "48h cutoff must drop stale items"

    def test_sorted_by_final_score_desc(self):
        from jobs.signal_selection import select_signals
        out = select_signals(_mixed_items())
        scores = [s.get("final_score", 0) for s in out["signals"]]
        assert scores == sorted(scores, reverse=True), \
            "signals must be sorted by final_score desc"

    def test_frontier_survives_commit_flood(self):
        """The cap's whole point: lower-final_score frontier items get slots."""
        from jobs.signal_selection import select_signals
        out = select_signals(_mixed_items())
        assert any(s["feed_id"] == "frontier-labs" for s in out["signals"])

    def test_missing_final_score_falls_back(self):
        """Items without final_score must not crash the sort."""
        from jobs.signal_selection import select_signals
        items = [
            {"title": "no score", "feed_id": "ai-engineering",
             "relevance_score": 0.5, "fetched_at": _fresh()},
        ]
        out = select_signals(items)
        assert len(out["signals"]) == 1

    def test_pure_no_mutation_of_input(self):
        """select_signals must not mutate the caller's list."""
        from jobs.signal_selection import select_signals
        items = _mixed_items()
        before = len(items)
        select_signals(items)
        assert len(items) == before

    def test_non_numeric_final_score_does_not_crash_sort(self):
        """Gate-2 HIGH: a malformed final_score (str/list) must degrade to 0,
        not TypeError the sort — signal_digest.json is a serialization boundary
        feeding two surfaces (O023)."""
        from jobs.signal_selection import select_signals
        items = [
            {"title": "good", "feed_id": "ai-engineering", "final_score": 1.5,
             "lang": "en", "fetched_at": _fresh()},
            {"title": "bad-score", "feed_id": "ai-engineering", "final_score": "oops",
             "lang": "en", "fetched_at": _fresh()},
            {"title": "list-score", "feed_id": "ai-leaders", "final_score": [1, 2],
             "lang": "en", "fetched_at": _fresh()},
        ]
        out = select_signals(items)  # must not raise
        assert len(out["signals"]) == 3
        # good (1.5) ranks above the two coerced-to-0 items
        assert out["signals"][0]["title"] == "good"


# ─────────────────────────── AC3: Welcome byte-identical ───────────────────────────

class TestWelcomePreserved:
    """build_session_briefing_data signals must be unchanged by the refactor.

    The prior contract (test_welcome_signal_cleanup.py) is re-verified here
    against the shared function to prove convergence didn't regress Welcome.
    """

    def _write_digest(self, tmp_path, items):
        ctx = tmp_path / ".context"
        ctx.mkdir(exist_ok=True)
        (ctx / "MEMORY.md").write_text("## Open Threads\n_(None)_\n", encoding="utf-8")
        sig_dir = tmp_path / "Services" / "signals"
        sig_dir.mkdir(parents=True, exist_ok=True)
        now = _fresh()
        for it in items:
            it.setdefault("fetched_at", now)
        digest = {"generated_at": now, "signals_count": len(items), "items": items}
        (sig_dir / "signal_digest.json").write_text(json.dumps(digest), encoding="utf-8")

    def test_welcome_excludes_eastmoney_and_caps_commits(self, tmp_path):
        from core.proactive_intelligence import build_session_briefing_data
        self._write_digest(tmp_path, _mixed_items())
        result = build_session_briefing_data(str(tmp_path))
        signals = result.get("signals", [])
        feeds = [s.get("feedId") for s in signals]
        assert "eastmoney-market" not in feeds
        assert sum(1 for f in feeds if f == "reference-commits") <= 3
        assert "china-trending" not in feeds
        # zh AI signal (non-trending) still surfaces
        assert "国产大模型发布" in [s.get("title") for s in signals]

    def test_welcome_hot_news_gets_trending(self, tmp_path):
        from core.proactive_intelligence import build_session_briefing_data
        self._write_digest(tmp_path, _mixed_items())
        result = build_session_briefing_data(str(tmp_path))
        hot_titles = [h.get("title") for h in result.get("hotNews", [])]
        assert "微博热搜第一" in hot_titles


# ─────────────────────────── AC4/AC5: Slack denoise + render ───────────────────────────

class TestSlackFormatter:

    def _patch_digest(self, monkeypatch, tmp_path, items):
        import jobs.executor as ex
        sig_dir = tmp_path / "Services" / "signals"
        sig_dir.mkdir(parents=True, exist_ok=True)
        now = _fresh()
        for it in items:
            it.setdefault("fetched_at", now)
        digest = {"generated_at": now, "signals_count": len(items), "items": items}
        (sig_dir / "signal_digest.json").write_text(json.dumps(digest), encoding="utf-8")
        monkeypatch.setattr(ex, "SWARMWS", tmp_path)

    def test_slack_excludes_eastmoney(self, monkeypatch, tmp_path):
        from jobs.executor import _format_signal_digest_message
        self._patch_digest(monkeypatch, tmp_path, _mixed_items())
        msg = _format_signal_digest_message(max_items=20)
        assert "惠城环保" not in msg, "eastmoney junk must not reach Slack"

    def test_slack_excludes_china_trending(self, monkeypatch, tmp_path):
        from jobs.executor import _format_signal_digest_message
        self._patch_digest(monkeypatch, tmp_path, _mixed_items())
        msg = _format_signal_digest_message(max_items=20)
        assert "微博热搜第一" not in msg, "china-trending is hot_news, not signals"

    def test_slack_caps_reference_commits(self, monkeypatch, tmp_path):
        from jobs.executor import _format_signal_digest_message
        self._patch_digest(monkeypatch, tmp_path, _mixed_items())
        msg = _format_signal_digest_message(max_items=20)
        commit_lines = msg.count("feat(moa): commit")
        assert commit_lines <= 3, f"reference-commits must be capped, got {commit_lines}"

    def test_slack_drops_stale(self, monkeypatch, tmp_path):
        from jobs.executor import _format_signal_digest_message
        self._patch_digest(monkeypatch, tmp_path, _mixed_items())
        msg = _format_signal_digest_message(max_items=20)
        assert "Old news" not in msg

    def test_slack_has_zh_group(self, monkeypatch, tmp_path):
        from jobs.executor import _format_signal_digest_message
        self._patch_digest(monkeypatch, tmp_path, _mixed_items())
        msg = _format_signal_digest_message(max_items=20)
        # zh AI signal present + grouped under a Chinese-language header
        assert "国产大模型发布" in msg
        assert "中文" in msg, "zh items must appear under a Chinese-language group header"

    def test_slack_no_perfeed_group_headers(self, monkeypatch, tmp_path):
        """Flat top-N, not grouped by raw feed_id (the old unfriendly structure)."""
        from jobs.executor import _format_signal_digest_message
        self._patch_digest(monkeypatch, tmp_path, _mixed_items())
        msg = _format_signal_digest_message(max_items=20)
        # Old code emitted per-feed labels like "Frontier Labs"/"Reference Commits"
        # as bold section headers. New flat render must not.
        assert "Reference Commits" not in msg
        assert "🔬 Frontier Labs" not in msg

    def test_slack_count_preserved_no_silent_drop(self, monkeypatch, tmp_path):
        """Every non-excluded, non-stale, non-trending signal within max_items
        must render exactly once — zh grouping must not drop/duplicate items."""
        from jobs.executor import _format_signal_digest_message
        from jobs.signal_selection import select_signals
        items = _mixed_items()
        self._patch_digest(monkeypatch, tmp_path, items)
        expected = select_signals(items)["signals"][:20]
        msg = _format_signal_digest_message(max_items=20)
        for s in expected:
            assert s["title"] in msg, f"signal dropped from Slack render: {s['title']}"

    def test_slack_empty_digest_returns_empty(self, monkeypatch, tmp_path):
        from jobs.executor import _format_signal_digest_message
        self._patch_digest(monkeypatch, tmp_path, [])
        assert _format_signal_digest_message() == ""

    def test_slack_non_string_fields_do_not_crash(self, monkeypatch, tmp_path):
        """Gate-2 MED: a malformed item with non-string title/summary/source
        must render (coerced) rather than crash the Slack job (O023)."""
        from jobs.executor import _format_signal_digest_message
        items = [{
            "title": 12345, "summary": ["a", "b"], "source": 99,
            "url": "https://x/1", "final_score": 1.0, "urgency": "high",
            "tier": "engineering", "lang": "en", "feed_id": "ai-engineering",
            "fetched_at": _fresh(),
        }]
        self._patch_digest(monkeypatch, tmp_path, items)
        msg = _format_signal_digest_message(max_items=20)  # must not raise
        assert "12345" in msg
