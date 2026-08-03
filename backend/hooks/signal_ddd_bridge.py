"""Signal DDD Bridge — Channel 4 of DDD Cultivation.

After signal_digest job produces daily digest, bridges high-relevance
signals (score >= 0.8) into PRODUCT.md proposals. Detects competitive
moves, market shifts, and technology trends relevant to active projects.

Also serves Channel 2 (learn-content): when invoked directly with a
knowledge card summary, scores relevance and proposes enrichment.

Trigger: Called by context_health_hook during daily deep check (reads
signal_digest.json). Not a session hook — runs independently.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Signal relevance threshold for DDD proposal generation
_MIN_RELEVANCE_SCORE = 0.8

# Maximum proposals per digest run (avoid flooding)
_MAX_PROPOSALS_PER_RUN = 3

# Keywords that indicate PRODUCT.md relevance (competitive/strategic)
_PRODUCT_KEYWORDS = {
    "competitor", "launch", "funding", "acquisition", "market",
    "pricing", "enterprise", "startup", "pivot", "strategy",
    "moat", "disruption", "trend", "adoption",
}

# Keywords that indicate TECH.md relevance (patterns/libraries/architecture)
_TECH_KEYWORDS = {
    "framework", "library", "architecture", "pattern", "protocol",
    "sdk", "api", "performance", "scaling", "migration",
    "security", "vulnerability", "deprecated", "release",
}


def bridge_signals_to_ddd(workspace_path: str) -> int:
    """Read signal_digest.json, bridge high-relevance signals to DDD proposals.

    Returns the number of proposals generated.
    """
    from core.ddd_cultivation import CultivationProposal, write_proposal

    root = Path(workspace_path)
    digest_path = root / "Services" / "signals" / "signal_digest.json"

    if not digest_path.exists():
        return 0

    try:
        data = json.loads(digest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("signal_ddd_bridge: cannot read digest: %s", exc)
        return 0

    items = data.get("items", [])
    if not items:
        return 0

    # Find SwarmAI project dir (primary target)
    project_dir = root / "Projects" / "SwarmAI"
    if not project_dir.exists():
        return 0

    proposals_generated = 0

    for item in items:
        if proposals_generated >= _MAX_PROPOSALS_PER_RUN:
            break

        # Support both "score" (test) and "relevance_score" (production)
        score = item.get("relevance_score") or item.get("score") or 0
        if score < _MIN_RELEVANCE_SCORE:
            continue

        title = item.get("title", "")
        summary = item.get("summary", "") or item.get("content", "")
        source_url = item.get("url", "") or item.get("source", "")

        if not title or not summary:
            continue

        # Determine target doc + section based on content keywords
        target_doc, target_section = _classify_signal(title, summary)

        content = f"**{title}** (relevance: {score:.1f})\n{summary[:200]}"
        if source_url:
            content += f"\nSource: {source_url}"

        proposal = CultivationProposal(
            target_doc=target_doc,
            target_section=target_section,
            content=content,
            source_run_id=f"signal_digest:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            confidence=min(score, 0.95),  # Cap at 0.95 — signals need human validation
            source_stage="signal_ddd_bridge",
        )

        write_proposal(proposal, project_dir)
        proposals_generated += 1
        logger.info(
            "signal_ddd_bridge: proposal for %s#%s (score=%.2f, title=%s)",
            target_doc, target_section, score, title[:40],
        )

    return proposals_generated


def bridge_learned_content_to_ddd(
    title: str,
    summary: str,
    source_url: str,
    workspace_path: str,
    project: str = "SwarmAI",
) -> bool:
    """Bridge a learned knowledge card to DDD proposal (Channel 2).

    Called by s_learn-content skill after saving a knowledge card.
    Scores relevance based on keywords and generates proposal if relevant.

    Returns True if a proposal was generated.
    """
    from core.ddd_cultivation import CultivationProposal, write_proposal

    root = Path(workspace_path)
    project_dir = root / "Projects" / project
    if not project_dir.exists():
        return False

    # Score relevance (PE-5: reuse shared helper)
    product_hits, tech_hits = _score_keywords(title, summary)
    total_hits = product_hits + tech_hits
    if total_hits < 2:
        # Not relevant enough for DDD
        return False

    # Determine target
    target_doc, target_section = _classify_signal(title, summary)

    # Confidence based on keyword density
    confidence = min(0.6 + (total_hits * 0.05), 0.9)

    content = f"**{title}**\n{summary[:300]}"
    if source_url:
        content += f"\nSource: {source_url}"

    proposal = CultivationProposal(
        target_doc=target_doc,
        target_section=target_section,
        content=content,
        source_run_id=f"learn_content:{source_url[:60] if source_url else title[:40]}",
        confidence=confidence,
        source_stage="learn_content_bridge",
    )

    write_proposal(proposal, project_dir)
    logger.info(
        "signal_ddd_bridge: learn-content proposal for %s#%s (conf=%.2f)",
        target_doc, target_section, confidence,
    )
    return True


def _score_keywords(title: str, summary: str) -> tuple[int, int]:
    """Score text against PRODUCT and TECH keyword sets.

    PE-5 fix: single helper for keyword scoring (DRY).
    Returns (product_hits, tech_hits).
    """
    text = f"{title} {summary}".lower()
    product_hits = sum(1 for kw in _PRODUCT_KEYWORDS if kw in text)
    tech_hits = sum(1 for kw in _TECH_KEYWORDS if kw in text)
    return product_hits, tech_hits


def _classify_signal(title: str, summary: str) -> tuple[str, str]:
    """Classify a signal into target_doc + target_section.

    Returns (target_doc, target_section).
    """
    text = f"{title} {summary}".lower()
    product_hits, tech_hits = _score_keywords(title, summary)

    if tech_hits > product_hits:
        # Technical content → TECH.md
        if any(kw in text for kw in ("pattern", "architecture", "design")):
            return "TECH.md", "Architecture"
        return "TECH.md", "Key Subsystems"
    else:
        # Strategic/competitive → PRODUCT.md
        if any(kw in text for kw in ("competitor", "market", "disruption")):
            return "PRODUCT.md", "What Makes SwarmAI Different"
        return "PRODUCT.md", "Vision"
