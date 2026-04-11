"""
Weekly Memory Health — LLM-Powered Context Maintenance

Runs weekly (Monday 11am ICT via weekly-maintenance job). Uses Bedrock to
intelligently prune MEMORY.md, maintain EVOLUTION.md, and detect
capability gaps — all in a single LLM pass.

The LLM reads:
  - Current MEMORY.md
  - Current EVOLUTION.md
  - Last 7 days of git commits
  - Last 7 days of DailyActivity
And produces a structured maintenance report with:
  - Stale memory entries to prune
  - Open Threads to resolve
  - Evolution entries to archive
  - Capability gaps detected from error/lesson patterns (L3)

Cost: ~$0.03/run (Sonnet 4.6, ~5K input tokens, ~1.5K output).
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..paths import SWARMWS, CONTEXT_DIR, DAILY_DIR

logger = logging.getLogger("swarm.jobs.memory_health")

MAX_OUTPUT_TOKENS = 2048


def _sanitize_memory_content(text: str) -> str:
    """Sanitize content through MemoryGuard before writing to MEMORY.md.

    Gracefully degrades to returning text unchanged if MemoryGuard is
    not available (cold start, import failure).
    """
    try:
        from core.memory_guard import MemoryGuard
        return MemoryGuard().sanitize(text)
    except ImportError:
        return text
    except Exception:
        return text


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

    # ── 3. Call Bedrock LLM ────────────────────────────────────────

    if dry_run:
        logger.info("[DRY RUN] Would call LLM with %d chars of context", len(prompt))
        return {"status": "dry_run", "prompt_length": len(prompt)}

    try:
        report = _call_llm(prompt)
    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return {"status": "error", "error": str(e)}

    # ── 4. Apply changes ───────────────────────────────────────────

    actions = _apply_report(report, memory_md, evolution_md)

    # ── 5. Write summary to DailyActivity ──────────────────────────

    _write_summary_to_daily(report, actions)

    # ── 6. Update health_findings.json for session briefing ────────

    _update_health_findings(report, actions)

    logger.info("Memory health complete: %d actions", len(actions))
    return {
        "status": "success",
        "actions": actions,
        "stale_memories_removed": report.get("stale_memories", []),
        "resolved_threads": report.get("resolved_threads", []),
        "archived_capabilities": report.get("archived_capabilities", []),
        "capability_gaps": report.get("capability_gaps", []),
        "stale_corrections": report.get("stale_corrections", []),
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
    """Build the maintenance prompt for the LLM."""
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
  "capability_gaps": [
    {{
      "pattern": "short description of the recurring problem",
      "evidence": ["session date: what happened", "session date: same class of problem"],
      "occurrences": 3,
      "suggested_action": "build skill | add correction | add steering rule",
      "priority": "high | medium | low"
    }}
  ],
  "stale_corrections": [
    {{"id": "C00X", "reason": "code referenced by this correction was deleted or refactored"}}
  ],
  "summary": "1-2 sentence overall assessment"
}}

Rules:
- "stale_memories": Recent Context entries superseded by a newer entry covering the same topic, OR contradicted by recent git activity. Do NOT archive based on age alone — a 6-month-old lesson that's still relevant stays.
- "resolved_threads": Open Threads where git log or DailyActivity shows the issue was fixed.
- "archived_capabilities": EVOLUTION.md capabilities with Usage Count == 0 and status "removed" or older than 30 days.
- "stale_decisions": Key Decisions that contradict recent git activity.
- "ddd_staleness": Only flag if you see clear evidence of code changes that invalidate docs.
- "capability_gaps": Look for PATTERNS across DailyActivity — the same CLASS of error, lesson, or workaround appearing 2+ times in different sessions. Evidence must cite specific sessions. Do NOT flag one-off issues. Focus on: (a) repeated errors/crashes with similar root cause, (b) tasks attempted multiple times without a skill to automate them, (c) corrections that keep getting re-triggered because the underlying pattern wasn't addressed.
- "stale_corrections": Corrections in EVOLUTION.md that reference code/features that no longer exist (check git log for deletions/renames).
- Empty arrays are fine. Don't invent issues.

Output ONLY the JSON object, nothing else."""


