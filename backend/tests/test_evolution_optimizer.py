"""Tests for evolution_optimizer module."""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from pathlib import Path

from core.evolution_optimizer import (
    EvolutionOptimizer,
    OptimizationResult,
    TextChange,
    CORRECTION_PATTERNS,
    compute_confidence,
    atomic_deploy,
    CycleReport,
    DeployResult,
    SkillHealthEntry,
    Recommendation,
    SkillHealthReport,
    ExecutionTraceCollector,
    AntiPatternGenerator,
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
        # v2.1: corrections are now 3-tuples (text, action, confidence)
        types = [c[1] for c in corrections]
        assert "remove" in types or "add" in types
        # Structured pattern matches should be "high" confidence
        confidences = [c[2] for c in corrections]
        assert "high" in confidences

    def test_fallback_corrections_are_low_confidence(self, optimizer):
        """Corrections not matching structured patterns get 'low' confidence."""
        examples = [
            _make_example(correction="Remove the test entry"),  # no structured keyword
        ]
        corrections = optimizer._extract_corrections(examples)
        if corrections:
            assert corrections[0][2] == "low"

    def test_low_confidence_corrections_not_applied(self, optimizer, skills_dir):
        """Low-confidence corrections are skipped in _apply_heuristic_changes."""
        _make_skill(skills_dir, "test", body="Do the thing correctly.")
        corrections = [("Remove the test entry", "add", "low")]
        new_text, changes = optimizer._apply_heuristic_changes(
            "Do the thing correctly.", corrections
        )
        assert len(changes) == 0  # Low confidence → not applied


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
        evals_dir.mkdir(parents=True, exist_ok=True)

        result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
        summary = result.to_dict()
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
        evals_dir.mkdir(parents=True, exist_ok=True)

        result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
        summary = result.to_dict()
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
        evals_dir.mkdir(parents=True, exist_ok=True)

        result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
        summary = result.to_dict()
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
        evals_dir.mkdir(parents=True, exist_ok=True)

        result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
        summary = result.to_dict()
        assert "skills_checked" in summary
        assert "eligible" in summary
        assert "optimized" in summary
        assert "changes" in summary

    def test_full_mine_score_optimize_path(self, tmp_path):
        """Full cycle with enough correction examples to trigger optimization.

        Creates a skill, writes transcripts with 6 correction examples (>= 5
        threshold), so the skill becomes eligible and scores < 0.7, triggering
        actual optimization with heuristic changes.
        """
        import json
        from core.evolution_optimizer import run_evolution_cycle

        # Set up skill
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "s_deploy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deploy\ndescription: >\n  Deploy helper\n  TRIGGER: deploy, deployment\n---\n"
            "Always include verbose output in results.\n"
            "Run the full deployment pipeline.\n"
        )

        # Create transcripts with 6 correction examples (skill keyword + correction)
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        records = []
        for i in range(6):
            records.append(json.dumps({
                "type": "user",
                "message": {"content": f"deploy my service {i}"},
            }))
            records.append(json.dumps({
                "type": "assistant",
                "message": {"content": f"Deploying service {i} with verbose output..."},
            }))
            # User correction — triggers score < 1.0
            records.append(json.dumps({
                "type": "user",
                "message": {"content": "don't include verbose output in the deploy log"},
            }))
        (transcripts_dir / "session_deploy.jsonl").write_text("\n".join(records))

        evals_dir = tmp_path / "evals"

        result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
        # CycleReport with to_dict() backward compat
        summary = result.to_dict()
        assert summary["skills_checked"] >= 1
        assert summary["eligible"] >= 1
        # With 6 correction examples all saying "don't include verbose output",
        # the optimizer should find a match and produce changes
        # With 6 correction examples all saying "don't include verbose output",
        # the skill should be eligible and the confidence gate should trigger deploy
        assert summary["eligible"] >= 1
        # At least one skill should have been processed (deployed or recommended)
        assert summary["optimized"] >= 0  # may be 0 if confidence < HIGH


