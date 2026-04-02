"""
Autonomous DDD Refresh — L4 Core Engine capability.

Reads recent code changes (git log + diff-stat), current DDD docs, and
engine_metrics DDD suggestions. Calls Bedrock Sonnet 4.6 to produce
updated TECH.md/IMPROVEMENT.md sections. Writes proposals to the project's
.artifacts/ directory for human review at next session.

This is NOT auto-apply. The output is a markdown proposal that appears
in the session briefing: "DDD refresh proposal ready for review."

Triggered by: weekly-maintenance (after memory_health) or on-demand.
Cost: ~$0.05/run (Sonnet 4.6, ~8K input, ~3K output).
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..paths import SWARMWS, PROJECTS_DIR

logger = logging.getLogger("swarm.jobs.ddd_refresh")

MAX_OUTPUT_TOKENS = 4096
_GIT_TIMEOUT = 10


def run_ddd_refresh(dry_run: bool = False) -> dict:
    """Scan projects for stale DDD docs and produce refresh proposals.

    Returns summary dict with proposals written and actions taken.
    """
    logger.info("DDD refresh starting")

    if not PROJECTS_DIR.is_dir():
        return {"status": "skipped", "reason": "no Projects/ directory"}

    proposals_written = 0
    projects_checked = 0

    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue

        tech_path = project_dir / "TECH.md"
        if not tech_path.exists():
            continue

        projects_checked += 1

        # Check if TECH.md is stale (>7 days old with recent code commits)
        stale_info = _check_staleness(project_dir)
        if not stale_info["stale"]:
            logger.info("Project %s: DDD docs are fresh, skipping", project_dir.name)
            continue

        logger.info(
            "Project %s: TECH.md is %dd old with %d recent commits — generating proposal",
            project_dir.name, stale_info["age_days"], stale_info["commit_count"],
        )

        if dry_run:
            logger.info("[DRY RUN] Would generate proposal for %s", project_dir.name)
            continue

        # Gather context
        context = _gather_project_context(project_dir, stale_info)

        # Call LLM
        try:
            proposal = _generate_proposal(project_dir.name, context)
        except Exception as e:
            logger.error("LLM call failed for %s: %s", project_dir.name, e)
            continue

        # Write proposal
        if proposal and not proposal.get("no_changes"):
            _write_proposal(project_dir, proposal)
            proposals_written += 1

    summary = (
        f"Checked {projects_checked} projects, wrote {proposals_written} proposals"
        if projects_checked > 0
        else "No projects with DDD docs found"
    )
    logger.info("DDD refresh complete: %s", summary)

    return {
        "status": "success",
        "projects_checked": projects_checked,
        "proposals_written": proposals_written,
        "summary": summary,
    }


# -- Staleness Detection --


def _check_staleness(project_dir: Path) -> dict:
    """Check if project DDD docs need refresh."""
    tech_path = project_dir / "TECH.md"
    now = datetime.now()
    age_days = (now - datetime.fromtimestamp(tech_path.stat().st_mtime)).days

    # Not stale if updated within 7 days
    if age_days <= 7:
        return {"stale": False, "age_days": age_days, "commit_count": 0}

    # Check for recent code commits (in swarmai codebase)
    commit_count = _count_recent_commits(7)

    # Stale = doc is old AND there have been code changes
    return {
        "stale": commit_count >= 3,  # At least 3 commits to justify a refresh
        "age_days": age_days,
        "commit_count": commit_count,
    }


def _count_recent_commits(days: int) -> int:
    """Count recent commits in the swarmai codebase."""
    swarmai_root = _find_swarmai_root()
    if not swarmai_root:
        return 0
    try:
        result = subprocess.run(
            ["git", "log", f"--since={days} days ago", "--oneline"],
            cwd=str(swarmai_root), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT,
        )
        return len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0
    except (subprocess.TimeoutExpired, OSError):
        return 0


# -- Context Gathering --


def _gather_project_context(project_dir: Path, stale_info: dict) -> dict:
    """Gather all context needed for the LLM to produce a proposal."""
    context: dict = {
        "project_name": project_dir.name,
        "age_days": stale_info["age_days"],
        "commit_count": stale_info["commit_count"],
    }

    # Current TECH.md (capped)
    tech_path = project_dir / "TECH.md"
    context["current_tech_md"] = tech_path.read_text(encoding="utf-8")[:10000]

    # Current IMPROVEMENT.md if exists
    improvement_path = project_dir / "IMPROVEMENT.md"
    if improvement_path.exists():
        context["current_improvement_md"] = improvement_path.read_text(encoding="utf-8")[:5000]

    # Recent git log from swarmai codebase
    swarmai_root = _find_swarmai_root()
    if swarmai_root:
        try:
            result = subprocess.run(
                ["git", "log", "--since=7 days ago", "--oneline", "--stat", "-30"],
                cwd=str(swarmai_root), capture_output=True, text=True,
                timeout=_GIT_TIMEOUT,
            )
            context["git_log"] = result.stdout.strip()[:5000] if result.stdout else ""
        except (subprocess.TimeoutExpired, OSError):
            context["git_log"] = ""

        # File changes summary (what was added/modified/deleted)
        try:
            result = subprocess.run(
                ["git", "diff", "--stat", f"HEAD~{min(stale_info['commit_count'], 30)}"],
                cwd=str(swarmai_root), capture_output=True, text=True,
                timeout=_GIT_TIMEOUT,
            )
            context["diff_stat"] = result.stdout.strip()[:3000] if result.stdout else ""
        except (subprocess.TimeoutExpired, OSError):
            context["diff_stat"] = ""

    # DDD suggestions from engine_metrics
    try:
        from core.engine_metrics import collect_ddd_change_suggestions
        suggestions = collect_ddd_change_suggestions(SWARMWS)
        project_suggestions = [s for s in suggestions if s.get("project") == project_dir.name]
        context["ddd_suggestions"] = project_suggestions
    except Exception:
        context["ddd_suggestions"] = []

    return context


# -- LLM Proposal Generation --


def _generate_proposal(project_name: str, context: dict) -> dict:
    """Call Bedrock Sonnet 4.6 to generate DDD doc update proposals."""
    from jobs.bedrock import invoke

    prompt = _build_prompt(project_name, context)

    content, input_tokens, output_tokens = invoke(
        prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.2,
    )

    logger.info(
        "DDD refresh LLM: %d input tokens, %d output tokens (~$%.3f)",
        input_tokens, output_tokens,
        input_tokens * 3.0 / 1_000_000 + output_tokens * 15.0 / 1_000_000,
    )

    # Parse JSON response
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse DDD refresh JSON, returning raw")
        return {"raw_response": text, "parse_error": True}


def _build_prompt(project_name: str, context: dict) -> str:
    """Build the DDD refresh prompt."""
    suggestions_text = ""
    if context.get("ddd_suggestions"):
        items = [
            f"  - {s['doc']} ({s['section']}): {s['reason']}"
            for s in context["ddd_suggestions"]
        ]
        suggestions_text = "## Detected Changes\n" + "\n".join(items)

    return f"""You are SwarmAI's autonomous DDD refresh system. Your job is to update project documentation to match the current codebase state.