def _call_llm(prompt: str) -> dict:
    """Call Bedrock Sonnet 4.6 and parse the JSON response.

    Uses the shared jobs.bedrock client (same credential chain as the
    SwarmAI app — AppConfigManager region, proper timeouts, credential
    eviction on auth errors).
    """
    from jobs.bedrock import invoke

    content, input_tokens, output_tokens = invoke(
        prompt, max_tokens=MAX_OUTPUT_TOKENS, temperature=0.2,
    )

    logger.info(
        "LLM response: %d input tokens, %d output tokens",
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
        logger.warning("Failed to parse LLM JSON response, returning raw")
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
            if prefix:
                removed = _remove_memory_entry(prefix)
                if removed:
                    actions.append(f"Removed stale memory: {prefix[:60]}")

    # 2. Resolve Open Threads
    resolved = report.get("resolved_threads", [])
    if resolved:
        for thread in resolved[:3]:  # Cap at 3 per run
            title = thread.get("title", "")
            if title:
                _resolve_open_thread(title)
                actions.append(f"Resolved thread: {title}")

    # 3. Archive stale Evolution entries (remove from EVOLUTION.md)
    archived = report.get("archived_capabilities", [])
    if archived:
        for cap in archived[:3]:
            cap_id = cap.get("id", "")
            if cap_id:
                removed = _remove_evolution_entry(cap_id)
                if removed:
                    actions.append(f"Archived: {cap_id} — {cap.get('reason', '')[:80]}")
                else:
                    actions.append(f"Flagged for archive (not found): {cap_id}")

    # 4. Stale decisions: mark superseded instead of removing (P2 Temporal Validity)
    stale_decisions = report.get("stale_decisions", [])
    for dec in stale_decisions[:3]:
        old_key = dec.get("key", "")
        new_key = dec.get("superseded_by", "")
        prefix = dec.get("entry_prefix", "")[:60]
        if old_key and new_key:
            try:
                from core.memory_index import mark_entry_superseded
                memory_path = CONTEXT_DIR / "MEMORY.md"
                if memory_path.exists():
                    content = memory_path.read_text(encoding="utf-8")
                    updated = mark_entry_superseded(content, old_key, new_key)
                    if updated != content:
                        memory_path.write_text(updated, encoding="utf-8")
                        actions.append(f"Superseded: {old_key} → {new_key} ({prefix})")
                    else:
                        actions.append(f"Stale decision flagged (key not found): {prefix}")
                else:
                    actions.append(f"Stale decision flagged: {prefix}")
            except Exception as exc:
                logger.warning("Failed to mark %s superseded: %s", old_key, exc)
                actions.append(f"Stale decision flagged: {prefix}")
        else:
            actions.append(f"Stale decision flagged: {prefix}")

    # 5. Capability gaps (log for briefing, don't auto-act)
    gaps = report.get("capability_gaps", [])
    for gap in gaps[:5]:
        pattern = gap.get("pattern", "")[:80]
        priority = gap.get("priority", "medium")
        occurrences = gap.get("occurrences", 0)
        actions.append(f"Capability gap [{priority}]: {pattern} ({occurrences}x)")

    # 6. Stale corrections (log for briefing)
    stale_corr = report.get("stale_corrections", [])
    for corr in stale_corr[:3]:
        actions.append(f"Stale correction: {corr.get('id', '')} — {corr.get('reason', '')[:60]}")

    return actions


def _normalize_prefix(prefix: str) -> str:
    """Strip index formatting so LLM prefixes match file content.

    The LLM returns e.g. ``"RC24 2026-03-13: MCP not working"``
    but the file has ``"- [RC24] 2026-03-13: MCP not working"``.
    Extract the date+topic core for fuzzy matching.
    """
    import re
    # Strip leading "- [RC24] " or "RC24 " but NOT dates like "2026-03-13"
    # Entry IDs are 1-3 uppercase letters + digits (RC24, KD01, COE03, LL12)
    cleaned = re.sub(r"^-?\s*\[?[A-Z]{1,3}\d+\]?\s*", "", prefix).strip()
    return cleaned[:50]  # First 50 chars of the content portion


def _remove_memory_entry(entry_prefix: str) -> bool:
    """Remove a specific entry from MEMORY.md (both index and body).

    Uses flock for safe concurrent access. Fuzzy-matches the entry
    prefix against each line to handle formatting differences between
    the LLM output and actual file content.

    Returns True if any lines were removed.
    """
    memory_path = CONTEXT_DIR / "MEMORY.md"
    if not memory_path.exists():
        return False

    needle = _normalize_prefix(entry_prefix)
    if len(needle) < 10:
        logger.warning("Prefix too short for safe matching: %r", needle)
        return False

    lock_path = memory_path.with_suffix(".md.lock")
    fd = None
    try:
        import fcntl
        fd = open(lock_path, "w")  # noqa: SIM115
        fcntl.flock(fd, fcntl.LOCK_EX)

        content = memory_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        new_lines = [l for l in lines if needle not in l]
        removed = len(lines) - len(new_lines)

        if removed > 0:
            memory_path.write_text(
                _sanitize_memory_content("\n".join(new_lines)), encoding="utf-8"
            )
            logger.info("Removed %d line(s) matching: %s", removed, needle[:50])
            return True
        else:
            logger.debug("No match for: %s", needle[:50])
            return False
    except Exception as e:
        logger.warning("Failed to remove memory entry: %s", e)
        return False
    finally:
        if fd:
            fd.close()


def _resolve_open_thread(title: str) -> None:
    """Move an Open Thread to the Resolved section in MEMORY.md.

    Uses flock for safe concurrent access.
    """
    memory_path = CONTEXT_DIR / "MEMORY.md"
    if not memory_path.exists():
        return

    lock_path = memory_path.with_suffix(".md.lock")
    fd = None
    try:
        import fcntl
        fd = open(lock_path, "w")  # noqa: SIM115
        fcntl.flock(fd, fcntl.LOCK_EX)

        content = memory_path.read_text(encoding="utf-8")

        # Find the thread line (fuzzy match on title)
        lines = content.split("\n")
        new_lines = []
        resolved_entry = None

        for line in lines:
            if title.lower() in line.lower() and ("🔵" in line or "🟡" in line or "🔴" in line):
                today = datetime.now(timezone.utc).strftime("%m/%d")
                resolved_entry = line.replace("🔵", "✅").replace("🟡", "✅").replace("🔴", "✅")
                resolved_entry = resolved_entry.rstrip() + f" (auto-resolved {today})"
            else:
                new_lines.append(line)

        if resolved_entry:
            inserted = False
            for i, line in enumerate(new_lines):
                if "### Resolved" in line:
                    # Dedup: skip if this entry already exists in Resolved
                    if resolved_entry not in new_lines:
                        new_lines.insert(i + 1, resolved_entry)
                    inserted = True
                    break

            if not inserted:
                # No "### Resolved" section — append one at the end of
                # the Open Threads area so the entry isn't silently dropped.
                new_lines.append("")
                new_lines.append("### Resolved")
                new_lines.append(resolved_entry)

            memory_path.write_text(
                _sanitize_memory_content("\n".join(new_lines)), encoding="utf-8"
            )
            logger.info("Resolved thread: %s", title)
    except Exception as e:
        logger.warning("Failed to resolve thread: %s", e)
    finally:
        if fd:
            fd.close()


def _remove_evolution_entry(entry_id: str) -> bool:
    """Remove an entry block from EVOLUTION.md by its ID (e.g. E003).

    Removes the ``### EXXX | ...`` header and all subsequent lines until
    the next ``### `` header or section boundary.  Uses flock.

    Returns True if the entry was found and removed.
    """
    evo_path = CONTEXT_DIR / "EVOLUTION.md"
    if not evo_path.exists():
        return False

    lock_path = evo_path.with_suffix(".md.lock")
    fd = None
    try:
        import fcntl
        fd = open(lock_path, "w")  # noqa: SIM115
        fcntl.flock(fd, fcntl.LOCK_EX)

        content = evo_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        new_lines = []
        skipping = False
        removed = False

        for line in lines:
            # Detect entry header: "### E003 | reactive | skill | 2026-03-08"
            if line.startswith("### ") and f" {entry_id} " in line:
                skipping = True
                removed = True
                continue
            # Stop skipping at next entry header or section header
            if skipping and (line.startswith("### ") or line.startswith("## ")):
                skipping = False
            if not skipping:
                new_lines.append(line)

        if removed:
            evo_path.write_text(
                _sanitize_memory_content("\n".join(new_lines)), encoding="utf-8"
            )
            logger.info("Removed evolution entry: %s", entry_id)
            return True
        return False
    except Exception as e:
        logger.warning("Failed to remove evolution entry %s: %s", entry_id, e)
        return False
    finally:
        if fd:
            fd.close()


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


def _update_health_findings(report: dict, actions: list[str]) -> None:
    """Update health_findings.json with memory health results.

    Merges into the existing file (written by ContextHealthHook).
    The proactive intelligence system reads this at session start.
    """
    from ..paths import JOBS_DATA_DIR

    findings_file = JOBS_DATA_DIR / "health_findings.json"
    JOBS_DATA_DIR.mkdir(parents=True, exist_ok=True)

    memory_health_data = {
        "actions": actions,
        "summary": report.get("summary", ""),
        "capability_gaps": report.get("capability_gaps", []),
        "stale_corrections": report.get("stale_corrections", []),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        if findings_file.exists():
            data = json.loads(findings_file.read_text(encoding="utf-8"))
        else:
            data = {"timestamp": datetime.now(timezone.utc).isoformat(), "findings": []}

        data["memory_health"] = memory_health_data

        findings_file.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Updated health_findings.json with memory health results")
    except Exception as e:
        logger.warning("Failed to update health findings: %s", e)