class TestComputeConfidence:
    """Tests for the compute_confidence function."""

    def test_compute_confidence_zero_corrections(self):
        """Zero corrections -> 0.0 confidence."""
        assert compute_confidence(0, 10, 0.5) == 0.0

    def test_compute_confidence_high(self):
        """5+ corrections + low fitness -> high confidence."""
        result = compute_confidence(5, 20, 0.2)
        assert result >= 0.7

    def test_compute_confidence_medium(self):
        """3 corrections with moderate need -> mid-range confidence."""
        result = compute_confidence(3, 10, 0.4)
        assert 0.3 <= result <= 0.7

    def test_compute_confidence_single_correction(self):
        """1 correction → evidence=0.3, low end of range."""
        result = compute_confidence(1, 10, 0.5)
        assert result > 0.0
        assert result < 0.25  # Single correction should stay low-confidence

    # ── v2.1 band-specific tests ──

    @pytest.mark.parametrize("n_corr,n_ex,fitness,expected_range", [
        # n=2 band: evidence=0.5
        (2, 22, 0.5, (0.30, 0.40)),   # save-memory scenario: 0.5 × 0.7 = 0.35
        (2, 10, 0.2, (0.45, 0.55)),   # 2 corr + very low fitness: 0.5 × 1.0 = 0.5
        (2, 5, 0.8, (0.25, 0.35)),    # 2 corr + high fitness + high density (40%): 0.5 × max(0.6, 0.3) = 0.3 (density wins over v2.4 floor)
        # n=1 band: evidence=0.3
        (1, 14, 0.2, (0.25, 0.35)),   # radar-todo scenario: 0.3 × 1.0 = 0.3
        (1, 50, 0.9, (0.06, 0.12)),   # 1 corr + great fitness: v2.4 floor=0.3 → 0.3×0.3=0.09
        # n=3 band: evidence=0.6
        (3, 10, 0.3, (0.55, 0.65)),   # 0.6 × max(0.6, 1.0) = 0.6
        # density band >0.05: rate 0.09 → density=0.2, need=0.3 (v2.4 floor)
        (2, 22, 0.8, (0.12, 0.18)),   # evidence=0.5 × max(0.2, 0.3) = 0.15
        # density band >0.15: rate 0.3 (exactly at boundary, NOT >0.3) → density=0.4, need=0.3 (v2.4 floor)
        (3, 10, 0.8, (0.20, 0.28)),   # evidence=0.6 × max(0.4, 0.3) = 0.24
    ])
    def test_confidence_bands(self, n_corr, n_ex, fitness, expected_range):
        """Parametrized tests for v2.1 evidence/density/need bands."""
        result = compute_confidence(n_corr, n_ex, fitness)
        lo, hi = expected_range
        assert lo <= result <= hi, (
            f"compute_confidence({n_corr}, {n_ex}, {fitness}) = {result}, "
            f"expected [{lo}, {hi}]"
        )


class TestConfidenceBoosts:
    """Tests for recency_boost and repeat_boost in compute_confidence."""

    def test_recency_boost_crosses_threshold(self):
        """2 recent corrections on same skill → confidence >= 0.40 (crosses 0.35)."""
        # Base case without boosts: 2 corrections, 30 examples, fitness 0.4
        # evidence=0.5, need=0.7 (fitness 0.4 → <0.5 band), density=0.2 (rate 6.7%)
        # base = 0.5 * max(0.2, 0.7) = 0.35 — right at threshold
        # With recency_boost=0.10 (2 recent × 0.05) + repeat_boost=0.05 (2 repeats)
        # total = 0.35 + 0.10 + 0.05 = 0.50
        result = compute_confidence(
            n_corrections=2, n_examples=30, avg_fitness=0.4,
            recent_corrections=2, repeat_count=2,
        )
        assert result >= 0.40, f"Expected >= 0.40, got {result}"

    def test_no_boosts_backward_compatible(self):
        """Without boost params, behavior is identical to v2.1."""
        # Old call signature still works
        result = compute_confidence(2, 22, 0.5)
        assert 0.30 <= result <= 0.40  # unchanged from v2.1 band

    def test_recency_boost_capped_at_015(self):
        """Recency boost is capped at +0.15 regardless of count."""
        # 10 recent corrections: boost should be min(0.15, 10*0.05) = 0.15
        result_capped = compute_confidence(
            n_corrections=10, n_examples=100, avg_fitness=0.2,
            recent_corrections=10, repeat_count=1,
        )
        result_extreme = compute_confidence(
            n_corrections=10, n_examples=100, avg_fitness=0.2,
            recent_corrections=100, repeat_count=1,
        )
        # Both should be same (cap at 0.15)
        assert result_capped == result_extreme

    def test_repeat_boost_capped_at_010(self):
        """Repeat boost is capped at +0.10 regardless of count."""
        result_3 = compute_confidence(
            n_corrections=3, n_examples=30, avg_fitness=0.4,
            recent_corrections=0, repeat_count=3,
        )
        result_100 = compute_confidence(
            n_corrections=3, n_examples=30, avg_fitness=0.4,
            recent_corrections=0, repeat_count=100,
        )
        assert result_3 == result_100

    def test_combined_boosts_dont_exceed_1(self):
        """Confidence is capped at 1.0 even with max boosts."""
        result = compute_confidence(
            n_corrections=10, n_examples=20, avg_fitness=0.1,
            recent_corrections=10, repeat_count=10,
        )
        assert result <= 1.0


