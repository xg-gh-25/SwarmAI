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

    # Sort by multi-dim final_score descending (falls back to relevance_score
    # for legacy items written before ranking upgrade). final_score folds in
    # tier authority + urgency + freshness, so a frontier/official item outranks
    # a raw-relevant-but-low-tier one instead of tying at the 1.0 cap.
    fresh.sort(key=lambda x: x.get("final_score", x.get("relevance_score", 0)), reverse=True)

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
# A "running" run updated within this window is actively executing in THIS
# session — not a crash orphan. Surfacing/mutating it produces a false
# "AUTO-RESUME the run that's running right now" alarm (run_0c8e007a).
# Kept SHORT (90s): an actively-running pipeline rewrites run.json on every
# stage boundary (run-update), so a live run's updated_at is always seconds
# old. A run untouched for >90s has genuinely stalled/crashed and SHOULD be
# surfaced as a resume orphan (preserves the existing 5-min-orphan contract in
# test_running_orphan_transitions_to_paused). The window only needs to exceed
# the longest single-stage gap between run-update calls, not the session length.
_ACTIVE_RUN_THRESHOLD_SECONDS = 90


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

    Writes via DIRECT sync sqlite3 into data.db `todos`. The previous implementation called
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
    # Cooldown for recently-HANDLED findings: a recurring source signal (e.g. a
    # capability gap that persists until the underlying pattern stops) would
    # otherwise recreate a todo the instant the user completes it (status=handled
    # leaves the active-only dedup) — recreate-forever. So a finding handled within
    # this window is NOT re-escalated; the user's action sticks. (adversarial HIGH)
    _HANDLED_COOLDOWN_DAYS = 7
    try:
        from datetime import timedelta
        conn = sqlite3.connect(str(_db_path), timeout=5)
        try:
            # Dedup: skip if an ACTIVE Health Alert todo already covers this finding.
            existing = conn.execute(
                "SELECT title FROM todos WHERE status IN ('pending','in_discussion') "
                "AND title LIKE 'Health Alert:%'"
            ).fetchall()
            for (etitle,) in existing:
                if dedup_key in (etitle or ""):
                    return  # already tracked

            # Cooldown: skip if the SAME finding was handled/cancelled recently.
            cutoff = (datetime.now(timezone.utc) - timedelta(days=_HANDLED_COOLDOWN_DAYS)).isoformat()
            recently_closed = conn.execute(
                "SELECT title FROM todos WHERE status IN ('handled','cancelled','deleted') "
                "AND title LIKE 'Health Alert:%' AND updated_at >= ?",
                (cutoff,),
            ).fetchall()
            for (etitle,) in recently_closed:
                if dedup_key in (etitle or ""):
                    return  # user acted recently — respect it, don't resurrect

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


