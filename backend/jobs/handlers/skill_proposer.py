"""
Autonomous Skill Proposer — L4.1 Core Engine capability.

When weekly maintenance detects recurring capability gaps (>=3 occurrences,
priority "high"), this handler generates SKILL.md proposals using Bedrock
Opus 4.6. Skill creation is a reasoning-heavy task — Opus produces
significantly better skill architecture, guardrails, and edge case handling.

Proposals are written to .artifacts/skill-proposals/ for human review.
Never auto-deployed. Surfaced in session briefing as [skill-proposal].

Triggered by: weekly-maintenance (after memory_health + ddd_refresh).
Cost: ~$0.20/run (Opus 4.6, ~8K input, ~3K output). Only runs when
qualifying gaps exist — most weeks: $0.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from ..paths import SWARMWS, PROJECTS_DIR

logger = logging.getLogger("swarm.jobs.skill_proposer")

# Opus 4.6 for skill creation — needs best reasoning for architecture decisions
MODEL_ID = "us.anthropic.claude-opus-4-6-v1"
MAX_OUTPUT_TOKENS = 4096

# Gate thresholds
MIN_OCCURRENCES = 3
MIN_CONFIDENCE = 6
MAX_PROPOSALS_PER_RUN = 1  # Scarcity forces quality


def run_skill_proposer(
    gaps: list[dict] | None = None,
    dry_run: bool = False,
) -> dict:
    """Generate skill proposals for qualifying capability gaps.

    Args:
        gaps: Capability gaps from memory_health. If None, reads from
              health_findings.json (normal path from maintenance job).
        dry_run: If True, skip LLM call, log what would happen.

    Returns summary dict with proposals written.
    """
    logger.info("Skill proposer starting")

    # 1. Get gaps (from arg or health_findings.json)
    if gaps is None:
        gaps = _load_gaps_from_findings()

    if not gaps:
        return {"status": "skipped", "reason": "no capability gaps detected"}

    # 2. Filter to qualifying gaps
    qualifying = _filter_qualifying_gaps(gaps)
    if not qualifying:
        return {
            "status": "skipped",
            "reason": f"{len(gaps)} gaps found, none qualify (need >={MIN_OCCURRENCES}x, priority high, action=build skill)",
            "gaps_checked": len(gaps),
        }

    # 3. Pick the highest-priority gap (max 1 per run)
    target = qualifying[0]
    logger.info(
        "Targeting gap: '%s' (%dx, priority=%s)",
        target.get("pattern", "?"), target.get("occurrences", 0),
        target.get("priority", "?"),
    )

    if dry_run:
        return {
            "status": "dry_run",
            "target_gap": target.get("pattern", ""),
            "would_propose": True,
        }

    # 4. Check for existing skill coverage
    existing_match = _find_existing_skill(target)
    if existing_match:
        logger.info("Gap already covered by skill: %s", existing_match)
        return {
            "status": "skipped",
            "reason": f"Gap covered by existing skill: {existing_match}",
            "target_gap": target.get("pattern", ""),
        }

    # 5. Gather context for LLM
    context = _gather_skill_context(target)

    # 6. Call Opus 4.6
    try:
        proposal = _generate_skill_proposal(target, context)
    except Exception as e:
        logger.error("Opus call failed: %s", e)
        return {"status": "error", "error": str(e)}

    # 7. Confidence gate
    confidence = proposal.get("confidence", 0)
    if confidence < MIN_CONFIDENCE:
        logger.info("Proposal confidence %d < %d, discarding", confidence, MIN_CONFIDENCE)
        return {
            "status": "low_confidence",
            "confidence": confidence,
            "target_gap": target.get("pattern", ""),
            "reasoning": proposal.get("reasoning", ""),
        }

    # 8. Write proposal
    skill_name = proposal.get("skill_name", "s_unknown")
    _write_skill_proposal(skill_name, proposal, target)

    return {
        "status": "success",
        "skill_name": skill_name,
        "confidence": confidence,
        "target_gap": target.get("pattern", ""),
        "summary": f"Proposed skill '{skill_name}' for gap: {target.get('pattern', '')}",
    }


# ---------------------------------------------------------------------------
# Gap loading & filtering
# ---------------------------------------------------------------------------

def _load_gaps_from_findings() -> list[dict]:
    """Read capability gaps from health_findings.json."""
    findings_path = SWARMWS / "Services" / "swarm-jobs" / "health_findings.json"
    if not findings_path.exists():
        return []
    try:
        data = json.loads(findings_path.read_text(encoding="utf-8"))
        return data.get("memory_health", {}).get("capability_gaps", [])
    except (json.JSONDecodeError, OSError):
        return []


def _filter_qualifying_gaps(gaps: list[dict]) -> list[dict]:
    """Filter gaps that qualify for skill proposal.

    Qualifying = high priority + enough occurrences + action is "build skill".
    Sorted by occurrences descending (most impactful first).
    """
    qualifying = []
    for gap in gaps:
        occurrences = gap.get("occurrences", 0)
        priority = gap.get("priority", "low")
        action = gap.get("suggested_action", "").lower()

        if occurrences < MIN_OCCURRENCES:
            continue
        if priority not in ("high", "critical"):
            continue
        if "skill" not in action and "build" not in action:
            continue

        qualifying.append(gap)

    qualifying.sort(key=lambda g: g.get("occurrences", 0), reverse=True)
    return qualifying[:MAX_PROPOSALS_PER_RUN]


# ---------------------------------------------------------------------------
# Existing skill dedup
# ---------------------------------------------------------------------------

def _find_existing_skill(gap: dict) -> str | None:
    """Check if an existing skill already covers this gap.

    Matches gap pattern keywords against skill trigger patterns.
    Returns skill name if match found, None otherwise.
    """
    pattern = gap.get("pattern", "").lower()
    # Extract significant words (>3 chars, not stopwords)
    stopwords = {"the", "and", "for", "with", "that", "this", "from", "when", "into"}
    keywords = {
        w for w in re.split(r"\W+", pattern)
        if len(w) > 3 and w not in stopwords
    }

    if not keywords:
        return None

    skills_dir = Path(__file__).resolve().parents[2] / "skills"
    if not skills_dir.is_dir():
        return None

    for skill_dir in skills_dir.iterdir():
        if not skill_dir.is_dir() or not skill_dir.name.startswith("s_"):
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        try:
            # Read just the frontmatter (first 500 chars) for trigger patterns
            header = skill_md.read_text(encoding="utf-8")[:500].lower()
            matches = sum(1 for kw in keywords if kw in header)
            # If >40% of gap keywords match a skill's description, it's covered
            if matches >= max(len(keywords) * 0.4, 2):
                return skill_dir.name
        except (OSError, UnicodeDecodeError):
            continue

    return None


# ---------------------------------------------------------------------------
# Context gathering
# ---------------------------------------------------------------------------

def _gather_skill_context(gap: dict) -> dict:
    """Gather context for the LLM to generate a high-quality skill."""
    context: dict = {}

    # Skill template (from s_skill-builder)
    templates_path = (
        Path(__file__).resolve().parents[2]
        / "skills" / "s_skill-builder" / "TEMPLATES.md"
    )
    if templates_path.exists():
        # Just the simple template (first one)
        content = templates_path.read_text(encoding="utf-8")
        # Extract Template 1 only
        marker = "## Template 2:"
        idx = content.find(marker)
        context["skill_template"] = content[:idx].strip() if idx > 0 else content[:3000]

    # 3 example skills (diverse types) for style reference
    examples: list[str] = []
    example_skills = ["s_summarize", "s_weather", "s_radar-todo"]
    skills_dir = Path(__file__).resolve().parents[2] / "skills"
    for name in example_skills:
        skill_path = skills_dir / name / "SKILL.md"
        if skill_path.exists():
            # First 80 lines — enough to show structure
            lines = skill_path.read_text(encoding="utf-8").splitlines()[:80]
            examples.append(f"### Example: {name}\n" + "\n".join(lines))

    context["example_skills"] = "\n\n".join(examples)

    # Existing skill triggers (for dedup awareness)
    triggers: list[str] = []
    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir() or not skill_dir.name.startswith("s_"):
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            header = skill_md.read_text(encoding="utf-8")[:300]
            # Extract TRIGGER line
            for line in header.splitlines():
                if "TRIGGER:" in line:
                    triggers.append(f"  - {skill_dir.name}: {line.strip()[:120]}")
                    break
        except (OSError, UnicodeDecodeError):
            continue

    context["existing_triggers"] = "\n".join(triggers[:30])

    return context


# ---------------------------------------------------------------------------
# LLM proposal generation
# ---------------------------------------------------------------------------

def _generate_skill_proposal(gap: dict, context: dict) -> dict:
    """Call Bedrock Opus 4.6 to generate a SKILL.md proposal."""
    import boto3

    prompt = _build_prompt(gap, context)

    client = boto3.client("bedrock-runtime", region_name="us-west-2")
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    })

    response = client.invoke_model(modelId=MODEL_ID, body=body)
    result = json.loads(response["body"].read())

    content = result["content"][0]["text"]
    input_tokens = result.get("usage", {}).get("input_tokens", 0)
    output_tokens = result.get("usage", {}).get("output_tokens", 0)

    # Opus pricing: $15/1M input, $75/1M output
    cost = input_tokens * 15.0 / 1_000_000 + output_tokens * 75.0 / 1_000_000

    logger.info(
        "Skill proposer LLM (Opus): %d input, %d output tokens (~$%.3f)",
        input_tokens, output_tokens, cost,
    )

    # Parse JSON
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse skill proposal JSON")
        return {"raw_response": text, "parse_error": True, "confidence": 0}


def _build_prompt(gap: dict, context: dict) -> str:
    """Build the skill creation prompt for Opus."""
    evidence = gap.get("evidence", [])
    evidence_text = "\n".join(f"  - {e}" for e in evidence[:10]) if evidence else "(no evidence)"

    return f"""You are SwarmAI's autonomous skill architect. A recurring capability gap has been detected. Your job is to design a new skill that permanently eliminates this class of problems.

