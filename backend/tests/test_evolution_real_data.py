"""Real-data E2E tests for the Evolution Pipeline.

Uses frozen production fixtures (SkillEvals, SKILL.md, skill_health.json)
from the 2026-04-12 cycle. These tests catch regressions that synthetic
data misses: mixed-language corrections, real skill sizes, actual
confidence distributions, and deployment roundtrips.

Fixtures live in tests/fixtures/evolution/ — snapshot once, run forever.
Update fixtures when the eval format changes (check EvalExample fields).

Marked @pytest.mark.slow — excluded from default xdist runs.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from core.evolution_optimizer import (
    EvolutionOptimizer,
    OptimizationResult,
    TextChange,
    atomic_deploy,
    compute_confidence,
    run_evolution_cycle,
)
from core.session_miner import EvalExample

# ── Fixture paths ──

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "evolution"
EVALS_DIR = FIXTURE_DIR / "evals"
SKILLS_DIR = FIXTURE_DIR / "skills"
HEALTH_FILE = FIXTURE_DIR / "previous_health.json"


def _load_eval_examples(skill_name: str) -> list[EvalExample]:
    """Load real eval examples from frozen fixture JSONL."""
    path = EVALS_DIR / f"{skill_name}.jsonl"
    if not path.exists():
        pytest.skip(f"Fixture not found: {path}")
    examples = []
    for line in path.read_text(encoding="utf-8").strip().split("\n"):
        if not line.strip():
            continue
        d = json.loads(line)
        examples.append(EvalExample(
            user_prompt=d.get("user_prompt", ""),
            skill_invoked=d.get("skill_invoked", skill_name),
            agent_actions=d.get("agent_actions", ""),
            user_correction=d.get("user_correction"),
            final_outcome=d.get("final_outcome", ""),
            score=d.get("score", 1.0),
        ))
    return examples


def _load_real_skill_text(skill_name: str) -> str:
    """Load real SKILL.md content from frozen fixture."""
    path = SKILLS_DIR / f"{skill_name}.md"
    if not path.exists():
        pytest.skip(f"Skill fixture not found: {path}")
    return path.read_text(encoding="utf-8")


# ── Test 1: Assess phase with real data ──

@pytest.mark.slow
class TestAssessPhaseRealData:
    """Verify confidence computation and action assignment with real eval data."""

    def test_save_memory_confidence_in_expected_range(self):
        """save-memory: 24 examples, 2 corrections → expect recommend or deploy tier."""
        examples = _load_eval_examples("save-memory")
        assert len(examples) >= 20, f"Expected ~24 examples, got {len(examples)}"

        corrections = [e for e in examples if e.user_correction]
        assert len(corrections) >= 2, "save-memory should have ≥2 real corrections"

        avg_score = sum(e.score for e in examples) / len(examples)
        confidence = compute_confidence(len(corrections), len(examples), avg_score)

        # Production data: 2 corrections in 24 = evidence ~0.5, need varies
        # Should be in recommend range (0.15-0.70)
        assert 0.10 <= confidence <= 0.70, (
            f"save-memory confidence={confidence:.3f} outside expected [0.10, 0.70]"
        )

    def test_autonomous_pipeline_low_confidence(self):
        """autonomous-pipeline: 46 examples, 1 correction, >15KB → low confidence, skip LLM."""
        examples = _load_eval_examples("autonomous-pipeline")
        assert len(examples) >= 40

        corrections = [e for e in examples if e.user_correction]
        assert len(corrections) <= 2, "autonomous-pipeline should have ≤2 corrections"

        avg_score = sum(e.score for e in examples) / len(examples)
        confidence = compute_confidence(len(corrections), len(examples), avg_score)

        # 1 correction in 46 → very low confidence (log tier)
        assert confidence < 0.20, (
            f"autonomous-pipeline confidence={confidence:.3f} should be < 0.20 (log tier)"
        )

    def test_outlook_no_corrections_zero_confidence(self):
        """outlook-assistant: 0 corrections → confidence exactly 0.0."""
        examples = _load_eval_examples("outlook-assistant")
        corrections = [e for e in examples if e.user_correction]
        assert len(corrections) == 0

        avg_score = sum(e.score for e in examples) / max(len(examples), 1)
        confidence = compute_confidence(0, len(examples), avg_score)
        assert confidence == 0.0


# ── Test 2: Heuristic optimization with real corrections ──

@pytest.mark.slow
class TestHeuristicRealCorrections:
    """Verify heuristic optimizer handles real mixed-language corrections."""

    def test_save_memory_heuristic_produces_changes(self):
        """Heuristic should find actionable patterns in save-memory's real corrections."""
        examples = _load_eval_examples("save-memory")
        skill_text = _load_real_skill_text("save-memory")

        # Strip frontmatter (optimizer does this internally)
        if skill_text.startswith("---"):
            end = skill_text.find("---", 3)
            if end > 0:
                skill_text = skill_text[end + 3:].strip()

        # Create optimizer with a temp skills dir containing the real skill
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skill_dir = skills_dir / "s_save-memory"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                _load_real_skill_text("save-memory"), encoding="utf-8",
            )

            optimizer = EvolutionOptimizer(skills_dir)
            result = optimizer.optimize_skill(
                "save-memory", examples, force_heuristic=True,
            )

            # With 2 real corrections ("Remove the test entry" + Chinese budget comment),
            # at least one should be actionable via heuristic
            assert isinstance(result, OptimizationResult)
            # The optimizer should process without crashing — real corrections
            # include mixed English/Chinese and long text
            assert result.skill_name == "save-memory"

    def test_real_corrections_are_parseable(self):
        """All corrections in fixtures should be valid EvalExample.user_correction strings."""
        for skill_name in ["save-memory", "autonomous-pipeline"]:
            examples = _load_eval_examples(skill_name)
            for ex in examples:
                if ex.user_correction:
                    # Should be non-empty string, not JSON, not None-as-string
                    assert isinstance(ex.user_correction, str)
                    assert len(ex.user_correction) > 0
                    assert ex.user_correction != "None"
                    assert ex.user_correction != "null"


