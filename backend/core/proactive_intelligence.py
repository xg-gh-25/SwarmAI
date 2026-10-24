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
"""

from __future__ import annotations

import json
import re
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

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
    deliverables: list[str] | None = None,
) -> list[dict]:
    """Remove threads whose topics appear in recent deliverables.

    Read-time safety net: even if distillation hasn't resolved the
    thread yet, the briefing won't suggest work that's already done.
    Uses >=50% word overlap matching (same heuristic as distillation).

    ``deliverables`` may be pre-computed by the caller and passed in to avoid
    re-reading the DailyActivity files (run_b1feca42 — build_session_briefing
    calls this and _filter_completed_hints back-to-back with the same daily_dir).
    When None, it is computed here so the function stays usable standalone.
    """
    if deliverables is None:
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
    deliverables: list[str] | None = None,
) -> list[str]:
    """Remove continue hints that match recent deliverables or dismissed items.

    Same fuzzy-matching heuristic as _filter_completed_threads — ≥50% word
    overlap or substring match.  Also filters hints the user explicitly dismissed.

    ``deliverables`` may be pre-computed by the caller (run_b1feca42) to avoid a
    redundant DailyActivity re-read; when None it is computed here (standalone use).
    """
    if deliverables is None:
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
        # Compute deliverables ONCE (run_b1feca42) — both filters below consumed
        # it and each re-read the same DailyActivity files before this hoist.
        deliverables = _extract_recent_deliverables(daily_dir)
        threads = _filter_completed_threads(threads, daily_dir, deliverables)

        # -- L3: Update learning state from previous session --
        learning_state = _load_learning_state(workspace)
        learning_state = _update_learning_from_activity(learning_state, daily_dir)

        # -- Filter continue hints against deliverables + dismissed items --
        dismissed = _get_dismissed_titles(learning_state)
        continue_hints = _filter_completed_hints(continue_hints, daily_dir, dismissed, deliverables)

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
        # (§4.2.1) — discard it here.
        focus_section, _background_section = _format_suggestions(ranked)

        sections: list[str] = []

        if focus_section:
            sections.append(focus_section)

        # ── SYSTEM-PROMPT PURITY (run_05b42b8b, SwarmAI TECH.md invariant) ───
        # The system-prompt briefing keeps ONLY the Suggested-focus section above
        # (pure fs: MEMORY.md + DailyActivity). No pipeline run state — see below.
        # The feed/status-board sections are REMOVED from this assembly path — they
        # are dashboard data, not this-turn judgment, and they dragged a DB read
        # (Radar todos, raw sqlite3), eval_service, and a health-findings scan onto
        # every per-message build. Removed: Radar todos (DB), System health,
        # Recurrence Radar, DDD escalations, Skill health, Self-Eval, + the earlier
        # temporal-Signals/External/Job-results/Learning/Codebase cuts. Feed data
        # lives in its own surfaces (Jobs, Signal Digest), never here.
        # HARD invariant: NO DB / eval / feed on this path.

        # NO pipeline run state here (XG-directed). A session's system prompt must not
        # carry other runs' status: it is noise to this turn, and the engine that fed
        # it MUTATED run.json from whichever session started next — it flipped every
        # run idle >90s from running to paused as a "crash", so a new tab paused a
        # pipeline still running in another tab and then told the new session to
        # resume it. Pipeline status lives in the Pipeline surface; resuming a run is
        # a deliberate act (artifact_cli run-resume), never a side effect of a prompt.

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
