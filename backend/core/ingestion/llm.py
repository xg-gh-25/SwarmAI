"""Thin CDF judge client with complete version-vector capture.

- ``BedrockJudge`` reuses the repository's timeout-scoped Bedrock client.
- Every invocation returns typed output plus reproducibility metadata.
- Every invocation's token usage is recorded per source by ``llm_usage.record``.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from jobs.bedrock import StructuredOutputError, get_model_id, text

from . import llm_usage
from .contracts import CONTRACT_VERSION, NORMALIZER_VERSION, PROMPT_VERSION, RULES_VERSION, Card1Answer, Card3bAnswer, HitJudgment
from .prompts import (
    UntypedOutput, card1_prompt, card1_v2_prompt, card3_prompt, card3b_prompt,
    card3b_entailment_prompt, entailment_prompt, extract_json, parse_card1,
    parse_card1_v2, parse_card3, parse_card3b, parse_entailment, prompt_hash,
)

T = TypeVar("T")

@dataclass(frozen=True)
class JudgeResult(Generic[T]):
    """Typed judge result and invocation-specific version fields."""

    value: T
    vector: dict[str, Any]
    raw: str


class BedrockJudge:
    """Fail-closed synchronous judge for offline online-mode evaluation."""

    provider = "aws-bedrock"
    temperature = 0.0
    # Claude 4.5+ on Bedrock rejects temperature and top_p together; send only temperature.
    top_p = None
    seed = None

    max_tokens = 4096
    read_timeout = 25

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or get_model_id("claude-sonnet-4-6")

    def _invoke(self, prompt: str, parser: Any) -> JudgeResult[Any]:
        """One call through jobs.bedrock.text(); the card's strict parser types the reply.

        A reply cut at max_tokens, or an empty one, is UntypedOutput with its kind in the
        message: deterministic for the same input, so it is not retried (design
        2026-10-28-structured-model-output §5.4).
        """
        usage: dict[str, int] = {}
        try:
            raw = text(prompt, model_id=self.model_id, max_tokens=self.max_tokens, feature=llm_usage.feature(),
                       temperature=self.temperature, read_timeout=self.read_timeout,
                       max_attempts=2, usage=usage)
        except StructuredOutputError as exc:
            raise UntypedOutput(f"reply {exc.kind}: {exc.detail}", raw=exc.raw) from exc
        finally:
            if usage:
                llm_usage.record(usage)
        try:
            value = parser(raw)
        except UntypedOutput as exc:
            exc.raw = raw  # keep the exact reply for recording and dev tuning
            raise
        return JudgeResult(value, self.version_vector(prompt), raw)

    def version_vector(self, prompt: str) -> dict[str, Any]:
        """Return all fixed and invocation-derived Phase A version fields."""
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "provider_version": "bedrock-2023-05-31",
            "temperature": self.temperature,
            "top_p": self.top_p,
            "seed": self.seed,
            "prompt_hash": prompt_hash(prompt),
            "contract_version": CONTRACT_VERSION,
            "rules_version": RULES_VERSION,
            "normalizer_version": NORMALIZER_VERSION,
            "prompt_version": PROMPT_VERSION,
            "runtime": f"python-{platform.python_version()}",
        }

    def card1(self, text: str, project_candidates: tuple[str, ...], context: str = "") -> JudgeResult[Card1Answer]:
        """Judge one normalized claim-group."""
        prompt = card1_prompt(text, project_candidates, context)
        return self._invoke(prompt, parse_card1)

    def card3(self, text: str, hits: list[dict[str, Any]]) -> JudgeResult[list[HitJudgment]]:
        """Judge every retrieved hit."""
        prompt = card3_prompt(text, hits)
        return self._invoke(prompt, lambda raw: parse_card3(raw, hits))

    def verify(self, unit: dict[str, Any], grounding: str,
               hits: list[dict[str, Any]]) -> JudgeResult[Any]:
        """The one verification card (design 2026-10-29-cdf-refactor §3.3)."""
        from .prompts import VERIFY_VERSION, parse_verify, verify_prompt
        result = self._invoke(verify_prompt(unit, grounding, hits), parse_verify)
        result.vector["verify_version"] = VERIFY_VERSION
        return result

    def card3b(self, old_unit: str, candidate: str) -> JudgeResult[Card3bAnswer]:
        """Generate and validate a baseline effective unit."""
        prompt = card3b_prompt(old_unit, candidate)
        return self._invoke(prompt, parse_card3b)

    def card1_v2(self, unit: dict[str, Any], project_candidates: tuple[str, ...],
                 known_domains: tuple[str, ...]) -> JudgeResult[Any]:
        """Re-check an Event, Gap, or Reference against the v2 contract."""
        prompt = card1_v2_prompt(unit, project_candidates, known_domains)
        return self._invoke(prompt, parse_card1_v2)

    def entailment(self, user_text: str, what: str, how: str | None,
                   why: str | None) -> JudgeResult[Any]:
        """Judge S1 entailment with no ambient conversation context."""
        prompt = entailment_prompt(user_text, what, how, why)
        return self._invoke(prompt, parse_entailment)

    def card3b_entailment(self, claims: list[dict[str, str]],
                          effective_unit: str) -> JudgeResult[list[dict[str, Any]]]:
        """Independently verify every parsed old claim survives the effective unit."""
        prompt = card3b_entailment_prompt(claims, effective_unit)
        return self._invoke(prompt, extract_json)