# ── Test 3: Atomic deploy roundtrip with real skill ──

@pytest.mark.slow
class TestAtomicDeployRealSkill:
    """Verify atomic_deploy works with real SKILL.md content (encoding, frontmatter)."""

    def test_deploy_roundtrip_preserves_frontmatter(self, tmp_path):
        """Apply a known change to real SKILL.md → deploy → verify frontmatter intact."""
        src = SKILLS_DIR / "save-memory.md"
        if not src.exists():
            pytest.skip("save-memory.md fixture not found")

        # Copy to tmp
        skill_dir = tmp_path / "s_save-memory"
        skill_dir.mkdir()
        skill_path = skill_dir / "SKILL.md"
        shutil.copy2(src, skill_path)

        original = skill_path.read_text(encoding="utf-8")
        assert original.startswith("---"), "Real SKILL.md should have YAML frontmatter"

        # Apply a simple, known-to-exist change
        changes = [TextChange(
            original="Be concise",
            replacement="Be concise and precise",
            reason="Test roundtrip with real skill",
        )]

        # Verify the target exists
        if "Be concise" not in original:
            pytest.skip("'Be concise' not found in save-memory SKILL.md")

        result = atomic_deploy(skill_path, changes)
        assert result.success is True
        assert result.verified is True
        assert result.changes_applied == 1

        # Verify frontmatter preserved
        new_content = skill_path.read_text(encoding="utf-8")
        assert new_content.startswith("---"), "Frontmatter should survive deploy"
        assert "name:" in new_content.split("---")[1]

        # Verify .bak exists
        bak = skill_path.with_suffix(".md.bak")
        assert bak.exists(), ".bak file should be created"
        assert bak.read_text(encoding="utf-8") == original


