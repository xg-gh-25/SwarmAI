"""Proactive Intelligence — session briefing engine.

Analyzes MEMORY.md (Open Threads) and recent DailyActivity files to
generate a compact session briefing injected into the system prompt.
Makes the agent *aware* at session start — no user prompt needed.

No LLM calls. Pure text parsing. Target: 200-400 tokens.

Levels:
- L0: Session briefing (parse threads + continue hints + pattern signals)
- L1: Temporal awareness (session gaps, stale P0s, first-session-of-day)
- L2: Actionable suggestions (score + rank items, suggest focus with reasoning)
- L3: Cross-session learning (track suggestions vs outcomes, adjust scores)
- L4: External signals (signal_digest.json, .job-results.jsonl)

Architecture (split 2026-03-25, Kiro feedback):
- proactive_scoring.py  — L2 scoring engine (ScoredItem, ranking, formatting)
- proactive_learning.py — L3 learning state (persistence, classification, effectiveness)
- proactive_intelligence.py — L0/L1 parsing, L4 signals, briefing builder (this file)

Key exports:
- build_session_briefing()      — main entry point, returns briefing string or None
- build_session_briefing_data() — structured dict for frontend Welcome Screen
"""

from __future__ import annotations

import json
import re
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from core.session_utils import fuzzy_title_matches_deliverable

# L2 scoring engine
from core.proactive_scoring import (
    ScoredItem,
    score_item as _score_item,
    estimate_thread_age as _estimate_thread_age,
    detect_blocking as _detect_blocking,
    build_suggestions as _build_suggestions_raw,
    generate_reasoning as _generate_reasoning,
    format_suggestions as _format_suggestions,
)

# L3 learning state
from core.proactive_learning import (
    LearningState,
    load_learning_state as _load_learning_state,
    save_learning_state as _save_learning_state,
    apply_learning as _apply_learning,
    update_learning_from_activity as _update_learning_from_activity,
    update_effectiveness as _update_effectiveness,
    classify_work_type as _classify_work_type,
    extract_deliverables as _extract_deliverables,
)

logger = logging.getLogger(__name__)

# Module-level compiled regex — used by _detect_temporal_signals and scoring engine.
# Anchored to word boundary: lookbehind requires start-of-string, whitespace, or common
# punctuation (colon, comma, open-paren) to avoid matching version numbers like "v2/3".
# Negative lookbehind for 'v', 'V', and '.' prevents matching "v3/4", "V2/3", "1.3/4".
# At least one side of m/d must be 2+ digits to reject ambiguous "3/4" (could be fraction
# or version component) while still matching "3/14", "03/4", "12/31" (Kiro feedback 2026-03-25).
_DATE_REF_RE = re.compile(
    r"(?:^|(?<=[\s:,(]))(?<![vV.])(?:(\d{2})/(\d{1,2})|(\d{1,2})/(\d{2}))(?=[\s,)]|$)|(\d{4}-\d{2}-\d{2})"
)

# ---------------------------------------------------------------------------
# Backward-compatible re-exports (existing imports use underscore names)
# ---------------------------------------------------------------------------
# These aliases ensure that `from core.proactive_intelligence import _load_learning_state`
# (used by distillation_hook.py and tests) continues to work without changes.
_load_learning_state = _load_learning_state  # noqa: F811 — intentional re-export
_save_learning_state = _save_learning_state  # noqa: F811
_update_effectiveness = _update_effectiveness  # noqa: F811


# ---------------------------------------------------------------------------
# Open Threads parser (L0)
# ---------------------------------------------------------------------------

_PRIORITY_EMOJI = {"P0": "BLOCKING", "P1": "IMPORTANT", "P2": "NICE-TO-HAVE"}
_THREAD_RE = re.compile(
    r"[-*]\s+"           # bullet
    r"(?:[\U0001F000-\U0001FFFF\u2600-\u27BF\u2B50-\u2BFF]\s+)?"  # optional emoji (Unicode emoji ranges)
    r"\*\*(.+?)\*\*"    # **title**
    r"\s*\(reported\s+(\d+)x",  # (reported Nx
    re.IGNORECASE,
)
_PRIORITY_HEADER_RE = re.compile(r"###\s+(P[012])\s")
_STATUS_RE = re.compile(r"Status:\s*(.+?)$", re.IGNORECASE)