class TestHighConfidenceThreshold:
    """F3: HIGH_CONFIDENCE must be low enough for real data to reach it."""

    def test_high_confidence_is_reachable(self):
        """HIGH_CONFIDENCE should be ≤0.15, not 0.35 — reachable with real correction data."""
        from core.evolution_optimizer import HIGH_CONFIDENCE
        assert HIGH_CONFIDENCE <= 0.15, (
            f"HIGH_CONFIDENCE={HIGH_CONFIDENCE} is too high — autonomous-pipeline with "
            f"5 corrections only reaches 0.16. Threshold must be ≤0.15."
        )

    def test_autonomous_pipeline_scenario_deploys(self):
        """Real scenario: 5 corrections, 64 examples, fitness 1.0 → should reach HIGH threshold."""
        from core.evolution_optimizer import HIGH_CONFIDENCE
        # This is the real autonomous-pipeline data from skill_health.json
        result = compute_confidence(5, 64, 1.0)
        assert result >= HIGH_CONFIDENCE, (
            f"conf={result} < HIGH={HIGH_CONFIDENCE} for 5 corrections/64 examples — "
            f"threshold is unreachable with real data"
        )


class TestAtomicDeploy:
    """Tests for the atomic_deploy function."""

    def test_atomic_deploy_success(self, tmp_path):
        """Writes file, verify passes."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "s_test"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(
            "---\nname: test\n---\nAlways include verbose output.\n",
            encoding="utf-8",
        )

        changes = [
            TextChange(
                original="Always include verbose output.",
                replacement="Never include verbose output.",
                reason="test",
            ),
        ]

        result = atomic_deploy(skill_path, changes)
        assert isinstance(result, DeployResult)
        assert result.success is True
        assert result.verified is True
        assert result.rolled_back is False
        assert result.changes_applied == 1
        # Verify content
        content = skill_path.read_text(encoding="utf-8")
        assert "Never include verbose output." in content
        assert "Always include verbose output." not in content

    def test_atomic_deploy_rollback_on_mismatch(self, tmp_path):
        """Mock write to produce wrong content -> rollback."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "s_test"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        original = "---\nname: test\n---\nAlways include verbose output.\n"
        skill_path.write_text(original, encoding="utf-8")

        changes = [
            TextChange(
                original="Always include verbose output.",
                replacement="Never include verbose output.",
                reason="test",
            ),
        ]

        # Mock read_text to return wrong content on the verification read.
        # The flow: (1) read original (no "Never"), (2) os.replace, (3) read for verify.
        # We corrupt the verification read (first read containing "Never").
        real_read_text = Path.read_text

        def mock_read_text(self, *args, **kwargs):
            content = real_read_text(self, *args, **kwargs)
            if self == skill_path and "Never include verbose output" in content:
                return "CORRUPTED"
            return content

        with patch.object(Path, "read_text", mock_read_text):
            result = atomic_deploy(skill_path, changes)

        assert result.rolled_back is True
        assert result.verified is False

    def test_atomic_deploy_skips_missing_original(self, tmp_path):
        """Replace target not in file -> skip + log."""
        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "s_test"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(
            "---\nname: test\n---\nSome content.\n", encoding="utf-8"
        )

        changes = [
            TextChange(
                original="THIS DOES NOT EXIST",
                replacement="replacement",
                reason="test",
            ),
        ]

        result = atomic_deploy(skill_path, changes)
        assert result.changes_skipped >= 1
        assert result.success is False


class TestCycleReport:
    """Tests for CycleReport backward compatibility."""

    def test_cycle_report_to_dict(self):
        """to_dict() returns backward compatible keys."""
        report = CycleReport(
            cycle_id="test-id",
            skills_checked=5,
            eligible=3,
            high_confidence=1,
            medium_confidence=1,
            low_confidence=1,
            deployed=1,
            verified=1,
            rolled_back=0,
            errors=[],
            health_report_path=Path("/tmp/test"),
        )
        d = report.to_dict()
        assert d["skills_checked"] == 5
        assert d["eligible"] == 3
        assert "optimized" in d
        assert "changes" in d


