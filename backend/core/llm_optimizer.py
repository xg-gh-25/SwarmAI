"""LLM-based skill optimization via Bedrock Opus API.

Replaces the heuristic optimizer's blind append/remove with semantic
understanding of corrections. Takes skill text + correction evidence,
asks Opus to propose specific TextChange objects.

Zero new dependencies — uses existing boto3/Bedrock connection.
Returns empty list on any failure (timeout, malformed response, API error)
so the caller can fall back to heuristic optimization.

All functions are synchronous — boto3.converse() is a sync API, wrapping
it in async would be fake async adding complexity for no benefit.

Key public symbols:
- ``optimize_skill_with_llm``  -- Main entry point. Sync.
- ``_call_bedrock_opus``       -- Low-level Bedrock invoke (mockable).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

import boto3
from botocore.config import Config as BotoConfig

from core.evolution_optimizer import TextChange
from model_registry import (
    FLAGSHIP_MODEL,
    resolve_bedrock_id,
    supports_custom_temperature,
)

logger = logging.getLogger(__name__)


def _resolve_bedrock_model() -> tuple[str, bool]:
    """Resolve Bedrock model ID from config + determine temperature safety.

    Returns:
        (model_id, supports_temperature) — model_id is the full Bedrock ARN-style
        ID (e.g. "us.anthropic.claude-opus-4-6-v1"), supports_temperature is False
        for models that reject temperature != 1 when thinking is adaptive (4.8+).
    """
    from core.app_config_manager import AppConfigManager

    cfg = AppConfigManager.instance()
    short_name = cfg.get("default_model", FLAGSHIP_MODEL)
    model_map = cfg.get("bedrock_model_map") or {}  # null-safe: config may have null
    # Config map first (a deployment may override), then the registry. The
    # f"us.anthropic.{short_name}" synthesis is a LAST resort only — Bedrock IDs
    # are not mechanically derivable (4-6 needs "-v1", 4-8 and 5 do not).
    model_id = (
        model_map.get(short_name)
        or resolve_bedrock_id(short_name)
        or f"us.anthropic.{short_name}"
    )

    # Opus 4.7+ rejects temperature != 1 with adaptive thinking. Derived by
    # VERSION COMPARISON in the registry, not a hardcoded set: the old
    # {"claude-opus-4-7", "claude-opus-4-8"} allowlist did not contain the
    # newer flagship, so a newly promoted opus silently read as
    # "supports temperature" and would have had one sent that it rejects.
    supports_temperature = supports_custom_temperature(short_name)

    return model_id, supports_temperature

# Cap changes per optimization to prevent runaway modifications
MAX_CHANGES = 5

# Skill text larger than this is truncated before sending to LLM.
# 10KB ≈ 3K tokens input. Keeps Opus cost at ~$0.05/skill max.
MAX_SKILL_TEXT_BYTES = 10 * 1024

_SYSTEM_PROMPT = """\
You are a skill instruction optimizer for an AI assistant called SwarmAI.

You receive:
1. The current SKILL.md instruction text (what the AI reads before executing the skill)
2. User corrections (feedback where the AI went wrong while following these instructions)

Your job: propose specific text changes to improve the instructions so the AI
won't make the same mistakes again.

Rules:
- Each change must address a specific correction pattern
- Prefer modifying existing text over appending new bullet points
- If the correction says "don't X", find where X is instructed and rewrite it
- If the correction says "should Y", add Y as a clear instruction in the relevant section
- Keep the instruction style consistent with the existing document
- Max 5 changes — focus on the highest-impact improvements
- Do NOT change YAML frontmatter (the --- block at the top)
- The "original" field must be EXACT text copied from the instructions — character-for-character

Return ONLY valid JSON (no markdown wrapping, no explanation):
{"changes": [{"original": "exact text to find and replace", "replacement": "new text", "reason": "why this change"}]}

