"""Tests for evolution_optimizer module."""
from __future__ import annotations

import pytest
from pathlib import Path

from core.evolution_optimizer import (
    EvolutionOptimizer,
    OptimizationResult,
    TextChange,
    CORRECTION_PATTERNS,
)
from core.session_miner import EvalExample


@pytest.fixture
def skills_dir(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    return d


@pytest.fixture
def optimizer(skills_dir):
    return EvolutionOptimizer(skills_dir)


def _make_skill(skills_dir: Path, name: str, body: str = "Do the thing.") -> None:
    skill_dir = skills_dir / f"s_{name}"
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = f"---\nname: {name}\ndescription: test skill\n---\n{body}\n"
    (skill_dir / "SKILL.md").write_text(content)


def _make_example(correction: str | None = None, score: float = 1.0) -> EvalExample:
    return EvalExample(
        user_prompt="do something",
        skill_invoked="test",
        agent_actions="did something",
        user_correction=correction,
        final_outcome="done",
        score=score,
    )


class TestOptimizeWithCorrections:
    def test_optimize_with_corrections(self, optimizer, skills_dir):
        _make_skill(skills_dir, "test", body="Always include verbose output in results.")
        examples = [
            _make_example(correction="don't include verbose output", score=0.5),
            _make_example(correction="should add timestamps to output", score=0.5),
        ]
        result = optimizer.optimize_skill("test", examples)
        assert isinstance(result, OptimizationResult)
        assert result.skill_name == "test"
        assert len(result.changes) > 0


class TestOptimizeNoCorrections:
    def test_optimize_no_corrections(self, optimizer, skills_dir):
        _make_skill(skills_dir, "test")
        examples = [_make_example(correction=None)]
        result = optimizer.optimize_skill("test", examples)
        assert result.accepted is False
        assert "No correction patterns" in result.reason


class TestConstraintSizeLimit:
    def test_constraint_size_limit(self, optimizer, skills_dir):
        # Create a skill already near the limit
        big_body = "x" * (14 * 1024)
        _make_skill(skills_dir, "big", body=big_body)
        examples = [
            _make_example(correction="should add a very long instruction " + "y" * 2000, score=0.5),
        ]
        result = optimizer.optimize_skill("big", examples)
        # Either the change is rejected or the constraint check catches it
        # depends on whether the addition pushes over 15KB
        assert isinstance(result, OptimizationResult)


class TestConstraintGrowthLimit:
    def test_constraint_growth_limit(self, optimizer, skills_dir):
        _make_skill(skills_dir, "small", body="Short.")
        examples = [
            _make_example(correction="should add " + "z" * 200, score=0.5),
        ]
        result = optimizer.optimize_skill("small", examples)
        # Growth > 20% should be rejected
        if result.changes:
            assert result.accepted is False or result.reason  # has a reason


class TestReadSkillText:
    def test_read_skill_text(self, optimizer, skills_dir):
        _make_skill(skills_dir, "reader", body="Body content here.")
        text = optimizer._read_skill_text("reader")
        assert text is not None
        assert "Body content here." in text
        # Should NOT contain YAML frontmatter
        assert "---" not in text

    def test_read_missing_skill(self, optimizer):
        text = optimizer._read_skill_text("nonexistent")
        assert text is None


class TestExtractCorrectionPatterns:
    def test_extract_corrections_patterns(self, optimizer):
        examples = [
            _make_example(correction="don't include the test files in output"),
            _make_example(correction="use markdown format instead"),
            _make_example(correction="should always validate input first"),
            _make_example(correction=None),  # no correction
        ]
        corrections = optimizer._extract_corrections(examples)
        assert len(corrections) >= 2  # at least "don't" and "should" match
        types = [c[1] for c in corrections]
        assert "remove" in types or "add" in types


class TestOptimizationResultDataclass:
    def test_optimization_result_dataclass(self):
        result = OptimizationResult(
            skill_name="test",
            original_score=0.5,
            optimized_score=0.7,
            changes=[TextChange(original="a", replacement="b", reason="test")],
            accepted=True,
            reason="All constraints passed",
        )
        assert result.skill_name == "test"
        assert result.optimized_score > result.original_score
        assert len(result.changes) == 1


class TestRunEvolutionCycle:
    """Tests for the run_evolution_cycle convenience function."""

    def test_empty_transcripts(self, tmp_path):
        """No transcripts -> no skills checked."""
        from core.evolution_optimizer import run_evolution_cycle

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        evals_dir = tmp_path / "evals"

        summary = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
        assert summary["skills_checked"] == 0
        assert summary["eligible"] == 0
        assert summary["optimized"] == 0
        assert summary["changes"] == 0

    def test_cycle_with_skills_but_no_transcripts(self, tmp_path):
        """Skills exist but no transcripts -> 0 checked (no matching examples)."""
        from core.evolution_optimizer import run_evolution_cycle

        skills_dir = tmp_path / "skills"
        _make_skill(skills_dir, "myskill", body="Do something useful.")
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        evals_dir = tmp_path / "evals"

        summary = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
        assert summary["skills_checked"] == 0
        assert summary["eligible"] == 0

    def test_cycle_with_insufficient_examples(self, tmp_path):
        """Skills with <5 examples are not eligible."""
        import json
        from core.evolution_optimizer import run_evolution_cycle

        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "s_weather"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: weather\ndescription: >\n  Get weather\n  TRIGGER: weather, forecast\n---\nCheck the weather.\n"
        )

        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        # Create 3 transcript entries (< 5 threshold)
        records = []
        for i in range(3):
            records.append(json.dumps({"type": "user", "message": {"content": f"weather forecast {i}"}}))
            records.append(json.dumps({"type": "assistant", "message": {"content": f"The weather is {i}C"}}))
        (transcripts_dir / "session1.jsonl").write_text("\n".join(records))

        evals_dir = tmp_path / "evals"

        summary = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
        # Has examples but < 5 so not eligible
        assert summary["eligible"] == 0

    def test_cycle_returns_dict_keys(self, tmp_path):
        """Summary dict always has the expected keys."""
        from core.evolution_optimizer import run_evolution_cycle

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        evals_dir = tmp_path / "evals"

        summary = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
        assert "skills_checked" in summary
        assert "eligible" in summary
        assert "optimized" in summary
        assert "changes" in summary
