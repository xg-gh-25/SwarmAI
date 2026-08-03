"""Skill fitness evaluator with two-layer scoring: structural heuristics + LLM judge.

Layer 1 (structural, 40% weight):
Scores skill outputs against expected behavior on 3 dimensions:
correctness (50%), procedure_following (30%), judgment_quality (20%).
Correctness uses three complementary signals blended together:
- Jaccard term overlap (word-level, broad)
- Bigram overlap (phrase-level, catches word ordering)
- Containment ratio (asymmetric: what fraction of expected terms appear in actual)

Layer 2 (LLM-as-judge, 60% weight):
Rubric-based scoring via Bedrock Haiku. Evaluates whether the actual output
demonstrates correct application of the skill instructions, penalizes missing
patterns, allows naming flexibility. Returns score + justification.

GEPA-inspired: two-layer metric pattern from DSPy/GEPA (ICLR 2026 Oral).
Layer 1 gives smooth gradient (fast, deterministic, zero-cost).
Layer 2 gives semantic understanding (catches what structural checks miss).

Key public symbols:
- ``FitnessScore``          -- 3-dimensional score dataclass.
- ``JudgeScore``            -- Combined two-layer score with justification.
- ``SkillFitnessEvaluator`` -- Multi-signal heuristic scorer (Layer 1).
- ``LLMJudge``             -- Rubric-based LLM scorer (Layer 2).
"""
from __future__ import annotations

import json
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


@dataclass
class JudgeScore:
    """Combined two-layer fitness score with justification."""
    score: float               # 0.0-1.0 combined (0.4*L1 + 0.6*L2)
    layer1_score: float        # Structural heuristic score
    layer2_score: float | None  # LLM judge score (None if skipped/failed)
    justification: str = ""    # LLM judge's one-sentence explanation


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


# ── Layer 2: LLM-as-Judge ──

_JUDGE_SYSTEM_PROMPT = """\
You are a skill quality judge for SwarmAI. You evaluate whether an AI agent's
actual output demonstrates correct application of skill instructions.

Score on a 0.0-1.0 scale using this rubric:
- 1.0: Output perfectly follows the skill's patterns and addresses the user's request
- 0.8: Output follows most patterns, minor omissions
- 0.6: Output partially correct but misses key patterns or has significant gaps
- 0.4: Output shows awareness of the skill but applies it incorrectly
- 0.2: Output barely relates to what the skill instructs
- 0.0: Output is completely wrong or unrelated

Rules for judging:
- DO NOT penalize naming differences (e.g., different variable names, ordering)
- DO penalize: missing the core action entirely, breaking existing behavior,
  using a different approach than instructed, hallucinating capabilities
- Weight "did it do what the user asked?" highest
- Consider the correction context: if the user corrected the agent, the output
  was wrong — factor that into your score

Return ONLY valid JSON: {"score": 0.7, "justification": "One sentence explaining the score."}
"""


