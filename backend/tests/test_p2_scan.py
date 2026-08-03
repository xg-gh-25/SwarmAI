"""Tests for Pollinate P2 hero framing scan function.

Verifies that p2_scan correctly identifies first-person hero framing
while allowing legitimate design philosophy discussion.
"""

import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "skills" / "s_pollinate" / "scripts" / "p2_scan.py"


def run_scan(text: str) -> subprocess.CompletedProcess:
    """Run p2_scan.py with text on stdin, return completed process."""
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestP2ScanDetectsHeroFraming:
    """AC1: p2_scan detects hero framing phrases and returns exit 1."""

    def test_detects_chinese_hero_created(self):
        result = run_scan("我造了一个全新的AI系统")
        assert result.returncode == 1

    def test_detects_chinese_hero_built(self):
        result = run_scan("我做了一个创新性的框架")
        assert result.returncode == 1

    def test_detects_chinese_hero_we_are(self):
        result = run_scan("我们是最前沿的AI团队")
        assert result.returncode == 1

    def test_detects_chinese_hero_we_possessive_achievement(self):
        result = run_scan("我们的系统远超竞品")
        assert result.returncode == 1

    def test_detects_english_i_built(self):
        result = run_scan("I built this revolutionary system")
        assert result.returncode == 1

    def test_detects_english_we_created(self):
        result = run_scan("We created the most advanced pipeline")
        assert result.returncode == 1

    def test_detects_english_contraction(self):
        result = run_scan("I've built a cutting-edge framework")
        assert result.returncode == 1

    def test_detects_chinese_colloquial(self):
        result = run_scan("我搞了一个全新的平台")
        assert result.returncode == 1

    def test_detects_html_embedded_hero(self):
        html = '<div class="card"><p>我造了一个伟大的系统</p></div>'
        result = run_scan(html)
        assert result.returncode == 1

    def test_reports_offending_lines(self):
        text = "这是正常内容\n我造了一个系统\n更多正常内容"
        result = run_scan(text)
        assert result.returncode == 1
        assert "我造了" in result.stdout


class TestP2ScanPassesCleanText:
    """AC2: p2_scan passes design philosophy discussion (exit 0)."""

    def test_passes_design_philosophy_discussion(self):
        text = "三级硬化是设计哲学的核心。Level 2 是 pattern 的中间态。"
        result = run_scan(text)
        assert result.returncode == 0

    def test_passes_technical_description(self):
        text = "Pipeline 读 DDD → domain-correct 交付 → REFLECT 写回 lessons"
        result = run_scan(text)
        assert result.returncode == 0

    def test_passes_thesis_statement(self):
        text = "Best practice 是建议。Enforcement 是物理定律。"
        result = run_scan(text)
        assert result.returncode == 0

    def test_passes_section_header_with_we(self):
        # "我们的设计哲学" as a section TITLE is legitimate
        text = "## 我们的设计哲学\n\n三级硬化从信念到不变量。"
        result = run_scan(text)
        assert result.returncode == 0

    def test_passes_empty_input(self):
        result = run_scan("")
        assert result.returncode == 0

    def test_passes_english_design_discussion(self):
        text = "The pipeline uses DDD for judgment. Enforcement is physics."
        result = run_scan(text)
        assert result.returncode == 0