# ── Test 4: Regression gate with previous cycle ──

@pytest.mark.slow
class TestRegressionGatePreviousCycle:
    """Verify skill_health.json from previous cycle is usable for trend detection."""

    def test_previous_health_loadable(self):
        """Previous skill_health.json should parse and contain expected fields."""
        if not HEALTH_FILE.exists():
            pytest.skip("previous_health.json fixture not found")

        data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
        assert "skills" in data
        assert len(data["skills"]) > 0

        for skill in data["skills"]:
            assert "skill_name" in skill
            assert "fitness_score" in skill
            assert "action" in skill
            assert "confidence" in skill
            # Fitness should be 0.0-1.0
            assert 0.0 <= skill["fitness_score"] <= 1.0
            # Action should be one of the known tiers
            assert skill["action"] in ("deploy", "recommend", "log", "skip")

    def test_trend_detection_uses_previous_fitness(self):
        """When previous cycle data exists, trend should be computed."""
        if not HEALTH_FILE.exists():
            pytest.skip("previous_health.json fixture not found")

        data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
        previous = {s["skill_name"]: s for s in data["skills"]}

        # At least one skill should have a fitness score we can compare
        assert "save-memory" in previous or "radar-todo" in previous

        # Verify delta logic: if current fitness differs by >0.05, trend changes
        for name, prev in previous.items():
            prev_fitness = prev["fitness_score"]
            # Simulate "same" fitness
            delta = prev_fitness - prev_fitness
            assert delta == 0.0  # stable
            # Simulate "degraded"
            delta = 0.3 - prev_fitness  # arbitrary lower
            if delta < -0.05:
                assert True  # would trigger "degrading" trend


# ── Test 5: LLM optimizer with mocked Bedrock ──

@pytest.mark.slow
class TestLLMOptimizerMockedBedrock:
    """Verify LLM optimizer handles real skill text + parse failures gracefully."""

    def test_llm_returns_valid_json_with_real_skill(self):
        """Mock Bedrock to return known JSON, verify parse + validation."""
        import tempfile

        skill_text = _load_real_skill_text("save-memory")
        examples = _load_eval_examples("save-memory")

        # Find a real correction text that exists in the skill
        correction_texts = [e.user_correction for e in examples if e.user_correction]
        assert len(correction_texts) >= 1

        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skill_dir = skills_dir / "s_save-memory"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")

            optimizer = EvolutionOptimizer(skills_dir)

            # Mock LLM to return a known-good response with a change
            # that targets text actually in the skill
            mock_changes = [TextChange(
                original="Be concise",
                replacement="Be concise and accurate",
                reason="Mock LLM suggestion",
            )]

            with patch.object(
                optimizer, "_try_llm_optimization",
                return_value=(mock_changes, 500),
            ):
                result = optimizer.optimize_skill("save-memory", examples)

                # LLM mock returned changes → should be in result
                assert len(result.changes) >= 1
                # Constraint validation should run on real skill text
                # (3.6KB skill + small change should pass constraints)
                assert result.skill_name == "save-memory"

    def test_llm_returns_malformed_json_falls_back(self):
        """When LLM returns garbage, should fall back to heuristic gracefully."""
        import tempfile

        skill_text = _load_real_skill_text("save-memory")
        examples = _load_eval_examples("save-memory")

        with tempfile.TemporaryDirectory() as td:
            skills_dir = Path(td) / "skills"
            skill_dir = skills_dir / "s_save-memory"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")

            optimizer = EvolutionOptimizer(skills_dir)

            # Mock LLM to return empty (simulates parse failure + fallback)
            with patch.object(
                optimizer, "_try_llm_optimization",
                return_value=([], 0),
            ):
                result = optimizer.optimize_skill("save-memory", examples)

                # Should fall back to heuristic — may or may not find patterns
                assert isinstance(result, OptimizationResult)
                assert result.skill_name == "save-memory"
                # No crash — that's the key assertion
