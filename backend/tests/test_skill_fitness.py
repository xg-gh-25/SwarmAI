"""Tests for skill_fitness module."""
from __future__ import annotations

import pytest

from core.skill_fitness import FitnessScore, SkillFitnessEvaluator


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