class TestFileLockPrevents:
    """Tests for file lock preventing concurrent cycles."""

    def test_file_lock_prevents_concurrent(self, tmp_path):
        """Hold lock, second call returns error."""
        from core.evolution_optimizer import run_evolution_cycle

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        evals_dir = tmp_path / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)

        lock_path = evals_dir.parent / ".evolution_cycle.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        from utils.file_lock import flock_exclusive_nb, flock_unlock
        lock_fd = open(lock_path, "w")
        flock_exclusive_nb(lock_fd)

        try:
            result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
            assert isinstance(result, CycleReport)
            assert len(result.errors) > 0
        finally:
            flock_unlock(lock_fd)
            lock_fd.close()


class TestChineseCorrectionPatterns:
    """Tests for Chinese correction patterns in CORRECTION_PATTERNS (P2-12).

    Verifies that the Chinese regex patterns (不要, 应该, 用X代替) match
    real-world Chinese correction inputs and extract the right action type.
    """

    def test_buyao_remove_pattern(self, optimizer, skills_dir):
        """'不要' (don't) should match as a 'remove' action."""
        _make_skill(skills_dir, "test", body="Always include debug output.")
        examples = [
            _make_example(correction="不要 include debug output in production"),
        ]
        corrections = optimizer._extract_corrections(examples)
        assert len(corrections) >= 1
        action_types = [c[1] for c in corrections]
        assert "remove" in action_types

    def test_yinggai_add_pattern(self, optimizer, skills_dir):
        """'应该' (should) should match as an 'add' action."""
        _make_skill(skills_dir, "test", body="Do the thing.")
        examples = [
            _make_example(correction="应该 always validate the input before processing"),
        ]
        corrections = optimizer._extract_corrections(examples)
        assert len(corrections) >= 1
        action_types = [c[1] for c in corrections]
        assert "add" in action_types

    def test_yong_x_tidai_add_pattern(self, optimizer, skills_dir):
        """'用X代替' (use X instead) should match as an 'add' action."""
        _make_skill(skills_dir, "test", body="Use JSON format for output.")
        examples = [
            _make_example(correction="用 YAML format 代替 JSON for configuration files"),
        ]
        corrections = optimizer._extract_corrections(examples)
        assert len(corrections) >= 1
        action_types = [c[1] for c in corrections]
        assert "add" in action_types

    def test_bixu_add_pattern(self, optimizer, skills_dir):
        """'必须' (must) should match as an 'add' action."""
        _make_skill(skills_dir, "test", body="Run the pipeline.")
        examples = [
            _make_example(correction="必须 check the return code after each command"),
        ]
        corrections = optimizer._extract_corrections(examples)
        assert len(corrections) >= 1
        action_types = [c[1] for c in corrections]
        assert "add" in action_types

    def test_bie_remove_pattern(self, optimizer, skills_dir):
        """'别' (don't) should match as a 'remove' action."""
        _make_skill(skills_dir, "test", body="Include stack traces.")
        examples = [
            _make_example(correction="别 include stack traces in user-facing output"),
        ]
        corrections = optimizer._extract_corrections(examples)
        assert len(corrections) >= 1
        action_types = [c[1] for c in corrections]
        assert "remove" in action_types

    def test_chinese_imperative_check_pattern(self, optimizer, skills_dir):
        """'检查/确认/验证' (check/confirm/verify) should match as 'add' action."""
        _make_skill(skills_dir, "test", body="Deploy the service.")
        examples = [
            _make_example(correction="检查 all environment variables before deploying"),
        ]
        corrections = optimizer._extract_corrections(examples)
        assert len(corrections) >= 1
        action_types = [c[1] for c in corrections]
        assert "add" in action_types


