"""Tests for Signal DDD Bridge (Channels 2 + 4 of DDD Cultivation).

Tests heuristic-based signal scoring and CultivationProposal generation
from daily signal digests and learn-content knowledge cards.
"""

import json
import pytest

from hooks.signal_ddd_bridge import (
    bridge_signals_to_ddd,
    bridge_learned_content_to_ddd,
    _classify_signal,
    _MAX_PROPOSALS_PER_RUN,
)


@pytest.fixture
def workspace(tmp_path):
    """Create workspace with signal digest and project dir."""
    (tmp_path / "Projects" / "SwarmAI").mkdir(parents=True)
    (tmp_path / "Services" / "signals").mkdir(parents=True)
    return tmp_path


class TestBridgeSignalsToDDD:
    """Channel 4: signal_digest.json → PRODUCT.md proposals."""

    def test_high_relevance_signal_generates_proposal(self, workspace):
        """Signals with score >= 0.8 produce proposals."""
        digest = {
            "items": [
                {
                    "title": "OpenAI launches competitor product targeting enterprise",
                    "summary": "New market disruption in AI agent space",
                    "score": 0.9,
                    "url": "https://example.com/news",
                }
            ]
        }
        (workspace / "Services" / "signals" / "signal_digest.json").write_text(
            json.dumps(digest)
        )

        count = bridge_signals_to_ddd(str(workspace))
        assert count == 1

        proposals_dir = workspace / "Projects" / "SwarmAI" / ".artifacts" / "proposals"
        assert proposals_dir.exists()
        files = list(proposals_dir.glob("*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert "competitor" in data["content"].lower() or "OpenAI" in data["content"]
        assert data["confidence"] <= 0.95

    def test_low_relevance_signal_skipped(self, workspace):
        """Signals with score < 0.8 produce no proposals."""
        digest = {
            "items": [
                {"title": "Minor tech update", "summary": "Not relevant", "score": 0.3}
            ]
        }
        (workspace / "Services" / "signals" / "signal_digest.json").write_text(
            json.dumps(digest)
        )

        count = bridge_signals_to_ddd(str(workspace))
        assert count == 0

    def test_max_proposals_cap(self, workspace):
        """Never generate more than _MAX_PROPOSALS_PER_RUN proposals."""
        items = [
            {
                "title": f"Enterprise AI competitor #{i}",
                "summary": "Major market shift in AI agent market disruption",
                "score": 0.95,
                "url": f"https://example.com/{i}",
            }
            for i in range(10)
        ]
        digest = {"items": items}
        (workspace / "Services" / "signals" / "signal_digest.json").write_text(
            json.dumps(digest)
        )

        count = bridge_signals_to_ddd(str(workspace))
        assert count == _MAX_PROPOSALS_PER_RUN

    def test_missing_digest_returns_zero(self, workspace):
        """No signal_digest.json → 0 proposals, no crash."""
        # Don't create the digest file
        (workspace / "Services" / "signals" / "signal_digest.json").unlink(
            missing_ok=True
        )
        count = bridge_signals_to_ddd(str(workspace))
        assert count == 0

    def test_empty_items_returns_zero(self, workspace):
        """Empty items list → 0 proposals."""
        digest = {"items": []}
        (workspace / "Services" / "signals" / "signal_digest.json").write_text(
            json.dumps(digest)
        )
        count = bridge_signals_to_ddd(str(workspace))
        assert count == 0


class TestBridgeLearnedContent:
    """Channel 2: s_learn-content knowledge card → DDD proposals."""

    def test_relevant_tech_content_generates_proposal(self, workspace):
        """Technical content with enough keywords → TECH.md proposal."""
        result = bridge_learned_content_to_ddd(
            title="New Agent Framework Architecture Patterns",
            summary="A library release showcasing scalable API design patterns for agent orchestration",
            source_url="https://example.com/article",
            workspace_path=str(workspace),
        )
        assert result is True

        proposals_dir = workspace / "Projects" / "SwarmAI" / ".artifacts" / "proposals"
        files = list(proposals_dir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["target_doc"] == "TECH.md"

    def test_relevant_product_content_generates_proposal(self, workspace):
        """Strategic/competitive content → PRODUCT.md proposal."""
        result = bridge_learned_content_to_ddd(
            title="AI Startup Funding Market Trends 2026",
            summary="Competitor funding rounds and enterprise adoption strategy shifts",
            source_url="https://example.com/market",
            workspace_path=str(workspace),
        )
        assert result is True

        proposals_dir = workspace / "Projects" / "SwarmAI" / ".artifacts" / "proposals"
        files = list(proposals_dir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["target_doc"] == "PRODUCT.md"

    def test_irrelevant_content_skipped(self, workspace):
        """Content without DDD-relevant keywords → no proposal."""
        result = bridge_learned_content_to_ddd(
            title="Best Restaurants in Beijing",
            summary="A food guide for local dining options",
            source_url="https://food.com",
            workspace_path=str(workspace),
        )
        assert result is False

    def test_confidence_scales_with_keywords(self, workspace):
        """More keyword hits → higher confidence."""
        result = bridge_learned_content_to_ddd(
            title="Enterprise API Framework Architecture Pattern Library SDK Release",
            summary="Performance scaling security protocol migration",
            source_url="https://example.com",
            workspace_path=str(workspace),
        )
        assert result is True

        proposals_dir = workspace / "Projects" / "SwarmAI" / ".artifacts" / "proposals"
        files = list(proposals_dir.glob("*.json"))
        data = json.loads(files[0].read_text())
        # Many keywords → high confidence (capped at 0.9)
        assert data["confidence"] >= 0.7


class TestClassifySignal:
    """Test signal classification into target doc + section."""

    def test_competitor_goes_to_product(self):
        doc, section = _classify_signal("OpenAI competitor launch", "market disruption")
        assert doc == "PRODUCT.md"

    def test_framework_goes_to_tech(self):
        doc, section = _classify_signal("New framework release", "architecture pattern library")
        assert doc == "TECH.md"

    def test_ambiguous_defaults_to_product(self):
        """When both categories match equally, defaults to PRODUCT."""
        doc, _ = _classify_signal("General news", "Nothing specific")
        assert doc == "PRODUCT.md"
