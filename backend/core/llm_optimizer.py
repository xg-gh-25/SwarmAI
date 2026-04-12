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

from core.evolution_optimizer import TextChange

logger = logging.getLogger(__name__)

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


# ── Bedrock client singleton ──

_bedrock_client = None


def _get_bedrock_client():
    """Lazy singleton — one client per process, connection pool reused."""
    global _bedrock_client
    if _bedrock_client is None:
        region = os.environ.get(
            "AWS_REGION",
            os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
        )
        _bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    return _bedrock_client


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
) -> str:
    """Build the user prompt with skill text and correction evidence."""
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

    return f"""Skill: {skill_name}

Current instructions:
---
{skill_text}
---

User corrections (where the AI went wrong):
{corrections_block}

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

    # Try to find JSON object in the response
    brace_start = text.find("{")
    if brace_start == -1:
        logger.warning("LLM optimizer: no JSON object found in response")
        return []

    # Find the matching closing brace
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                text = text[brace_start : i + 1]
                break

    try:
        data = json.loads(text)
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


def _call_bedrock_opus(prompt: str, system: str = _SYSTEM_PROMPT) -> tuple[str, LLMUsage]:
    """Invoke Bedrock Opus and return (response_text, usage).

    Sync call — boto3.converse() is a sync API.
    Max tokens: 2000 (changes are small).
    """
    client = _get_bedrock_client()

    response = client.converse(
        modelId="us.anthropic.claude-opus-4-6-v1",
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        system=[{"text": system}],
        inferenceConfig={
            "maxTokens": 2000,
            "temperature": 0.3,  # Low temp for precise, structured output
        },
    )

    # Extract usage
    usage_data = response.get("usage", {})
    usage = LLMUsage(
        input_tokens=usage_data.get("inputTokens", 0),
        output_tokens=usage_data.get("outputTokens", 0),
    )

    # Extract text from response
    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])
    for block in content_blocks:
        if "text" in block:
            return block["text"], usage

    return "", usage


def optimize_skill_with_llm(
    skill_text: str,
    corrections: list[tuple[str, str, str]],
    skill_name: str,
) -> tuple[list[TextChange], LLMUsage]:
    """Optimize a skill's instructions using Bedrock Opus.

    Takes the skill body text and all correction evidence (high + low confidence),
    asks Opus to propose specific text changes that address the correction patterns.

    Returns (changes, usage) where changes are compatible with atomic_deploy().
    Returns ([], LLMUsage()) on any failure — caller should fall back to heuristic.

    Args:
        skill_text: The SKILL.md body text (below YAML frontmatter).
        corrections: List of (correction_text, action_type, confidence) tuples.
        skill_name: Skill name for prompt context.
    """
    empty = ([], LLMUsage())

    if not corrections:
        return empty

    prompt = _build_prompt(skill_text, corrections, skill_name)

    try:
        response_text, usage = _call_bedrock_opus(prompt)
        if not response_text:
            logger.warning("LLM optimizer: empty response from Bedrock for %s", skill_name)
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