def _parse_open_threads(memory_text: str) -> list[dict]:
    """Extract structured Open Threads from MEMORY.md text.

    Returns list of {priority, title, report_count, status} dicts,
    ordered P0 -> P1 -> P2.
    """
    threads: list[dict] = []
    # Find the Open Threads section
    ot_match = re.search(r"## Open Threads\b", memory_text)
    if not ot_match:
        return threads

    ot_text = memory_text[ot_match.end():]
    # Cut at the next ## section (if any)
    next_section = re.search(r"\n## [^#]", ot_text)
    if next_section:
        ot_text = ot_text[:next_section.start()]

    current_priority = "P2"  # default if no header found

    for line in ot_text.splitlines():
        # Check for priority header
        ph = _PRIORITY_HEADER_RE.search(line)
        if ph:
            current_priority = ph.group(1)
            continue

        # Skip resolved items — lines starting with check or strikethrough.
        line_stripped = line.strip()
        if line_stripped.startswith(("-", "*")):
            if line_stripped.startswith("- \u2705") or line_stripped.startswith("- ~~"):
                continue

        # Check for thread bullet
        tm = _THREAD_RE.search(line)
        if tm:
            title = tm.group(1).strip()
            report_count = int(tm.group(2))
            threads.append({
                "priority": current_priority,
                "title": title,
                "report_count": report_count,
            })
            continue

        # Simpler pattern: **title** without report count
        simple = re.search(r"\*\*(.+?)\*\*", line)
        if simple and line_stripped.startswith(("-", "*")):
            title = simple.group(1).strip()
            threads.append({
                "priority": current_priority,
                "title": title,
                "report_count": 1,
            })
            continue

        # Status line — attach to most recent thread
        sm = _STATUS_RE.search(line)
        if sm and threads:
            status_text = sm.group(1).strip()
            threads[-1]["status"] = status_text
            # Remove thread if status clearly indicates resolved.
            # Guard against negated phrases like "not resolved" or "not durably resolved".
            status_lower = status_text.lower()
            if any(kw in status_lower for kw in ["resolved", "done", "closed", "moot"]):
                # Check for negation: "not resolved", "not durably resolved", "unresolved"
                if not re.search(r"\bnot\b.*\bresolved\b|\bunresolved\b", status_lower):
                    threads.pop()

    return threads


# ---------------------------------------------------------------------------
# DailyActivity "Next" / continue_from parser (L0)
# ---------------------------------------------------------------------------

def _parse_continue_hints(daily_dir: Path, max_files: int = 2) -> list[str]:
    """Extract **Next:** lines from recent DailyActivity files.

    Returns deduplicated list of continue-from hints, newest first.
    Skips "Ongoing:" prefixed items that are stale (>2 days old).
    """
    hints: list[str] = []
    if not daily_dir.is_dir():
        return hints

    da_files = sorted(
        [f for f in daily_dir.glob("*.md") if f.stem[:4].isdigit()],
        key=lambda f: f.stem,
        reverse=True,
    )[:max_files]

    seen: set[str] = set()
    for da_file in da_files:
        try:
            content = da_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for line in content.splitlines():
            line_stripped = line.strip()
            if not line_stripped.startswith("**Next:**"):
                continue

            hint = line_stripped.removeprefix("**Next:**").strip()
            if not hint:
                continue

            # Skip "Ongoing:" hints — these are typically stale user messages
            # captured verbatim, not actionable continue-from items
            if hint.startswith("Ongoing:"):
                continue

            # Normalize and deduplicate
            hint_key = hint[:80].lower()
            if hint_key not in seen:
                seen.add(hint_key)
                hints.append(hint)

    return hints


def _extract_recent_deliverables(daily_dir: Path, max_files: int = 3) -> list[str]:
    """Extract deliverable lines from recent DailyActivity files.

    Returns lowercased deliverable strings for matching against thread
    titles.  Reuses the same parsing logic as the distillation hook's
    effectiveness scoring.
    """
    deliverables: list[str] = []
    if not daily_dir.is_dir():
        return deliverables

    da_files = sorted(
        [f for f in daily_dir.glob("*.md") if f.stem[:4].isdigit()],
        key=lambda f: f.stem,
        reverse=True,
    )[:max_files]

    for da_file in da_files:
        try:
            content = da_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        in_deliverables = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "**Deliverables:**" or stripped.startswith("### Deliverables"):
                in_deliverables = True
                continue
            if in_deliverables and (
                (stripped.startswith("**") and stripped.endswith(":**"))
                or stripped.startswith("### ")
                or stripped.startswith("## ")
            ):
                in_deliverables = False
                continue
            if in_deliverables and stripped.startswith("- "):
                deliverables.append(stripped.lstrip("- ").strip().lower())

    return deliverables


