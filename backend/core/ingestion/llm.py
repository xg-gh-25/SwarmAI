"""Thin CDF judge client with complete version-vector capture.

- ``BedrockJudge`` reuses the repository's timeout-scoped Bedrock client.
- Every invocation returns typed output plus reproducibility metadata.
- Every invocation's token usage is recorded per source by ``llm_usage.record``.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from jobs.bedrock import build_timeout_client, get_model_id

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

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or get_model_id("claude-sonnet-4-6")
        self._client = build_timeout_client(read_timeout=25, max_attempts=2)

    def _invoke(self, prompt: str, parser: Any) -> JudgeResult[Any]:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        response = self._client.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        decoded = json.loads(response["body"].read())
        llm_usage.record(decoded.get("usage") or {})
        blocks = decoded.get("content", [])
        raw = blocks[0].get("text", "") if blocks and blocks[0].get("type") == "text" else ""
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
