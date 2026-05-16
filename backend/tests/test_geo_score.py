"""Tests for Pollinate GEO Signal Stack scorer (geo_score.py).

Verifies that the GEO scorer correctly evaluates article/narrative content
across 4 pillars: Evidence Density, Structure & Position, Authority Signals,
AI Crawlability. Scores 0-100.
"""
import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

SCRIPT_PATH = Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts" / "geo_score.py"


# ── Fixtures ────────────────────────────────────────────────────────────────


HIGH_QUALITY_ARTICLE = dedent("""\
# AI Agent Harnesses Are Dead. Long Live Agent Harnesses.

**By Xiaogang Wang** · Updated May 15, 2026

## TL;DR

Agent harnesses that wrap LLM APIs with retry logic and tool routing are commoditized.
The new moat is persistent context — memory that compounds across sessions. SwarmAI's
approach (self-sovereign memory + DDD knowledge layer) outperforms 3 alternatives by 2.7x
on recall benchmarks (n=1,552 sessions over 8 weeks).

## Key Findings

According to a 2026 Stanford HAI report, 73% of enterprise AI deployments fail
due to context loss between sessions. Dr. Sarah Chen (Stanford HAI) notes: "The
bottleneck isn't intelligence — it's continuity."

Our measurements show:
- Session resume enrichment: 50-100K tokens reconstructed in 0.46s (vs 3-5K baseline)
- Memory recall precision: 94.2% on verified claims (FTS5 + keyword hybrid)
- Evolution pipeline: 6.1% correction detection rate with confidence-gated deployment
- Context assembly: 39K tokens in 78ms (L1 cache hit)
- DDD cultivation: 4 documents × 3 maturity levels × automated health scoring

Professor James Liu (CMU) observes: "Self-sovereign memory is the only architecture
that survives model provider switches without data loss."

## Methodology

We compared 4 architectures over 8 weeks of daily use:
1. SwarmAI (self-sovereign, local-first)
2. Claude Memory (platform-managed)
3. MemPalace (embedding-heavy, 96.6% recall)
4. Raw JSONL replay (Claude Code style)

All tests used identical prompts across 1,552 sessions.

### Limitations

This study has several limitations:
- Single user (n=1) — patterns may not generalize
- Self-reported recall quality — no external annotation
- macOS-only testing environment
""")


LOW_QUALITY_ARTICLE = dedent("""\
In today's rapidly evolving landscape of artificial intelligence, many companies
are building agent frameworks. These tools are transforming how we think about
software development and are revolutionizing the industry.

Studies show that AI is becoming more important every day. Experts agree that
the future will be shaped by these technologies. Many organizations are adopting
AI solutions to improve their workflows.

The benefits are numerous and the potential is unlimited. As we move forward,
it's clear that AI will continue to play an increasingly important role in our lives.
""")


MEDIUM_QUALITY_ARTICLE = dedent("""\
# How DDD Cultivation Works

## Summary

DDD Cultivation automatically grows project knowledge from daily work.
Every pipeline run that completes REFLECT proposes updates to 4 documents.

## How It Works

The system processes sessions through 8 feed channels:
- Pipeline REFLECT output (lessons learned)
- Corrections from EVOLUTION.md
- Architecture decisions from commits
- Tool usage patterns

Each proposal goes through a confidence gate (threshold: 0.7) before auto-applying.
Below threshold proposals are escalated for human review.

The maturity model has 3 levels: Sparse → Growing → Evergreen.
""")


# ── Helper ──────────────────────────────────────────────────────────────────


def run_scorer(content: str, tmp_path: Path) -> dict:
    """Write content to tmp file and run geo_score.py, return parsed JSON."""
    content_file = tmp_path / "article.md"
    content_file.write_text(content, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(content_file), "--json"],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.fail(f"Script did not return valid JSON.\nstdout: {result.stdout}\nstderr: {result.stderr}")


# ── Tests: Score ranges ─────────────────────────────────────────────────────


class TestScoreRanges:
    """High-quality content scores high, low-quality scores low."""

    def test_high_quality_above_60(self, tmp_path):
        result = run_scorer(HIGH_QUALITY_ARTICLE, tmp_path)
        assert result["total_score"] >= 60
        assert result["pass"] is True

    def test_low_quality_below_40(self, tmp_path):
        result = run_scorer(LOW_QUALITY_ARTICLE, tmp_path)
        assert result["total_score"] < 40
        assert result["pass"] is False

    def test_medium_quality_in_range(self, tmp_path):
        result = run_scorer(MEDIUM_QUALITY_ARTICLE, tmp_path)
        assert 20 <= result["total_score"] <= 70


# ── Tests: Pillar scoring ───────────────────────────────────────────────────


class TestPillarScoring:
    """Each pillar scored independently and contributes to total."""

    def test_has_four_pillars(self, tmp_path):
        result = run_scorer(HIGH_QUALITY_ARTICLE, tmp_path)
        assert "pillars" in result
        pillars = result["pillars"]
        assert "evidence_density" in pillars
        assert "structure_position" in pillars
        assert "authority_signals" in pillars
        assert "ai_crawlability" in pillars

    def test_evidence_density_high_for_data_rich(self, tmp_path):
        result = run_scorer(HIGH_QUALITY_ARTICLE, tmp_path)
        assert result["pillars"]["evidence_density"]["score"] >= 70

    def test_evidence_density_low_for_vague(self, tmp_path):
        result = run_scorer(LOW_QUALITY_ARTICLE, tmp_path)
        assert result["pillars"]["evidence_density"]["score"] < 30

    def test_structure_detects_tldr(self, tmp_path):
        result = run_scorer(HIGH_QUALITY_ARTICLE, tmp_path)
        assert result["pillars"]["structure_position"]["score"] >= 50

    def test_authority_detects_byline(self, tmp_path):
        result = run_scorer(HIGH_QUALITY_ARTICLE, tmp_path)
        assert result["pillars"]["authority_signals"]["score"] >= 50


# ── Tests: Anti-patterns (veto conditions) ──────────────────────────────────


class TestAntiPatterns:
    """Specific anti-patterns should reduce score or trigger warnings."""

    def test_generic_opener_penalized(self, tmp_path):
        result = run_scorer(LOW_QUALITY_ARTICLE, tmp_path)
        assert any("anti_pattern" in w or "generic" in w.lower()
                   for w in result.get("warnings", []))

    def test_no_entities_penalized(self, tmp_path):
        result = run_scorer(LOW_QUALITY_ARTICLE, tmp_path)
        # Low quality article has zero named entities → score capped
        assert result["pillars"]["evidence_density"]["score"] < 30


# ── Tests: Output format ────────────────────────────────────────────────────


class TestOutputFormat:
    """Script returns well-structured JSON."""

    def test_json_structure(self, tmp_path):
        result = run_scorer(HIGH_QUALITY_ARTICLE, tmp_path)
        assert "total_score" in result
        assert "pass" in result
        assert "pillars" in result
        assert "warnings" in result
        assert isinstance(result["total_score"], (int, float))
        assert isinstance(result["pass"], bool)
        assert 0 <= result["total_score"] <= 100

    def test_exit_code_0_on_pass(self, tmp_path):
        content_file = tmp_path / "article.md"
        content_file.write_text(HIGH_QUALITY_ARTICLE, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(content_file), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0

    def test_exit_code_1_on_fail(self, tmp_path):
        content_file = tmp_path / "article.md"
        content_file.write_text(LOW_QUALITY_ARTICLE, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(content_file), "--json"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 1
