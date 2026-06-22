"""Evolution Pipeline v3 Phase 1 — judgment classifier.

Turns a raw correction record (from ``corrections.jsonl``) into a
:class:`JudgmentClassification` that names the *axis* of failure and, for the
cognitive axis, the SOUL correction CLASS + parent principle.

Two tiers, by record type:
  - ``tool_failure``     -> Tier-1 mechanical. Operational axis. No LLM.
  - ``user_correction``  -> Tier-2 LLM (Bedrock Sonnet). Cognitive axis.

The two axes are orthogonal (design §4):
  - operational = *what* skill produced bad output (blast radius confined to one tool)
  - cognitive   = *why* — a judgment pattern (CLASS_A/B/C from SOUL/EVOLUTION.md)

Safety invariants (design §9):
  - NEVER writes to SOUL/AGENT/STEERING. This module only *classifies*.
  - Degrades to ``None`` on ANY failure (LLM down, malformed output, bad record).
    The caller (maintenance hook) must treat None as "skip, log, continue".
  - Tier-2 LLM fires ONLY for ``user_correction`` records — never for the 92%
    ``tool_failure`` noise (bounded cost).

Anti-repetition guard (run_76273219): a keyword-only classifier scored 100%
false-negative on real corrections. Tier-2 uses an LLM on the real prompt text;
tests validate against real-corpus record shapes, not synthetic magic words.

Key public symbols:
    JudgmentClassification — the classification result dataclass
    classify_correction    — classify one record -> JudgmentClassification | None

Design: Knowledge/Designs/2026-06-22-evolution-pipeline-v3-governance-routing-design.md
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Sonnet — locked by XG 2026-06-22 (Chinese judgment-nuance recall > Haiku).
_SONNET_MODEL_ID = "us.anthropic.claude-sonnet-4-6"

# Closed label space — mirrors SOUL/EVOLUTION.md taxonomy (design §6).
_VALID_CLASSES = {"CLASS_A", "CLASS_B", "CLASS_C"}
_VALID_PRINCIPLES = {"P1", "P2", "P3", "P4", "P5"}

_CLASSIFY_SYSTEM = (
    "You classify a single developer self-correction into a fixed taxonomy. "
    "Output ONLY a compact JSON object, no prose. Schema: "
    '{"axis":"cognitive|operational","class_name":"CLASS_A|CLASS_B|CLASS_C|null",'
    '"parent_principle":"P1|P2|P3|P4|P5|null","confidence":0.0-1.0,"evidence":"<=120 chars"}. '
    "CLASS_A = confidence overrode process (shipped untested / skipped a gate / "
    "'I wrote it so it works'). CLASS_B = symptom-fix or inference without verifying "
    "(coded against unverified API, wrong root cause). CLASS_C = shallow / wrong-layer "
    "execution (README-level research, fix scope mismatch). "
    "Principles: P1 verify-don't-infer, P2 done=tried-to-break-it, P3 understand-before-output, "
    "P4 own-it-solve-it, P5 cognition-serves-rules. "
    "If the text is not a genuine behavioral correction, return axis=operational, class_name=null."
)


@dataclass
class JudgmentClassification:
    """Result of classifying one correction record.

    ``counter_state`` enforces the asymmetric-autonomy decision (design §7):
      - "counted"          -> operational/low-risk; the router may record() immediately.
      - "pending_confirm"  -> cognitive/CLASS_*; the router parks it for human confirm,
                              NEVER auto-increments the 3x counter.
    """

    correction_ref: str
    axis: str  # "operational" | "cognitive"
    class_name: str | None  # CLASS_A/B/C, or None for operational
    parent_principle: str | None  # P1-P5, or None
    skill_spread: list[str] = field(default_factory=list)
    blast_radius: int = 0
    evidence: list[str] = field(default_factory=list)
    tier: str = "mechanical"  # "mechanical" | "llm"
    confidence: float = 0.0
    counter_state: str = "counted"  # "counted" | "pending_confirm"


def _ref(record: dict) -> str:
    """Stable reference for a record: ``ts:session-prefix``."""
    ts = record.get("ts", 0)
    sid = str(record.get("session_id", ""))[:8]
    return f"{ts}:{sid}"


def _classify_operational(record: dict) -> JudgmentClassification:
    """Tier-1 mechanical: a tool_failure is operational. No LLM.

    blast_radius for a single record is 1 (one tool). Cross-record clustering
    (blast_radius >= 3 => promote to cognitive) is a Phase-2 concern; Phase 1
    classifies one record at a time, so a lone tool_failure is operational.
    """
    tool = record.get("tool", "")
    return JudgmentClassification(
        correction_ref=_ref(record),
        axis="operational",
        class_name=None,
        parent_principle=None,
        skill_spread=[tool] if tool else [],
        blast_radius=1,
        evidence=[str(record.get("error", ""))[:120]],
        tier="mechanical",
        confidence=0.5,
        counter_state="counted",  # operational auto-counts (low stakes)
    )


def _resolve_client(bedrock_client):
    """Return a converse-capable Bedrock client, reusing the shared builder.

    Raises on failure — caller wraps in the degrade-to-None guard.
    """
    if bedrock_client is not None:
        return bedrock_client
    from core.llm_optimizer import _get_bedrock_client  # lazy: avoid import cost/cycles

    return _get_bedrock_client()


def _invoke_sonnet(client, prompt: str) -> str:
    """Invoke Bedrock Sonnet via converse, return the text block. Sync API."""
    response = client.converse(
        modelId=_SONNET_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        system=[{"text": _CLASSIFY_SYSTEM}],
        inferenceConfig={"maxTokens": 400, "temperature": 0.2},
    )
    content = response.get("output", {}).get("message", {}).get("content", [])
    for block in content:
        if "text" in block:
            return block["text"]
    return ""


def _parse_label(raw: str) -> dict:
    """Parse the LLM JSON label. Tolerates ```json fences. Raises on garbage."""
    text = raw.strip()
    if text.startswith("```"):
        # strip ```json ... ``` fence
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in LLM response")
    # raw_decode parses the FIRST balanced object from `start`, ignoring any
    # trailing prose or a second object (multi-object / streamed responses).
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    if not isinstance(obj, dict):
        raise ValueError("LLM response is not a JSON object")
    return obj


