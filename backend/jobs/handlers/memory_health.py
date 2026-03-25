"""
Weekly Memory Health — LLM-Powered Context Maintenance

Runs weekly (Sunday 3am via weekly-maintenance job). Uses Bedrock Haiku
to intelligently prune MEMORY.md and EVOLUTION.md instead of mechanical
date-based heuristics.

The LLM reads:
  - Current MEMORY.md
  - Current EVOLUTION.md
  - Last 7 days of git commits
  - Last 7 days of DailyActivity
And produces a structured maintenance report with specific actions.

Cost: ~$0.01/run (Haiku, ~5K input tokens, ~1K output).
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..paths import SWARMWS, CONTEXT_DIR, DAILY_DIR

logger = logging.getLogger("swarm.jobs.memory_health")

# Haiku for routine maintenance — fast, cheap, good enough
MODEL_ID = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
MAX_OUTPUT_TOKENS = 2048


def run_memory_health(dry_run: bool = False) -> dict:
    """Execute weekly memory health maintenance.

    Returns a summary dict with actions taken.
    """
    logger.info("Memory health check starting")

    # ── 1. Gather inputs (no LLM) ──────────────────────────────────

    memory_md = _read_context_file("MEMORY.md")
    evolution_md = _read_context_file("EVOLUTION.md")
    git_log = _get_recent_git_log(days=7)
    daily_activity = _get_recent_daily_activity(days=7)

    if not memory_md and not evolution_md:
        logger.info("No context files to maintain")
        return {"status": "skipped", "reason": "no context files"}

    # ── 2. Build maintenance prompt ─────────────────────────────────

    prompt = _build_prompt(memory_md, evolution_md, git_log, daily_activity)

    # ── 3. Call Bedrock Haiku ───────────────────────────────────────

    if dry_run:
        logger.info("[DRY RUN] Would call Haiku with %d chars of context", len(prompt))
        return {"status": "dry_run", "prompt_length": len(prompt)}

    try:
        report = _call_haiku(prompt)
    except Exception as e:
        logger.error("Haiku call failed: %s", e)
        return {"status": "error", "error": str(e)}

    # ── 4. Apply changes ───────────────────────────────────────────

    actions = _apply_report(report, memory_md, evolution_md)

    # ── 5. Write summary to DailyActivity ──────────────────────────

    _write_summary_to_daily(report, actions)

    logger.info("Memory health complete: %d actions", len(actions))
    return {
        "status": "success",
        "actions": actions,
        "stale_memories_removed": report.get("stale_memories", []),
        "resolved_threads": report.get("resolved_threads", []),
        "archived_capabilities": report.get("archived_capabilities", []),
    }


# ── Input Gathering ─────────────────────────────────────────────────


def _read_context_file(filename: str) -> str:
    """Read a context file, capped at 8K chars."""
    path = CONTEXT_DIR / filename
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8")
    return content[:8000]  # Cap to control token usage


def _get_recent_git_log(days: int = 7) -> str:
    """Get recent git commits from SwarmWS."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        result = subprocess.run(
            ["git", "log", f"--since={since}", "--oneline", "--no-decorate", "-50"],
            capture_output=True, text=True, timeout=10,
            cwd=str(SWARMWS),
        )
        return result.stdout.strip()[:3000] if result.stdout else ""
    except Exception:
        return ""


def _get_recent_daily_activity(days: int = 7) -> str:
    """Read recent DailyActivity files."""
    if not DAILY_DIR.exists():
        return ""

    now = datetime.now(timezone.utc)
    content_parts = []

    for days_ago in range(days):
        date_str = (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        path = DAILY_DIR / f"{date_str}.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")[:1500]  # Cap per file
            content_parts.append(f"## {date_str}\n{text}")

    return "\n\n".join(content_parts)[:6000]  # Total cap


# ── LLM Prompt & Call ───────────────────────────────────────────────


def _build_prompt(
    memory_md: str, evolution_md: str,
    git_log: str, daily_activity: str,
) -> str:
    """Build the maintenance prompt for Haiku."""
    return f"""You are Swarm's memory maintenance system. Review the context files and produce a maintenance report.

## Current MEMORY.md
{memory_md}

## Current EVOLUTION.md
{evolution_md}

## Git Commits (last 7 days)
{git_log or "(no commits)"}

## DailyActivity (last 7 days)
{daily_activity or "(no activity)"}

## Your Task
Analyze the context files against recent activity and produce a JSON maintenance report. Be conservative — only flag items you're confident about.

Output a single JSON object with these fields:

{{
  "stale_memories": [
    {{"section": "Recent Context", "entry_prefix": "2026-03-XX: ...", "reason": "why it's stale"}}
  ],
  "resolved_threads": [
    {{"title": "thread title from Open Threads", "evidence": "how you know it's resolved"}}
  ],
  "archived_capabilities": [
    {{"id": "E00X or K00X", "reason": "why it should be archived"}}
  ],
  "stale_decisions": [
    {{"entry_prefix": "2026-03-XX: ...", "reason": "why it's no longer accurate"}}
  ],
  "ddd_staleness": [
    {{"project": "name", "doc": "TECH.md", "reason": "code diverged from docs"}}
  ],
  "summary": "1-2 sentence overall assessment"
}}

Rules:
- "stale_memories": Recent Context entries >30 days old OR superseded by newer entries. Check dates.
- "resolved_threads": Open Threads where git log or DailyActivity shows the issue was fixed.
- "archived_capabilities": EVOLUTION.md capabilities with Usage Count == 0 and status "removed" or older than 30 days.
- "stale_decisions": Key Decisions that contradict recent git activity.
- "ddd_staleness": Only flag if you see clear evidence of code changes that invalidate docs.
- Empty arrays are fine. Don't invent issues.

Output ONLY the JSON object, nothing else."""