class LLMJudge:
    """Layer 2 scoring: rubric-based LLM judgment via Bedrock Opus.

    Evaluates (skill_text, expected, actual) triples against a quality rubric.
    Returns 0.0-1.0 score with justification text.

    Uses Opus for quality (KD28: power over token budget). Same model as
    llm_optimizer — one model everywhere, zero complexity.
    Falls back to None on any failure (timeout, API error, parse failure).
    """

    EFFORT = "low"  # Internal judge — structured eval, no deep reasoning needed
    TIMEOUT_SECONDS = 30

    def __init__(self):
        pass  # Uses shared Bedrock client from llm_optimizer

    def _get_client(self):
        """Use the shared Bedrock client from llm_optimizer (one client, one TTL)."""
        from core.llm_optimizer import _get_bedrock_client
        return _get_bedrock_client()

    def _build_judge_prompt(
        self,
        skill_text: str,
        expected: str,
        actual: str,
        correction_context: str = "",
    ) -> str:
        """Build the judge prompt with skill context and output pair."""
        # Truncate skill text for judge (it just needs the gist).
        # Use char-level truncation — safe for CJK, simpler than byte-level.
        if len(skill_text) > 4000:
            skill_text = skill_text[:4000] + "\n[... truncated ...]"

        parts = [
            f"## Skill Instructions (what the agent should follow)\n{skill_text}",
            f"\n## Expected Output\n{expected[:2000]}",
            f"\n## Actual Output\n{actual[:2000]}",
        ]
        if correction_context:
            parts.append(f"\n## User Correction (agent was wrong)\n{correction_context[:500]}")

        parts.append("\nScore this output. Return JSON only.")
        return "\n".join(parts)

    def score(
        self,
        skill_text: str,
        expected: str,
        actual: str,
        correction_context: str = "",
    ) -> float | None:
        """Score a single (expected, actual) pair using LLM judge.

        Returns 0.0-1.0 on success, None on failure (timeout, parse error, etc.).
        """
        if not expected and not actual:
            return 1.0  # Both empty = trivially correct

        prompt = self._build_judge_prompt(skill_text, expected, actual, correction_context)

        try:
            from core.llm_optimizer import _resolve_bedrock_model

            client = self._get_client()
            model_id, supports_temperature = _resolve_bedrock_model()

            inference_config: dict = {"maxTokens": 200}
            if supports_temperature:
                inference_config["temperature"] = 0.1  # Low temp for precise judge scoring

            response = client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                system=[{"text": _JUDGE_SYSTEM_PROMPT}],
                inferenceConfig=inference_config,
                additionalModelRequestFields={
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": self.EFFORT},
                },
            )

            # Extract text (skip reasoningContent blocks from adaptive thinking)
            content_blocks = response.get("output", {}).get("message", {}).get("content", [])
            text = ""
            for block in content_blocks:
                if "text" in block:
                    text = block["text"]
                    break

            if not text:
                if content_blocks:
                    logger.warning(
                        "LLMJudge: %d block(s) but no text (thinking-only response) "
                        "— no score returned",
                        len(content_blocks),
                    )
                else:
                    logger.warning(
                        "LLMJudge: zero content blocks (empty response) "
                        "— no score returned",
                    )
                return None

            # Parse JSON response
            data = json.loads(text.strip())
            score_val = float(data.get("score", 0.0))
            return max(0.0, min(1.0, score_val))

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("LLM judge parse error: %s", exc)
            return None
        except Exception as exc:
            logger.warning("LLM judge call failed: %s", exc)
            return None

    def score_batch(
        self,
        skill_text: str,
        examples: list[tuple[str, str]],
        corrections: list[str] | None = None,
    ) -> JudgeScore:
        """Score a batch of examples with combined two-layer metric.

        Returns JudgeScore with:
        - layer1_score: structural heuristic average
        - layer2_score: LLM judge average (None if all failed)
        - score: combined 0.4*L1 + 0.6*L2 (or L1-only if L2 failed)
        - justification: from the last successful judge call

        Args:
            skill_text: Full SKILL.md body for context.
            examples: List of (expected, actual) pairs.
            corrections: Optional correction texts for context (one per example).
        """
        if not examples:
            return JudgeScore(score=0.0, layer1_score=0.0, layer2_score=None)

        # Layer 1: structural heuristic
        evaluator = SkillFitnessEvaluator()
        layer1 = evaluator.score_batch(examples)

        # Layer 2: LLM judge (sample up to 5 examples, 60s batch timeout)
        import time as _time
        sample = examples[:5]
        judge_scores: list[float] = []
        batch_start = _time.monotonic()
        BATCH_TIMEOUT = 60  # seconds total for all judge calls
        for i, (expected, actual) in enumerate(sample):
            if _time.monotonic() - batch_start > BATCH_TIMEOUT:
                logger.warning("LLM judge batch timeout after %d/%d examples", i, len(sample))
                break
            correction = corrections[i] if corrections and i < len(corrections) else ""
            s = self.score(skill_text, expected, actual, correction)
            if s is not None:
                judge_scores.append(s)

        if judge_scores:
            layer2 = sum(judge_scores) / len(judge_scores)
            combined = 0.4 * layer1 + 0.6 * layer2
            return JudgeScore(
                score=combined,
                layer1_score=layer1,
                layer2_score=layer2,
                justification=f"L1={layer1:.2f}, L2={layer2:.2f} (n={len(judge_scores)})",
            )
        else:
            # LLM judge failed entirely — fall back to Layer 1 only
            return JudgeScore(
                score=layer1,
                layer1_score=layer1,
                layer2_score=None,
                justification="LLM judge unavailable, using structural score only",
            )