For append-only changes (new instructions), use empty string for "original":
{"changes": [{"original": "", "replacement": "- New instruction here", "reason": "why"}]}
"""


# ── Bedrock client singleton with TTL ──

import threading

_bedrock_client = None
_bedrock_client_created_at: float = 0.0
_CLIENT_TTL_SECONDS = 3600  # Re-create client hourly (credential rotation)
_client_lock = threading.Lock()  # Thread safety for shared client (Finding 2, adversarial review)


def _get_bedrock_client():
    """Lazy singleton with 1-hour TTL — re-creates after credential rotation.

    Thread-safe: protected by _client_lock to prevent race between
    reset_bedrock_client() and concurrent callers (LLMJudge + optimizer).
    """
    global _bedrock_client, _bedrock_client_created_at
    import time

    with _client_lock:
        now = time.monotonic()
        if _bedrock_client is None or (now - _bedrock_client_created_at) > _CLIENT_TTL_SECONDS:
            region = os.environ.get(
                "AWS_REGION",
                os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            )
            _bedrock_client = boto3.client(
                "bedrock-runtime",
                region_name=region,
                config=BotoConfig(read_timeout=30, connect_timeout=10, retries={"max_attempts": 1}),
            )
            _bedrock_client_created_at = now
        return _bedrock_client


def reset_bedrock_client():
    """Force re-creation on next call. Called by _run_evolution_cycle_locked at cycle start."""
    global _bedrock_client, _bedrock_client_created_at
    with _client_lock:
        _bedrock_client = None
        _bedrock_client_created_at = 0.0


# ── Token usage tracking ──

@dataclass
class LLMUsage:
    """Token usage from a single Bedrock call."""
    input_tokens: int = 0
    output_tokens: int = 0


def _build_prompt(
    skill_text: str,
    corrections: list[tuple[str, str, str]],
    skill_name: str,
    execution_traces: list[str] | None = None,
) -> str:
    """Build the user prompt with skill text, correction evidence, and execution traces.

    v2.3: Added execution_traces parameter. GEPA-inspired: feeding the agent's
    reasoning traces (what it was thinking when it went wrong) produces targeted
    mutations instead of blind pattern matching.
    """
    # Truncate oversized skills to stay within token budget
    if len(skill_text.encode("utf-8")) > MAX_SKILL_TEXT_BYTES:
        truncated = skill_text.encode("utf-8")[:MAX_SKILL_TEXT_BYTES].decode(
            "utf-8", errors="ignore"
        )
        skill_text = truncated + "\n\n[... truncated — optimize the sections above ...]"

    correction_lines = []
    for i, (text, action_type, confidence) in enumerate(corrections, 1):
        conf_label = "strong" if confidence == "high" else "weak"
        correction_lines.append(f"  {i}. [{conf_label}] ({action_type}) {text}")

    corrections_block = (
        "\n".join(correction_lines)
        if correction_lines
        else "  (no corrections — optimize for clarity and completeness)"
    )

    # v2.3: Include execution traces for context-aware optimization
    trace_block = ""
    if execution_traces:
        trace_lines = []
        for i, trace in enumerate(execution_traces[:5], 1):
            trace_lines.append(f"  Trace {i}:\n    {trace[:1500]}")
        trace_block = f"""

Execution traces (what the agent was doing when it went wrong):
{chr(10).join(trace_lines)}
"""

    return f"""Skill: {skill_name}

Current instructions:
---
{skill_text}
---