## Capability Gap

**Pattern:** {gap.get('pattern', '?')}
**Occurrences:** {gap.get('occurrences', 0)}
**Priority:** {gap.get('priority', '?')}
**Suggested action:** {gap.get('suggested_action', '?')}

**Evidence (from DailyActivity sessions):**
{evidence_text}

## Skill Template (follow this structure)

{context.get('skill_template', '(template not available)')}

## Example Skills (match this style and quality)

{context.get('example_skills', '(no examples)')}

## Existing Skills (DO NOT duplicate)

These triggers are already covered:
{context.get('existing_triggers', '(none)')}

## Your Task

Design a SKILL.md that addresses the capability gap. The skill should:
1. Activate on natural trigger phrases related to the gap pattern
2. Provide clear step-by-step instructions the agent follows
3. Include guardrails that prevent the specific failure patterns from the evidence
4. Include 2-3 concrete examples based on the evidence
5. List sibling skills (what NOT to use this for)

Output a single JSON object:

{{
  "skill_name": "s_descriptive-name",
  "skill_md": "<full SKILL.md content as a string>",
  "trigger_patterns": ["phrase1", "phrase2", "phrase3"],
  "confidence": 8,
  "reasoning": "Why this skill addresses the gap and why the specific guardrails were chosen"
}}