def _classify_cognitive(record: dict, bedrock_client) -> JudgmentClassification | None:
    """Tier-2 LLM: a user_correction -> cognitive class + principle.

    Returns None on any failure (no client, LLM error, malformed output).
    """
    prompt = record.get("prompt", "")
    if not prompt:
        return None

    client = _resolve_client(bedrock_client)
    raw = _invoke_sonnet(client, prompt)
    label = _parse_label(raw)

    axis = label.get("axis", "cognitive")
    class_name = label.get("class_name")
    raw_class_valid = class_name in _VALID_CLASSES
    if not raw_class_valid:
        class_name = None
    principle = label.get("parent_principle")
    if principle not in _VALID_PRINCIPLES:
        principle = None

    # Adversarial #3: a genuinely-cognitive correction with a MALFORMED class
    # (LLM returned axis=cognitive but class_name not in the valid set) must NOT
    # be silently downgraded to operational + auto-counted. Degrade to None so it
    # is skipped + logged, not mis-counted in the wrong direction.
    if axis == "cognitive" and not raw_class_valid:
        logger.debug(
            "cognitive correction with invalid class_name %r -> degrade to None",
            label.get("class_name"),
        )
        return None

    # If the LLM judged this not a real behavioral correction, it returns
    # axis=operational/class=null. Honor that — operational auto-counts.
    is_cognitive = axis == "cognitive" and class_name is not None
    return JudgmentClassification(
        correction_ref=_ref(record),
        axis="cognitive" if is_cognitive else "operational",
        class_name=class_name,
        parent_principle=principle,
        skill_spread=[],  # user_correction has no skill field (design §3 finding)
        blast_radius=0,
        evidence=[str(label.get("evidence", ""))[:120]],
        tier="llm",
        confidence=float(label.get("confidence", 0.0) or 0.0),
        counter_state="pending_confirm" if is_cognitive else "counted",
    )


def classify_correction(
    record: dict,
    evolution_classes: list[str] | None = None,
    bedrock_client=None,
) -> JudgmentClassification | None:
    """Classify a single correction record. Returns None on any failure.

    Args:
        record: one parsed corrections.jsonl entry (dict with a ``type`` field).
        evolution_classes: known CLASS names from EVOLUTION.md (reserved for
            Phase-2 recurrence matching; accepted now for a stable signature).
        bedrock_client: optional pre-built converse client (tests inject a mock);
            if None and the cognitive path is taken, the shared builder is used.

    Returns:
        JudgmentClassification, or None if the record is unclassifiable / the
        classifier failed (degrade-to-log — the caller must continue).
    """
    try:
        rtype = record.get("type")
        if rtype == "tool_failure":
            return _classify_operational(record)
        if rtype == "user_correction":
            return _classify_cognitive(record, bedrock_client)
        # subagent_finding and unknown types: not classifiable in Phase 1.
        return None
    except Exception as exc:  # noqa: BLE001 — degrade-to-log is the contract
        logger.debug(
            "judgment_classifier degraded to None for %s: %s: %s",
            record.get("type"),
            type(exc).__name__,
            exc,
        )
        return None