def _filter_completed_threads(
    threads: list[dict],
    daily_dir: Path,
) -> list[dict]:
    """Remove threads whose topics appear in recent deliverables.

    Read-time safety net: even if distillation hasn't resolved the
    thread yet, the briefing won't suggest work that's already done.
    Uses >=50% word overlap matching (same heuristic as distillation).
    """
    deliverables = _extract_recent_deliverables(daily_dir)
    if not deliverables:
        return threads

    deliv_word_sets = [set(d.split()) for d in deliverables]
    filtered: list[dict] = []

    for t in threads:
        title = t.get("title", "")

        completed = fuzzy_title_matches_deliverable(
            title, deliverables, deliv_word_sets,
        )

        if not completed:
            filtered.append(t)
        else:
            logger.debug(
                "Filtered completed thread from briefing: %s", title,
            )

    return filtered


# ---------------------------------------------------------------------------
# Pattern detection (L1)
# ---------------------------------------------------------------------------

def _detect_patterns(
    threads: list[dict],
    daily_dir: Path,
    memory_text: str,
) -> list[str]:
    """Detect actionable patterns from Open Threads + DailyActivity.

    Returns list of short signal strings like:
    - "Tab-switch bug reported 4x — still unresolved"
    - "3 fixes pending rebuild verification"
    - "Distillation flag present — memory maintenance needed"
    """
    signals: list[str] = []

    # 1. Repeat offenders — bugs reported 3+ times
    for t in threads:
        if t.get("report_count", 1) >= 3:
            signals.append(
                f'"{t["title"]}" reported {t["report_count"]}x — needs durable fix'
            )

    # 2. Pending rebuild — scan for "Needs rebuild" in threads or recent activity
    rebuild_keywords = ["needs rebuild", "needs rebuild & verify", "not yet run", "untested"]
    rebuild_count = 0
    for t in threads:
        status = t.get("status", "").lower()
        if any(kw in status for kw in rebuild_keywords):
            rebuild_count += 1
    if rebuild_count > 0:
        signals.append(f"{rebuild_count} fix(es) pending rebuild verification")

    # 3. COE Registry items (only unresolved ones)
    coe_match = re.search(r"## COE Registry\b", memory_text)
    if coe_match:
        coe_section = memory_text[coe_match.end():]
        next_sec = re.search(r"\n## [^#]", coe_section)
        if next_sec:
            coe_section = coe_section[:next_sec.start()]
        investigating = sum(
            1 for line in coe_section.splitlines()
            if "Investigating" in line and "\u2705" not in line and "Resolved" not in line
        )
        if investigating > 0:
            signals.append(f"{investigating} COE(s) still under investigation")

    # 4. Uncommitted work — check for "need git commit" in memory
    if re.search(r"need[s]?\s+(?:git\s+)?commit", memory_text, re.IGNORECASE):
        signals.append("Uncommitted work detected in Open Threads")

    # 5. Temporal signals (Level 1)
    temporal = _detect_temporal_signals(threads, daily_dir)
    signals.extend(temporal)

    return signals


