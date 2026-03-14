"""Proactive Intelligence — session briefing engine.

Analyzes MEMORY.md (Open Threads) and recent DailyActivity files to
generate a compact session briefing injected into the system prompt.
Makes the agent *aware* at session start — no user prompt needed.

No LLM calls. Pure text parsing. Target: 200-400 tokens.

Key exports:
- build_session_briefing()  — main entry point, returns briefing string or None
"""

from __future__ import annotations

import re
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Open Threads parser
# ---------------------------------------------------------------------------

_PRIORITY_EMOJI = {"P0": "BLOCKING", "P1": "IMPORTANT", "P2": "NICE-TO-HAVE"}
_THREAD_RE = re.compile(
    r"[-*]\s+"           # bullet
    r"(?:[^\s]+\s+)?"   # optional emoji (e.g. red/yellow/blue circle)
    r"\*\*(.+?)\*\*"    # **title**
    r"\s*\(reported\s+(\d+)x",  # (reported Nx
    re.IGNORECASE,
)
_PRIORITY_HEADER_RE = re.compile(r"###\s+(P[012])\s")
_STATUS_RE = re.compile(r"Status:\s*(.+?)$", re.IGNORECASE)


def _parse_open_threads(memory_text: str) -> list[dict]:
    """Extract structured Open Threads from MEMORY.md text.

    Returns list of {priority, title, report_count, status} dicts,
    ordered P0 → P1 → P2.
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
        if simple and line.strip().startswith(("-", "*")):
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
            threads[-1]["status"] = sm.group(1).strip()

    return threads


# ---------------------------------------------------------------------------
# DailyActivity "Next" / continue_from parser
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

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    recent_dates = {today, yesterday}

    seen: set[str] = set()
    for da_file in da_files:
        try:
            content = da_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        file_date = da_file.stem  # YYYY-MM-DD
        is_recent = file_date in recent_dates

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


# ---------------------------------------------------------------------------
# Pattern detection
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

    # 3. COE Registry items
    coe_match = re.search(r"## COE Registry\b", memory_text)
    if coe_match:
        coe_section = memory_text[coe_match.end():]
        next_sec = re.search(r"\n## [^#]", coe_section)
        if next_sec:
            coe_section = coe_section[:next_sec.start()]
        investigating = coe_section.count("Investigating")
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

    # ── Session gap detection ──
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

            # ── First session of day ──
            # If today's file doesn't exist yet, this is the first session
            today_file = daily_dir / f"{today}.md"
            if not today_file.exists():
                signals.append("First session today — full briefing")

    # ── Stale P0 detection ──
    # Look for date references (e.g. "3/13", "2026-03-13") in thread titles/status
    # to estimate thread age
    _date_ref_re = re.compile(r"(\d{1,2})/(\d{1,2})|(\d{4}-\d{2}-\d{2})")
    for t in threads:
        if t["priority"] != "P0":
            continue
        # Search title + status for earliest date reference
        search_text = f"{t['title']} {t.get('status', '')}"
        dates_found = _date_ref_re.findall(search_text)
        earliest = None
        for m, d, full in dates_found:
            try:
                if full:
                    dt = datetime.strptime(full, "%Y-%m-%d")
                else:
                    # Assume current year, month/day format
                    dt = datetime(now.year, int(m), int(d))
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

        # ── Parse components ──
        threads = _parse_open_threads(memory_text)
        continue_hints = _parse_continue_hints(daily_dir)
        signals = _detect_patterns(threads, daily_dir, memory_text)

        # ── Build briefing ──
        sections: list[str] = []

        # 1. Blockers first (P0)
        p0_threads = [t for t in threads if t["priority"] == "P0"]
        if p0_threads:
            items = []
            for t in p0_threads:
                count_suffix = f" ({t['report_count']}x)" if t.get("report_count", 1) > 1 else ""
                items.append(f"  - BLOCKING: {t['title']}{count_suffix}")
            sections.append("**Blockers:**\n" + "\n".join(items))

        # 2. Pattern signals
        if signals:
            items = [f"  - {s}" for s in signals]
            sections.append("**Signals:**\n" + "\n".join(items))

        # 3. Continue-from (max 3 most recent)
        if continue_hints:
            # Truncate long hints
            truncated = []
            for h in continue_hints[:3]:
                if len(h) > 120:
                    h = h[:117] + "..."
                truncated.append(f"  - {h}")
            sections.append("**Continue from last session:**\n" + "\n".join(truncated))

        # 4. Important threads (P1) — just titles, compact
        p1_threads = [t for t in threads if t["priority"] == "P1"]
        if p1_threads:
            items = []
            for t in p1_threads:
                count_suffix = f" ({t['report_count']}x)" if t.get("report_count", 1) > 1 else ""
                items.append(f"  - {t['title']}{count_suffix}")
            sections.append("**Also pending (P1):**\n" + "\n".join(items))

        if not sections:
            return None

        briefing = "## Session Briefing\n" + "\n".join(sections)

        # Token estimate sanity check — if too long, trim P1 section
        token_est = len(briefing) // 4  # rough estimate
        if token_est > 500 and len(sections) > 2:
            # Drop the P1 section to stay compact
            sections = [s for s in sections if not s.startswith("**Also pending")]
            briefing = "## Session Briefing\n" + "\n".join(sections)

        logger.info(
            "Proactive briefing built: %d chars, ~%d tokens, %d threads, %d signals, %d hints",
            len(briefing), len(briefing) // 4, len(threads), len(signals), len(continue_hints),
        )
        return briefing

    except Exception as exc:
        logger.warning("Proactive intelligence failed (non-blocking): %s", exc)
        return None
