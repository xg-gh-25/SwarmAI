"""LLM-based skill optimization via Bedrock Opus API.

Replaces the heuristic optimizer's blind append/remove with semantic
understanding of corrections. Takes skill text + correction evidence,
asks Opus to propose specific TextChange objects.

Zero new dependencies — uses existing boto3/Bedrock connection.
Returns empty list on any failure (timeout, malformed response, API error)
so the caller can fall back to heuristic optimization.

Key public symbols:
- ``optimize_skill_with_llm``  -- Main entry point. Async.
- ``_call_bedrock_opus``       -- Low-level Bedrock invoke (mockable).
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from core.evolution_optimizer import TextChange

logger = logging.getLogger(__name__)

# Cap changes per optimization to prevent runaway modifications
MAX_CHANGES = 5

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

Return ONLY valid JSON (no markdown wrapping, no explanation):
{"changes": [{"original": "exact text to find and replace", "replacement": "new text", "reason": "why this change"}]}

For append-only changes (new instructions), use empty string for "original":
{"changes": [{"original": "", "replacement": "- New instruction here", "reason": "why"}]}
"""


def _build_prompt(skill_text: str, corrections: list[tuple[str, str, str]], skill_name: str) -> str:
    """Build the user prompt with skill text and correction evidence."""
    correction_lines = []
    for i, (text, action_type, confidence) in enumerate(corrections, 1):
        conf_label = "strong" if confidence == "high" else "weak"
        correction_lines.append(f"  {i}. [{conf_label}] ({action_type}) {text}")

    corrections_block = "\n".join(correction_lines) if correction_lines else "  (no corrections — optimize for clarity and completeness)"

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


async def _call_bedrock_opus(prompt: str, system: str = _SYSTEM_PROMPT) -> str:
    """Invoke Bedrock Opus and return the text response.

    Uses the converse API for cleaner request/response handling.
    Timeout: 30 seconds. Max tokens: 2000 (changes are small).
    """
    import boto3

    client = boto3.client("bedrock-runtime", region_name="us-east-1")

    response = client.converse(
        modelId="us.anthropic.claude-opus-4-6-v1",
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        system=[{"text": system}],
        inferenceConfig={
            "maxTokens": 2000,
            "temperature": 0.3,  # Low temp for precise, structured output
        },
    )

    # Extract text from response
    output = response.get("output", {})
    message = output.get("message", {})
    content_blocks = message.get("content", [])
    for block in content_blocks:
        if "text" in block:
            return block["text"]

    return ""


async def optimize_skill_with_llm(
    skill_text: str,
    corrections: list[tuple[str, str, str]],
    skill_name: str,
) -> list[TextChange]:
    """Optimize a skill's instructions using Bedrock Opus.

    Takes the skill body text and all correction evidence (high + low confidence),
    asks Opus to propose specific text changes that address the correction patterns.

    Returns list[TextChange] compatible with atomic_deploy().
    Returns empty list on any failure — caller should fall back to heuristic.

    Args:
        skill_text: The SKILL.md body text (below YAML frontmatter).
        corrections: List of (correction_text, action_type, confidence) tuples.
        skill_name: Skill name for prompt context.
    """
    if not corrections:
        return []

    prompt = _build_prompt(skill_text, corrections, skill_name)

    try:
        response = await _call_bedrock_opus(prompt)
        if not response:
            logger.warning("LLM optimizer: empty response from Bedrock for %s", skill_name)
            return []

        changes = _parse_llm_response(response)
        if changes:
            logger.info(
                "LLM optimizer: %d changes proposed for %s",
                len(changes), skill_name,
            )
        return changes

    except Exception as exc:
        logger.warning("LLM optimizer failed for %s: %s", skill_name, exc)
        return []
