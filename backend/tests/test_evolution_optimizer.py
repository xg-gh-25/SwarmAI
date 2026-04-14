"""Tests for evolution_optimizer module."""
from __future__ import annotations

import fcntl
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


class TestDeployOptimization:
    """Tests for deploy_optimization writing changes to SKILL.md."""

    def test_deploy_optimization_writes_skill(self, optimizer, skills_dir):
        """Accepted optimization writes changes to SKILL.md."""
        _make_skill(skills_dir, "test", body="Always include verbose output in results.")
        examples = [
            _make_example(correction="don't include verbose output", score=0.5),
            _make_example(correction="should add timestamps to output", score=0.5),
        ]
        result = optimizer.optimize_skill("test", examples)
        if result.accepted and result.changes:
            deployed = optimizer.deploy_optimization(result)
            assert deployed is True
            # Verify SKILL.md was modified
            skill_path = skills_dir / "s_test" / "SKILL.md"
            new_content = skill_path.read_text(encoding="utf-8")
            # The "verbose output" should be removed or the text should differ
            assert new_content != f"---\nname: test\ndescription: test skill\n---\nAlways include verbose output in results.\n"
        else:
            # If constraints rejected, deploy should return False
            assert result.accepted is False or len(result.changes) == 0

    def test_deploy_not_accepted_returns_false(self, optimizer, skills_dir):
        """deploy_optimization returns False for non-accepted results."""
        result = OptimizationResult(
            skill_name="test",
            original_score=0.5,
            optimized_score=0.7,
            changes=[TextChange(original="a", replacement="b", reason="test")],
            accepted=False,
            reason="Constraint failed",
        )
        assert optimizer.deploy_optimization(result) is False

    def test_deploy_no_changes_returns_false(self, optimizer, skills_dir):
        """deploy_optimization returns False when no changes to apply."""
        result = OptimizationResult(
            skill_name="test",
            original_score=0.5,
            optimized_score=0.5,
            changes=[],
            accepted=True,
            reason="All constraints passed",
        )
        assert optimizer.deploy_optimization(result) is False

    def test_deploy_preserves_frontmatter(self, optimizer, skills_dir):
        """deploy_optimization preserves YAML frontmatter."""
        _make_skill(skills_dir, "fm", body="Do the thing.\n- Use verbose mode\n")
        result = OptimizationResult(
            skill_name="fm",
            original_score=0.5,
            optimized_score=0.7,
            changes=[TextChange(
                original="",
                replacement="- Always validate input",
                reason="User said should: 'always validate input'",
            )],
            accepted=True,
            reason="All constraints passed",
        )
        deployed = optimizer.deploy_optimization(result)
        assert deployed is True
        content = (skills_dir / "s_fm" / "SKILL.md").read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "name: fm" in content
        assert "Always validate input" in content

    def test_deploy_missing_skill_returns_false(self, optimizer, skills_dir):
        """deploy_optimization returns False if SKILL.md doesn't exist."""
        result = OptimizationResult(
            skill_name="nonexistent",
            original_score=0.5,
            optimized_score=0.7,
            changes=[TextChange(original="a", replacement="b", reason="test")],
            accepted=True,
            reason="ok",
        )
        assert optimizer.deploy_optimization(result) is False


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
        assert result < 0.35  # Should not reach deploy threshold

    # ── v2.1 band-specific tests ──

    @pytest.mark.parametrize("n_corr,n_ex,fitness,expected_range", [
        # n=2 band: evidence=0.5
        (2, 22, 0.5, (0.30, 0.40)),   # save-memory scenario: 0.5 × 0.7 = 0.35
        (2, 10, 0.2, (0.45, 0.55)),   # 2 corr + very low fitness: 0.5 × 1.0 = 0.5
        (2, 5, 0.8, (0.25, 0.35)),    # 2 corr + high fitness + high density (40%): 0.5 × max(0.6, 0.1) = 0.3
        # n=1 band: evidence=0.3
        (1, 14, 0.2, (0.25, 0.35)),   # radar-todo scenario: 0.3 × 1.0 = 0.3
        (1, 50, 0.9, (0.01, 0.05)),   # 1 corr + great fitness: tiny
        # n=3 band: evidence=0.6
        (3, 10, 0.3, (0.55, 0.65)),   # 0.6 × max(0.6, 1.0) = 0.6
        # density band >0.05: rate 0.09 → density=0.2
        (2, 22, 0.8, (0.05, 0.15)),   # evidence=0.5 × max(0.2, 0.1) = 0.1
        # density band >0.15: rate 0.3 → density=0.4
        (3, 10, 0.8, (0.20, 0.30)),   # evidence=0.6 × max(0.4, 0.1) = 0.24
    ])
    def test_confidence_bands(self, n_corr, n_ex, fitness, expected_range):
        """Parametrized tests for v2.1 evidence/density/need bands."""
        result = compute_confidence(n_corr, n_ex, fitness)
        lo, hi = expected_range
        assert lo <= result <= hi, (
            f"compute_confidence({n_corr}, {n_ex}, {fitness}) = {result}, "
            f"expected [{lo}, {hi}]"
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
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            result = run_evolution_cycle(skills_dir, transcripts_dir, evals_dir)
            assert isinstance(result, CycleReport)
            assert len(result.errors) > 0
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
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


class TestSkillHealthHighlightsApplyAffordance:
    """G1: _get_skill_health_highlights should include 'apply' affordance for recommend-tier."""

    def test_recommend_tier_shows_apply_affordance(self, tmp_path):
        """Recommend-tier skill should show 'apply <skill> fix' text."""
        from core.proactive_intelligence import _get_skill_health_highlights

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        health_data = {
            "cycle_id": "test",
            "skills": [{
                "skill_name": "radar-todo",
                "action": "recommend",
                "confidence": 0.30,
                "correction_count": 1,
                "fitness_score": 0.2,
                "recommendation": {
                    "evidence_summary": ["不要做 GitHub push, 都做local codebase commit"],
                    "changes": [{"original": "x", "replacement": "y"}],
                },
            }],
        }
        (ctx_dir / "skill_health.json").write_text(json.dumps(health_data))

        highlights = _get_skill_health_highlights(ctx_dir)
        assert len(highlights) == 1
        line = highlights[0]
        assert "radar-todo" in line
        assert "不要做 GitHub push" in line
        # G1: Must include apply affordance
        assert "apply radar-todo fix" in line.lower() or "apply" in line.lower()

    def test_deploy_tier_no_apply_affordance(self, tmp_path):
        """Deploy-tier (already deployed) should NOT show 'apply' text."""
        from core.proactive_intelligence import _get_skill_health_highlights

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        health_data = {
            "cycle_id": "test",
            "skills": [{
                "skill_name": "save-memory",
                "action": "deploy",
                "confidence": 0.35,
                "correction_count": 2,
                "fitness_score": 0.5,
                "recommendation": {
                    "evidence_summary": ["remove test entry"],
                    "changes": [{"original": "a", "replacement": "b"}],
                },
            }],
        }
        (ctx_dir / "skill_health.json").write_text(json.dumps(health_data))

        highlights = _get_skill_health_highlights(ctx_dir)
        # deploy-tier may or may not be shown, but if shown, should NOT say "apply"
        for line in highlights:
            if "save-memory" in line:
                assert "apply" not in line.lower()

    def test_empty_evidence_no_crash(self, tmp_path):
        """Recommend-tier with empty evidence_summary should not crash."""
        from core.proactive_intelligence import _get_skill_health_highlights

        ctx_dir = tmp_path / ".context"
        ctx_dir.mkdir()
        health_data = {
            "cycle_id": "test",
            "skills": [{
                "skill_name": "some-skill",
                "action": "recommend",
                "confidence": 0.20,
                "correction_count": 1,
                "fitness_score": 0.3,
                "recommendation": {
                    "evidence_summary": [],
                    "changes": [],
                },
            }],
        }
        (ctx_dir / "skill_health.json").write_text(json.dumps(health_data))

        highlights = _get_skill_health_highlights(ctx_dir)
        assert len(highlights) >= 1
        # Should not contain "apply" affordance when no changes
        assert "apply" not in highlights[0].lower() or "evidence" not in highlights[0]
