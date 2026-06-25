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
    get_dismissed_titles as _get_dismissed_titles,
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

def _parse_continue_hints(daily_dir: Path, max_files: int = 1) -> list[str]:
    """Extract **Next:** lines from the most recent session blocks.

    Only reads the last ``MAX_BLOCKS`` session blocks (``## HH:MM | ...``
    headings) from today's DailyActivity — older blocks are stale context
    from parallel tabs and shouldn't drive today's focus.

    Uses fuzzy word-overlap dedup to catch near-duplicate hints from
    parallel sessions (e.g. "Add users:read scopes to Slack app config"
    vs "Add users:read scopes in Slack App dashboard").
    """
    MAX_HINTS = 5   # scoring engine takes top 5 anyway
    MAX_BLOCKS = 5  # only look at the 5 most recent session blocks

    if not daily_dir.is_dir():
        return []

    da_files = sorted(
        [f for f in daily_dir.glob("*.md") if f.stem[:4].isdigit()],
        key=lambda f: f.stem,
        reverse=True,
    )[:max_files]

    # Collect hints from the last MAX_BLOCKS session blocks only
    candidates: list[str] = []
    for da_file in da_files:
        try:
            lines = da_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue

        # Find session block boundaries (## HH:MM | session_id | ...)
        block_starts: list[int] = [
            i for i, line in enumerate(lines)
            if line.startswith("## ") and "|" in line[:30]
        ]

        # Take only lines from the last MAX_BLOCKS blocks
        if block_starts:
            start_line = block_starts[-MAX_BLOCKS] if len(block_starts) > MAX_BLOCKS else 0
            recent_lines = lines[start_line:]
        else:
            recent_lines = lines

        file_hints: list[str] = []
        for line in recent_lines:
            stripped = line.strip()
            if not stripped.startswith("**Next:**"):
                continue
            hint = stripped.removeprefix("**Next:**").strip()
            if not hint or hint.startswith("Ongoing:"):
                continue
            file_hints.append(hint)

        # Reverse so newest entries (bottom of file) come first
        candidates.extend(reversed(file_hints))

    # Fuzzy dedup: two hints are "same" if >60% word overlap
    hints: list[str] = []
    seen_word_sets: list[set[str]] = []
    for hint in candidates:
        if len(hints) >= MAX_HINTS:
            break

        words = set(hint[:100].lower().split())
        if len(words) < 2:
            continue  # skip single-word or empty hints

        is_dup = False
        for existing_words in seen_word_sets:
            overlap = len(words & existing_words)
            smaller = min(len(words), len(existing_words))
            if smaller > 0 and overlap / smaller > 0.6:
                is_dup = True
                break

        if not is_dup:
            seen_word_sets.append(words)
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


def _filter_completed_hints(
    hints: list[str],
    daily_dir: Path,
    dismissed_titles: set[str],
) -> list[str]:
    """Remove continue hints that match recent deliverables or dismissed items.

    Same fuzzy-matching heuristic as _filter_completed_threads — ≥50% word
    overlap or substring match.  Also filters hints the user explicitly dismissed.
    """
    deliverables = _extract_recent_deliverables(daily_dir)
    if not deliverables and not dismissed_titles:
        return hints

    deliv_word_sets = [set(d.split()) for d in deliverables]
    dismissed_word_sets = [set(t.split()) for t in dismissed_titles]

    filtered: list[str] = []
    for hint in hints:
        # Check against deliverables
        if deliverables and fuzzy_title_matches_deliverable(
            hint, deliverables, deliv_word_sets,
        ):
            logger.debug("Filtered completed hint from briefing: %s", hint)
            continue

        # Check against dismissed titles (same fuzzy match)
        if dismissed_titles and fuzzy_title_matches_deliverable(
            hint, list(dismissed_titles), dismissed_word_sets,
        ):
            logger.debug("Filtered dismissed hint from briefing: %s", hint)
            continue

        filtered.append(hint)

    return filtered


def _filter_dismissed_ranked(
    ranked: list,
    dismissed: set[str],
) -> list:
    """Remove ranked ScoredItems whose titles fuzzy-match dismissed titles."""
    if not dismissed:
        return ranked
    dismissed_list = list(dismissed)
    dismissed_word_sets = [set(t.split()) for t in dismissed_list]
    return [
        item for item in ranked
        if not fuzzy_title_matches_deliverable(
            item.title, dismissed_list, dismissed_word_sets,
        )
    ]


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


_MAX_PIPELINE_RESUME_ATTEMPTS = 3
# Exponential cooldown between resume attempts (seconds): 30s, 60s, 120s
_RESUME_COOLDOWN_SECONDS = [30, 60, 120]