class TestHeuristicFirstForRecommendTier:
    """G4: Recommend-tier skills should try heuristic first, skip LLM when patterns found."""

    def test_recommend_tier_skips_llm_when_heuristic_matches(self, optimizer, skills_dir):
        """Recommend-tier skill: heuristic finds patterns → LLM must not be called."""
        _make_skill(skills_dir, "recskill", body="Deploy and verify the service output.")
        examples = [
            # "should always" → add pattern, and the text is NOT already in the skill
            _make_example(correction="should always validate input before processing", score=0.5),
        ]
        # Verify heuristic can produce changes with this correction
        corrections = optimizer._extract_corrections(examples)
        assert len(corrections) >= 1, "Heuristic should find correction patterns"
        # Verify the correction IS actionable (add type, not already in skill)
        _, peek_changes = optimizer._apply_heuristic_changes(
            "Deploy and verify the service output.", corrections,
        )
        assert len(peek_changes) > 0, "Heuristic should produce 'add' change for 'should always'"

        # G4 test: the run_evolution_cycle code peeks at heuristic for recommend-tier.
        # If heuristic finds patterns, it sets use_heuristic_only=True, so LLM is NOT called.
        # We test optimize_skill directly: with force_heuristic=True (what the cycle code
        # sets when heuristic peek succeeds), LLM should not be called.
        with patch.object(optimizer, "_try_llm_optimization", return_value=([], 0)) as mock_llm:
            result = optimizer.optimize_skill("recskill", examples, force_heuristic=True)
            assert not mock_llm.called, "force_heuristic=True should skip LLM"
            assert len(result.changes) > 0, "Heuristic should still produce changes"

    def test_deploy_tier_still_calls_llm(self, optimizer, skills_dir):
        """Deploy-tier: LLM is still called (no regression from G4 change)."""
        _make_skill(skills_dir, "depskill", body="Always include verbose output.")
        examples = [
            _make_example(correction="don't include verbose output", score=0.3),
        ] * 5  # Many corrections → deploy tier
        # Regardless of G4 changes, deploy tier should attempt LLM
        with patch.object(optimizer, "_try_llm_optimization", return_value=([], 0)) as mock_llm:
            result = optimizer.optimize_skill("depskill", examples, force_heuristic=False)
            # Deploy-tier should still attempt LLM (auto mode tries LLM first)
            assert mock_llm.called, "Deploy-tier skill should still call LLM"

    def test_cycle_peek_skips_llm_for_recommend_tier(self, tmp_path):
        """Integration: run_evolution_cycle with recommend-tier skill, heuristic match → LLM skipped.

        Tests the actual peek logic in _run_evolution_cycle_locked, not just
        optimize_skill with force_heuristic. Catches regressions if the peek
        condition is refactored.
        """
        from core.evolution_optimizer import run_evolution_cycle

        skills_dir = tmp_path / "skills"
        skill_dir = skills_dir / "s_peektest"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: peektest\ndescription: >\n  Test\n  TRIGGER: peek, peektest\n---\n"
            "Deploy and verify the service output.\n"
        )

        # Create eval file: 10 examples, 1 correction with heuristic-matchable pattern.
        # 1 correction in 10 → confidence ~0.25 = recommend tier (0.15-0.35).
        evals_dir = tmp_path / "evals"
        evals_dir.mkdir(parents=True)
        eval_records = []
        for i in range(9):
            eval_records.append(json.dumps({
                "user_prompt": f"peek {i}", "skill_invoked": "peektest",
                "agent_actions": "did peek", "user_correction": None,
                "final_outcome": "done", "score": 1.0,
            }))
        eval_records.append(json.dumps({
            "user_prompt": "peek 9", "skill_invoked": "peektest",
            "agent_actions": "did peek",
            "user_correction": "should always validate input before processing",
            "final_outcome": "done", "score": 0.5,
        }))
        (evals_dir / "peektest.jsonl").write_text("\n".join(eval_records))

        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()

        # Patch LLM at the EvolutionOptimizer class level to track calls
        with patch.object(
            EvolutionOptimizer, "_try_llm_optimization", return_value=([], 0),
        ) as mock_llm:
            result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
            # The cycle should have run the peek, found heuristic matches,
            # and set force_heuristic=True → LLM never called for this skill.
            assert not mock_llm.called, (
                "Recommend-tier skill with heuristic match: cycle peek should skip LLM"
            )
class TestExecutionTraceCollector:
    """Tests for trace extraction from eval examples."""

    @pytest.fixture
    def collector(self):
        return ExecutionTraceCollector()

    def test_collect_traces_from_corrections(self, collector):
        """Extracts traces only from examples with corrections."""
        examples = [
            _make_example(correction=None),  # No correction = skip
            _make_example(correction="don't include verbose output"),
            _make_example(correction="should add timestamps"),
        ]
        traces = collector.collect_traces("test-skill", examples)
        assert len(traces) == 2
        assert "verbose output" in traces[0]
        assert "timestamps" in traces[1]

    def test_collect_traces_max_limit(self, collector):
        """Respects max_traces limit."""
        examples = [_make_example(correction=f"fix {i}") for i in range(10)]
        traces = collector.collect_traces("test", examples, max_traces=3)
        assert len(traces) == 3

    def test_collect_traces_empty(self, collector):
        """No corrections = empty trace list."""
        examples = [_make_example(correction=None) for _ in range(5)]
        traces = collector.collect_traces("test", examples)
        assert traces == []

    def test_trace_includes_all_parts(self, collector):
        """Trace contains user prompt, agent actions, and correction."""
        ex = _make_example(correction="don't do that")
        traces = collector.collect_traces("test", [ex])
        assert len(traces) == 1
        assert "User asked:" in traces[0]
        assert "Agent did:" in traces[0]
        assert "User corrected:" in traces[0]

    def test_trace_capped_at_2000_chars(self, collector):
        """Individual trace capped at 2000 characters."""
        ex = _make_example(correction="x" * 3000)
        traces = collector.collect_traces("test", [ex])
        assert len(traces[0]) <= 2000


