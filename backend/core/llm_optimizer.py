"""LLM-based skill optimization via Bedrock (the configured default model).

Replaces the heuristic optimizer's blind append/remove with semantic
understanding of corrections. Takes skill text + correction evidence,
asks the model to propose specific TextChange objects.

The answer comes through jobs.bedrock.structured() (design
docs/design/2026-10-28-structured-model-output.md). Returns an empty list on any
failure (timeout, unusable reply, API error) so the caller can fall back to
heuristic optimization.

All functions are synchronous — the Converse call is a sync API, wrapping it in
async would be fake async adding complexity for no benefit.

Key public symbols:
- ``optimize_skill_with_llm``  -- Main entry point. Sync.
- ``_ask_changes``             -- The model call (mockable).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

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

Record the changes with the changes tool: each with "original" (exact text to find and
replace), "replacement" (the new text) and "reason". For an append-only change (a new
instruction), "original" is the empty string.
"""


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
Propose specific text changes to improve these instructions."""


# The answer, shaped by the API.
_S = {"type": "string"}
_CHANGES_SCHEMA = {
    "type": "object",
    "properties": {"changes": {"type": "array", "items": {
        "type": "object", "properties": {"original": _S, "replacement": _S, "reason": _S},
        "required": ["original", "replacement"]}}},
    "required": ["changes"],
}
_MAX_TOKENS = 2000  # changes are small
# Fail-fast transport: a reply still unread after this is a hang.
_READ_TIMEOUT = 60


def _changes_from(data: dict) -> list[TextChange]:
    """The usable changes of an answer: at most MAX_CHANGES, no-ops and empties dropped."""
    changes = []
    for item in data["changes"][:MAX_CHANGES]:
        original, replacement = item["original"], item["replacement"]
        if original == replacement or (not original and not replacement):
            continue
        changes.append(TextChange(original=original, replacement=replacement,
                                  reason=item.get("reason") or "LLM-proposed change"))
    return changes


def _ask_changes(prompt: str, system: str = _SYSTEM_PROMPT) -> tuple[dict, LLMUsage]:
    """The model's {"changes": [...]} for *prompt*, and the call's token usage.
    Model from config.json default_model (_resolve_bedrock_model). Raises
    jobs.bedrock.StructuredOutputError or a Bedrock error."""
    from jobs.bedrock import structured

    model_id, _ = _resolve_bedrock_model()
    raw_usage: dict = {}
    data = structured(prompt, schema=_CHANGES_SCHEMA, system=system, model_id=model_id, feature="skill.optimize",
                      max_tokens=_MAX_TOKENS, temperature=0.3, name="changes",
                      description="Record the proposed text changes.",
                      region=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1")),
                      read_timeout=_READ_TIMEOUT, max_attempts=1, usage=raw_usage)
    return data, LLMUsage(input_tokens=raw_usage["input_tokens"],
                          output_tokens=raw_usage["output_tokens"])


def optimize_skill_with_llm(
    skill_text: str,
    corrections: list[tuple[str, str, str]],
    skill_name: str,
    execution_traces: list[str] | None = None,
) -> tuple[list[TextChange], LLMUsage]:
    """Optimize a skill's instructions using the configured Bedrock model.

    Takes the skill body text and all correction evidence (high + low confidence),
    asks the model to propose specific text changes that address the correction patterns.

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

    from jobs.bedrock import StructuredOutputError

    try:
        data, usage = _ask_changes(prompt)
    except StructuredOutputError as exc:
        logger.warning("LLM optimizer reply %s for %s (%s)", exc.kind, skill_name, exc.detail)
        return empty
    except Exception as exc:
        logger.warning("LLM optimizer failed for %s: %s", skill_name, exc)
        return empty

    try:
        changes = _changes_from(data)

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