## Project: {project_name}

## Current TECH.md
{context.get('current_tech_md', '(not available)')}

## Recent Git Activity (last 7 days)
{context.get('git_log', '(no commits)')}

## File Change Summary
{context.get('diff_stat', '(not available)')}

{suggestions_text}

## Your Task

Compare the current TECH.md against the recent code changes. Produce a JSON object with proposed updates.

Rules:
- Only propose changes where the doc is ACTUALLY wrong or missing significant new information
- Preserve the existing structure and style of TECH.md
- Don't rewrite sections that are still accurate
- Be specific: show the exact text to add/modify, not vague suggestions
- If no changes are needed, return {{"no_changes": true, "reason": "docs are current"}}

Output JSON:
{{
  "no_changes": false,
  "summary": "1-2 sentence description of what changed",
  "tech_md_updates": [
    {{
      "section": "## Section Name",
      "action": "add" | "modify" | "remove",
      "current_text": "existing text to find (for modify/remove)",
      "proposed_text": "new text to insert/replace with",
      "reason": "why this change is needed"
    }}
  ],
  "improvement_md_updates": [
    {{
      "section": "## What Worked | ## What Failed | ## Known Issues",
      "entry": "- YYYY-MM-DD: lesson learned from recent work",
      "reason": "why this should be recorded"
    }}
  ],
  "confidence": 8
}}

Only output the JSON, nothing else."""


# -- Proposal Output --


def _write_proposal(project_dir: Path, proposal: dict) -> None:
    """Write proposal to project .artifacts/ and a summary to JobResults."""
    artifacts_dir = project_dir / ".artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d")
    proposal_path = artifacts_dir / f"ddd-refresh-{timestamp}.md"

    # Build readable markdown from JSON proposal
    lines = [
        f"# DDD Refresh Proposal — {project_dir.name}",
        f"**Generated:** {datetime.now().isoformat(timespec='seconds')}",
        f"**Confidence:** {proposal.get('confidence', '?')}/10",
        f"**Summary:** {proposal.get('summary', 'No summary')}",
        "",
    ]

    tech_updates = proposal.get("tech_md_updates", [])
    if tech_updates:
        lines.append("## TECH.md Updates")
        lines.append("")
        for i, update in enumerate(tech_updates, 1):
            lines.append(f"### {i}. {update.get('section', '?')} ({update.get('action', '?')})")
            lines.append(f"**Reason:** {update.get('reason', '?')}")
            if update.get("current_text"):
                lines.append(f"\n**Current:**\n```\n{update['current_text']}\n```")
            if update.get("proposed_text"):
                lines.append(f"\n**Proposed:**\n```\n{update['proposed_text']}\n```")
            lines.append("")

    improvement_updates = proposal.get("improvement_md_updates", [])
    if improvement_updates:
        lines.append("## IMPROVEMENT.md Updates")
        lines.append("")
        for update in improvement_updates:
            lines.append(f"- **{update.get('section', '?')}:** {update.get('entry', '?')}")
            lines.append(f"  _Reason: {update.get('reason', '?')}_")
        lines.append("")

    lines.append("---")
    lines.append("_Review this proposal and apply changes manually or ask Swarm to apply them._")

    proposal_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote DDD refresh proposal to %s", proposal_path)

    # Also save raw JSON for programmatic consumption
    json_path = artifacts_dir / f"ddd-refresh-{timestamp}.json"
    json_path.write_text(json.dumps(proposal, indent=2, ensure_ascii=False), encoding="utf-8")


# -- Helpers --


def _find_swarmai_root() -> Path | None:
    """Find the SwarmAI codebase root.

    Resolution order:
    1. SWARMAI_ROOT env var (explicit override)
    2. Relative to this file (works in dev and PyInstaller)
    3. Sibling of workspace parent (legacy layout)
    """
    import os

    env_root = os.environ.get("SWARMAI_ROOT")
    if env_root:
        p = Path(env_root)
        if (p / "backend").is_dir():
            return p

    # Relative to this source file: ddd_refresh.py → handlers/ → jobs/ → backend/ → swarmai/
    source_root = Path(__file__).resolve().parents[3]
    if (source_root / "backend").is_dir():
        return source_root

    sibling = SWARMWS.parent / "swarmai"
    if (sibling / "backend").is_dir():
        return sibling

    return None