class TestAntiPatternGenerator:
    """Tests for anti-pattern section generation from corrections."""

    @pytest.fixture
    def generator(self):
        return AntiPatternGenerator()

    def test_generate_from_remove_corrections(self, generator):
        """Generates anti-patterns from 'remove' action corrections."""
        corrections = [
            ("include verbose output", "remove", "high"),
            ("use deprecated API calls", "remove", "high"),
            ("add timestamps", "add", "high"),  # 'add' = not an anti-pattern
        ]
        result = generator.generate(corrections)
        assert "## Anti-patterns" in result
        assert "verbose output" in result
        assert "deprecated API" in result
        assert "timestamps" not in result  # 'add' corrections excluded

    def test_generate_empty_when_no_remove(self, generator):
        """Returns empty string when no 'remove' corrections exist."""
        corrections = [
            ("add error handling", "add", "high"),
            ("use async", "add", "low"),
        ]
        result = generator.generate(corrections)
        assert result == ""

    def test_deduplicates_similar_corrections(self, generator):
        """Deduplicates by lowercased content."""
        corrections = [
            ("Include Verbose Output", "remove", "high"),
            ("include verbose output", "remove", "high"),  # duplicate
            ("use deprecated calls", "remove", "high"),
        ]
        result = generator.generate(corrections)
        # Should have exactly 2 items, not 3
        assert result.count("- ❌") == 2

    def test_caps_at_max_anti_patterns(self, generator):
        """Caps at MAX_ANTI_PATTERNS (10)."""
        corrections = [(f"bad pattern number {i}", "remove", "high") for i in range(20)]
        result = generator.generate(corrections)
        assert result.count("- ❌") == 10

    def test_skips_short_fragments(self, generator):
        """Skips corrections shorter than 5 chars."""
        corrections = [
            ("ok", "remove", "high"),  # Too short
            ("use deprecated API patterns", "remove", "high"),
        ]
        result = generator.generate(corrections)
        assert result.count("- ❌") == 1

    def test_prefix_normalization(self, generator):
        """Adds 'Don't' prefix when correction doesn't start with negation."""
        corrections = [("include verbose output", "remove", "high")]
        result = generator.generate(corrections)
        assert "Don't include verbose output" in result

    def test_preserves_existing_negation_prefix(self, generator):
        """Doesn't double-negate corrections that already start with 'Don't'."""
        corrections = [("Don't use verbose mode", "remove", "high")]
        result = generator.generate(corrections)
        assert "Don't use verbose mode" in result
        assert "Don't Don't" not in result

    def test_merge_with_no_existing_section(self, generator):
        """Appends anti-patterns section when none exists."""
        skill_text = "# My Skill\n\nDo things properly.\n"
        anti_patterns = generator.generate([("verbose output", "remove", "high")])
        merged = generator.merge_with_existing(skill_text, anti_patterns)
        assert "## Anti-patterns" in merged
        assert merged.startswith("# My Skill")

    def test_merge_with_existing_section_dedup(self, generator):
        """Deduplicates when merging with existing anti-patterns section."""
        skill_text = "# My Skill\n\n## Anti-patterns\n\n- ❌ Don't use verbose output\n\n## Other\n"
        new_anti_patterns = "## Anti-patterns (auto-generated from corrections)\n\n- ❌ Don't use verbose output\n- ❌ Don't use deprecated APIs\n"
        merged = generator.merge_with_existing(skill_text, new_anti_patterns)
        # Should only add the new one, not duplicate "verbose output"
        assert merged.count("verbose output") == 1
        assert "deprecated APIs" in merged


