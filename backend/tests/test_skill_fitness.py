"""Tests for skill_fitness module (Layer 1 + Layer 2).

Tests cover:
- Layer 1: structural heuristic scoring (existing)
- Layer 2: LLM-as-judge scoring (v2.3 GEPA-inspired)
- Combined two-layer metric (JudgeScore)
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from core.skill_fitness import JudgeScore, LLMJudge, SkillFitnessEvaluator


@pytest.fixture
def evaluator():
    return SkillFitnessEvaluator()


class TestPerfectMatch:
    def test_perfect_match(self, evaluator):
        text = "create a new build pipeline with validation and testing"
        score = evaluator.score(text, text)
        assert score.correctness == pytest.approx(1.0)
        assert score.overall >= 0.9


class TestNoOverlap:
    def test_no_overlap(self, evaluator):
        expected = "deploy infrastructure with terraform modules"
        actual = "painting colorful landscapes using watercolor brushes"
        score = evaluator.score(expected, actual)
        assert score.correctness < 0.1
        assert score.overall < 0.3


class TestPartialOverlap:
    def test_partial_overlap(self, evaluator):
        expected = "create a new build pipeline with validation checks"
        actual = "build a pipeline for testing and deployment"
        score = evaluator.score(expected, actual)
        assert 0.1 < score.correctness < 0.9
        assert 0.1 < score.overall < 0.9


class TestProcedureVerbs:
    def test_procedure_verbs(self, evaluator):
        expected = "create a file and then validate the output"
        actual = "I will create the file and validate everything"
        score = evaluator.score(expected, actual)
        assert score.procedure == 1.0  # both "create" and "validate" present

    def test_missing_procedure_verbs(self, evaluator):
        expected = "create and deploy the application"
        actual = "the application is ready and waiting"
        score = evaluator.score(expected, actual)
        assert score.procedure < 1.0


class TestJudgmentMarkers:
    def test_judgment_markers(self, evaluator):
        expected = "approve the changes and proceed with deployment"
        actual = "I approve these changes and will proceed now"
        score = evaluator.score(expected, actual)
        assert score.judgment == 1.0

    def test_missing_judgment(self, evaluator):
        expected = "reject the proposal and stop work"
        actual = "the work continues forward without issues"
        score = evaluator.score(expected, actual)
        assert score.judgment < 1.0


class TestScoreBatch:
    def test_score_batch(self, evaluator):
        pairs = [
            ("create build pipeline", "create build pipeline"),
            ("totally different", "nothing similar here"),
        ]
        avg = evaluator.score_batch(pairs)
        assert 0.2 < avg < 0.9

    def test_score_batch_empty(self, evaluator):
        assert evaluator.score_batch([]) == 0.0


class TestEmptyInputs:
    def test_empty_inputs(self, evaluator):
        score = evaluator.score("", "")
        assert score.correctness == 1.0  # both empty = perfect match
        assert score.overall == 1.0

    def test_one_empty(self, evaluator):
        score = evaluator.score("hello world test", "")
        assert score.correctness == 0.0


# ── Layer 2: LLM Judge Tests (v2.3) ──


class TestLLMJudge:
    """Test LLM-as-judge scoring with mocked Bedrock calls."""

    @pytest.fixture
    def judge(self):
        return LLMJudge()

    def test_score_both_empty(self, judge):
        """Both empty = trivially correct, no LLM call needed."""
        result = judge.score("skill text", "", "")
        assert result == 1.0

    @patch("core.skill_fitness.LLMJudge._get_client")
    def test_score_success(self, mock_get_client, judge):
        """Successful judge call returns parsed score."""
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"score": 0.8, "justification": "Good output."}'}]}},
            "usage": {"inputTokens": 100, "outputTokens": 50},
        }
        mock_get_client.return_value = mock_client

        result = judge.score(
            skill_text="Create files and validate output.",
            expected="File created successfully with validation.",
            actual="Created the file and ran validation checks.",
        )
        assert result == pytest.approx(0.8)
        mock_client.converse.assert_called_once()

    @patch("core.skill_fitness.LLMJudge._get_client")
    def test_score_api_failure_returns_none(self, mock_get_client, judge):
        """API failure returns None (graceful fallback)."""
        mock_client = MagicMock()
        mock_client.converse.side_effect = Exception("Bedrock timeout")
        mock_get_client.return_value = mock_client

        result = judge.score("skill", "expected", "actual")
        assert result is None

    @patch("core.skill_fitness.LLMJudge._get_client")
    def test_score_invalid_json_returns_none(self, mock_get_client, judge):
        """Malformed JSON response returns None."""
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "not json at all"}]}},
        }
        mock_get_client.return_value = mock_client

        result = judge.score("skill", "expected", "actual")
        assert result is None

    @patch("core.skill_fitness.LLMJudge._get_client")
    def test_score_zero_content_blocks_logs_distinct_signal(self, mock_get_client, judge, caplog):
        """Zero content blocks → None + a DISTINCT (non-thinking-only) log signal.

        Guards the empty-cause else-branch: previously a zero-block response was
        silent at this site (only the thinking-only case logged). Mirrors
        test_llm_optimizer.TestEmptyReturnLogSignals — drives the REAL score()
        path, mocking only the boto3 client boundary.
        """
        import logging

        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": []}},  # zero content blocks
        }
        mock_get_client.return_value = mock_client

        with caplog.at_level(logging.WARNING, logger="core.skill_fitness"):
            result = judge.score("skill", "expected", "actual")

        assert result is None
        msgs = [r.getMessage() for r in caplog.records]
        assert msgs, f"zero-content-blocks was silent at source: {msgs}"
        assert not any("thinking-only" in m.lower() for m in msgs), (
            f"zero-block wrongly labeled thinking-only: {msgs}"
        )

    @patch("core.skill_fitness.LLMJudge._get_client")
    def test_score_clamps_to_range(self, mock_get_client, judge):
        """Score outside [0,1] gets clamped."""
        mock_client = MagicMock()
        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"score": 1.5, "justification": "Overshot."}'}]}},
        }
        mock_get_client.return_value = mock_client

        result = judge.score("skill", "expected", "actual")
        assert result == 1.0

    @patch("core.skill_fitness.LLMJudge.score")
    def test_score_batch_combined(self, mock_score, judge):
        """score_batch combines L1 and L2 with 0.4/0.6 weights."""
        # Mock L2 judge to return 0.9 for all examples
        mock_score.return_value = 0.9

        examples = [
            ("create build pipeline", "create build pipeline"),  # L1 = ~1.0
            ("create build pipeline", "create build pipeline"),  # L1 = ~1.0
        ]
        result = judge.score_batch("Skill instructions here.", examples)

        assert isinstance(result, JudgeScore)
        assert result.layer1_score > 0.8  # Heuristic on identical text = high
        assert result.layer2_score == pytest.approx(0.9)
        # Combined = 0.4*L1 + 0.6*0.9
        assert result.score == pytest.approx(0.4 * result.layer1_score + 0.6 * 0.9)

    @patch("core.skill_fitness.LLMJudge.score")
    def test_score_batch_judge_failure_fallback(self, mock_score, judge):
        """When all judge calls fail, falls back to Layer 1 only."""
        mock_score.return_value = None  # All calls fail

        examples = [("build pipeline", "build pipeline")]
        result = judge.score_batch("Skill instructions.", examples)

        assert result.layer2_score is None
        assert result.score == result.layer1_score  # L1-only fallback
        assert "unavailable" in result.justification
