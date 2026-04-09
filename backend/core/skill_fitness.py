"""Skill fitness evaluator using multi-signal heuristics.

Scores skill outputs against expected behavior on 3 dimensions:
correctness (50%), procedure_following (30%), judgment_quality (20%).

Correctness uses three complementary signals blended together:
- Jaccard term overlap (word-level, broad)
- Bigram overlap (phrase-level, catches word ordering)
- Containment ratio (asymmetric: what fraction of expected terms appear in actual)

This avoids the pure-Jaccard trap where two texts share keywords but
have completely different instructions.  The threshold in
``run_evolution_cycle`` uses an adaptive formula that requires more
evidence (lower threshold) when example count is low.

Key public symbols:
- ``FitnessScore``          -- 3-dimensional score dataclass.
- ``SkillFitnessEvaluator`` -- Multi-signal heuristic scorer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FitnessScore:
    correctness: float     # 0.0-1.0 -- multi-signal term/phrase overlap
    procedure: float       # 0.0-1.0 -- action verbs present
    judgment: float        # 0.0-1.0 -- decision outcomes match
    overall: float         # Weighted: 0.5*c + 0.3*p + 0.2*j


_STOPWORDS = frozenset({
    "the", "and", "for", "that", "this", "with", "from",
    "have", "been", "were", "will", "would", "could",
    "should", "also", "than", "then", "into", "about", "which",
    "are", "was", "not", "can", "all", "but", "when",
    "your", "you", "they", "them", "their", "there", "here",
    "what", "where", "how", "does", "each", "some", "more",
    "other", "only", "just", "like", "over", "such", "after",
    "before", "between", "through", "during", "without",
    "being", "those", "these", "very", "most", "make",
})


class SkillFitnessEvaluator:
    """Multi-signal heuristic scorer for skill fitness.

    Blends 3 correctness signals (Jaccard, bigram, containment) to produce
    scores that spread across the 0-1 range instead of clustering around
    0.3-0.5 like pure Jaccard does on real-world skill text.
    """

    # Action verbs that indicate procedure following
    PROCEDURE_VERBS = {
        "create", "update", "delete", "search", "read", "write",
        "scan", "validate", "check", "run", "test", "build",
        "deploy", "commit", "install", "configure",
    }

    def _extract_key_terms(self, text: str) -> set[str]:
        """Extract significant terms (>3 chars, not stopwords)."""
        words = set(re.findall(r"\b[a-zA-Z_]\w{3,}\b", text.lower()))
        return words - _STOPWORDS

    def _extract_bigrams(self, text: str) -> set[tuple[str, str]]:
        """Extract consecutive word bigrams (lowered, no stopwords)."""
        words = [
            w for w in re.findall(r"\b[a-zA-Z_]\w{2,}\b", text.lower())
            if w not in _STOPWORDS
        ]
        return {(words[i], words[i + 1]) for i in range(len(words) - 1)} if len(words) >= 2 else set()

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        """Jaccard similarity between two sets."""
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    @staticmethod
    def _containment(expected: set, actual: set) -> float:
        """Fraction of expected items found in actual (asymmetric recall)."""
        if not expected:
            return 1.0
        return len(expected & actual) / len(expected)

    def _correctness(self, expected: str, actual: str) -> float:
        """Multi-signal correctness: blend Jaccard, bigram overlap, and containment.

        Weights: Jaccard 30%, bigram overlap 30%, containment 40%.
        Containment is weighted highest because it answers "did the actual
        text cover what the expected text asked for?" — the most useful
        signal for correction-driven optimization.
        """
        exp_terms = self._extract_key_terms(expected)
        act_terms = self._extract_key_terms(actual)

        jaccard = self._jaccard(exp_terms, act_terms)
        containment = self._containment(exp_terms, act_terms)

        exp_bigrams = self._extract_bigrams(expected)
        act_bigrams = self._extract_bigrams(actual)
        bigram_sim = self._jaccard(exp_bigrams, act_bigrams)

        return jaccard * 0.3 + bigram_sim * 0.3 + containment * 0.4

    def score(self, expected: str, actual: str) -> FitnessScore:
        """Score actual output against expected behavior.

        correctness: multi-signal (Jaccard + bigram + containment)
        procedure: fraction of expected action verbs found in actual
        judgment: fraction of expected decision markers found in actual
        """
        correctness = self._correctness(expected, actual)

        expected_terms = self._extract_key_terms(expected)
        actual_terms = self._extract_key_terms(actual)

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