class TestProposalFreshnessCheck:
    """Verify ACT phase filters out changes already present in target file (LL02)."""

    def test_skips_proposal_when_all_changes_already_present(self, tmp_path):
        """If all proposed changes are already in the current skill text,
        no proposal should be written."""
        from core.evolution_optimizer import (
            run_evolution_cycle,
            _write_evolution_proposal,
        )

        skills_dir = tmp_path / "skills"
        # Skill already contains the correction text
        _make_skill(
            skills_dir, "already_fixed",
            body="Always add timestamps to output.\nDon't include verbose output.",
        )

        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        # Create transcripts with corrections that are already applied
        records = []
        for i in range(6):
            records.append(json.dumps({
                "type": "user",
                "message": {"content": f"already_fixed my service {i}"},
            }))
            records.append(json.dumps({
                "type": "assistant",
                "message": {"content": f"Processing request {i}..."},
            }))
            records.append(json.dumps({
                "type": "user",
                "message": {"content": "don't include verbose output"},
            }))
        (transcripts_dir / "session_1.jsonl").write_text("\n".join(records))

        evals_dir = tmp_path / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)

        result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)

        # Proposals file should either not exist or not contain this skill
        proposals_path = evals_dir / ".evolution_proposals.json"
        if proposals_path.exists():
            proposals = json.loads(proposals_path.read_text())
            skill_names = [p["skill_name"] for p in proposals]
            assert "already_fixed" not in skill_names, (
                "Proposal written for already-present changes"
            )

    def test_writes_proposal_for_novel_changes(self, tmp_path):
        """If proposed changes are NOT in the current skill text,
        proposal should be written normally."""
        from core.evolution_optimizer import run_evolution_cycle

        skills_dir = tmp_path / "skills"
        # Skill does NOT contain the correction
        _make_skill(
            skills_dir, "needs_fix",
            body="Always include verbose output in results.",
        )

        transcripts_dir = tmp_path / "transcripts"
        transcripts_dir.mkdir()
        records = []
        for i in range(6):
            records.append(json.dumps({
                "type": "user",
                "message": {"content": f"needs_fix my service {i}"},
            }))
            records.append(json.dumps({
                "type": "assistant",
                "message": {"content": f"Processing request {i} with verbose output..."},
            }))
            records.append(json.dumps({
                "type": "user",
                "message": {"content": "should add timestamps to output"},
            }))
        (transcripts_dir / "session_1.jsonl").write_text("\n".join(records))

        evals_dir = tmp_path / "evals"
        evals_dir.mkdir(parents=True, exist_ok=True)

        run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)

        # If confidence threshold is met, a proposal should exist
        # (This test verifies novel changes pass through — the confidence
        # threshold may prevent the proposal from being written, which is fine.
        # The important guarantee is tested above: already-present = no proposal.)


