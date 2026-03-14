"""Proactive Intelligence — session briefing engine.

Analyzes MEMORY.md (Open Threads) and recent DailyActivity files to
generate a compact session briefing injected into the system prompt.
Makes the agent *aware* at session start — no user prompt needed.

No LLM calls. Pure text parsing. Target: 200-400 tokens.

Levels:
- L0: Session briefing (parse threads + continue hints + pattern signals)
- L1: Temporal awareness (session gaps, stale P0s, first-session-of-day)
- L2: Actionable suggestions (score + rank items, suggest focus with reasoning)

Key exports:
- build_session_briefing()  — main entry point, returns briefing string or None
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
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
# Level 2: Actionable Suggestions — scoring engine
# ---------------------------------------------------------------------------

@dataclass
class ScoredItem:
    """A candidate action with a computed priority score."""
    title: str
    priority: str  # P0, P1, P2
    score: int = 0
    report_count: int = 1
    days_open: int = 0
    blocks_others: bool = False
    blocked_count: int = 0
    from_continue_hint: bool = False
    status: str = ""
    source: str = ""  # "thread" or "hint"


# Score weights — tuned per design doc
_PRIORITY_WEIGHT = {"P0": 100, "P1": 40, "P2": 10}
_STALENESS_PER_DAY = 5
_STALENESS_CAP = 30
_FREQUENCY_PER_REPORT = 8
_FREQUENCY_CAP = 40
_BLOCKING_BONUS = 30
_MOMENTUM_BONUS = 15


def _score_item(item: ScoredItem) -> int:
    """Compute priority score for a single item. Pure, deterministic."""
    score = _PRIORITY_WEIGHT.get(item.priority, 10)
    score += min(item.days_open * _STALENESS_PER_DAY, _STALENESS_CAP)
    score += min((item.report_count - 1) * _FREQUENCY_PER_REPORT, _FREQUENCY_CAP)
    if item.blocks_others:
        score += _BLOCKING_BONUS
    if item.from_continue_hint:
        score += _MOMENTUM_BONUS
    return max(score, 0)


def _estimate_thread_age(thread: dict) -> int:
    """Estimate days open from date references in thread title/status."""
    now = datetime.now()
    date_ref_re = re.compile(r"(\d{1,2})/(\d{1,2})|(\d{4}-\d{2}-\d{2})")
    search_text = f"{thread.get('title', '')} {thread.get('status', '')}"
    dates_found = date_ref_re.findall(search_text)
    earliest = None
    for m, d, full in dates_found:
        try:
            if full:
                dt = datetime.strptime(full, "%Y-%m-%d")
            else:
                dt = datetime(now.year, int(m), int(d))
            if earliest is None or dt < earliest:
                earliest = dt
        except (ValueError, TypeError):
            continue
    # Clamp to 0 — future dates (e.g. 12/20 referenced in January) shouldn't go negative
    return max((now - earliest).days, 0) if earliest else 0


def _detect_blocking(threads: list[dict]) -> tuple[dict[str, bool], dict[str, int]]:
    """Detect which threads block others.

    Returns {title: True} for threads that block other work.
    Heuristics:
    - Status contains "blocking", "blocks"
    - Multiple other threads have "Needs rebuild" and this is rebuild-related
    - P0 thread and 2+ P1s reference same subsystem keywords
    """
    blocking: dict[str, bool] = {}
    blocked_counts: dict[str, int] = {}

    # Keyword-based blocking detection
    block_keywords = ["blocking", "blocks", "blocked by"]
    rebuild_statuses = [
        t for t in threads
        if any(kw in t.get("status", "").lower()
               for kw in ["needs rebuild", "pending rebuild", "not yet run"])
    ]

    for t in threads:
        title = t.get("title", "")
        status = t.get("status", "").lower()

        # Direct blocking language in status
        if any(kw in status for kw in block_keywords):
            blocking[title] = True
            blocked_counts[title] = blocked_counts.get(title, 0) + 1
            continue

        # If 2+ items need rebuild, any rebuild-blocking thread is a blocker
        if len(rebuild_statuses) >= 2:
            title_lower = title.lower()
            if any(kw in title_lower for kw in ["rebuild", "build", "deploy"]):
                blocking[title] = True
                blocked_counts[title] = len(rebuild_statuses)
                continue

        # P0 with multiple P1s referencing similar keywords
        if t["priority"] == "P0":
            p0_words = set(title.lower().split())
            related_p1s = 0
            for other in threads:
                if other["priority"] == "P1" and other.get("title") != title:
                    other_words = set(other.get("title", "").lower().split())
                    if p0_words & other_words:  # any shared keyword
                        related_p1s += 1
            if related_p1s >= 2:
                blocking[title] = True
                blocked_counts[title] = related_p1s

    return blocking, blocked_counts


def _build_suggestions(
    threads: list[dict],
    continue_hints: list[str],
    signals: list[str],
) -> list[ScoredItem]:
    """Build scored and ranked suggestion list from all sources.

    Merges Open Threads + continue hints into ScoredItems, scores each,
    sorts descending. Returns full ranked list (caller takes top N).
    """
    items: list[ScoredItem] = []
    seen_titles: set[str] = set()

    # Detect blocking relationships
    blocking_map, blocked_counts = _detect_blocking(threads)

    # 1. Convert threads to scored items
    for t in threads:
        title = t.get("title", "")
        if not title:
            continue

        # Check if any continue hint references this thread
        title_lower = title.lower()
        has_momentum = any(
            title_lower[:30] in h.lower() or h.lower()[:30] in title_lower
            for h in continue_hints
        )

        item = ScoredItem(
            title=title,
            priority=t.get("priority", "P2"),
            report_count=t.get("report_count", 1),
            days_open=_estimate_thread_age(t),
            blocks_others=blocking_map.get(title, False),
            blocked_count=blocked_counts.get(title, 0),
            from_continue_hint=has_momentum,
            status=t.get("status", ""),
            source="thread",
        )
        item.score = _score_item(item)
        items.append(item)
        seen_titles.add(title_lower)

    # 2. Add continue hints that aren't already threads
    for hint in continue_hints:
        hint_lower = hint.lower()
        # Skip if already covered by a thread
        if any(hint_lower[:30] in t for t in seen_titles):
            continue
        if any(t in hint_lower for t in seen_titles):
            continue

        item = ScoredItem(
            title=hint[:100],
            priority="P1",  # continue hints are implicitly important
            from_continue_hint=True,
            source="hint",
        )
        item.score = _score_item(item)
        items.append(item)

    # Sort by score descending, tiebreak: P0 > P1 > P2, then alphabetical
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    items.sort(key=lambda x: (-x.score, priority_order.get(x.priority, 3), x.title))

    return items


def _generate_reasoning(ranked: list[ScoredItem]) -> str:
    """Generate a short "why this order" explanation from top-ranked items.

    Template-based, not LLM-generated. Returns empty string if no
    interesting reasons to surface.
    """
    reasons: list[str] = []
    for item in ranked[:3]:
        parts: list[str] = []
        if item.blocks_others:
            parts.append(f"blocks {item.blocked_count} other item(s)")
        if item.report_count >= 3:
            parts.append(f"reported {item.report_count}x")
        if item.days_open >= 3:
            parts.append(f"open {item.days_open} days")
        if item.from_continue_hint and not parts:
            parts.append("momentum from last session")
        if parts:
            # Use short title (first 50 chars)
            short_title = item.title[:50] + ("..." if len(item.title) > 50 else "")
            reasons.append(f"{short_title}: {', '.join(parts)}")

    return ". ".join(reasons) + "." if reasons else ""


def _format_suggestions(ranked: list[ScoredItem], max_focus: int = 3) -> tuple[str, str]:
    """Format ranked items into briefing sections.

    Returns (focus_section, background_section).
    focus_section: top N items as numbered list with reasoning.
    background_section: remaining items as compact bullets.
    """
    if not ranked:
        return "", ""

    # Dynamic top-N: if score gap between #1 and #2 > 30, just show #1
    focus_count = max_focus
    if len(ranked) >= 2 and (ranked[0].score - ranked[1].score) > 30:
        focus_count = min(2, max_focus)  # show top 2 at most when dominant
    focus_items = ranked[:focus_count]
    background_items = ranked[focus_count:]

    # Focus section
    focus_lines: list[str] = []
    for i, item in enumerate(focus_items, 1):
        count_suffix = f" ({item.report_count}x)" if item.report_count > 1 else ""
        focus_lines.append(f"  {i}. {item.title}{count_suffix}")

    reasoning = _generate_reasoning(focus_items)

    focus_section = "**Suggested focus for this session:**\n" + "\n".join(focus_lines)
    if reasoning:
        focus_section += f"\n\n**Why this order:** {reasoning}"

    # Background section
    background_section = ""
    if background_items:
        bg_lines = []
        for item in background_items[:5]:  # cap at 5
            bg_lines.append(f"  - {item.title}")
        background_section = "**Also in the background:**\n" + "\n".join(bg_lines)

    return focus_section, background_section


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

        # ── Build briefing (L2: ranked suggestions) ──
        ranked = _build_suggestions(threads, continue_hints, signals)

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

        if not sections:
            return None

        briefing = "## Session Briefing\n" + "\n".join(sections)

        # Token estimate sanity check
        token_est = len(briefing) // 4
        if token_est > 500 and len(sections) > 2:
            sections = [s for s in sections if not s.startswith("**Also in")]
            briefing = "## Session Briefing\n" + "\n".join(sections)

        logger.info(
            "Proactive briefing (L2): %d chars, ~%d tokens, %d ranked items, %d signals",
            len(briefing), len(briefing) // 4, len(ranked), len(signals),
        )
        return briefing

    except Exception as exc:
        logger.warning("Proactive intelligence failed (non-blocking): %s", exc)
        return None