User corrections (where the AI went wrong):
{corrections_block}
{trace_block}
Propose specific text changes to improve these instructions. Return JSON only."""


def _parse_llm_response(response: str) -> list[TextChange]:
    """Parse LLM response into TextChange objects.

    Handles: raw JSON, JSON wrapped in ```json ... ```, partial JSON.
    Returns empty list on any parse failure.
    """
    text = response.strip()

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # Extract the first valid JSON object using raw_decode — handles
    # unbalanced braces inside JSON string values (code snippets, f-strings)
    # that break naive depth-counting. See B1 fix.
    brace_start = text.find("{")
    if brace_start == -1:
        logger.warning("LLM optimizer: no JSON object found in response")
        return []

    decoder = json.JSONDecoder()
    try:
        data, _ = decoder.raw_decode(text, brace_start)
    except json.JSONDecodeError as exc:
        logger.warning("LLM optimizer: JSON parse failed: %s", exc)
        return []

    if not isinstance(data, dict) or "changes" not in data:
        logger.warning("LLM optimizer: response missing 'changes' key")
        return []

    changes = []
    for item in data["changes"][:MAX_CHANGES]:
        if not isinstance(item, dict):
            continue
        original = item.get("original", "")
        replacement = item.get("replacement", "")
        reason = item.get("reason", "LLM-proposed change")

        # Skip no-op changes
        if original == replacement:
            continue
        # Skip empty changes (both empty = nothing to do)
        if not original and not replacement:
            continue

        changes.append(TextChange(
            original=str(original),
            replacement=str(replacement),
            reason=str(reason),
        ))

    return changes


# Effort level for direct Bedrock API calls (internal LLM-as-judge utilities).
# "low" = skip extended thinking, fast + cheap. These are structured eval tasks
# that don't benefit from deep reasoning chains.
BEDROCK_EFFORT = "low"


def _call_bedrock_opus(prompt: str, system: str = _SYSTEM_PROMPT) -> tuple[str, LLMUsage]:
    """Invoke Bedrock Opus and return (response_text, usage).

    Sync call — boto3.converse() is a sync API.
    Max tokens: 2000 (changes are small).
    Uses ``BEDROCK_EFFORT`` to control thinking depth (default: low).
    Model resolved from config.json default_model (single source of truth).
    """
    client = _get_bedrock_client()
    model_id, supports_temperature = _resolve_bedrock_model()

    inference_config: dict = {"maxTokens": 2000}
    if supports_temperature:
        inference_config["temperature"] = 0.3  # Low temp for precise, structured output

    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        system=[{"text": system}],
        inferenceConfig=inference_config,
        additionalModelRequestFields={
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": BEDROCK_EFFORT},
        },
    )

    # Extract usage
    usage_data = response.get("usage", {})
    usage = LLMUsage(
        input_tokens=usage_data.get("inputTokens", 0),
        output_tokens=usage_data.get("outputTokens", 0),
    )

    # Extract text from response (skip reasoningContent blocks from adaptive thinking)
    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])
    for block in content_blocks:
        if "text" in block:
            return block["text"], usage

    # Distinguish the two empty-return causes at the source so a future
    # "LLM optimization silently degraded" incident is diagnosable from the log
    # alone. The caller no longer adds a generic "empty response" line — that
    # double-logged the thinking-only case and was the zero-block case's only
    # signal. The exception path logs its own distinct signal in the caller.
    if content_blocks:
        logger.warning(
            "LLM optimizer: Bedrock returned %d block(s) but no text block "
            "(thinking-only response) — no changes extracted",
            len(content_blocks),
        )
    else:
        logger.warning(
            "LLM optimizer: Bedrock returned zero content blocks "
            "(empty response) — no changes extracted",
        )
    return "", usage


def optimize_skill_with_llm(
    skill_text: str,
    corrections: list[tuple[str, str, str]],
    skill_name: str,
    execution_traces: list[str] | None = None,
) -> tuple[list[TextChange], LLMUsage]:
    """Optimize a skill's instructions using Bedrock Opus.

    Takes the skill body text and all correction evidence (high + low confidence),
    asks Opus to propose specific text changes that address the correction patterns.

    v2.3: Added execution_traces parameter. GEPA-inspired trace-guided mutation:
    feeding the agent's reasoning during failed executions gives the optimizer
    targeted context for what specifically went wrong, enabling precise fixes
    instead of generic improvements.

    Returns (changes, usage) where changes are compatible with atomic_deploy().
    Returns ([], LLMUsage()) on any failure — caller should fall back to heuristic.

    Args:
        skill_text: The SKILL.md body text (below YAML frontmatter).
        corrections: List of (correction_text, action_type, confidence) tuples.
        skill_name: Skill name for prompt context.
        execution_traces: Optional list of agent reasoning traces from failed executions.
    """
    empty = ([], LLMUsage())

    if not corrections:
        return empty

    prompt = _build_prompt(skill_text, corrections, skill_name, execution_traces)

    try:
        response_text, usage = _call_bedrock_opus(prompt)
        if not response_text:
            # _call_bedrock_opus already logged the specific empty cause
            # (thinking-only vs zero-content-blocks). Falls back to heuristic
            # via the caller in evolution_optimizer.
            return empty

        changes = _parse_llm_response(response_text)

        # Pre-validate: drop changes whose 'original' doesn't exist in skill text.
        # LLM often returns approximate quotes that don't match character-for-character.
        validated = []
        for change in changes:
            if change.original and change.original not in skill_text:
                logger.warning(
                    "LLM optimizer: proposed original not found in %s, dropping: %r",
                    skill_name, change.original[:80],
                )
                continue
            validated.append(change)

        if validated:
            logger.info(
                "LLM optimizer: %d changes proposed for %s (%d dropped, %d in/%d out tokens)",
                len(validated), skill_name,
                len(changes) - len(validated),
                usage.input_tokens, usage.output_tokens,
            )
        return validated, usage

    except Exception as exc:
        logger.warning("LLM optimizer failed for %s: %s", skill_name, exc)
        return empty
