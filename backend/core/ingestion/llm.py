"""The base of CDF's model clients (the source extractor, sources/extract_llm).

- ``BedrockJudge``: the model id and the reproducibility fields (``version_vector``) every
  call records; the calls themselves are the subclass's (``structured()``).
- ``JudgeResult``: a typed answer with its version vector and raw reply.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from jobs.bedrock import get_model_id

from .contracts import CONTRACT_VERSION, NORMALIZER_VERSION, PROMPT_VERSION, RULES_VERSION
from .prompts import prompt_hash

T = TypeVar("T")

@dataclass(frozen=True)
class JudgeResult(Generic[T]):
    """Typed judge result and invocation-specific version fields."""

    value: T
    vector: dict[str, Any]
    raw: str


class BedrockJudge:
    """The model a CDF call runs on, and what it records about each call."""

    provider = "aws-bedrock"
    temperature = 0.0
    # Claude 4.5+ on Bedrock rejects temperature and top_p together; send only temperature.
    top_p = None
    seed = None

    max_tokens = 4096
    read_timeout = 25

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = model_id or get_model_id("claude-sonnet-4-6")

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
