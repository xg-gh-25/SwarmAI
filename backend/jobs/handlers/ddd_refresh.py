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
import os
import subprocess
from datetime import datetime
from pathlib import Path

from ..paths import SWARMWS, PROJECTS_DIR
from core.ddd_paths import ddd_path
from core.ddd_health import compute_section_health

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
    proposals_failed = 0
    projects_checked = 0
    health_refreshed = 0

    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue

        tech_path = ddd_path(project_dir, "TECH.md")
        if not tech_path.exists():
            continue

        projects_checked += 1

        # Scheme A (run_d1e933aa, Gate-0): this scheduled job is the SCHEDULED writer
        # of section_health.json — the ONE refresh that runs on a timer and does NOT
        # depend on any UI traffic. (It is NOT the only writer: GET
        # /api/system/engine-metrics → _collect_ddd_health also calls
        # compute_section_health and writes the file. That GET-path write is a
        # pre-existing wrinkle — a disk write inside a read handler, the very thing
        # the DddCard brain-detail path forbids — and it means trust also refreshes
        # whenever the engine-metrics dashboard is hit; it is NOT this run's concern
        # to fix. What this job guarantees is that trust/diagnostics get recomputed
        # on a cadence even if that endpoint is never hit — otherwise the scores
        # freeze at the last engine-metrics load and silently rot, Principle-1 drift.)
        # NOTE: still no deploy-time warm compute — a fresh deploy with no
        # engine-metrics traffic can show a "scored Nd ago" badge until the first
        # Monday run or the first dashboard load; the badge is always honest about
        # its age, so this is acceptable, not a bug.
        # Health refresh is UNCONDITIONAL and runs BEFORE the staleness `continue` —
        # a brain's HEALTH is independent of whether its docs are "stale vs code".
        # Per-project try/except (resilient-lens): one project's failure must never
        # abort the whole refresh. Not gated on dry_run — recomputing a health
        # snapshot is idempotent (the write is atomic; unlike an LLM proposal write
        # it has no irreversible external effect).
        try:
            compute_section_health(project_dir)
            health_refreshed += 1
        except Exception as e:  # noqa: BLE001 — never abort the batch on one brain
            logger.error(
                "section_health refresh failed for %s: %s: %s",
                project_dir.name, type(e).__name__, e,
            )

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
            proposals_failed += 1
            continue

        # Write proposal
        if proposal and not proposal.get("no_changes"):
            _write_proposal(project_dir, proposal)
            proposals_written += 1

    summary = (
        f"Checked {projects_checked} projects, refreshed {health_refreshed} health, "
        f"wrote {proposals_written} proposals"
        + (f", {proposals_failed} proposal calls failed" if proposals_failed else "")
        if projects_checked > 0
        else "No projects with DDD docs found"
    )
    logger.info("DDD refresh complete: %s", summary)

    return {
        "status": "success",
        "projects_checked": projects_checked,
        "health_refreshed": health_refreshed,
        "proposals_written": proposals_written,
        "proposals_failed": proposals_failed,
        "summary": summary,
    }


# -- Staleness Detection --


def _check_staleness(project_dir: Path) -> dict:
    """Check if project DDD docs need refresh."""
    tech_path = ddd_path(project_dir, "TECH.md")
    now = datetime.now()
    age_days = (now - datetime.fromtimestamp(tech_path.stat().st_mtime)).days

    # Not stale if updated within 7 days
    if age_days <= 7:
        return {"stale": False, "age_days": age_days, "commit_count": 0}

    # Check for recent code commits in the repos this project governs
    commit_count = _count_recent_commits(project_dir, 7)

    # Stale = doc is old AND there have been code changes
    return {
        "stale": commit_count >= 3,  # At least 3 commits to justify a refresh
        "age_days": age_days,
        "commit_count": commit_count,
    }


def _count_recent_commits(project_dir: Path, days: int) -> int:
    """Recent commits across the repos *project_dir* governs (its bindings.yaml). Before
    2026-10-24 every project counted the SwarmAI source checkout's commits; usable-by-
    anyone P7: each project counts its own repos, and a project with none counts 0."""
    total = 0
    for repo in _repos(project_dir):
        try:
            result = subprocess.run(
                ["git", "log", f"--since={days} days ago", "--oneline"],
                cwd=str(repo), capture_output=True, text=True,
                timeout=_GIT_TIMEOUT, env=_GIT_ENV,
            )
            total += len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0
        except (subprocess.TimeoutExpired, OSError):
            continue
    return total


