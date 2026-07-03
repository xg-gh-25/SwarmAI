"""Tests for Welcome-page signal cleanup + repositioning (run_b579f702, Plan A).

Covers the backend logic changes to ``build_session_briefing_data`` (the Welcome
Screen data source):

  AC3: eastmoney-market items are EXCLUDED from the Welcome ``signals`` array
       (they are stock-market gainers, not AI/tech signals — noise in Signals).
  AC5: reference-commits items are CAPPED at <=3 within ``signals`` so the
       hermes-agent/openclaw commit stream stops flooding the top-8; higher-tier
       (frontier/leaders) items still surface.

Methodology: TDD RED->GREEN. Both assertions FAIL against the pre-change code
(no eastmoney exclusion; no per-feed cap) and pass after the fix.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone


def _write_digest(tmp_path, items: list[dict]) -> None:
    """Write a minimal workspace + signal_digest.json fixture."""
    ctx = tmp_path / ".context"
    ctx.mkdir(exist_ok=True)
    (ctx / "MEMORY.md").write_text("## Open Threads\n_(None)_\n", encoding="utf-8")
    sig_dir = tmp_path / "Services" / "signals"
    sig_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    for it in items:
        it.setdefault("fetched_at", now)
    digest = {"generated_at": now, "signals_count": len(items), "items": items}
    (sig_dir / "signal_digest.json").write_text(json.dumps(digest), encoding="utf-8")


class TestEastmoneyExcludedFromSignals:
    """AC3: eastmoney-market (stock gainers) must NOT appear in Welcome signals."""

    def test_eastmoney_item_not_in_signals(self, tmp_path):
        from core.proactive_intelligence import build_session_briefing_data

        _write_digest(tmp_path, [
            {"title": "DeepSeek V4", "summary": "AI model", "source": "blog",
             "url": "https://example.com/1", "relevance_score": 0.8,
             "urgency": "medium", "tier": "engineering",
             "lang": "en", "feed_id": "ai-engineering"},
            {"title": "惠城环保 +20%", "summary": "stock gainer", "source": "东方财富",
             "url": "https://eastmoney.com/1", "relevance_score": 0.9,
             "urgency": "medium", "tier": "engineering",
             "lang": "zh", "feed_id": "eastmoney-market"},
        ])

        result = build_session_briefing_data(str(tmp_path))
        signal_feeds = [s.get("feedId") for s in result.get("signals", [])]
        signal_titles = [s.get("title") for s in result.get("signals", [])]

        assert "eastmoney-market" not in signal_feeds, \
            "eastmoney-market must be excluded from Welcome signals"
        assert "惠城环保 +20%" not in signal_titles
        # The genuine AI signal still surfaces.
        assert "DeepSeek V4" in signal_titles


class TestReferenceCommitsCapped:
    """AC5: reference-commits capped at <=3 in signals; higher-tier still surfaces."""

    def test_reference_commits_capped_at_3(self, tmp_path):
        from core.proactive_intelligence import build_session_briefing_data

        items = []
        # 10 reference-commits items, pre-sorted highest final_score first
        # (mimics real digest where the commit stream dominates the top).
        for i in range(10):
            items.append({
                "title": f"feat(moa): commit {i}", "summary": "repo update",
                "source": "python", "url": f"https://github.com/x/{i}",
                "relevance_score": 0.9, "final_score": 2.0 - i * 0.01,
                "urgency": "high", "tier": "engineering",
                "lang": "en", "feed_id": "reference-commits",
            })
        # 2 lower-ranked but high-value frontier items after the commit flood.
        for i in range(2):
            items.append({
                "title": f"Frontier Labs release {i}", "summary": "model launch",
                "source": "frontier-labs", "url": f"https://openai.com/{i}",
                "relevance_score": 0.7, "final_score": 1.0 - i * 0.01,
                "urgency": "high", "tier": "frontier",
                "lang": "en", "feed_id": "frontier-labs",
            })

        _write_digest(tmp_path, items)
        result = build_session_briefing_data(str(tmp_path))
        signals = result.get("signals", [])
        ref_count = sum(1 for s in signals if s.get("feedId") == "reference-commits")
        frontier_titles = [s.get("title") for s in signals
                           if s.get("feedId") == "frontier-labs"]

        assert ref_count <= 3, \
            f"reference-commits must be capped at <=3 in signals, got {ref_count}"
        # The cap must let the lower-ranked frontier items through (the point of the fix).
        assert len(frontier_titles) >= 1, \
            "capping reference-commits must free slots for higher-tier feeds"