def _detect_temporal_signals(
    threads: list[dict],
    daily_dir: Path,
) -> list[str]:
    """Detect time-based signals from thread ages and session gaps.

    Level 1 temporal awareness — pure datetime comparisons, no LLM.

    Signals:
    - Session gap (no DailyActivity for >1 day)
    - Stale P0 (open >2 days based on date mentions in thread)
    - First session of day (full briefing is warranted)
    - Rebuild debt staleness (fixes pending >2 days)
    """
    signals: list[str] = []
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # -- Session gap detection --
    if daily_dir.is_dir():
        da_files = sorted(
            [f for f in daily_dir.glob("*.md") if f.stem[:4].isdigit()],
            key=lambda f: f.stem,
            reverse=True,
        )
        if da_files:
            last_date_str = da_files[0].stem  # e.g. "2026-03-14"
            try:
                last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
                gap_days = (now - last_date).days
                if gap_days >= 2:
                    signals.append(
                        f"{gap_days} days since last session — review Open Threads for stale items"
                    )
            except ValueError:
                pass

            # -- First session of day --
            # If today's file doesn't exist yet, this is the first session
            today_file = daily_dir / f"{today}.md"
            if not today_file.exists():
                signals.append("First session today — full briefing")

    # -- Stale P0 detection --
    for t in threads:
        if t["priority"] != "P0":
            continue
        # Search title + status for earliest date reference
        search_text = f"{t['title']} {t.get('status', '')}"
        dates_found = _DATE_REF_RE.findall(search_text)
        earliest = None
        for groups in dates_found:
            # 5 groups: (2d_month, 1-2d_day, 1-2d_month, 2d_day, full_date)
            m = groups[0] or groups[2]
            d = groups[1] or groups[3]
            full = groups[4]
            try:
                if full:
                    dt = datetime.strptime(full, "%Y-%m-%d")
                else:
                    # Assume current year, month/day format.
                    # If the resulting date is in the future, try previous year.
                    dt = datetime(now.year, int(m), int(d))
                    if dt > now:
                        dt = datetime(now.year - 1, int(m), int(d))
                if earliest is None or dt < earliest:
                    earliest = dt
            except (ValueError, TypeError):
                continue
        if earliest:
            age_days = (now - earliest).days
            if age_days >= 3:
                signals.append(
                    f'P0 "{t["title"]}" open {age_days} days — consider escalating'
                )

    return signals


# ---------------------------------------------------------------------------
# L4: External signal highlights
# ---------------------------------------------------------------------------

def _sanitize_prompt_field(s: str, max_len: int = 200) -> str:
    """Sanitize a string before injecting into a system prompt.

    Strips control characters and collapses excessive markdown formatting
    to prevent prompt injection from user-writable files (signal_digest.json,
    .job-results.jsonl).
    """
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)
    s = re.sub(r"[*_]{3,}", "**", s)
    return s[:max_len].strip()