# -- Context Gathering --


def _gather_project_context(project_dir: Path, stale_info: dict) -> dict:
    """Gather all context needed for the LLM to produce a proposal."""
    context: dict = {
        "project_name": project_dir.name,
        "age_days": stale_info["age_days"],
        "commit_count": stale_info["commit_count"],
    }

    # Current TECH.md (capped)
    tech_path = ddd_path(project_dir, "TECH.md")
    context["current_tech_md"] = tech_path.read_text(encoding="utf-8")[:10000]

    # Current IMPROVEMENT.md if exists
    improvement_path = ddd_path(project_dir, "IMPROVEMENT.md")
    if improvement_path.exists():
        context["current_improvement_md"] = improvement_path.read_text(encoding="utf-8")[:5000]

    # Recent git history of the repos this project governs (each labelled, shared caps)
    logs: list[str] = []
    stats: list[str] = []
    for repo in _repos(project_dir):
        label = f"## {repo.name}\n"
        try:
            result = subprocess.run(
                ["git", "log", "--since=7 days ago", "--oneline", "--stat", "-30"],
                cwd=str(repo), capture_output=True, text=True,
                timeout=_GIT_TIMEOUT, env=_GIT_ENV,
            )
            if result.stdout.strip():
                logs.append(label + result.stdout.strip())
            # File changes summary over the same window
            result = subprocess.run(
                ["git", "log", "--since=7 days ago", "--format=", "--shortstat"],
                cwd=str(repo), capture_output=True, text=True,
                timeout=_GIT_TIMEOUT, env=_GIT_ENV,
            )
            if result.stdout.strip():
                stats.append(label + result.stdout.strip())
        except (subprocess.TimeoutExpired, OSError):
            continue
    context["git_log"] = "\n\n".join(logs)[:5000]
    context["diff_stat"] = "\n\n".join(stats)[:3000]

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


# The proposal, shaped by the API (jobs.bedrock.structured(); design
# docs/design/2026-10-28-structured-model-output.md).
_S = {"type": "string"}
_PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "no_changes": {"type": "boolean"}, "reason": _S, "summary": _S,
        "tech_md_updates": {"type": "array", "items": {
            "type": "object",
            "properties": {"section": _S, "action": {"type": "string", "enum": ["add", "modify", "remove"]},
                           "current_text": _S, "proposed_text": _S, "reason": _S},
            "required": ["section", "action", "reason"]}},
        "improvement_md_updates": {"type": "array", "items": {
            "type": "object", "properties": {"section": _S, "entry": _S, "reason": _S},
            "required": ["section", "entry", "reason"]}},
        "confidence": {"type": "number", "description": "1 to 10"},
    },
    "required": ["no_changes"],
}


def _generate_proposal(project_name: str, context: dict) -> dict:
    """Call Bedrock Sonnet 4.6 for DDD doc update proposals. Raises for a Bedrock
    error or an unusable reply (jobs.bedrock.StructuredOutputError)."""
    from jobs.bedrock import get_model_id, structured

    usage: dict = {}
    proposal = structured(_build_prompt(project_name, context), schema=_PROPOSAL_SCHEMA,
                          model_id=get_model_id(), max_tokens=MAX_OUTPUT_TOKENS,
                          temperature=0.2, name="proposal",
                          description="Record the proposed DDD doc updates.",
                          record_usage=True, usage=usage)
    logger.info(
        "DDD refresh LLM: %d input tokens, %d output tokens (~$%.3f)",
        usage["input_tokens"], usage["output_tokens"],
        usage["input_tokens"] * 3.0 / 1_000_000 + usage["output_tokens"] * 15.0 / 1_000_000,
    )
    return proposal


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

Compare the current TECH.md against the recent code changes. Record your proposed updates with the proposal tool.

Rules:
- Only propose changes where the doc is ACTUALLY wrong or missing significant new information
- Preserve the existing structure and style of TECH.md
- Don't rewrite sections that are still accurate
- Be specific: show the exact text to add/modify, not vague suggestions
- If no changes are needed, record no_changes true with the reason (e.g. "docs are current")

The fields, with an example:
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
}}"""


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


_GIT_ENV = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}


def _repos(project_dir: Path) -> list[Path]:
    """The repos *project_dir*'s bindings.yaml declares (ddd_bindings.governed_repo_roots)."""
    from core.ddd_bindings import governed_repo_roots
    return governed_repo_roots(project_dir)