Rules:
- skill_name must start with "s_" and use kebab-case
- skill_md must include the YAML frontmatter with name, description, TRIGGER, DO NOT USE
- Include a "Why?" line explaining the problem this solves
- Include Guardrails section with specific DO NOT rules based on the evidence
- confidence 1-10: how well does this skill address the root cause?
- If the gap is too vague to build a useful skill, set confidence to 3 and explain why

Only output the JSON, nothing else."""


# ---------------------------------------------------------------------------
# Proposal output
# ---------------------------------------------------------------------------

def _write_skill_proposal(
    skill_name: str,
    proposal: dict,
    gap: dict,
) -> None:
    """Write skill proposal to .artifacts/skill-proposals/."""
    # Write to SwarmAI project artifacts
    proposals_dir = PROJECTS_DIR / "SwarmAI" / ".artifacts" / "skill-proposals" / skill_name
    proposals_dir.mkdir(parents=True, exist_ok=True)

    # Write SKILL.md
    skill_md = proposal.get("skill_md", "")
    if skill_md:
        (proposals_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    # Write metadata
    metadata = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": MODEL_ID,
        "gap_pattern": gap.get("pattern", ""),
        "gap_occurrences": gap.get("occurrences", 0),
        "gap_evidence": gap.get("evidence", []),
        "confidence": proposal.get("confidence", 0),
        "reasoning": proposal.get("reasoning", ""),
        "trigger_patterns": proposal.get("trigger_patterns", []),
    }
    (proposals_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    logger.info(
        "Wrote skill proposal: %s (confidence=%d, gap='%s')",
        skill_name, proposal.get("confidence", 0), gap.get("pattern", "")[:60],
    )