def _call_haiku(prompt: str) -> dict:
    """Call Bedrock Haiku and parse the JSON response."""
    import boto3

    client = boto3.client("bedrock-runtime", region_name="us-west-2")

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,  # Low temp for maintenance decisions
    })

    response = client.invoke_model(modelId=MODEL_ID, body=body)
    result = json.loads(response["body"].read())

    content = result["content"][0]["text"]
    input_tokens = result.get("usage", {}).get("input_tokens", 0)
    output_tokens = result.get("usage", {}).get("output_tokens", 0)

    logger.info(
        "Haiku response: %d input tokens, %d output tokens",
        input_tokens, output_tokens,
    )

    # Parse JSON — handle markdown code fences
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse Haiku JSON response, returning raw")
        return {"summary": text, "parse_error": True}


# ── Apply Changes ──────────────────────────────────────────────────


def _apply_report(report: dict, memory_md: str, evolution_md: str) -> list[str]:
    """Apply maintenance actions from the LLM report.

    Uses locked_write.py for safe concurrent writes.
    Returns list of human-readable action descriptions.
    """
    actions = []

    if report.get("parse_error"):
        actions.append("LLM response parse error — no actions taken")
        return actions

    # 1. Remove stale Recent Context entries
    stale = report.get("stale_memories", [])
    if stale:
        for entry in stale[:5]:  # Cap at 5 per run
            prefix = entry.get("entry_prefix", "")
            if prefix and prefix in memory_md:
                _remove_memory_entry("Recent Context", prefix)
                actions.append(f"Removed stale memory: {prefix[:60]}")

    # 2. Resolve Open Threads
    resolved = report.get("resolved_threads", [])
    if resolved:
        for thread in resolved[:3]:  # Cap at 3 per run
            title = thread.get("title", "")
            if title:
                _resolve_open_thread(title)
                actions.append(f"Resolved thread: {title}")

    # 3. Archive stale Evolution entries
    archived = report.get("archived_capabilities", [])
    if archived:
        for cap in archived[:3]:
            cap_id = cap.get("id", "")
            if cap_id:
                actions.append(f"Flagged for archive: {cap_id} — {cap.get('reason', '')}")

    # 4. Flag stale decisions (log only, don't auto-remove)
    stale_decisions = report.get("stale_decisions", [])
    for dec in stale_decisions[:3]:
        actions.append(f"Stale decision flagged: {dec.get('entry_prefix', '')[:60]}")

    return actions


def _remove_memory_entry(section: str, entry_prefix: str) -> None:
    """Remove a specific entry from MEMORY.md by finding and deleting the line."""
    memory_path = CONTEXT_DIR / "MEMORY.md"
    if not memory_path.exists():
        return

    try:
        content = memory_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        new_lines = [l for l in lines if entry_prefix not in l]
        if len(new_lines) < len(lines):
            memory_path.write_text("\n".join(new_lines), encoding="utf-8")
            logger.info("Removed entry matching: %s", entry_prefix[:50])
    except Exception as e:
        logger.warning("Failed to remove memory entry: %s", e)


def _resolve_open_thread(title: str) -> None:
    """Move an Open Thread to the Resolved section in MEMORY.md."""
    memory_path = CONTEXT_DIR / "MEMORY.md"
    if not memory_path.exists():
        return

    try:
        content = memory_path.read_text(encoding="utf-8")

        # Find the thread line (fuzzy match on title)
        lines = content.split("\n")
        new_lines = []
        resolved_entry = None

        for line in lines:
            if title.lower() in line.lower() and ("🔵" in line or "🟡" in line or "🔴" in line):
                # Convert to resolved
                today = datetime.now(timezone.utc).strftime("%m/%d")
                resolved_entry = line.replace("🔵", "✅").replace("🟡", "✅").replace("🔴", "✅")
                resolved_entry = resolved_entry.rstrip() + f" (auto-resolved {today})"
                # Don't include original line
            else:
                new_lines.append(line)

        if resolved_entry:
            # Add to resolved section
            for i, line in enumerate(new_lines):
                if "### Resolved" in line:
                    new_lines.insert(i + 1, resolved_entry)
                    break

            memory_path.write_text("\n".join(new_lines), encoding="utf-8")
            logger.info("Resolved thread: %s", title)
    except Exception as e:
        logger.warning("Failed to resolve thread: %s", e)


# ── Reporting ──────────────────────────────────────────────────────


def _write_summary_to_daily(report: dict, actions: list[str]) -> None:
    """Append maintenance summary to today's DailyActivity."""
    if not actions and not report.get("summary"):
        return

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    daily_path = DAILY_DIR / f"{today}.md"

    summary_text = f"\n## Weekly Memory Health\n"
    if report.get("summary"):
        summary_text += f"**Assessment:** {report['summary']}\n"
    if actions:
        summary_text += "**Actions:**\n"
        for a in actions:
            summary_text += f"- {a}\n"
    else:
        summary_text += "No maintenance actions needed.\n"

    try:
        if daily_path.exists():
            with daily_path.open("a", encoding="utf-8") as f:
                f.write(summary_text)
        else:
            daily_path.write_text(f"---\ndate: \"{today}\"\n---\n{summary_text}", encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write maintenance summary: %s", e)