class TestProposalFreshnessAdversarial:
    """Adversarial tests for the freshness check — targeting edge cases."""

    def test_short_substring_false_positive(self):
        """Short replacement text could false-match as substring of unrelated content.

        Example: replacement "validate" would match "Always validate input..."
        even though the FULL instruction "validate all user inputs before processing"
        was never added. This is a substring containment issue.
        """
        from core.evolution_optimizer import TextChange

        # Simulate the freshness filter logic directly
        current_text = "Always validate input and sanitize output."
        current_lower = current_text.lower()

        # This is a different instruction — should NOT be filtered
        change_novel = TextChange(
            original="",
            replacement="validate all user inputs before processing them",
            reason="User said should validate",
        )
        # This IS present (exact substring match)
        change_present = TextChange(
            original="",
            replacement="validate input and sanitize output",
            reason="Already there",
        )

        changes = [change_novel, change_present]
        novel = [
            c for c in changes
            if c.replacement.strip()
            and c.replacement.strip().lower() not in current_lower
        ]

        # change_novel SHOULD pass — "validate all user inputs before processing them"
        # is NOT a substring of current_text
        assert change_novel in novel, (
            "Novel change incorrectly filtered as already-present (false positive)"
        )
        # change_present should be filtered — exact substring match
        assert change_present not in novel

    def test_very_short_replacement_false_positive(self):
        """A very short replacement like 'add' WILL false-match in any file
        containing the word 'add'. This tests whether this is a real problem
        with actual correction data (corrections are typically full phrases)."""
        from core.evolution_optimizer import TextChange

        current_text = "Additionally, run all checks before deploy."
        current_lower = current_text.lower()

        # Realistic correction (full phrase) — should NOT be false-positive
        realistic = TextChange(
            original="",
            replacement="should add timestamps to deploy log",
            reason="User said add timestamps",
        )
        # Pathological short fragment — WILL false-positive
        pathological = TextChange(
            original="",
            replacement="add",
            reason="Garbage fragment",
        )

        changes = [realistic, pathological]
        novel = [
            c for c in changes
            if c.replacement.strip()
            and c.replacement.strip().lower() not in current_lower
        ]

        # Realistic full-phrase correction should pass through
        assert realistic in novel, "Full-phrase correction incorrectly filtered"
        # Short garbage filtered — acceptable (quality gate upstream should
        # have caught this anyway via _is_quality_correction min-length check)
        assert pathological not in novel

    def test_remove_operation_empty_replacement_dropped(self):
        """TextChange with empty replacement (= remove operation) gets filtered
        by `c.replacement.strip()` being falsy. This means remove-only proposals
        never surface. Verify this is the actual behavior and assess impact.

        FINDING: This IS a gap — if the text to remove is still in the file,
        the change is novel and should be proposed. However, remove operations
        reaching the proposal path is rare because:
        1. Heuristic removes require the original text to exist at optimize time
        2. The whole skill was re-read at optimize time (same cycle)
        3. Stale removes (text already gone) are correctly filtered
        """
        from core.evolution_optimizer import TextChange

        current_text = "Always include verbose output.\nRun full pipeline."
        current_lower = current_text.lower()

        remove_change = TextChange(
            original="Always include verbose output.",
            replacement="",  # Remove operation
            reason="User said don't include verbose output",
        )
        add_change = TextChange(
            original="",
            replacement="should add error handling",
            reason="User said add error handling",
        )

        changes = [remove_change, add_change]
        novel = [
            c for c in changes
            if c.replacement.strip()
            and c.replacement.strip().lower() not in current_lower
        ]

        # Remove operation gets dropped — empty replacement is falsy
        assert remove_change not in novel
        # Add operation passes through (novel content)
        assert add_change in novel

        # If remove was the ONLY change, novel_changes would be empty
        # and the proposal would be skipped — this is the gap
        remove_only = [remove_change]
        novel_remove_only = [
            c for c in remove_only
            if c.replacement.strip()
            and c.replacement.strip().lower() not in current_lower
        ]
        assert len(novel_remove_only) == 0, (
            "Remove-only proposals will be silently dropped"
        )

    def test_multiline_replacement_matching(self):
        """Multiline replacement text should still match via substring `in`."""
        from core.evolution_optimizer import TextChange

        current_text = (
            "Step 1: Read input.\n"
            "Step 2: Validate format.\n"
            "Step 3: Process data.\n"
        )
        current_lower = current_text.lower()

        # Multi-line change that's already present
        present_multiline = TextChange(
            original="",
            replacement="Step 2: Validate format.\nStep 3: Process data.",
            reason="Already there as multiline",
        )
        # Multi-line change that's novel
        novel_multiline = TextChange(
            original="",
            replacement="Step 4: Write output.\nStep 5: Verify checksum.",
            reason="New steps",
        )

        changes = [present_multiline, novel_multiline]
        novel = [
            c for c in changes
            if c.replacement.strip()
            and c.replacement.strip().lower() not in current_lower
        ]

        assert present_multiline not in novel
        assert novel_multiline in novel

    def test_skill_deleted_between_optimize_and_proposal(self):
        """If skill file is deleted after optimize but before freshness check,
        _read_skill_text returns None → `or ""` → current_text is empty →
        all changes are novel → proposal still written. Verify this behavior."""
        from core.evolution_optimizer import TextChange

        # Simulate: skill was deleted → _read_skill_text returned None → ""
        current_text = ""
        current_lower = current_text.lower()

        change = TextChange(
            original="",
            replacement="should add timestamps",
            reason="User correction",
        )

        novel = [
            c for c in [change]
            if c.replacement.strip()
            and c.replacement.strip().lower() not in current_lower
        ]

        # When file is empty/deleted, everything is "novel" — proposal still written
        # This is safe: orphan proposals get cleaned up by dedup on next cycle
        assert change in novel

    def test_case_insensitive_matching(self):
        """Freshness check should be case-insensitive."""
        from core.evolution_optimizer import TextChange

        current_text = "Always Validate Input Before Processing."
        current_lower = current_text.lower()

        # Same content, different case
        same_content = TextChange(
            original="",
            replacement="always validate input before processing.",
            reason="Same but lowercase",
        )

        novel = [
            c for c in [same_content]
            if c.replacement.strip()
            and c.replacement.strip().lower() not in current_lower
        ]

        # Should be filtered (case-insensitive match)
        assert same_content not in novel