def _get_paused_pipeline_highlights(workspace: Path, max_items: int = 3) -> list[str]:
    """Scan for paused/running pipeline runs and produce auto-resume directives.

    Auto-resume strategy (max 3 attempts, with exponential cooldown 30s/60s/120s):
    - If resume_attempts < 3 AND cooldown elapsed: emit DIRECTIVE to auto-resume.
      Increments resume_attempts in run.json with file-level locking.
    - If cooldown not elapsed: skip (will be picked up on next session start).
    - If resume_attempts >= 3: emit informational-only line (human must intervene).
    - For "running" status (orphaned from crash): same logic — transition to
      "paused" first, preserving original checkpoint reason.

    Finds runs updated within the last 24h. Graceful no-op on any error.
    """
    import fcntl
    from datetime import datetime, timezone

    lines: list[str] = []
    try:
        projects_dir = workspace / "Projects"
        if not projects_dir.exists():
            return []

        now = time.time()
        max_age_seconds = 24 * 3600  # Only surface runs from last 24h

        # Directives (auto-resume) are rate-limited via [:max_items] (STEERING #1).
        # Exhausted runs are NOT — they collapse into one summary line so no stale
        # run is ever silently dropped from the briefing.
        # candidates: (mtime, line) for directives only.
        candidates: list[tuple[float, str]] = []
        # exhausted: (project_name, run_id, resume_executions) — collapsed into
        # one line below. resume_executions drives the delivery-vs-pipeline diagnosis.
        exhausted: list[tuple[str, str, int]] = []

        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            runs_dir = project_dir / ".artifacts" / "runs"
            if not runs_dir.exists():
                continue

            for run_dir in runs_dir.iterdir():
                run_file = run_dir / "run.json"
                if not run_file.exists():
                    continue
                try:
                    run_data = json.loads(run_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue

                status = run_data.get("status", "")
                if status not in ("paused", "running"):
                    continue

                # Check freshness FIRST — skip old runs before any mutation
                updated = run_data.get("updated_at", "")
                if updated:
                    try:
                        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        age = now - dt.timestamp()
                        if age > max_age_seconds:
                            continue
                    except (ValueError, TypeError):
                        pass  # Can't parse date — include it anyway

                # Save original updated_at BEFORE any mutation — used for cooldown check
                original_updated_at = run_data.get("updated_at", "")

                # Orphan detection: "running" status in a NEW session = previous
                # session crashed. Transition to "paused" preserving original reason.
                if status == "running":
                    run_data["status"] = "paused"
                    run_data["updated_at"] = datetime.now(timezone.utc).isoformat()
                    if "checkpoint" not in run_data:
                        run_data["checkpoint"] = {}
                    # Preserve original reason if it exists
                    if not run_data["checkpoint"].get("reason"):
                        run_data["checkpoint"]["reason"] = "session_crash_auto_detected"
                    try:
                        run_file.write_text(
                            json.dumps(run_data, indent=2), encoding="utf-8"
                        )
                    except OSError:
                        pass  # Best-effort
                    status = "paused"

                # Gather run metadata
                project_name = project_dir.name
                run_id = run_data.get("id", run_dir.name)
                requirement = run_data.get("requirement", "")[:60]
                stages = run_data.get("stages", [])
                completed_stages = [s.get("stage", "?") for s in stages
                                    if s.get("status") == "completed"]
                last_stage = completed_stages[-1] if completed_stages else "init"
                next_stage = run_data.get("checkpoint", {}).get("next_stage", "")
                resume_stage = next_stage or last_stage

                # Check resume attempts with file lock to prevent race conditions
                resume_attempts = run_data.get("resume_attempts", 0)

                if resume_attempts < _MAX_PIPELINE_RESUME_ATTEMPTS:
                    # Cooldown check: don't retry too fast after a failed resume.
                    # First attempt (resume_attempts=0) has no cooldown — the run
                    # just crashed and deserves immediate recovery. Subsequent
                    # attempts use exponential backoff (30s, 60s).
                    # Uses ORIGINAL updated_at (pre-mutation) to avoid orphan
                    # transition setting it to "now" which would always trigger cooldown.
                    if resume_attempts > 0:
                        cooldown_idx = min(resume_attempts - 1, len(_RESUME_COOLDOWN_SECONDS) - 1)
                        cooldown = _RESUME_COOLDOWN_SECONDS[cooldown_idx]
                        if original_updated_at:
                            try:
                                last_dt = datetime.fromisoformat(original_updated_at.replace("Z", "+00:00"))
                                elapsed = max(0, now - last_dt.timestamp())
                                if elapsed < cooldown:
                                    # Not enough time since last attempt — skip for now
                                    continue
                            except (ValueError, TypeError):
                                pass  # Can't parse — proceed with resume

                    # AUTO-RESUME: increment counter with exclusive lock
                    lock_file = run_dir / ".resume.lock"
                    fd = None
                    try:
                        fd = lock_file.open("w")
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                        # Re-read under lock to prevent TOCTOU race
                        run_data = json.loads(run_file.read_text(encoding="utf-8"))
                        resume_attempts = run_data.get("resume_attempts", 0)
                        if resume_attempts >= _MAX_PIPELINE_RESUME_ATTEMPTS:
                            # Another session already exhausted attempts
                            fd.close()
                            continue

                        run_data["resume_attempts"] = resume_attempts + 1
                        run_data["updated_at"] = datetime.now(timezone.utc).isoformat()
                        run_file.write_text(
                            json.dumps(run_data, indent=2), encoding="utf-8"
                        )
                        fd.close()
                    except OSError:
                        # Lock held by another session or IO error — skip
                        if fd is not None:
                            try:
                                fd.close()
                            except Exception:
                                pass
                        continue

                    attempt_label = f"attempt {resume_attempts + 1}/{_MAX_PIPELINE_RESUME_ATTEMPTS}"
                    line = (
                        f"  - 🚀 AUTO-RESUME ({attempt_label}): "
                        f"[{project_name}] \"{requirement}\" — "
                        f"resume from {resume_stage}. "
                        f"Execute: `artifact_cli.py run-resume "
                        f"--project {project_name} --run-id {run_id}` "
                        f"then invoke `s_autonomous-pipeline` with "
                        f"`--resume --run-id {run_id} --project {project_name}`."
                    )
                    mtime = run_file.stat().st_mtime
                    candidates.append((mtime, line))
                else:
                    # EXHAUSTED: collapse into one summary line below (NOT capped by
                    # max_items — every stale run must stay visible). Carry the
                    # execution count (R2) so the summary can diagnose WHICH failure
                    # mode this is: executed-0x = delivery (agent never ran the
                    # directive), executed-Nx = pipeline (resume runs, keeps failing).
                    resume_execs = run_data.get("resume_executions", 0)
                    exhausted.append((project_name, run_id, resume_execs))

        # Directives: rate-limited (STEERING #1), newest first, capped at max_items.
        # NOTE: max_items caps ONLY directives by design. The exhausted summary is
        # always appended (even at max_items=0) — stale runs must never be hidden.
        candidates.sort(key=lambda x: -x[0])
        lines = [line for _, line in candidates[:max_items]]

        # Exhausted: ONE collapsed summary line. The COUNT is always exact; the id
        # list is bounded (first N) so the briefing line can't grow unbounded as
        # stale runs accumulate. Preserves "exhausted"/"Manual intervention"
        # substrings the briefing reader (and tests) key off.
        if exhausted:
            _ID_CAP = 10
            shown = exhausted[:_ID_CAP]
            # R2: per-run ref carries execution count so each run's failure mode
            # is visible at a glance — "run_x [Proj] (executed 0×)" vs "(executed 3×)".
            run_refs = ", ".join(
                f"{rid} [{proj}] (executed {execs}×)" for proj, rid, execs in shown
            )
            overflow = len(exhausted) - len(shown)
            if overflow > 0:
                run_refs += f", +{overflow} more"

            # Diagnose the dominant failure mode. R2: a run that emitted 3
            # directives but was NEVER executed (execs==0) is a DELIVERY failure
            # (the agent never picked up the briefing directive) — a different
            # problem, with a different fix, than a run that resumed N times and
            # kept crashing (a PIPELINE failure). Conflating them was the bug.
            any_executed = any(execs > 0 for _, _, execs in exhausted)
            all_never_executed = all(execs == 0 for _, _, execs in exhausted)
            if all_never_executed:
                diagnosis = (
                    "directives emitted but NEVER executed (DELIVERY issue — the "
                    "auto-resume directive is not being picked up; investigate why "
                    "the briefing directive isn't acted on)"
                )
            elif any_executed:
                diagnosis = (
                    "resume executed but pipeline kept failing (PIPELINE issue — "
                    "needs diagnosis of why the run can't progress)"
                )
            else:  # pragma: no cover — defensive
                diagnosis = "mode unknown"
            # NOTE: "exhausted" + "Manual intervention needed" substrings are a
            # stable contract the briefing reader and tests key off — keep them.
            # R2 appends the failure-mode diagnosis; it does not replace them.
            lines.append(
                f"  - ⚠️ {len(exhausted)} pipeline(s) exhausted "
                f"{_MAX_PIPELINE_RESUME_ATTEMPTS} auto-resume attempts — "
                f"Manual intervention needed [{diagnosis}]: {run_refs}."
            )

    except Exception as exc:
        logger.debug("Paused pipeline scan failed: %s", exc)

    return lines


def _get_todo_highlights(max_items: int = 5) -> list[str]:
    """Read pending/overdue Radar todos from SQLite for system prompt injection.

    Direct SQLite read (sync, WAL mode safe). Returns formatted lines
    like ``  - [HIGH] Fix streaming bug — Next: reproduce in dev``.
    Graceful no-op if DB unavailable.
    """
    import sqlite3
    from jobs.paths import DB_PATH as _db_path
    if not _db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(_db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT title, priority, status, linked_context "
            "FROM todos WHERE status IN ('pending', 'overdue') "
            "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 "
            "WHEN 'low' THEN 2 ELSE 3 END, created_at DESC LIMIT ?",
            (max_items,),
        ).fetchall()
        conn.close()
    except (sqlite3.Error, OSError) as exc:
        logger.debug("Todo highlights read failed: %s", exc)
        return []

    lines: list[str] = []
    priority_labels = {"high": "HIGH", "medium": "MED", "low": "LOW"}
    for row in rows:
        label = priority_labels.get(row["priority"], "")
        prefix = f"[{label}] " if label else ""
        overdue = " ⚠️ OVERDUE" if row["status"] == "overdue" else ""
        next_step = ""
        if row["linked_context"]:
            try:
                ctx = json.loads(row["linked_context"])
                ns = ctx.get("next_step", "")
                if ns:
                    next_step = f" — Next: {ns[:80]}"
            except (json.JSONDecodeError, TypeError):
                pass
        lines.append(f"  - {prefix}{row['title']}{overdue}{next_step}")
    return lines


def _get_ddd_trust_summary(workspace: Path) -> list[str]:
    """F3: Surface DDD sections with low trust in session briefing.

    Reads maturity annotations from DDD docs directly (not a separate
    health file). Sections with trust=low or trust=very_low are surfaced
    so the agent knows to verify before relying on them.

    Returns max 5 lines to avoid briefing bloat (~50 tokens).
    """
    lines: list[str] = []
    projects_dir = workspace / "Projects"
    if not projects_dir.is_dir():
        return lines

    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        # Read maturity annotations from DDD docs
        for doc_name in ("TECH.md", "IMPROVEMENT.md", "PRODUCT.md"):
            doc_path = project_dir / doc_name
            if not doc_path.exists():
                continue
            try:
                content = doc_path.read_text(encoding="utf-8")
            except OSError:
                continue
            # Parse maturity comments: <!-- maturity: X | ... | trust: Y | ... -->
            for match in re.finditer(
                r"^##\s+(.+)\n<!-- maturity: (\w+) .+trust: (\w+)",
                content, re.MULTILINE
            ):
                section_name = match.group(1).strip()
                _level = match.group(2)
                trust = match.group(3)
                if trust in ("low", "very_low"):
                    lines.append(
                        f"{project_dir.name}/{doc_name} §{section_name} "
                        f"[trust:{trust}]"
                    )

    return lines[:5]


def _get_skill_health_highlights(ctx_dir: Path) -> list[str]:
    """Read skill_health.json and surface medium-confidence recommendations.

    Staleness filter: items unchanged for >7 days are suppressed. If the user
    hasn't acted on a recommendation in a week, repeating it every session
    is noise, not a reminder. The item stays in skill_health.json for the
    evolution pipeline — it just stops polluting the briefing.
    """
    import time as _time

    health_path = ctx_dir / "skill_health.json"
    if not health_path.exists():
        return []

    try:
        report = json.loads(health_path.read_text(encoding="utf-8"))
        # Staleness gate: if file hasn't been modified in >7 days, all items are stale
        file_age_days = (_time.time() - health_path.stat().st_mtime) / 86400
        if file_age_days > 7:
            return []  # All recommendations are stale — suppress entirely

        highlights = []
        for skill in report.get("skills", []):
            try:
                action = skill.get("action", "")
                if action == "recommend" and skill.get("recommendation"):
                    rec = skill["recommendation"]
                    evidence = rec.get("evidence_summary", [])
                    first_evidence = evidence[0] if evidence else "multiple corrections detected"
                    name = skill.get("skill_name", "unknown")
                    corr = skill.get("correction_count", 0)
                    fitness = skill.get("fitness_score", 0.0)
                    # G1: Include apply affordance when actionable changes exist
                    has_changes = bool(rec.get("changes"))
                    affordance = f'. Say "apply {name} fix" to review changes' if has_changes else ""
                    highlights.append(
                        f"[medium] **{name}** needs attention -- "
                        f"{corr} corrections, "
                        f"fitness {fitness:.1%}. "
                        f"Suggested: {first_evidence}{affordance}"
                    )
            except (KeyError, TypeError, ValueError):
                continue  # Skip malformed entry, don't lose all highlights
        return highlights[:3]  # Max 3 in briefing
    except Exception:
        return []


def _get_auto_apply_review_window(workspace: Path) -> list[str]:
    """Surface recent DDD auto-applies within the 72h review window (Gap #21).

    Reads the auto_refresh_log.jsonl and shows entries applied within the last
    72 hours with a countdown timer. This gives the user visibility into what
    was auto-changed and time to revert if something looks wrong.
    """
    log_path = workspace / ".context" / ".auto_refresh_log.jsonl"
    if not log_path.exists():
        return []

    import time as _t
    now = _t.time()
    review_window = 72 * 60 * 60  # 72 hours
    lines: list[str] = []

    try:
        for raw_line in log_path.read_text(encoding="utf-8").splitlines()[-20:]:
            if not raw_line.strip():
                continue
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            applied_at = entry.get("applied_at", 0)
            age = now - applied_at
            if age > review_window or age < 0:
                continue

            hours_left = max(0, int((review_window - age) / 3600))
            target = entry.get("target_file", "?")
            change = entry.get("description", entry.get("old_value", ""))[:50]
            lines.append(f"{target}: {change}… ({hours_left}h left to revert)")

    except (OSError, ValueError):
        pass

    return lines[:5]  # Max 5 items in briefing


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

    # Weekly memory health summary — only surface gaps (not routine maintenance)
    # Maintenance actions (stale memory cleanup, compression) are expected automated
    # housekeeping — showing them every session is pure noise.
    mem_health = data.get("memory_health")
    if mem_health:
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
            # Active-maintenance reflex (run_e681a61d): a HIGH-priority capability
            # gap stops being passive display — escalate it to an actionable Radar
            # todo so a recurring pain proposes its own fix. Dedup-guarded inside
            # _create_health_todo; medium/low gaps stay display-only (no noise).
            if priority == "high":
                try:
                    _create_health_todo(
                        f"{pattern} ({occurrences}x) — {action}",
                        severity="warning",
                        escalate=True,
                    )
                except Exception:
                    pass  # Non-blocking — escalation is best-effort

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

    # L4.2: Governance promotion candidates (Three-Layer Governance)
    # Signal file written by evolution_maintenance_hook when bias class reaches 3x
    governance_signal = (
        Path(working_directory) / ".context" / ".governance_promotion_candidates.json"
    )
    if governance_signal.exists():
        try:
            sig_data = json.loads(governance_signal.read_text(encoding="utf-8"))
            candidates = sig_data.get("candidates", {})
            if candidates:
                parts = [f"Bias {b} ({c}x)" for b, c in candidates.items()]
                lines.append(
                    f"  - [governance/promote] Promotion threshold reached: "
                    f"{', '.join(parts)} — run s_self-evolution PROMOTE"
                )
        except (json.JSONDecodeError, OSError):
            pass  # Graceful — malformed signal is not critical

    # L4.3: Evolution v3 — correction class tracker status
    try:
        from core.evolution.correction_tracker import CorrectionClassTracker
        tracker = CorrectionClassTracker()
        tracker_lines = tracker.briefing_lines()
        if tracker_lines:
            for tl in tracker_lines:
                lines.append(f"  - [evolution] {tl}")
    except Exception:
        pass  # Non-blocking — tracker absence must never break briefing

    # L4.4: Evolution governance proposals (L1) pending review
    evolution_proposals_path = (
        Path(working_directory) / ".context" / ".evolution_proposals.json"
    )
    if evolution_proposals_path.exists():
        try:
            evo_proposals = json.loads(
                evolution_proposals_path.read_text(encoding="utf-8")
            )
            gov_proposals = [
                p for p in evo_proposals if p.get("target") == "governance"
            ]
            if gov_proposals:
                for gp in gov_proposals[:3]:  # Cap at 3 to avoid briefing bloat
                    gc_id = gp.get("gc_id") or ""
                    source = gp.get("source_class") or ""
                    rule = _sanitize_prompt_field(gp.get("proposed_rule") or "", 80)
                    count = gp.get("occurrence_count", 0)
                    label = gc_id or source or "unknown"
                    lines.append(
                        f"  - [evolution/governance] {label}: "
                        f"\"{rule}\" ({count}x evidence)"
                    )
        except (json.JSONDecodeError, OSError, TypeError, KeyError):
            pass  # Graceful — malformed proposals must never break briefing

    return lines


def _create_health_todo(
    message: str, severity: str = "warning", escalate: bool = False
) -> None:
    """Create a Radar todo for a health finding — the propose-action reflex.

    Creates a todo when the finding is `critical`, OR when a caller explicitly
    opts in with `escalate=True` (a recurring/high-priority warning that should
    stop being passive display and become an action item). A plain `warning`
    with escalate=False is a no-op — this preserves the existing
    ddd_orchestrator.py caller's contract (it passes severity="warning" and must
    not flood todos every refresh).

    Writes via DIRECT sync sqlite3 into data.db `todos` (the proven
    `_get_todo_highlights` pattern). The previous implementation called
    ToDoManager.list_todos/create_todo — methods that DO NOT EXIST on the async
    ToDoManager, so the path threw on every call and was swallowed (a silent
    no-op masked by a mock in tests). Direct sqlite is sync-safe from this
    briefing context (no event loop bridging) and matches the read path.

    Deduplicates against existing active todos with the same finding prefix.
    Best-effort — never raises (briefing assembly must not break).
    """
    if severity != "critical" and not escalate:
        return

    import sqlite3
    import uuid
    from datetime import datetime, timezone
    from jobs.paths import DB_PATH as _db_path

    if not _db_path.exists():
        return

    title = f"Health Alert: {message[:80]}"
    dedup_key = message[:40]
    try:
        conn = sqlite3.connect(str(_db_path), timeout=5)
        try:
            # Dedup: skip if an active (pending/in_discussion) Health Alert todo
            # already covers this finding.
            existing = conn.execute(
                "SELECT title FROM todos WHERE status IN ('pending','in_discussion') "
                "AND title LIKE 'Health Alert:%'"
            ).fetchall()
            for (etitle,) in existing:
                if dedup_key in (etitle or ""):
                    return  # already tracked

            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO todos (id, workspace_id, title, description, source, "
                "source_type, status, priority, created_at, updated_at) "
                "VALUES (?, 'swarmws', ?, ?, 'health-alert', 'ai_detected', "
                "'pending', 'high', ?, ?)",
                (
                    str(uuid.uuid4()),
                    title,
                    f"Auto-created by health alerting system.\n\nFinding: {message}",
                    now, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Failed to create health todo: %s", exc)


# ---------------------------------------------------------------------------
# Internal bridge functions (delegate to sub-modules with _DATE_REF_RE)
# ---------------------------------------------------------------------------

from core.proactive_scoring import estimate_thread_age as _raw_estimate_thread_age  # noqa: E402


def _estimate_thread_age(thread: dict) -> int:
    """Estimate thread age using module-level _DATE_REF_RE."""
    return _raw_estimate_thread_age(thread, _DATE_REF_RE)


def _build_suggestions(
    threads: list[dict],
    continue_hints: list[str],
    signals: list[str],
) -> list[ScoredItem]:
    """Bridge: call scoring engine's build_suggestions with _DATE_REF_RE."""
    return _build_suggestions_raw(threads, continue_hints, signals, _DATE_REF_RE)


# M3b: Recurrence Radar — known hot-zone keywords mapped to a display label.
# A "zone" is a recurring failure cluster; the radar fires only when the
# CURRENT session touches one AND it has recurred >= threshold DISTINCT times.
# Keywords are COMPOUND/specific (not bare English) so generic mentions of
# "resume"/"streaming" in prose don't false-fire (adversarial run_123a6530).
_RADAR_ZONES: dict[str, tuple[str, ...]] = {
    "reconcile": ("reconcile", "truncated render", "tab-switch render"),
    "session lifecycle": ("session_unit", "session_router", "self-heal session"),
    "streaming render": ("streaming content loss", "stream state machine", "isstreaming"),
    "deploy": ("prod.sh build", "daemon restart", "deploy scope"),
}
_RADAR_THRESHOLD = 3  # a zone must have recurred this many DISTINCT times to warn


def _extract_what_failed_lines(improvement_text: str) -> list[str]:
    """Return the bullet lines under '## What Failed' (one line = one incident)."""
    lines: list[str] = []
    in_section = False
    for ln in improvement_text.splitlines():
        if ln.startswith("## "):
            in_section = ln.strip().lower().startswith("## what failed")
            continue
        if in_section and ln.lstrip().startswith("- "):
            lines.append(ln.lower())
    return lines


def compute_recurrence_radar(
    improvement_text: str,
    session_context: str,
    threshold: int = _RADAR_THRESHOLD,
) -> list[str]:
    """Zone-gated recurrence warnings (self-knowledge-loop M3b).

    Fires ONLY when the CURRENT session touches a tracked hot zone that has
    recurred >= `threshold` DISTINCT incidents in IMPROVEMENT.md "## What
    Failed". Count = number of distinct What-Failed BULLET LINES mentioning a
    zone keyword (NOT substring frequency across the whole doc — adversarial
    found that inflated counts 50-200x and fired on every session). Returns []
    when the session touches no recurring hot zone.
    """
    if not improvement_text or not session_context:
        return []
    ctx_lower = session_context.lower()
    failed_lines = _extract_what_failed_lines(improvement_text)
    if not failed_lines:
        return []

    out: list[str] = []
    for label, keywords in _RADAR_ZONES.items():
        # Does the CURRENT session touch this zone?
        if not any(kw in ctx_lower for kw in keywords):
            continue
        # Distinct prior incidents = What-Failed lines mentioning any keyword.
        count = sum(
            1 for line in failed_lines if any(kw in line for kw in keywords)
        )
        if count < threshold:
            continue
        out.append(
            f"⚠️ {label}-class: {count} prior incidents — treat a new {label} bug "
            f"as STRUCTURAL; before patching, ask if the underlying MODEL is wrong."
        )
    return out


def _detect_active_project(workspace: Path) -> str | None:
    """Detect the active project from Projects/ directory.

    Simple heuristic: if SwarmAI project has a code_intel.db, use it.
    Future: detect from recent DailyActivity file mentions.
    """
    projects_dir = workspace / "Projects"
    if not projects_dir.is_dir():
        return None
    # Check SwarmAI first (default project), then others
    for name in ["SwarmAI"] + sorted(
        d.name for d in projects_dir.iterdir()
        if d.is_dir() and d.name != "SwarmAI"
    ):
        if (projects_dir / name / "code_intel.db").exists():
            return name
    return None


# TTL cache for _detect_active_coding_project (avoids repeated FS traversal)
_coding_project_cache: dict[str, tuple[float, str | None]] = {}
_CODING_PROJECT_TTL = 60.0  # 60 seconds


def _detect_active_coding_project(workspace: Path) -> str | None:
    """Detect if the current session context suggests coding work.

    Cached for 60s to avoid repeated filesystem traversal during prompt assembly.

    Only returns a project name if there's evidence this session involves code:
    1. Recent DailyActivity (today/yesterday) mentions code files (.py, .ts, .rs, etc.)
    2. There's an active pipeline run (status=running) for a project with code_intel.db
    3. The most recent session's git activity shows uncommitted changes

    Returns None for non-coding sessions — saves ~300 tokens of route/risk briefing.
    """
    import time

    # TTL cache check — avoid repeated FS traversal within same prompt assembly
    cache_key = str(workspace)
    cached = _coding_project_cache.get(cache_key)
    if cached:
        ts, result = cached
        if time.time() - ts < _CODING_PROJECT_TTL:
            return result

    result = _detect_active_coding_project_impl(workspace)
    _coding_project_cache[cache_key] = (time.time(), result)
    return result


def _detect_active_coding_project_impl(workspace: Path) -> str | None:
    """Inner implementation without cache."""
    import time

    projects_dir = workspace / "Projects"
    if not projects_dir.is_dir():
        return None

    # Signal 1: Active pipeline run → definitely coding
    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        runs_dir = proj_dir / ".artifacts" / "runs"
        if not runs_dir.is_dir():
            continue
        # Check for any running pipeline (recently modified run.json)
        for run_dir in sorted(runs_dir.iterdir(), reverse=True)[:3]:
            run_json = run_dir / "run.json"
            if run_json.exists():
                try:
                    import json
                    data = json.loads(run_json.read_text())
                    if data.get("status") == "running":
                        if (proj_dir / "code_intel.db").exists():
                            return proj_dir.name
                except Exception:
                    continue

    # Signal 2: Today's DailyActivity mentions code-related files
    daily_dir = workspace / "Knowledge" / "DailyActivity"
    if daily_dir.is_dir():
        today = time.strftime("%Y-%m-%d")
        code_extensions = {".py", ".ts", ".tsx", ".js", ".rs", ".go", ".java"}
        da_files = [f for f in daily_dir.iterdir() if f.suffix == ".md" and f.stem[:4].isdigit()]
        for da_file in sorted(da_files, reverse=True)[:2]:
            if today in da_file.name:
                try:
                    content = da_file.read_text(errors="replace")[:5000]
                    # Check for code file mentions or git activity
                    if any(ext in content for ext in code_extensions) or "git" in content.lower():
                        return _detect_active_project(workspace)
                except Exception:
                    continue

    # Signal 3: Uncommitted changes in any indexed project
    for name in ["SwarmAI"] + sorted(
        d.name for d in projects_dir.iterdir()
        if d.is_dir() and d.name != "SwarmAI"
    ):
        if not (projects_dir / name / "code_intel.db").exists():
            continue
        # Read repo_root from code_intel.db meta (cheap — single row query)
        try:
            import sqlite3
            db_path = projects_dir / name / "code_intel.db"
            conn = sqlite3.connect(str(db_path), timeout=1)
            row = conn.execute("SELECT value FROM graph_meta WHERE key='repo_root'").fetchone()
            conn.close()
            if row:
                repo_root = Path(row[0])
                git_dir = repo_root / ".git"
                if git_dir.is_dir():
                    # Check for any staged/modified files (cheap: just check index mtime)
                    index_file = git_dir / "index"
                    if index_file.exists():
                        age = time.time() - index_file.stat().st_mtime
                        if age < 14400:  # git index touched in last 4 hours → active coding
                            return name
        except Exception:
            continue

    return None


# ---------------------------------------------------------------------------
# Briefing builder — main entry point
# ---------------------------------------------------------------------------

def _render_self_eval_lines(
    health: dict, tracker_red: bool, case_count: int
) -> list[str]:
    """Render the briefing self-eval line(s) with divergence awareness (M4-3).

    Pure (no I/O) so the rendering — especially the score↔reality divergence
    OVERRIDE — is testable on synthetic state. Two independent red signals,
    surfaced separately, NEVER conflated:

      - cases_error > 0  → judge INFRA broke (score measured a subset). Existing
        finding-1 red-light, preserved here.
      - tracker_red      → the AGENT is still repeating a known correction CLASS
        past its deployed gate (🔴) while the eval reads clean. This is the NEW
        M4-3 divergence: a high score that does NOT prove the loop is closed.

    Divergence takes the headline (it OVERRIDES the clean number) because a
    green score next to a recurring failure class is the precise "100/100 on a
    dead loop" lie the closed-loop design exists to expose.
    """
    try:
        if health.get("overall_score") is None:
            if case_count > 0:
                return [f"**Self-Eval:** {case_count} cases (no runs yet)"]
            return []

        score = health["overall_score"]
        last_run = health.get("last_run") or {}
        last_date = (last_run.get("triggered_at") or "")[:10] or "never"
        n_error = last_run.get("cases_error", 0) or 0

        from core.eval_service import EvalService

        div = EvalService.compute_score_divergence(health, tracker_red)

        # Two ORTHOGONAL red signals — surfaced separately, NEVER conflated by
        # suppression (adversarial #3): cases_error = judge INFRA broke (score
        # measured a subset); divergence = the AGENT is still recurring a known
        # class despite a clean score. When BOTH hold the user must see BOTH —
        # an early-return on divergence would hide the infra-break the design
        # promises to keep distinct. Divergence leads (it reframes the headline),
        # the infra-break follows as a second line.
        lines: list[str] = []
        if div["diverged"]:
            lines.append(
                f"**Self-Eval:** 🔴 DIVERGENCE — {div['reason']}. "
                f"{case_count} cases | Last: {last_date}"
            )
        if n_error:
            lines.append(
                f"**Self-Eval:** 🔴 {n_error} case(s) ERRORED (judge infra failed — "
                f"score {score} excludes them, NOT a clean pass). "
                f"{case_count} cases | Last: {last_date}"
            )
        if lines:
            return lines

        return [f"**Self-Eval:** {case_count} cases | Score: {score} | Last: {last_date}"]
    except Exception:
        # Briefing helpers must never raise.
        return []


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

        # -- Filter continue hints against deliverables + dismissed items --
        dismissed = _get_dismissed_titles(learning_state)
        continue_hints = _filter_completed_hints(continue_hints, daily_dir, dismissed)

        # -- Build briefing (L2: ranked suggestions + L3: learning adjustments) --
        ranked = _build_suggestions(threads, continue_hints, signals)

        # Apply L3 learning adjustments
        for item in ranked:
            _apply_learning(item, learning_state)
        # Re-sort after adjustments
        priority_order = {"P0": 0, "P1": 1, "P2": 2}
        ranked.sort(key=lambda x: (-x.score, priority_order.get(x.priority, 3), x.title))

        # Filter ranked items against user-dismissed titles
        ranked = _filter_dismissed_ranked(ranked, dismissed)

        # Quality filter: suppress low-signal P2 suggestions without momentum.
        # "MCP Gateway" showing every session when untouched for weeks = noise.
        # Keep items with: momentum, P0/P1, blocking, or high frequency.
        # Exception: if ALL items would be filtered, keep the top 1 (better than
        # empty focus section when there IS open work, just nothing urgent).
        high_signal = [item for item in ranked
                       if item.from_continue_hint
                       or item.priority in ("P0", "P1")
                       or item.blocks_others
                       or item.report_count >= 3]
        ranked = high_signal  # Empty is fine — no noise better than stale noise

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

        # L4: Paused pipelines — auto-resume directives (max 3 attempts) or informational
        pipeline_lines = _get_paused_pipeline_highlights(workspace)
        if pipeline_lines:
            sections.append(
                "**Pipeline auto-resume (execute immediately before other work):**\n"
                + "\n".join(pipeline_lines)
            )

        # L4: Pending Radar todos — surface so agent proactively addresses them
        todo_lines = _get_todo_highlights()
        if todo_lines:
            sections.append("**Pending Radar todos:**\n" + "\n".join(todo_lines))

        # L4: System health alerts from health_findings.json
        health_lines = _get_health_highlights(str(workspace))
        if health_lines:
            sections.append("**System health:**\n" + "\n".join(health_lines))

        # M3b: Recurrence Radar — zone-gated structural warning. Fires only when
        # the current session (recent DailyActivity) touches a hot zone that has
        # recurred >= threshold times in IMPROVEMENT.md. Counts derived from doc.
        try:
            improvement_path = workspace / "Projects" / "SwarmAI" / "IMPROVEMENT.md"
            if improvement_path.exists():
                # session_context = the MOST-RECENT activity block only (what
                # THIS session is doing) — NOT 2 days of history (adversarial:
                # the rolling log mentions every zone, defeating the gate).
                recent_ctx = ""
                if daily_dir.is_dir():
                    files = sorted(daily_dir.glob("*.md"), reverse=True)
                    if files:
                        try:
                            text = files[0].read_text(encoding="utf-8", errors="ignore")
                            # last "## HH:MM |" block = the current session's slice
                            blocks = re.split(r"\n## \d\d:\d\d \|", text)
                            recent_ctx = blocks[-1][:3000] if blocks else text[:3000]
                        except OSError:
                            recent_ctx = ""
                radar_lines = compute_recurrence_radar(
                    improvement_path.read_text(encoding="utf-8", errors="ignore"),
                    recent_ctx,
                )
                if radar_lines:
                    sections.append("**Recurrence Radar:**\n" + "\n".join(radar_lines))
        except Exception as exc:
            logger.debug("Recurrence radar failed: %s", exc)

        # L5: DDD escalations (risky changes needing human decision)
        # Only show escalations from the last 7 days — older ones are stale
        # (either user silently approved by not acting, or they're low priority)
        try:
            from core.ddd_cultivation import read_pending_proposals
            active_proj = _detect_active_project(workspace) or "SwarmAI"
            ddd_escalations = read_pending_proposals(workspace, active_proj)
            if ddd_escalations:
                import time as _t
                now = _t.time()
                # Keep items without created_at (can't judge staleness) + fresh items
                to_show = [p for p in ddd_escalations
                           if not hasattr(p, 'created_at')
                           or p.created_at is None
                           or (now - p.created_at) < 7 * 86400]
                # Fall back to all if filter removed everything
                if not to_show:
                    to_show = ddd_escalations
                if to_show:
                    esc_lines = [f"  - [{p.target_doc}] {p.content[:100]}" for p in to_show[:5]]
                    sections.append(
                        f"**DDD escalations ({len(to_show)} awaiting decision):**\n" + "\n".join(esc_lines)
                    )
        except Exception as exc:
            logger.debug("DDD escalations read failed: %s", exc)

        # L3b (F3): DDD low-trust sections — surface knowledge the agent should verify
        try:
            trust_lines = _get_ddd_trust_summary(workspace)
            if trust_lines:
                sections.append(
                    "**DDD low-trust sections** (verify before relying):\n"
                    + "\n".join(f"  - {line}" for line in trust_lines)
                )
        except Exception as exc:
            logger.debug("DDD trust summary failed: %s", exc)

        # Gap #21: L2 review window — show recent auto-applies with revert countdown
        try:
            review_lines = _get_auto_apply_review_window(workspace)
            if review_lines:
                sections.append(
                    "**DDD auto-applies (72h review window):**\n"
                    + "\n".join(f"  - {line}" for line in review_lines)
                )
        except Exception as exc:
            logger.debug("DDD review window failed: %s", exc)

        # L4: Self-Eval awareness (golden set health — lightweight, ~1 line)
        try:
            golden_path = workspace / "Projects" / "SwarmAI" / "golden_set.yaml"
            if golden_path.exists():
                from core.eval_service import get_eval_service
                svc = get_eval_service()
                health = svc.get_health()
                # M4-3: compute the mechanical red signal — is any correction
                # class recurring past its deployed gate? If so, a clean eval
                # score is a LIE worth overriding. Kept out of the pure renderer
                # (which must stay I/O-free + testable on synthetic state).
                tracker_red = False
                try:
                    from core.evolution.correction_tracker import CorrectionClassTracker

                    # GATE-FAILURE signal, not generic red (meta-review HIGH):
                    # divergence headlines ONLY a deployed structural gate that
                    # did not hold — the real "loop broken on a green score" event.
                    # has_red() (which includes rule-only chronic recurrence like
                    # the live OPERATIONAL class, 799x) would fire the banner EVERY
                    # session forever → banner-blindness. Rule-only red stays in the
                    # per-class tracker line; only gate-failure earns the headline.
                    # Computed from state (format-independent), not by emoji-scan.
                    tracker_red = CorrectionClassTracker().has_gate_failure()
                except Exception:
                    tracker_red = False  # tracker absence must never break briefing

                sections.extend(
                    _render_self_eval_lines(health, tracker_red, svc.case_count)
                )
        except Exception as exc:
            logger.debug("Self-eval briefing failed: %s", exc)

        # L4: Skill health recommendations from evolution pipeline
        ctx_dir = workspace / ".context"
        skill_health_lines = _get_skill_health_highlights(ctx_dir)
        if skill_health_lines:
            sections.append("**Skill health:**\n" + "\n".join(f"  - {line}" for line in skill_health_lines))

        # L5: Codebase intelligence from code_intel.db
        # Only inject when the session is likely code-related:
        # - Session has a bound project with code_intel.db, OR
        # - Recent DailyActivity mentions code files
        # Skip for non-coding sessions (chat, research, reports) to save ~300 tokens.
        try:
            from core.code_intel.codebase_map import generate_codebase_map
            active_project = _detect_active_coding_project(workspace)
            if active_project:
                codebase_ctx = generate_codebase_map(active_project)
                if codebase_ctx:
                    sections.append(f"**Codebase intelligence ({active_project}):**\n{codebase_ctx}")
        except ImportError:
            pass  # code_intel not available
        except Exception as exc:
            logger.debug("Codebase map generation failed: %s", exc)

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


def _tail_read_lines(path: Path, max_bytes: int = 4096) -> list[str]:
    """Read the last `max_bytes` of a file and return complete lines.

    O(1) regardless of total file size — seeks to end and reads backward.
    Discards the first (likely partial) line to ensure valid JSON per line.
    Falls back to full read for files smaller than max_bytes.
    """
    file_size = path.stat().st_size
    if file_size <= max_bytes:
        # Small file — read all
        return path.read_text(encoding="utf-8").strip().splitlines()

    with open(path, "rb") as f:
        f.seek(-max_bytes, 2)  # seek from end
        tail = f.read().decode("utf-8", errors="replace")

    lines = tail.splitlines()
    # First line is likely partial (we seeked into the middle) — discard it
    if len(lines) > 1:
        lines = lines[1:]
    return [ln for ln in lines if ln.strip()]


# ── Briefing Hub v2 helpers ──────────────────────────────────────────


def _extract_report_field(text: str, field: str, fallback: str = "") -> str:
    """Extract a meaningful title from a pipeline REPORT.md.

    Priority order:
    1. YAML frontmatter `title:` field (strip "Pipeline Report:" prefix)
    2. Markdown H1 title (strip "Pipeline Report:" / "Pipeline Report —" prefix)
    3. "## N. <field>" section content (first line)
    4. Fallback string
    """
    import re

    # 1. YAML frontmatter: title: "Pipeline Report: Feature Name" or title: "Feature Name"
    fm_match = re.search(r'^---\s*\n.*?^title:\s*["\']?(.+?)["\']?\s*$.*?^---', text, re.MULTILINE | re.DOTALL)
    if fm_match:
        title = fm_match.group(1).strip()
        # Strip common prefixes to get the meaningful feature name
        title = re.sub(r'^Pipeline\s+Report\s*[:—–\-]\s*', '', title)
        if title:
            return title[:120]

    # 2. Markdown H1: "# Pipeline Report: Feature Name" or "# Pipeline Report — Feature Name"
    h1_match = re.search(r'^#\s+(.+?)$', text, re.MULTILINE)
    if h1_match:
        title = h1_match.group(1).strip()
        # Strip prefix variants: "Pipeline Report:", "Pipeline Report —", "Pipeline Report -"
        cleaned = re.sub(r'^Pipeline\s+Report\s*[:—–\-]\s*', '', title)
        # Strip "Autonomous Pipeline Report" variant
        cleaned = re.sub(r'^Autonomous\s+Pipeline\s+Report\s*[:—–\-]?\s*', '', cleaned)
        # Strip run IDs: "(run_abc123)" or bare "run_abc123"
        cleaned = re.sub(r'\s*\(run_[a-f0-9]+\)\s*$', '', cleaned)
        cleaned = re.sub(r'^run_[a-f0-9]+$', '', cleaned)
        if cleaned and cleaned != title and 'Pipeline Report' not in cleaned:
            return cleaned[:120]
        # If H1 has meaningful content without Pipeline Report prefix
        if cleaned and 'Pipeline Report' not in cleaned:
            return cleaned[:120]

    # 3. Section-based extraction: "## N. Requirement\n<content>" or "## Requirement\n<content>"
    for pattern in [
        rf"##\s*\d+\.\s*{field}\s*\n(.+?)(?:\n##|\Z)",
        rf"##\s*{field}\s*\n(.+?)(?:\n##|\Z)",
    ]:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            for line in m.group(1).strip().split('\n'):
                line = line.strip().lstrip('> -')
                if line:
                    return line[:120]

    # 4. Last resort: first line of ## Summary (better than generic "Pipeline Report")
    summary_match = re.search(r"##\s*(?:TL;DR|Summary)\s*\n(.+?)(?:\n##|\Z)", text, re.DOTALL)
    if summary_match:
        for line in summary_match.group(1).strip().split('\n'):
            line = line.strip().lstrip('> -')
            if line:
                # Truncate long summaries to first sentence
                sentence = re.split(r'[.。!]\s', line)[0]
                return sentence[:120]

    return fallback


def _extract_report_confidence(text: str) -> int | None:
    """Extract confidence score (N/10) from REPORT.md header."""
    import re
    m = re.search(r"\*\*Confidence:\*\*\s*(\d+)/10", text)
    return int(m.group(1)) if m else None


def _extract_first_heading(file_path: Path) -> str:
    """Extract title from markdown file — checks YAML frontmatter title, then first heading."""
    try:
        text = file_path.read_text(encoding="utf-8")[:500]
        lines = text.splitlines()
        # Check YAML frontmatter for title field
        if lines and lines[0].strip() == "---":
            for line in lines[1:]:
                if line.strip() == "---":
                    break
                if line.strip().startswith("title:"):
                    val = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val[:120]
        # Fall back to first markdown heading
        for line in lines:
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("#").strip()[:120]
    except (OSError, UnicodeDecodeError):
        pass
    return file_path.stem


def _detect_content_media_type(content_dir: Path) -> str:
    """Detect media type from files in a Pollinate content directory."""
    try:
        all_files = set()
        for p in content_dir.rglob("*"):
            if p.is_file():
                all_files.add(p.suffix.lower())
        if ".mp4" in all_files:
            return "video"
        if ".wav" in all_files or ".mp3" in all_files:
            # wav + srt = video production (TTS audio for video)
            if ".srt" in all_files:
                return "video"
            return "podcast"
        if ".html" in all_files or ".png" in all_files:
            return "poster"
    except OSError:
        pass
    return "article"


def _extract_jobs_summary() -> dict[str, Any]:
    """Build jobs summary from system + user job definitions and scheduler state."""
    jobs_list: list[dict] = []
    total = healthy = failed = disabled = 0
    last_run: str | None = None

    try:
        from jobs.system_jobs import SYSTEM_JOBS
        from jobs.models import SchedulerState
        import yaml

        # Load scheduler state
        from jobs.paths import STATE_FILE as _state_file
        state_path = _state_file
        state_data: dict = {}
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        job_states = state_data.get("jobs", {})

        # Collect all job definitions (system + user)
        all_jobs = list(SYSTEM_JOBS)
        from jobs.paths import USER_JOBS_FILE as _user_jobs_file
        user_jobs_path = _user_jobs_file
        if user_jobs_path.exists():
            try:
                from jobs.models import Job
                user_data = yaml.safe_load(user_jobs_path.read_text(encoding="utf-8"))
                for jd in user_data.get("jobs", []):
                    try:
                        all_jobs.append(Job(**jd))
                    except Exception:
                        pass
            except Exception:
                pass

        for job in all_jobs:
            total += 1
            js = job_states.get(job.id, {})
            job_last_run = js.get("last_run")
            job_last_status = js.get("last_status")

            if not job.enabled:
                disabled += 1
                status = "disabled"
            elif job_last_status == "failed" or job_last_status == "error":
                failed += 1
                status = "failed"
            else:
                healthy += 1
                status = "healthy"

            # Track global last run
            if job_last_run and (last_run is None or job_last_run > last_run):
                last_run = job_last_run

            # Human-readable schedule
            schedule = job.schedule
            if schedule.startswith("after:"):
                schedule = f"after {schedule[6:]}"

            jobs_list.append({
                "id": job.id,
                "name": job.name,
                "status": status,
                "lastRun": job_last_run,
                "lastStatus": job_last_status,
                "schedule": schedule,
            })
    except Exception as exc:
        logger.debug("Jobs summary extraction failed (non-blocking): %s", exc)

    return {
        "total": total,
        "healthy": healthy,
        "failed": failed,
        "disabled": disabled,
        "lastRun": last_run,
        "jobs": jobs_list,
    }


def _extract_working_items(workspace: Path) -> list[dict]:
    """Extract actionable work items from morning-inbox, morning-reflect, channel-monitor.

    Parses RADAR_TODOS JSON blocks from job result markdown files.
    Falls back to extracting "Urgent" section items from morning-reflect.
    """
    from datetime import timedelta, timezone as _tz

    items: list[dict] = []
    # Use local time for filenames — JobResults files are named with local dates
    _now = datetime.now(_tz.utc).astimezone()
    today_str = _now.strftime("%Y-%m-%d")
    results_dir = workspace / "Knowledge" / "JobResults"
    if not results_dir.is_dir():
        return items

    # Try morning-inbox RADAR_TODOS first (structured JSON)
    for job_id in ("morning-inbox", "morning-reflect", "channel-monitor"):
        result_file = results_dir / f"{today_str}-{job_id}.md"
        if not result_file.exists():
            # Try yesterday
            yesterday = (_now - timedelta(days=1)).strftime("%Y-%m-%d")
            result_file = results_dir / f"{yesterday}-{job_id}.md"
            if not result_file.exists():
                continue

        try:
            text = result_file.read_text(encoding="utf-8")

            # Extract RADAR_TODOS JSON block
            import re
            radar_match = re.search(
                r"<!--\s*RADAR_TODOS\s*\n(.*?)\n\s*-->",
                text, re.DOTALL,
            )
            if radar_match:
                try:
                    todos_data = json.loads(radar_match.group(1))
                    for todo in todos_data:
                        ctx = todo.get("context", {})
                        source = "email"
                        if job_id == "channel-monitor":
                            source = "slack-channel"
                        elif ctx.get("channel"):
                            source = "slack-dm"

                        items.append({
                            "title": todo.get("title", ""),
                            "priority": todo.get("priority", "medium"),
                            "source": source,
                            "sourceDetail": ctx.get("email_from", ctx.get("message_from", "")),
                            "summary": todo.get("description", "")[:150],
                            "action": ctx.get("suggested_action", "review"),
                            "resultFile": str(result_file.relative_to(workspace)),
                            "timestamp": ctx.get("email_date", ""),
                        })
                except (json.JSONDecodeError, TypeError):
                    pass

            # Fallback: extract "## Urgent" section from morning-reflect
            if job_id == "morning-reflect" and not items:
                urgent_match = re.search(
                    r"##\s*Urgent.*?\n(.*?)(?:\n##|\Z)",
                    text, re.DOTALL | re.IGNORECASE,
                )
                if urgent_match:
                    for line in urgent_match.group(1).splitlines():
                        line = line.strip().lstrip("-*").strip()
                        if line and len(line) > 10:
                            items.append({
                                "title": line[:120],
                                "priority": "high",
                                "source": "reflect",
                                "sourceDetail": "",
                                "summary": "",
                                "action": "review",
                                "resultFile": str(result_file.relative_to(workspace)),
                                "timestamp": "",
                            })
        except (OSError, UnicodeDecodeError):
            continue

    # Deduplicate by title
    seen_titles: set[str] = set()
    unique: list[dict] = []
    for item in items:
        if item["title"] not in seen_titles:
            seen_titles.add(item["title"])
            unique.append(item)

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    unique.sort(key=lambda x: priority_order.get(x.get("priority", "low"), 3))
    return unique[:10]


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
        "hotNews": [],
        "working": [],
        "stocks": [],
        "output": {"builds": [], "content": [], "files": []},
        "jobsSummary": {"total": 0, "healthy": 0, "failed": 0, "disabled": 0, "lastRun": None, "jobs": []},
        "todos": [],
        "learning": None,
        "generated_at": datetime.now().isoformat(),
        "jobs": [],  # backward compat
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

        # Filter continue hints against deliverables + dismissed items
        dismissed = _get_dismissed_titles(learning_state)
        continue_hints = _filter_completed_hints(continue_hints, daily_dir, dismissed)

        ranked = _build_suggestions(threads, continue_hints, signals)
        for item in ranked:
            _apply_learning(item, learning_state)
        priority_order = {"P0": 0, "P1": 1, "P2": 2}
        ranked.sort(key=lambda x: (-x.score, priority_order.get(x.priority, 3), x.title))

        # Filter ranked items against user-dismissed titles
        ranked = _filter_dismissed_ranked(ranked, dismissed)

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

        # ── Briefing Hub v2: Working items from job outputs ────────────
        working_items = _extract_working_items(workspace)

        # ── Briefing Hub v2: area-based signal extraction ──────────────
        # Split signal_digest.json into signals (tech) vs hotNews (trending).
        # Keep source language (D9), include lang/feed_id/platform fields.
        _TRENDING_FEEDS = frozenset({"china-trending"})
        signals_list: list[dict] = []
        hot_news_list: list[dict] = []
        digest_path = workspace / "Services" / "signals" / "signal_digest.json"
        if digest_path.exists():
            try:
                data = json.loads(digest_path.read_text(encoding="utf-8"))
                cutoff = time.time() - 48 * 3600
                for sig in data.get("items", []):
                    fetched = sig.get("fetched_at", "")
                    if isinstance(fetched, str) and fetched:
                        try:
                            dt_val = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
                            if dt_val.timestamp() < cutoff:
                                continue
                        except (ValueError, TypeError):
                            continue
                    else:
                        continue
                    feed_id = sig.get("feed_id", "")
                    is_trending = feed_id in _TRENDING_FEEDS

                    if is_trending:
                        if len(hot_news_list) < 10:
                            hot_news_list.append({
                                "title": sig.get("title", ""),
                                "platform": sig.get("platform", sig.get("source", "")),
                                "rank": sig.get("rank", 0),
                                "url": sig.get("url", ""),
                                "region": sig.get("region", "cn"),
                                "lang": sig.get("lang", "zh"),
                            })
                    else:
                        if len(signals_list) < 8:
                            raw_source = sig.get("source", "")
                            # For GitHub/commits, source is a programming language —
                            # use feed label as the readable source instead.
                            _FEED_SOURCE_LABELS = {
                                "frontier-labs": "Frontier Labs",
                                "ai-leaders": "AI Leaders",
                                "ai-engineering": "AI Engineering",
                                "ai-newsletters": "Newsletter",
                                "tool-releases": "Tool Release",
                                "github-trending": "GitHub Trending",
                                "reference-commits": "Repo Update",
                            }
                            _LANG_SOURCE_FEEDS = {"github-trending", "reference-commits"}
                            source_label = (
                                _FEED_SOURCE_LABELS.get(feed_id, raw_source)
                                if feed_id in _LANG_SOURCE_FEEDS
                                else raw_source
                            )
                            signals_list.append({
                                "title": sig.get("title", ""),
                                "summary": sig.get("summary", ""),
                                "source": source_label,
                                "sourceUrl": sig.get("url", ""),
                                "urgency": sig.get("urgency", "medium"),
                                "relevance": sig.get("relevance_score", 0),
                                "lang": sig.get("lang", "en"),
                                "feedId": feed_id,
                            })
            except (json.JSONDecodeError, OSError):
                pass
        # Backward compat: ext_signals used by _get_signal_highlights for system prompt
        ext_signals = signals_list[:5]

        # Job results — tail-read optimization: only read the last ~4KB
        # of the JSONL file instead of loading the entire file into memory.
        # This is O(1) regardless of file size (the file grows unbounded).
        jobs = []
        jsonl_path = workspace / "Knowledge" / "JobResults" / ".job-results.jsonl"
        if jsonl_path.exists():
            try:
                cutoff_24h = time.time() - 24 * 3600
                tail_lines = _tail_read_lines(jsonl_path, max_bytes=4096)
                # Sort by timestamp descending — concurrent job writes may
                # violate JSONL append order within the tail window.
                parsed_entries = []
                for line in tail_lines:
                    try:
                        parsed_entries.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue
                parsed_entries.sort(
                    key=lambda e: e.get("run_at", e.get("completed_at", "")),
                    reverse=True,
                )
                for entry in parsed_entries:
                    if len(jobs) >= 5:
                        break
                    try:
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

        # Pending Radar todos — direct SQLite read (sync, WAL mode safe).
        # This function is always called from sync context, so no async needed.
        todos: list[dict[str, Any]] = []
        try:
            import sqlite3
            from jobs.paths import DB_PATH as _db_path_todos
            db_path = _db_path_todos
            if db_path.exists():
                conn = sqlite3.connect(str(db_path), timeout=5)
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        "SELECT id, title, priority, status, due_date, linked_context "
                        "FROM todos WHERE status IN ('pending', 'overdue') "
                        "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 "
                        "WHEN 'low' THEN 2 ELSE 3 END, created_at DESC LIMIT 5"
                    ).fetchall()
                    for row in rows:
                        ctx = {}
                        if row["linked_context"]:
                            try:
                                ctx = json.loads(row["linked_context"])
                            except (json.JSONDecodeError, TypeError):
                                pass
                        todos.append({
                            "id": row["id"][:8],
                            "title": row["title"],
                            "priority": row["priority"],
                            "status": row["status"],
                            "due_date": row["due_date"],
                            "next_step": ctx.get("next_step", ""),
                        })
                finally:
                    conn.close()
        except Exception as exc:
            logger.debug("Todo briefing fetch failed (non-blocking): %s", exc)

        # ── Stock reports ────────────────────────────────────────────
        # Show today's reports; if none (weekend/holiday), fall back to most recent date
        stocks: list[dict] = []
        reports_dir = workspace / "Services" / "stock-analysis" / "reports"
        if reports_dir.is_dir():
            try:
                today_str = datetime.now().strftime("%Y-%m-%d")
                # Sorted reverse = newest first; detect target date from first .md file
                all_reports = sorted(
                    (f for f in reports_dir.iterdir() if f.name.endswith(".md")),
                    reverse=True,
                )
                target_date: str | None = None
                for f in all_reports:
                    parts = f.stem.split("-", 3)  # YYYY-MM-DD-rest
                    if len(parts) < 4:
                        continue
                    file_date = f"{parts[0]}-{parts[1]}-{parts[2]}"
                    # Prefer today; otherwise lock to first (most recent) date seen
                    if target_date is None:
                        target_date = today_str if f.name.startswith(today_str) else file_date
                    if not f.name.startswith(target_date):
                        continue
                    rest = parts[3]  # "515070-人工智能ETF"
                    ticker_parts = rest.split("-", 1)
                    ticker = ticker_parts[0]
                    name = ticker_parts[1] if len(ticker_parts) > 1 else ticker
                    # Check file size as proxy for success (> 500 bytes = has content)
                    status = "success" if f.stat().st_size > 500 else "partial"
                    stocks.append({
                        "ticker": ticker,
                        "name": name,
                        "status": status,
                        "reportFile": f"Services/stock-analysis/reports/{f.name}",
                    })
                    if len(stocks) >= 15:
                        break
            except OSError:
                pass

        # ── Pipeline builds ──────────────────────────────────────────
        builds: list[dict] = []
        projects_dir = workspace / "Projects"
        if projects_dir.is_dir():
            try:
                _json = json  # alias for local scope readability
                cutoff_7d = time.time() - 7 * 86400
                for proj_dir in projects_dir.iterdir():
                    runs_dir = proj_dir / ".artifacts" / "runs"
                    if not runs_dir.is_dir():
                        continue
                    for run_dir in runs_dir.iterdir():
                        report = run_dir / "REPORT.md"
                        run_json = run_dir / "run.json"
                        if not run_json.exists():
                            continue
                        if run_json.stat().st_mtime < cutoff_7d:
                            continue
                        # Use run.json completed_at for authoritative date
                        try:
                            run_data = _json.loads(run_json.read_text(encoding="utf-8"))
                        except (ValueError, OSError):
                            continue
                        if run_data.get("status") != "completed":
                            continue
                        # Prefer completed_at from run.json for sort order
                        completed_at = run_data.get("completed_at", "")
                        if not completed_at:
                            completed_at = datetime.fromtimestamp(run_json.stat().st_mtime).isoformat()
                        # Only show builds that have a proper REPORT.md (= DELIVER completed)
                        if not report.exists():
                            continue
                        # Title priority: run.json requirement (unique per run) > REPORT.md extraction
                        title = ""
                        req = run_data.get("requirement", "")
                        if req and len(req) > 10:
                            # Truncate at sentence boundary (period/。 followed by space or EOL)
                            import re as _re
                            _sent = _re.split(r'[.。]\s', req, maxsplit=1)
                            title = _sent[0][:120]
                        if not title:
                            text = report.read_text(encoding="utf-8")[:600]
                            title = _extract_report_field(text, "Requirement", "Pipeline Report")
                        else:
                            text = report.read_text(encoding="utf-8")[:600]
                        confidence = _extract_report_confidence(text)
                        report_file = str(report.relative_to(workspace))
                        builds.append({
                            "runId": run_dir.name,
                            "project": proj_dir.name,
                            "title": title or f"{proj_dir.name} pipeline",
                            "confidence": confidence,
                            "status": "complete",
                            "date": completed_at,
                            "reportFile": report_file,
                        })
                builds.sort(key=lambda x: x["date"], reverse=True)
                builds = builds[:10]
            except OSError:
                pass

        # ── Pollinate content ────────────────────────────────────────
        content_items: list[dict] = []
        # Primary: Knowledge/Pollinate/ (visible in Explorer, git-tracked)
        # Fallback: Services/pollinate-studio/content/ (legacy, hidden)
        studio_content = workspace / "Knowledge" / "Pollinate"
        if not studio_content.is_dir():
            studio_content = workspace / "Services" / "pollinate-studio" / "content"
        if studio_content.is_dir():
            try:
                cutoff_30d = time.time() - 30 * 86400
                for slug_dir in studio_content.iterdir():
                    if not slug_dir.is_dir():
                        continue
                    # Two formats: (1) content_package.md (legacy), (2) run.json (current)
                    pkg = slug_dir / "content_package.md"
                    run_json = slug_dir / "run.json"
                    if pkg.exists():
                        # Legacy format: title from markdown heading, date from mtime
                        if pkg.stat().st_mtime < cutoff_30d:
                            continue
                        title = _extract_first_heading(pkg)
                        media_type = _detect_content_media_type(slug_dir)
                        item_date = datetime.fromtimestamp(pkg.stat().st_mtime).isoformat()
                        content_path = str(pkg.relative_to(workspace))
                    elif run_json.exists():
                        # Current format: title from run.json topic, date from created_at
                        if run_json.stat().st_mtime < cutoff_30d:
                            continue
                        try:
                            run_data = json.loads(run_json.read_text())
                        except (json.JSONDecodeError, OSError):
                            continue
                        if run_data.get("type") != "pollinate":
                            continue
                        title = run_data.get("topic") or run_data.get("message", slug_dir.name)
                        media_type = _detect_content_media_type(slug_dir)
                        item_date = run_data.get("created_at", datetime.fromtimestamp(run_json.stat().st_mtime).isoformat())
                        content_path = str(run_json.relative_to(workspace))
                    else:
                        continue
                    content_items.append({
                        "slug": slug_dir.name,
                        "title": title,
                        "type": media_type,
                        "contentPackage": content_path,
                        "date": item_date,
                    })
                content_items.sort(key=lambda x: x["date"], reverse=True)
                content_items = content_items[:10]
            except OSError:
                pass

        # ── Jobs summary ─────────────────────────────────────────────
        jobs_summary = _extract_jobs_summary()

        # ── Output (unified Swarm output: builds + content + files) ─
        # files = existing artifacts (recently modified workspace files from git)
        artifact_files: list[dict] = []
        # Reuse existing logic from RadarView — lightweight git status scan
        # For now, keep it empty; ArtifactsSection fetches independently via
        # its own API. We'll wire this when the frontend consumes it.

        output = {
            "builds": builds,
            "content": content_items,
            "files": artifact_files,
        }

        # Learning insight
        learning = learning_state.learning_summary()

        return {
            "focus": focus,
            "signals": signals_list,
            "hotNews": hot_news_list,
            "working": working_items,
            "stocks": stocks,
            "output": output,
            "jobsSummary": jobs_summary,
            "todos": todos,
            "learning": learning,
            "generated_at": datetime.now().isoformat(),
            # Backward compat (removed in next release)
            "jobs": jobs,
        }

    except Exception as exc:
        logger.warning("Briefing data generation failed (non-blocking): %s", exc)
        return empty


def get_focus_keywords(workspace_dir: str | Path) -> str:
    """Extract keyword string from session briefing focus items.

    Used by Progressive Memory Disclosure to select relevant MEMORY.md
    sections at prompt-assembly time.  The user's actual first message
    isn't available yet (system prompt is built before the user types),
    so we use the briefing's predicted focus as a keyword proxy.

    Returns a space-separated string of focus item titles and sources,
    suitable for keyword matching.  Never raises — returns empty string
    on any failure.
    """
    try:
        data = build_session_briefing_data(workspace_dir)
        keywords: list[str] = []
        for item in data.get("focus", []):
            title = item.get("title", "")
            if title:
                keywords.append(title)
        return " ".join(keywords)
    except Exception:
        return ""
