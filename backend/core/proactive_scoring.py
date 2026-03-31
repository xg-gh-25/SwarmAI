"""Proactive Intelligence — Level 2 scoring engine.

Deterministic priority scoring for session briefing suggestions.
Scores Open Threads and continue hints based on priority, staleness,
frequency, blocking relationships, and momentum. No LLM calls.

Key exports:
- ScoredItem          — scored candidate action
- score_item()        — compute priority score for one item
- estimate_thread_age() — days open from date references
- detect_blocking()   — which threads block others
- build_suggestions() — full scored+ranked list from all sources
- format_suggestions() — render into briefing markdown sections
- generate_reasoning() — "why this order" explanation

Split from proactive_intelligence.py (2026-03-25, Kiro feedback).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Scored item dataclass
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


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------

def score_item(item: ScoredItem) -> int:
    """Compute priority score for a single item. Pure, deterministic."""
    score = _PRIORITY_WEIGHT.get(item.priority, 10)
    score += min(item.days_open * _STALENESS_PER_DAY, _STALENESS_CAP)
    score += min((item.report_count - 1) * _FREQUENCY_PER_REPORT, _FREQUENCY_CAP)
    if item.blocks_others:
        score += _BLOCKING_BONUS
    if item.from_continue_hint:
        score += _MOMENTUM_BONUS
    return max(score, 0)


def estimate_thread_age(thread: dict, date_re: re.Pattern) -> int:
    """Estimate days open from date references in thread title/status.

    Uses the provided compiled regex (anchored to word boundary) to avoid
    matching version numbers like 'v2/3'.
    """
    now = datetime.now()
    search_text = f"{thread.get('title', '')} {thread.get('status', '')}"
    dates_found = date_re.findall(search_text)
    earliest = None
    for groups in dates_found:
        # Regex has 5 groups: (2d_month, 1-2d_day, 1-2d_month, 2d_day, full_date)
        # Either groups 0+1 or 2+3 match (at least one side must be 2+ digits).
        m = groups[0] or groups[2]
        d = groups[1] or groups[3]
        full = groups[4]
        try:
            if full:
                dt = datetime.strptime(full, "%Y-%m-%d")
            else:
                # Assume current year, month/day format.
                # If the resulting date is in the future (e.g. 12/20
                # referenced in January), try previous year instead.
                dt = datetime(now.year, int(m), int(d))
                if dt > now:
                    dt = datetime(now.year - 1, int(m), int(d))
            if earliest is None or dt < earliest:
                earliest = dt
        except (ValueError, TypeError):
            continue
    # Clamp to 0 — safety net for edge cases
    return max((now - earliest).days, 0) if earliest else 0


def detect_blocking(threads: list[dict]) -> tuple[dict[str, bool], dict[str, int]]:
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
            if any(kw in title_lower for kw in ["rebuild", "deploy"]):
                blocking[title] = True
                blocked_counts[title] = len(rebuild_statuses)
                continue

        # P0 with multiple P1s referencing similar keywords.
        # Filter to words >4 chars to avoid false positives on common short
        # words like "session", "the", "fix" (Kiro feedback 2026-03-25).
        if t["priority"] == "P0":
            p0_words = {w for w in title.lower().split() if len(w) > 4}
            related_p1s = 0
            for other in threads:
                if other["priority"] == "P1" and other.get("title") != title:
                    other_words = {w for w in other.get("title", "").lower().split() if len(w) > 4}
                    if p0_words & other_words:  # shared subsystem keyword
                        related_p1s += 1
            if related_p1s >= 2:
                blocking[title] = True
                blocked_counts[title] = related_p1s

    return blocking, blocked_counts


def build_suggestions(
    threads: list[dict],
    continue_hints: list[str],
    signals: list[str],
    date_re: re.Pattern,
) -> list[ScoredItem]:
    """Build scored and ranked suggestion list from all sources.

    Merges Open Threads + continue hints into ScoredItems, scores each,
    sorts descending. Returns full ranked list (caller takes top N).
    """
    items: list[ScoredItem] = []
    seen_titles: set[str] = set()

    # Detect blocking relationships
    blocking_map, blocked_counts = detect_blocking(threads)

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
            days_open=estimate_thread_age(t, date_re),
            blocks_others=blocking_map.get(title, False),
            blocked_count=blocked_counts.get(title, 0),
            from_continue_hint=has_momentum,
            status=t.get("status", ""),
            source="thread",
        )
        item.score = score_item(item)
        items.append(item)
        seen_titles.add(title_lower)

    # 2. Add continue hints that aren't already threads
    for hint in continue_hints:
        hint_lower = hint.lower()
        # Skip if already covered by a thread (same 30-char prefix match as momentum)
        if any(hint_lower[:30] in t or t[:30] in hint_lower for t in seen_titles):
            continue

        # Truncate at word boundary to avoid mid-word cuts in UI
        if len(hint) > 100:
            truncated = hint[:100].rsplit(" ", 1)[0]
            title = truncated + "…" if truncated else hint[:100]
        else:
            title = hint

        item = ScoredItem(
            title=title,
            priority="P1",  # continue hints are implicitly important
            from_continue_hint=True,
            source="hint",
        )
        item.score = score_item(item)
        items.append(item)

    # Sort by score descending, tiebreak: P0 > P1 > P2, then alphabetical
    priority_order = {"P0": 0, "P1": 1, "P2": 2}
    items.sort(key=lambda x: (-x.score, priority_order.get(x.priority, 3), x.title))

    return items


def generate_reasoning(ranked: list[ScoredItem]) -> str:
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


def format_suggestions(ranked: list[ScoredItem], max_focus: int = 3) -> tuple[str, str]:
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

    reasoning = generate_reasoning(focus_items)

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