def _get_signal_highlights(working_directory: str, max_items: int = 3) -> list[str]:
    """Read signal_digest.json and return formatted highlights for the session briefing.

    Filters to items fetched within the last 48 hours for freshness.
    Returns up to *max_items* formatted lines sorted by relevance_score desc.

    Returns an empty list if the digest file doesn't exist or has no fresh items
    (signal fetcher may not be configured yet — this is a graceful no-op).
    """
    digest_path = Path(working_directory) / "Services" / "signals" / "signal_digest.json"
    if not digest_path.exists():
        return []

    try:
        data = json.loads(digest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    items = data.get("items", [])
    if not items:
        return []

    # 48-hour freshness cutoff
    cutoff = time.time() - 48 * 3600
    fresh = []
    for item in items:
        fetched_at = item.get("fetched_at", "")
        if isinstance(fetched_at, str) and fetched_at:
            try:
                dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
                if dt.timestamp() >= cutoff:
                    fresh.append(item)
            except (ValueError, TypeError):
                continue
        elif isinstance(fetched_at, (int, float)):
            if fetched_at >= cutoff:
                fresh.append(item)

    if not fresh:
        return []

    # Sort by relevance_score descending
    fresh.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    lines = []
    for item in fresh[:max_items]:
        title = item.get("title", "Untitled")
        summary = item.get("summary", "")
        source = item.get("source", "")
        urgency = item.get("urgency", "")

        title = _sanitize_prompt_field(title, 100)
        summary = _sanitize_prompt_field(summary, 150)
        source = _sanitize_prompt_field(source, 50)
        urgency = _sanitize_prompt_field(urgency, 20)

        prefix = f"[{urgency}]" if urgency else ""
        source_tag = f" ({source})" if source else ""
        line = f"  - {prefix} **{title}**{source_tag}"
        if summary:
            # Truncate summary to ~100 chars for briefing compactness
            short = summary[:100].rstrip() + ("..." if len(summary) > 100 else "")
            line += f": {short}"
        lines.append(line)

    return lines


def _get_job_result_highlights(working_directory: str, max_items: int = 5) -> list[str]:
    """Read .job-results.jsonl and return formatted highlights for the session briefing.

    Filters to results from the last 24 hours. Returns up to *max_items*
    formatted lines showing recent job outcomes (success/failure).

    Returns an empty list if the JSONL file doesn't exist or has no recent results
    (the job system may not have run yet — this is a graceful no-op).
    """
    jsonl_path = (
        Path(working_directory) / "Knowledge" / "JobResults" / ".job-results.jsonl"
    )
    if not jsonl_path.exists():
        return []

    try:
        raw = jsonl_path.read_text(encoding="utf-8").strip()
    except OSError:
        return []

    if not raw:
        return []

    # Parse JSONL — each line is a JSON object
    cutoff = time.time() - 24 * 3600
    recent: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        run_at = entry.get("run_at", "")
        if isinstance(run_at, str) and run_at:
            try:
                dt = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
                if dt.timestamp() >= cutoff:
                    recent.append(entry)
            except (ValueError, TypeError):
                continue

    if not recent:
        return []

    # Most recent first
    recent.sort(key=lambda x: x.get("run_at", ""), reverse=True)

    lines = []
    for entry in recent[:max_items]:
        job_name = _sanitize_prompt_field(
            str(entry.get("job_name", entry.get("job_id", "Unknown"))), 60
        )
        status = _sanitize_prompt_field(str(entry.get("status", "unknown")), 20)
        tokens = entry.get("tokens_used", 0)
        duration = entry.get("duration_seconds", 0)

        icon = "\u2705" if status == "success" else "\u274c" if status == "failed" else "\u23ed\ufe0f"

        # Build detail parenthetical — always balanced parens
        parts: list[str] = []
        if duration:
            parts.append(f"{duration:.0f}s")
        if tokens:
            parts.append(f"{tokens} tok")
        detail = f" ({', '.join(parts)})" if parts else ""

        line = f"  - {icon} {job_name}: {status}{detail}"
        if status == "failed":
            raw_summary = str(entry.get("summary", ""))[:100].strip()
            summary = _sanitize_prompt_field(raw_summary, 100)
            if summary:
                line += f" — {summary}"
        lines.append(line)

    return lines


def _get_health_highlights(working_directory: str) -> list[str]:
    """Read health_findings.json and return formatted alerts for session briefing.

    Shows warnings/critical findings from ContextHealthHook and weekly
    memory maintenance results. Graceful no-op if file doesn't exist.
    """
    findings_path = (
        Path(working_directory) / "Services" / "swarm-jobs" / "health_findings.json"
    )
    if not findings_path.exists():
        return []

    try:
        data = json.loads(findings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    lines: list[str] = []

    # Context health findings (warnings and critical only)
    for finding in data.get("findings", []):
        level = finding.get("level", "info")
        msg = _sanitize_prompt_field(finding.get("message", ""), 150)
        if level == "critical":
            lines.append(f"  - [critical] {msg}")
            # Auto-create Radar todo for critical findings
            try:
                _create_health_todo(msg, severity="critical")
            except Exception:
                pass  # Non-blocking
        elif level == "warning":
            lines.append(f"  - [warning] {msg}")

    # Weekly memory health summary
    mem_health = data.get("memory_health")
    if mem_health:
        actions = mem_health.get("actions", [])
        summary = mem_health.get("summary", "")
        if actions:
            action_text = ", ".join(a[:50] for a in actions[:3])
            lines.append(f"  - [maintenance] {action_text}")
        elif summary:
            lines.append(f"  - [maintenance] {_sanitize_prompt_field(summary, 100)}")

        # Capability gaps — recurring error patterns detected by weekly analysis
        gaps = mem_health.get("capability_gaps", [])
        for gap in gaps[:3]:
            pattern = _sanitize_prompt_field(gap.get("pattern", ""), 80)
            priority = gap.get("priority", "medium")
            occurrences = gap.get("occurrences", 0)
            action = _sanitize_prompt_field(gap.get("suggested_action", ""), 50)
            lines.append(
                f"  - [gap/{priority}] {pattern} ({occurrences}x) — suggest: {action}"
            )

        # Stale corrections — corrections referencing deleted code
        stale = mem_health.get("stale_corrections", [])
        for corr in stale[:2]:
            cid = corr.get("id", "")
            reason = _sanitize_prompt_field(corr.get("reason", ""), 60)
            lines.append(f"  - [stale-correction] {cid}: {reason}")

    # L4.0: DDD refresh proposals ready for review
    projects_dir = Path(working_directory) / "Projects"
    if projects_dir.is_dir():
        for project_dir in sorted(projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            artifacts = project_dir / ".artifacts"
            if not artifacts.is_dir():
                continue
            for proposal in sorted(artifacts.glob("ddd-refresh-*.md"), reverse=True):
                # Only show proposals from last 7 days
                try:
                    age_days = (datetime.now() - datetime.fromtimestamp(proposal.stat().st_mtime)).days
                    if age_days <= 7:
                        lines.append(
                            f"  - [ddd-proposal] {project_dir.name}: "
                            f"DDD refresh proposal ready ({proposal.name})"
                        )
                        break  # Only latest per project
                except OSError:
                    continue

            # L4.1: Skill proposals ready for review
            skill_proposals = artifacts / "skill-proposals"
            if skill_proposals.is_dir():
                for skill_dir in sorted(skill_proposals.iterdir(), reverse=True):
                    if not skill_dir.is_dir():
                        continue
                    meta_path = skill_dir / "metadata.json"
                    if not meta_path.exists():
                        continue
                    try:
                        age_days = (datetime.now() - datetime.fromtimestamp(meta_path.stat().st_mtime)).days
                        if age_days <= 7:
                            meta = json.loads(meta_path.read_text(encoding="utf-8"))
                            gap = meta.get("gap_pattern", "unknown gap")[:60]
                            conf = meta.get("confidence", "?")
                            lines.append(
                                f"  - [skill-proposal] {skill_dir.name}: "
                                f"addresses '{gap}' (confidence={conf})"
                            )
                    except (OSError, json.JSONDecodeError):
                        continue

    return lines


def _create_health_todo(message: str, severity: str = "warning") -> None:
    """Create a Radar todo for critical health findings.

    Only creates for severity="critical". Deduplicates by checking
    if an active todo with similar title already exists.
    """
    if severity != "critical":
        return

    try:
        from core.todo_manager import ToDoManager
        mgr = ToDoManager()
        title = f"Health Alert: {message[:80]}"

        # Check for existing active todo with same prefix
        existing = mgr.list_todos(status="active")
        for todo in existing:
            if todo.get("title", "").startswith("Health Alert:") and \
               message[:40] in todo.get("title", ""):
                return  # Already exists, don't duplicate

        mgr.create_todo(
            title=title,
            description=f"Auto-created by health alerting system.\n\nFinding: {message}",
            priority="high",
        )
    except Exception as exc:
        logger.warning("Failed to create health todo: %s", exc)


# ---------------------------------------------------------------------------
# Internal bridge functions (delegate to sub-modules with _DATE_REF_RE)
# ---------------------------------------------------------------------------

def _estimate_thread_age(thread: dict) -> int:
    """Estimate thread age using module-level _DATE_REF_RE."""
    return _estimate_thread_age.__wrapped__(thread, _DATE_REF_RE)

# Store original for delegation
_estimate_thread_age.__wrapped__ = _estimate_thread_age  # type: ignore[attr-defined]

# Actually fix the delegation properly — can't use __wrapped__ trick on ourselves.
# Instead, import the raw function under a different name.
from core.proactive_scoring import estimate_thread_age as _raw_estimate_thread_age  # noqa: E402


def _estimate_thread_age(thread: dict) -> int:  # noqa: F811 — intentional redefinition
    """Estimate thread age using module-level _DATE_REF_RE."""
    return _raw_estimate_thread_age(thread, _DATE_REF_RE)


def _build_suggestions(
    threads: list[dict],
    continue_hints: list[str],
    signals: list[str],
) -> list[ScoredItem]:
    """Bridge: call scoring engine's build_suggestions with _DATE_REF_RE."""
    return _build_suggestions_raw(threads, continue_hints, signals, _DATE_REF_RE)


# ---------------------------------------------------------------------------
# Briefing builder — main entry point
# ---------------------------------------------------------------------------

def build_session_briefing(
    workspace_dir: str | Path,
) -> Optional[str]:
    """Build a proactive session briefing from MEMORY.md + DailyActivity.

    Returns a compact markdown string (~200-400 tokens) for system prompt
    injection, or None if there's nothing actionable to surface.

    This function never raises — all exceptions are caught and logged.
    """
    try:
        workspace = Path(workspace_dir)
        memory_path = workspace / ".context" / "MEMORY.md"
        daily_dir = workspace / "Knowledge" / "DailyActivity"

        if not memory_path.exists():
            return None

        memory_text = memory_path.read_text(encoding="utf-8")

        # -- Parse components --
        threads = _parse_open_threads(memory_text)
        continue_hints = _parse_continue_hints(daily_dir)
        signals = _detect_patterns(threads, daily_dir, memory_text)

        # -- Read-time staleness filter --
        # Suppress threads whose topics appear in recent deliverables.
        # This is the safety net for when distillation hasn't run yet
        # (e.g. first session after a productive one).  See COE: memory
        # pipeline temporal lag gap (2026-03-19).
        threads = _filter_completed_threads(threads, daily_dir)

        # -- L3: Update learning state from previous session --
        learning_state = _load_learning_state(workspace)
        learning_state = _update_learning_from_activity(learning_state, daily_dir)

        # -- Build briefing (L2: ranked suggestions + L3: learning adjustments) --
        ranked = _build_suggestions(threads, continue_hints, signals)

        # Apply L3 learning adjustments
        for item in ranked:
            _apply_learning(item, learning_state)
        # Re-sort after adjustments
        priority_order = {"P0": 0, "P1": 1, "P2": 2}
        ranked.sort(key=lambda x: (-x.score, priority_order.get(x.priority, 3), x.title))

        if not ranked and not signals:
            return None

        focus_section, background_section = _format_suggestions(ranked)

        sections: list[str] = []

        if focus_section:
            sections.append(focus_section)

        # Include temporal/pattern signals that aren't about specific threads
        # (e.g. "First session today", "2 days since last session")
        non_thread_signals = [
            s for s in signals
            if not (s.startswith('"') or s.startswith("P0 "))
            and "reported" not in s.lower()
            and "pending rebuild" not in s.lower()
        ]
        if non_thread_signals:
            items = [f"  - {s}" for s in non_thread_signals]
            sections.append("**Signals:**\n" + "\n".join(items))

        if background_section:
            sections.append(background_section)

        # L4: External signal highlights from signal_digest.json
        signal_lines = _get_signal_highlights(str(workspace))
        if signal_lines:
            sections.append("**External signals since last session:**\n" + "\n".join(signal_lines))

        # L4: Recent job results from .job-results.jsonl
        job_lines = _get_job_result_highlights(str(workspace))
        if job_lines:
            sections.append("**Recent job results (last 24h):**\n" + "\n".join(job_lines))

        # L4: System health alerts from health_findings.json
        health_lines = _get_health_highlights(str(workspace))
        if health_lines:
            sections.append("**System health:**\n" + "\n".join(health_lines))

        # L3: Surface learning insight
        learning_insight = learning_state.learning_summary()
        if learning_insight:
            sections.append(f"**Learning:** {learning_insight}")

        if not sections:
            return None

        briefing = "## Session Briefing\n" + "\n".join(sections)

        # Token estimate sanity check
        token_est = len(briefing) // 4
        if token_est > 500 and len(sections) > 2:
            sections = [s for s in sections if not s.startswith("**Also in")]
            briefing = "## Session Briefing\n" + "\n".join(sections)

        # L3: Save current suggestions for next session's comparison
        learning_state.last_briefing_date = datetime.now().strftime("%Y-%m-%d")
        learning_state.last_briefing_suggested = [
            item.title for item in ranked[:3]
        ]
        _save_learning_state(workspace, learning_state)

        logger.info(
            "Proactive briefing (L4): %d chars, ~%d tokens, %d ranked, %d signals, "
            "ext_signals=%d, learning=%s, effectiveness=%s",
            len(briefing), len(briefing) // 4, len(ranked), len(signals),
            len(signal_lines), "active" if learning_insight else "gathering",
            learning_state.effectiveness.get("trend", "gathering"),
        )
        return briefing

    except Exception as exc:
        logger.warning("Proactive intelligence failed (non-blocking): %s", exc)
        return None


def build_session_briefing_data(
    workspace_dir: str | Path,
) -> dict[str, Any]:
    """Build a structured briefing dict for the frontend Welcome Screen.

    Returns a JSON-serializable dict with focus items, external signals,
    job results, and learning insights. Never raises — returns empty
    structure on any failure.

    This is the structured counterpart of ``build_session_briefing()``
    which returns a markdown string for the system prompt.
    """
    empty: dict[str, Any] = {
        "focus": [],
        "signals": [],
        "jobs": [],
        "learning": None,
        "generated_at": datetime.now().isoformat(),
    }
    try:
        workspace = Path(workspace_dir)
        memory_path = workspace / ".context" / "MEMORY.md"
        daily_dir = workspace / "Knowledge" / "DailyActivity"

        if not memory_path.exists():
            return empty

        memory_text = memory_path.read_text(encoding="utf-8")

        # Parse threads + hints (same logic as build_session_briefing)
        threads = _parse_open_threads(memory_text)
        continue_hints = _parse_continue_hints(daily_dir)
        signals = _detect_patterns(threads, daily_dir, memory_text)
        threads = _filter_completed_threads(threads, daily_dir)

        # Score and rank
        learning_state = _load_learning_state(workspace)
        learning_state = _update_learning_from_activity(learning_state, daily_dir)
        ranked = _build_suggestions(threads, continue_hints, signals)
        for item in ranked:
            _apply_learning(item, learning_state)
        priority_order = {"P0": 0, "P1": 1, "P2": 2}
        ranked.sort(key=lambda x: (-x.score, priority_order.get(x.priority, 3), x.title))

        # Build focus items
        focus = []
        for item in ranked[:5]:
            focus.append({
                "title": item.title,
                "priority": item.priority,
                "score": item.score,
                "source": item.source,
                "momentum": item.from_continue_hint,
            })

        # External signals from signal_digest.json
        ext_signals = []
        digest_path = workspace / "Services" / "signals" / "signal_digest.json"
        if digest_path.exists():
            try:
                data = json.loads(digest_path.read_text(encoding="utf-8"))
                cutoff = time.time() - 48 * 3600
                for sig in data.get("items", [])[:5]:
                    fetched = sig.get("fetched_at", "")
                    if isinstance(fetched, str) and fetched:
                        try:
                            dt = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
                            if dt.timestamp() < cutoff:
                                continue
                        except (ValueError, TypeError):
                            continue
                    ext_signals.append({
                        "title": sig.get("title", ""),
                        "summary": sig.get("summary", ""),
                        "source": sig.get("source", ""),
                        "url": sig.get("url", ""),
                        "urgency": sig.get("urgency", "medium"),
                        "relevance": sig.get("relevance_score", 0),
                    })
            except (json.JSONDecodeError, OSError):
                pass

        # Job results
        jobs = []
        jsonl_path = workspace / "Knowledge" / "JobResults" / ".job-results.jsonl"
        if jsonl_path.exists():
            try:
                cutoff_24h = time.time() - 24 * 3600
                for line in reversed(jsonl_path.read_text(encoding="utf-8").strip().splitlines()):
                    if len(jobs) >= 5:
                        break
                    try:
                        entry = json.loads(line)
                        ts = entry.get("run_at", entry.get("completed_at", ""))
                        if isinstance(ts, str) and ts:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if dt.timestamp() < cutoff_24h:
                                break  # older entries won't be newer
                        # Build short summary from JSONL summary field
                        raw_summary = str(entry.get("summary", "")).strip()
                        # Skip log-style output (timestamps, [INFO], etc.)
                        if raw_summary and not raw_summary[:1].isdigit() and "[INFO]" not in raw_summary[:30]:
                            short_summary = raw_summary[:120].rstrip()
                            if len(raw_summary) > 120:
                                short_summary += "…"
                        else:
                            short_summary = ""

                        # Construct path to the result markdown file
                        job_id = entry.get("job_id", "")
                        result_file = ""
                        if job_id and ts:
                            try:
                                date_str = dt.strftime("%Y-%m-%d")
                                slug = str(job_id).replace(" ", "-").lower()
                                candidate = workspace / "Knowledge" / "JobResults" / f"{date_str}-{slug}.md"
                                if candidate.exists():
                                    result_file = f"Knowledge/JobResults/{date_str}-{slug}.md"
                            except (ValueError, AttributeError):
                                pass

                        jobs.append({
                            "name": entry.get("job_name", entry.get("job_id", "")),
                            "status": entry.get("status", "unknown"),
                            "duration": entry.get("duration_seconds", 0),
                            "summary": short_summary,
                            "result_file": result_file,
                        })
                    except (json.JSONDecodeError, ValueError):
                        continue
            except OSError:
                pass

        # Learning insight
        learning = learning_state.learning_summary()

        return {
            "focus": focus,
            "signals": ext_signals,
            "jobs": jobs,
            "learning": learning,
            "generated_at": datetime.now().isoformat(),
        }

    except Exception as exc:
        logger.warning("Briefing data generation failed (non-blocking): %s", exc)
        return empty
