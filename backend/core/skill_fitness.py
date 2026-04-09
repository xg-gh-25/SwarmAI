"""Skill fitness evaluator using keyword-overlap heuristics.

Scores skill outputs against expected behavior on 3 dimensions:
correctness (50%), procedure_following (30%), judgment_quality (20%).

Key public symbols:
- ``FitnessScore``          -- 3-dimensional score dataclass.
- ``SkillFitnessEvaluator`` -- Heuristic scorer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FitnessScore:
    correctness: float     # 0.0-1.0 -- key terms overlap
    procedure: float       # 0.0-1.0 -- action verbs present
    judgment: float        # 0.0-1.0 -- decision outcomes match
    overall: float         # Weighted: 0.5*c + 0.3*p + 0.2*j


class SkillFitnessEvaluator:
    # Action verbs that indicate procedure following
    PROCEDURE_VERBS = {
        "create", "update", "delete", "search", "read", "write",
        "scan", "validate", "check", "run", "test", "build",
        "deploy", "commit", "install", "configure",
    }

    def _extract_key_terms(self, text: str) -> set[str]:
        """Extract significant terms (>3 chars, not stopwords)."""
        stopwords = {
            "the", "and", "for", "that", "this", "with", "from",
            "have", "been", "were", "will", "would", "could",
            "should", "also", "than", "then", "into", "about", "which",
        }
        words = set(re.findall(r"\b[a-zA-Z_]\w{3,}\b", text.lower()))
        return words - stopwords

    def _jaccard(self, a: set, b: set) -> float:
        """Jaccard similarity between two sets."""
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def score(self, expected: str, actual: str) -> FitnessScore:
        """Score actual output against expected behavior.

        correctness: Jaccard similarity of key terms
        procedure: fraction of expected action verbs found in actual
        judgment: fraction of expected decision markers found in actual
        """
        expected_terms = self._extract_key_terms(expected)
        actual_terms = self._extract_key_terms(actual)

        correctness = self._jaccard(expected_terms, actual_terms)

        expected_verbs = expected_terms & self.PROCEDURE_VERBS
        if expected_verbs:
            procedure = len(expected_verbs & actual_terms) / len(expected_verbs)
        else:
            procedure = 1.0  # No verbs expected = procedure satisfied

        # Judgment: look for decision markers
        decision_markers = {
            "approve", "reject", "defer", "accept", "decline",
            "proceed", "stop", "skip",
        }
        expected_decisions = expected_terms & decision_markers
        if expected_decisions:
            judgment = len(expected_decisions & actual_terms) / len(expected_decisions)
        else:
            judgment = 1.0

        overall = 0.5 * correctness + 0.3 * procedure + 0.2 * judgment
        return FitnessScore(
            correctness=correctness,
            procedure=procedure,
            judgment=judgment,
            overall=overall,
        )

    def score_batch(self, examples: list[tuple[str, str]]) -> float:
        """Score a batch of (expected, actual) pairs. Returns average overall score."""
        if not examples:
            return 0.0
        scores = [self.score(exp, act).overall for exp, act in examples]
        return sum(scores) / len(scores)