def _newest_completed_run(runs_dir: Path) -> "tuple[float, str | None]":
    """Return (max_updated_at_epoch, run_id) across all COMPLETED runs in a
    project's runs dir, or (0.0, None) if none. ONE pass — pure read, no mutation.

    Used as a recency-based supersede signal: a paused run older than the newest
    completed run in the SAME project is superseded (its work was finished by a
    later run). Recency, NOT requirement-text similarity — text overlap
    false-matches genuinely-different runs that share boilerplate words
    ("Fix session_unit.py crash" vs "...timeout"), which would destructively
    archive live work (Gate-1 finding, run_0c8e007a).

    Computed ONCE per project (not per run) to keep the gauge's no-op path O(runs)
    not O(runs²) — the gauge already iterates every run once; this is one extra
    pass, and the id is returned alongside the ts so the supersede marker needs
    no second scan (RP30: 208 runs in SwarmAI — a per-run rescan would be O(n²)).
    """
    from datetime import datetime, timezone
    newest_ts, newest_id = 0.0, None
    if not runs_dir.exists():
        return newest_ts, newest_id
    # IO pre-filter: only the NEWEST completed run matters as a supersede signal,
    # and a paused run is only superseded if a completed run is MORE recent than
    # it. Since we only surface paused runs <24h old (caller pre-filters at 48h),
    # any completed run that could supersede one is also <48h. A completed run
    # untouched on disk >48h cannot be newer than a surfaced paused run → skip
    # its read. Equivalent because we take the max: pre-skipping older entries
    # never changes the maximum. Same 48h cutoff as the caller (recomputed here
    # from its own time.time() — differs by microseconds, immaterial to the gate).
    mtime_skip_cutoff = time.time() - 2 * 24 * 3600
    for rd in runs_dir.iterdir():
        rf = rd / "run.json"
        try:
            if rf.stat().st_mtime < mtime_skip_cutoff:
                continue
        except OSError:
            continue
        try:
            data = json.loads(rf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("status") not in ("completed", "complete"):
            continue
        upd = data.get("updated_at", "") or data.get("completed_at", "")
        if not upd:
            continue
        try:
            dt = datetime.fromisoformat(upd.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
        except (ValueError, TypeError):
            continue
        if ts > newest_ts:
            newest_ts, newest_id = ts, data.get("id", rd.name)
    return newest_ts, newest_id


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
    # Deliberate-pause guard discriminant — single-source, word-boundary matcher
    # (reused, not re-implemented; Gate-1 Item-3). Imported once at function entry,
    # not per-run-iteration (Gate-2 LOW style nit).
    from scripts.artifact_cli import (
        _CRASH_ZOMBIE_REASON,
        _checkpoint_reason_has_true_trigger,
        _run_tokens,
        is_terminal_run as _is_terminal_run,
    )

    lines: list[str] = []
    try:
        projects_dir = workspace / "Projects"
        if not projects_dir.exists():
            return []

        now = time.time()
        max_age_seconds = 24 * 3600  # Only surface runs from last 24h
        # IO pre-filter: skip json.loads for runs untouched on disk beyond a 2×
        # buffer. run.json is rewritten (mtime bumped) on EVERY status mutation,
        # so st_mtime >= the content updated_at always. A 48h-cold file therefore
        # has updated_at >24h (already skipped by the content check below) OR no
        # parseable updated_at (a 48h-dead orphan — correctly not surfaced). The
        # 2× buffer keeps the real 24h window byte-identical: anything that could
        # be <24h is always let through to the exact content check. Prod has 343
        # run.json (1.5MB); this avoids reading the ~95% that are long-dead.
        mtime_skip_cutoff = now - 2 * max_age_seconds

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

            # Supersede signal (computed ONCE per project): the newest updated_at
            # across all COMPLETED runs + that run's id. A paused run older than
            # this was finished by a later run — surfacing it is a false alarm
            # (run_0c8e007a). One pass; id carried so marking needs no rescan.
            newest_completed_ts, newest_completed_id = _newest_completed_run(runs_dir)

            for run_dir in runs_dir.iterdir():
                run_file = run_dir / "run.json"
                # IO pre-filter (cheap stat before expensive read+parse): a file
                # untouched beyond the 2× buffer cannot be a <24h run — skip it
                # without reading. See mtime_skip_cutoff rationale above.
                try:
                    _file_mtime = run_file.stat().st_mtime
                    if _file_mtime < mtime_skip_cutoff:
                        continue
                except OSError:
                    continue  # missing/unstattable — nothing to surface
                try:
                    run_data = json.loads(run_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue

                status = run_data.get("status", "")
                if status not in ("paused", "running"):
                    continue

                # TERMINAL GUARD (run_bf840159): a run whose STAGES are all done
                # (or has a completed reflect/deliver) is FINISHED — never a crash
                # orphan, even if a session-refresh orphan-transition flipped its
                # status to 'paused'+session_crash_auto_detected. Skip it entirely:
                # do NOT surface it as resumable, do NOT flip running->paused, do
                # NOT supersede/abandon it. Stage-based, not status-based, because
                # the status string is exactly what the false-positive corrupts.
                if _is_terminal_run(run_data):
                    continue

                # EMPTY-SHELL GUARD (mirror of the terminal guard above;
                # run_843962a5 follow-up root-cause fix). A run that crashed BEFORE
                # recording a single stage — stages == [] AND zero tokens AND the
                # crash auto-checkpoint reason — has NO recoverable state. Left alone
                # it enters the auto-resume flow, exhausts its 3 attempts, and emits a
                # "manual intervention needed" nag EVERY session; worse, each emit
                # rewrites updated_at, which resets the age-gated crash-zombie cleaner
                # (cleanup-orphans, >2h) so the shell is never reaped → a
                # self-perpetuating false alarm. Terminal (all done) → skip;
                # empty-shell (nothing recorded) → abandon NOW.
                #
                # Discriminant is provably safe against false-killing recoverable
                # work: the trigger is a TRULY EMPTY stage list (stages == []), NOT
                # merely "no completed stage". Any stage that made progress is
                # persisted to run.json BEFORE completion — a stage that published an
                # artifact is written status="recorded" (artifact_cli
                # _append_stage_to_run), and THINK/PLAN land as "recorded", not
                # "completed". So a run that reached ANY stage has a non-empty
                # stages[] and is excluded here — it has resumable state even at
                # token_cost 0. Only a run that never recorded a stage is an
                # unrecoverable shell. (Adversarial BLOCK, run_843962a5 follow-up: the
                # earlier "no COMPLETED stage" form would false-kill a run stopped
                # mid-THINK/PLAN whose stages are "recorded"; tightened to stages==[].)
                # A deliberate pause carries a true-trigger reason (not the crash
                # marker) → excluded. Written under the same .resume.lock +
                # re-read-under-lock pattern as the supersede branch (no TOCTOU race
                # with a parallel session that may have just resumed it).
                _shell_reason = (run_data.get("checkpoint", {}) or {}).get("reason") or ""
                _no_stages = not (run_data.get("stages") or [])
                if (
                    _shell_reason == _CRASH_ZOMBIE_REASON
                    and _no_stages
                    and _run_tokens(run_data) == 0
                ):
                    lock_file = run_dir / ".resume.lock"
                    fd = None
                    try:
                        fd = lock_file.open("w")
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fresh = json.loads(run_file.read_text(encoding="utf-8"))
                        _fresh_reason = (fresh.get("checkpoint", {}) or {}).get("reason") or ""
                        _fresh_no_stages = not (fresh.get("stages") or [])
                        # Re-verify the whole shell predicate under lock — a parallel
                        # session may have resumed it (→ running / recorded a stage /
                        # spent tokens) since the scan.
                        if (
                            fresh.get("status") == "paused"
                            and _fresh_reason == _CRASH_ZOMBIE_REASON
                            and _fresh_no_stages
                            and _run_tokens(fresh) == 0
                        ):
                            fresh["status"] = "abandoned"
                            fresh["abandon_reason"] = "crash_residue_empty_shell"
                            fresh["abandoned_at"] = datetime.now(timezone.utc).isoformat()
                            run_file.write_text(
                                json.dumps(fresh, indent=2), encoding="utf-8"
                            )
                    except (OSError, json.JSONDecodeError):
                        # Lock held by another session / IO error / corrupt file —
                        # skip surfacing regardless (an empty shell is never a real
                        # resume candidate; another session owns the write).
                        pass
                    finally:
                        if fd is not None:
                            try:
                                fd.close()
                            except Exception:
                                pass
                    continue

                # Check freshness FIRST — skip old runs before any mutation
                updated = run_data.get("updated_at", "")
                run_ts = None
                if updated:
                    try:
                        dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                        run_ts = dt.timestamp()
                        age = now - run_ts
                        if age > max_age_seconds:
                            continue
                    except (ValueError, TypeError):
                        pass  # Can't parse date — include it anyway

                # ACTIVE-RUN SKIP: a "running" run updated within the active
                # window is executing in THIS session, not a crash orphan. Do NOT
                # surface OR mutate it (the gauge was telling the user to
                # AUTO-RESUME the run that's running right now — run_0c8e007a).
                if (
                    status == "running"
                    and run_ts is not None
                    and (now - run_ts) <= _ACTIVE_RUN_THRESHOLD_SECONDS
                ):
                    continue

                # Read resume_attempts HERE (hoisted above the supersede branch —
                # the deliberate-pause guard below needs it to decide whether a crash
                # orphan has exhausted its resume budget; the auto-resume block further
                # down re-reads it under its own lock). Gate-1 Item-1.
                resume_attempts = run_data.get("resume_attempts", 0)

                # SUPERSEDED SKIP + MARK: a PAUSED run older than the newest
                # COMPLETED run in this project WAS finished by a later run. Mark
                # it abandoned (status=abandoned + abandon_reason=superseded_by_<id>)
                # so the gauge stops re-scanning it, and skip surfacing it. This is
                # a genuine supersession (a completed run did the work) — distinct
                # from _auto_abandon_stale_runs in artifact_cli.py, which labels a
                # stale *running* orphan 'orphaned_no_resume' (no run finished it).
                # Recency signal only — NOT requirement-text similarity (Gate-1:
                # text overlap destructively false-archives different-but-similar
                # runs). File-locked + re-read-under-lock to avoid racing a
                # parallel session that may have just resumed this run.
                #
                # PAUSED-ONLY (Gate-2 HIGH): never supersede a "running" run. A
                # live pipeline in a slow stage (BUILD/TEST/research >90s) is
                # past the active-skip window but is NOT crashed — archiving its
                # run.json mid-execution is the exact data-loss this fix exists
                # to prevent, from the opposite direction. A genuinely-stalled
                # running run is handled by the orphan-transition path below
                # (running→paused), and becomes supersede-eligible only on the
                # NEXT session once it is paused.
                #
                # DELIBERATE-PAUSE GUARD (run_17e3399c): supersede archives ONLY a
                # genuine crash orphan that has EXHAUSTED its resume budget — never a
                # run paused awaiting a human decision. Two false-archive vectors this
                # closes (both verified against real run data + Gate-1 review):
                #   (a) a run deliberately paused for an L2/judgment/Gate-BLOCK decision
                #       (checkpoint.reason carries a true-trigger) must keep surfacing
                #       until the human decides — recency must NOT bury it (the bug:
                #       6 such runs were silently archived in production).
                #   (b) a fresh crash orphan (resume_attempts < MAX) must finish its
                #       auto-resume budget first — archiving it here short-circuits the
                #       resume block below (Gate-1 Attack-1).
                # Discriminant REUSES the single-source, word-boundary matcher from
                # artifact_cli (NOT a hand-rolled substring denylist — substring
                # 'l2'/'block' false-matches 'model2'/'roadblock', a bug class this
                # codebase already fixed at artifact_cli:1542; Gate-1 Item-3). The exact
                # crash marker 'session_crash_auto_detected' is NOT a true-trigger
                # (verified: '_crash_' is mid-word, no \\b match), so it is gated by
                # resume-budget instead. Empty reason → neither deliberate nor crash →
                # supersede-eligible (no leak; :856 is the ONLY paused→abandoned writer).
                _reason = (run_data.get("checkpoint", {}) or {}).get("reason") or ""
                _is_deliberate = _checkpoint_reason_has_true_trigger(_reason)
                _is_crash = _reason == "session_crash_auto_detected"
                if (
                    status == "paused"
                    and run_ts is not None
                    and newest_completed_ts > run_ts
                    and not _is_deliberate
                    and (not _is_crash or resume_attempts >= _MAX_PIPELINE_RESUME_ATTEMPTS)
                ):
                    sup_id = newest_completed_id
                    lock_file = run_dir / ".resume.lock"
                    fd = None
                    try:
                        fd = lock_file.open("w")
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        # Re-read under lock — a parallel session may have changed
                        # status (e.g. resumed → running anew) since the scan.
                        # Re-check status==paused AND re-evaluate the guard under lock:
                        # a parallel session may have resumed it (→ running) OR rewritten
                        # checkpoint.reason / resume_attempts since the scan.
                        fresh = json.loads(run_file.read_text(encoding="utf-8"))
                        _fresh_reason = (fresh.get("checkpoint", {}) or {}).get("reason") or ""
                        _fresh_deliberate = _checkpoint_reason_has_true_trigger(_fresh_reason)
                        _fresh_crash = _fresh_reason == "session_crash_auto_detected"
                        _fresh_attempts = fresh.get("resume_attempts", 0)
                        if (
                            fresh.get("status") == "paused"
                            and not _fresh_deliberate
                            and (not _fresh_crash or _fresh_attempts >= _MAX_PIPELINE_RESUME_ATTEMPTS)
                        ):
                            fresh["status"] = "abandoned"
                            fresh["abandon_reason"] = (
                                f"superseded_by_{sup_id}" if sup_id
                                else "superseded_by_completed_run"
                            )
                            fresh["abandoned_at"] = datetime.now(
                                timezone.utc
                            ).isoformat()
                            run_file.write_text(
                                json.dumps(fresh, indent=2), encoding="utf-8"
                            )
                    except (OSError, json.JSONDecodeError):
                        # Lock held / IO error / corrupt run.json — skip
                        # surfacing regardless (the run is superseded; another
                        # session owns the write, or the file is unreadable).
                        pass
                    finally:
                        # Always release the lock fd — JSONDecodeError (a
                        # ValueError, not OSError) would otherwise leak it.
                        if fd is not None:
                            try:
                                fd.close()
                            except Exception:
                                pass
                    continue

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
                    # INFORMATIONAL awareness, NOT an imperative command (run_f3975b8b,
                    # DoD#5). The briefing has NO session identity (build_session_briefing
                    # receives only workspace_dir), so it CANNOT prove this paused run
                    # belongs to the session reading it — it may be a SIBLING's. The old
                    # text ("🚀 ... Execute: ... then invoke ...") ordered the reader to
                    # resume it NOW, which let one session hijack/resume an unrelated run.
                    # We KEEP the AUTO-RESUME label + run-resume command as a COPYABLE hint
                    # (also a stable substring the briefing reader/tests key off), but
                    # reframe to "resume IF IT IS YOURS" — the reader decides ownership;
                    # the briefing never commands the action.
                    line = (
                        f"  - AUTO-RESUME candidate ({attempt_label}): "
                        f"[{project_name}] \"{requirement}\" paused at {resume_stage}. "
                        f"If this run is yours, resume with `artifact_cli.py run-resume "
                        f"--project {project_name} --run-id {run_id}` "
                        f"then `s_autonomous-pipeline --resume "
                        f"--run-id {run_id} --project {project_name}`. "
                        f"If it belongs to another session, leave it for that session."
                    )
                    # Reuse the mtime from the pre-filter stat (above) — the
                    # candidate sort only needs a coarse recency key, and re-stat'ing
                    # would be a redundant syscall (adversarial LOW, run_885eb466).
                    candidates.append((_file_mtime, line))
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

        # background_section ("Also in...") is CUT from the markdown briefing
        # (§4.2.1) — discard it here. _format_suggestions still computes it for
        # build_session_briefing_data (the Welcome Screen data twin).
        focus_section, _background_section = _format_suggestions(ranked)

        sections: list[str] = []

        if focus_section:
            sections.append(focus_section)

        # ── SYSTEM-PROMPT PURITY (run_05b42b8b, SwarmAI TECH.md invariant) ───
        # The system-prompt briefing keeps ONLY the Suggested-focus section above
        # (pure fs: MEMORY.md + DailyActivity) PLUS the auto-resume DIRECTIVE below.
        # The feed/status-board sections are REMOVED from this assembly path — they
        # are dashboard data, not this-turn judgment, and they dragged a DB read
        # (Radar todos, raw sqlite3), eval_service, and a health-findings scan onto
        # every per-message build. Removed: Radar todos (DB), System health,
        # Recurrence Radar, DDD escalations, Skill health, Self-Eval, + the earlier
        # temporal-Signals/External/Job-results/Learning/Codebase cuts. Feed data
        # lives in build_session_briefing_data() (Welcome Screen), UNCHANGED.
        # HARD invariant: NO DB / eval / feed on this path.

        # KEEP — Pipeline auto-resume (LIVE ENGINE, not display). _get_paused_
        # pipeline_highlights is fs-only (globs+rewrites run.json under fcntl lock,
        # increments resume_attempts) and is the ONLY trigger that auto-resumes a
        # paused/crashed pipeline (verified live: 147 runs have resume_attempts>0).
        # It does NOT violate the no-DB rule (run.json = filesystem, not app DB).
        # ⚠️ TECH-DEBT (offline-root-cause run): this is a STATE-MUTATING glob on the
        # per-message prompt path — assembling a prompt should not mutate pipeline
        # state. The trigger belongs on a scheduler tick, not here. Tracked for the
        # offline run; kept here until moved so recovery is never silently lost.
        pipeline_lines = _get_paused_pipeline_highlights(workspace)
        if pipeline_lines:
            sections.append(
                "**Pipeline auto-resume (execute immediately before other work):**\n"
                + "\n".join(pipeline_lines)
            )

        if not sections:
            return None

        briefing = "## Session Briefing\n" + "\n".join(sections)

        # L3: Save current suggestions for next session's comparison
        learning_state.last_briefing_date = datetime.now().strftime("%Y-%m-%d")
        learning_state.last_briefing_suggested = [
            item.title for item in ranked[:3]
        ]
        _save_learning_state(workspace, learning_state)

        # Token estimate uses the SINGLE CJK-aware estimator (estimate_tokens),
        # NOT the old `len//4` — consistent with the assembly + recall logs
        # (design §1: dual-estimator divergence killed). The CUT (run_a16d61ad)
        # removed the External-signals/Job-results/Learning sections, so the
        # `signal_lines`/`learning_insight` locals this log used to reference no
        # longer exist — they are dropped here too (their dangling refs were a
        # silent NameError that made the whole briefing return None).
        from core.context_directory_loader import ContextDirectoryLoader
        _brief_tok = ContextDirectoryLoader.estimate_tokens(briefing)
        logger.info(
            "Proactive briefing (L4): %d chars, ~%d tok, %d ranked, %d sections, "
            "effectiveness=%s",
            len(briefing), _brief_tok, len(ranked), len(sections),
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
        # Denoising (feed exclusion / trending split / per-feed cap / 48h cutoff /
        # final_score sort) is delegated to jobs.signal_selection.select_signals —
        # the SINGLE source shared with the Slack digest formatter so the two
        # surfaces never drift. This function keeps only its own field-shaping.
        signals_list: list[dict] = []
        hot_news_list: list[dict] = []
        digest_path = workspace / "Services" / "signals" / "signal_digest.json"
        if digest_path.exists():
            try:
                data = json.loads(digest_path.read_text(encoding="utf-8"))
                from jobs.signal_selection import readable_source, select_signals
                _selected = select_signals(data.get("items", []))
                for sig in _selected["signals"][:8]:
                    feed_id = sig.get("feed_id", "")
                    # Shared label map (github/commit feeds → readable label)
                    # lives in signal_selection.readable_source — single source.
                    source_label = readable_source(feed_id, sig.get("source", ""))
                    signals_list.append({
                        "title": sig.get("title", ""),
                        "summary": sig.get("summary", ""),
                        "source": source_label,
                        "sourceUrl": sig.get("url", ""),
                        "urgency": sig.get("urgency", "medium"),
                        "relevance": sig.get("relevance_score", 0),
                        "finalScore": sig.get("final_score", sig.get("relevance_score", 0)),
                        "rank": sig.get("rank", 0),
                        "lang": sig.get("lang", "en"),
                        "feedId": feed_id,
                    })
                for sig in _selected["hot_news"][:10]:
                    hot_news_list.append({
                        "title": sig.get("title", ""),
                        "platform": sig.get("platform", sig.get("source", "")),
                        "rank": sig.get("rank", 0),
                        "url": sig.get("url", ""),
                        "region": sig.get("region", "cn"),
                        "lang": sig.get("lang", "zh"),
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
