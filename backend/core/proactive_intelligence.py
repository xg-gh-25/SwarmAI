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

Key exports:
- build_session_briefing()  — main entry point, returns briefing string or None
"""

from __future__ import annotations

import json
import re
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from core.session_utils import fuzzy_title_matches_deliverable

logger = logging.getLogger(__name__)

# Module-level compiled regex — used by _detect_temporal_signals and _estimate_thread_age.
# Anchored to word boundary: lookbehind requires start-of-string, whitespace, or common
# punctuation (colon, comma, open-paren) to avoid matching version numbers like "v2/3".
_DATE_REF_RE = re.compile(
    r"(?:^|(?<=[\s:,(]))(\d{1,2})/(\d{1,2})(?=[\s,)]|$)|(\d{4}-\d{2}-\d{2})"
)

# ---------------------------------------------------------------------------
# Open Threads parser
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

        # Skip resolved items — lines starting with ✅ or ~~strikethrough~~.
        # Only skip the thread bullet itself, not status/detail lines
        # (which may contain "resolved" in a negative context like "not resolved").
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
    Uses ≥50% word overlap matching (same heuristic as distillation).
    """
    deliverables = _extract_recent_deliverables(daily_dir)
    if not deliverables:
        return threads

    deliv_word_sets = [set(d.split()) for d in deliverables]
    filtered: list[dict] = []

    for t in threads:
        title = t.get("title", "")
        title_lower = title.lower()

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
    for t in threads:
        if t["priority"] != "P0":
            continue
        # Search title + status for earliest date reference
        search_text = f"{t['title']} {t.get('status', '')}"
        dates_found = _DATE_REF_RE.findall(search_text)
        earliest = None
        for m, d, full in dates_found:
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
    """Estimate days open from date references in thread title/status.

    Uses module-level _DATE_REF_RE (anchored to word boundary) to avoid
    matching version numbers like 'v2/3'.
    """
    now = datetime.now()
    search_text = f"{thread.get('title', '')} {thread.get('status', '')}"
    dates_found = _DATE_REF_RE.findall(search_text)
    earliest = None
    for m, d, full in dates_found:
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
            if any(kw in title_lower for kw in ["rebuild", "deploy"]):
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
        # Skip if already covered by a thread (same 30-char prefix match as momentum)
        if any(hint_lower[:30] in t or t[:30] in hint_lower for t in seen_titles):
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
# Level 3: Cross-Session Learning
# ---------------------------------------------------------------------------

_SKIP_THRESHOLD = 2          # skips before penalty kicks in
_SKIP_PENALTY_PER = 10       # -10 per skip after threshold
_SKIP_PENALTY_CAP = 30       # max penalty
_AFFINITY_BONUS = 15         # boost for matching user's preferred work type
_OBSERVATIONS_CAP = 30       # rolling window size

# Work type classification keywords — longer phrases checked first (weighted 2x)
_WORK_TYPE_KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "feature": [
        ("implemented", 1), ("shipped", 1), ("added new", 2), ("added", 1),
        ("built new", 2), ("built", 1), ("created new", 2), ("new feature", 2),
    ],
    "maintenance": [
        ("fixed", 1), ("rebuilt", 1), ("verified", 1), ("upgraded", 1),
        ("migrated", 1), ("patched", 1), ("resolved", 1), ("fix ", 1),
        ("fixing", 1), ("repair", 1),
    ],
    "investigation": [
        ("root cause", 2), ("investigated", 1), ("diagnosed", 1),
        ("analyzed", 1), ("traced", 1), ("debugged", 1),
    ],
    "design": [
        ("design doc", 2), ("wireframe", 2), ("mockup", 2), ("architecture", 1),
        ("designed", 1), ("spec", 1), ("drafted", 1),
    ],
}


@dataclass
class LearningState:
    """Persistent learning state across sessions."""
    version: int = 1
    last_updated: str = ""
    last_briefing_date: str = ""
    last_briefing_suggested: list[str] = field(default_factory=list)
    item_history: dict[str, dict[str, Any]] = field(default_factory=dict)
    work_type_distribution: dict[str, int] = field(default_factory=lambda: {
        "feature": 0, "maintenance": 0, "investigation": 0, "design": 0, "other": 0,
    })
    observations: list[dict[str, Any]] = field(default_factory=list)
    # Dedup guard: "stem:sessions_count" of the DailyActivity file last
    # processed by _update_learning_from_activity(). Prevents re-counting
    # the same deliverables across multiple session starts within the same
    # day.  Previous mtime-based guard was unreliable because DailyActivity
    # is append-only — mtime changes every session, causing double-counting.
    last_processed_activity_key: str = ""

    # L4: Effectiveness scoring — tracks whether briefing suggestions
    # actually influenced user behavior, enabling self-tuning.
    effectiveness: dict[str, Any] = field(default_factory=lambda: {
        "total_suggestions": 0,
        "followed": 0,
        "skipped": 0,
        "follow_rate": 0.0,
        "trend": "gathering",  # gathering | improving | declining | stable
    })

    def preferred_work_type(self) -> Optional[str]:
        """Return the work type with highest count, or None if no data."""
        if not self.work_type_distribution:
            return None
        total = sum(self.work_type_distribution.values())
        if total == 0:
            return None
        return max(self.work_type_distribution, key=self.work_type_distribution.get)

    def get_item_history(self, title: str) -> Optional[dict]:
        """Fuzzy lookup — matches if significant words overlap."""
        title_words = set(title.lower().split()) - {"the", "a", "an", "in", "on", "for", "and", "or", "to"}
        best_match = None
        best_overlap = 0
        for k, v in self.item_history.items():
            k_words = set(k.lower().split()) - {"the", "a", "an", "in", "on", "for", "and", "or", "to"}
            overlap = len(title_words & k_words)
            if overlap >= max(min(len(k_words), len(title_words)) // 2, 1) and overlap > best_overlap:
                best_match = v
                best_overlap = overlap
        return best_match

    def learning_summary(self) -> Optional[str]:
        """Generate a brief learning insight for the briefing."""
        total = sum(self.work_type_distribution.values())
        if total < 3:
            return None  # not enough data
        preferred = self.preferred_work_type()
        if not preferred:
            return None
        count = self.work_type_distribution[preferred]
        pct = int(count / total * 100)
        if pct < 40:
            return None  # no clear preference
        return f"Pattern: {preferred} work preferred ({count}/{total} sessions, {pct}%)"


def _state_path(workspace_dir: Path) -> Path:
    """State file lives inside SwarmWS (gitignored)."""
    return workspace_dir / "proactive_state.json"


def _load_learning_state(workspace_dir: Path) -> LearningState:
    """Load learning state from JSON. Returns default on any failure."""
    path = _state_path(workspace_dir)
    if not path.exists():
        return LearningState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = LearningState(
            version=data.get("version", 1),
            last_updated=data.get("last_updated", ""),
            last_briefing_date=data.get("last_briefing_date", ""),
            last_briefing_suggested=data.get("last_briefing_suggested", []),
            item_history=data.get("item_history", {}),
            work_type_distribution=data.get("work_type_distribution", {
                "feature": 0, "maintenance": 0, "investigation": 0, "design": 0, "other": 0,
            }),
            observations=data.get("observations", []),
            last_processed_activity_key=data.get("last_processed_activity_key", ""),
            effectiveness=data.get("effectiveness", {
                "total_suggestions": 0, "followed": 0, "skipped": 0,
                "follow_rate": 0.0, "trend": "gathering",
            }),
        )
        return state
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("Corrupt proactive_state.json, resetting: %s", exc)
        return LearningState()


def _save_learning_state(workspace_dir: Path, state: LearningState) -> None:
    """Atomically save learning state to JSON.

    NOTE: Concurrent tabs may each call this independently. Last writer wins.
    This is acceptable because learning data is statistical (counters, distributions),
    not precise — a lost increment is noise, not corruption. Do NOT add file locking
    here unless we move to precise counters that require transactional guarantees.
    """
    path = _state_path(workspace_dir)
    state.last_updated = datetime.now().isoformat(timespec="seconds")
    data = {
        "version": state.version,
        "last_updated": state.last_updated,
        "last_briefing_date": state.last_briefing_date,
        "last_briefing_suggested": state.last_briefing_suggested,
        "item_history": state.item_history,
        "work_type_distribution": state.work_type_distribution,
        "observations": state.observations[-_OBSERVATIONS_CAP:],
        "last_processed_activity_key": state.last_processed_activity_key,
        "effectiveness": state.effectiveness,
    }
    tmp_path = path.with_suffix(".tmp")
    try:
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        # Restrict permissions — file contains behavioral data (work patterns)
        import os
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(path)
    except Exception as exc:
        logger.warning("Failed to save proactive_state.json: %s", exc)
        # Clean up orphaned temp file to avoid stale data on next load
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


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
            short = summary[:100].rstrip() + ("…" if len(summary) > 100 else "")
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

        icon = "✅" if status == "success" else "❌" if status == "failed" else "⏭️"

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


def _update_effectiveness(
    learning_state: "LearningState",
    last_suggested: list[str],
    actual_deliverables: list[str],
) -> None:
    """Compare what was suggested vs what was actually done. Update effectiveness stats.

    Called during distillation when DailyActivity deliverables are available.
    A suggestion is "followed" if any deliverable title fuzzy-matches it
    (case-insensitive substring match in either direction).
    """
    if not last_suggested:
        return

    eff = learning_state.effectiveness
    followed = 0
    for suggestion in last_suggested:
        s_lower = suggestion.lower()
        for deliverable in actual_deliverables:
            d_lower = deliverable.lower()
            if s_lower in d_lower or d_lower in s_lower:
                followed += 1
                break

    skipped = len(last_suggested) - followed
    eff["total_suggestions"] = eff.get("total_suggestions", 0) + len(last_suggested)
    eff["followed"] = eff.get("followed", 0) + followed
    eff["skipped"] = eff.get("skipped", 0) + skipped

    total = eff["total_suggestions"]
    eff["follow_rate"] = round(eff["followed"] / total, 3) if total > 0 else 0.0

    # Trend detection (need >=10 data points)
    if total >= 10:
        rate = eff["follow_rate"]
        if rate < 0.3:
            eff["trend"] = "declining"
        elif rate > 0.8:
            eff["trend"] = "improving"
        else:
            eff["trend"] = "stable"
    else:
        eff["trend"] = "gathering"

    learning_state.effectiveness = eff


def _classify_work_type(text: str) -> str:
    """Classify a deliverable or title into a work type by weighted keyword matching.

    Multi-word phrases use substring match. Single words use word-boundary
    match to avoid 'built' matching inside 'rebuilt'.
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}
    for work_type, keywords in _WORK_TYPE_KEYWORDS.items():
        score = 0
        for kw, weight in keywords:
            if " " in kw:
                # Multi-word phrase: substring match
                if kw in text_lower:
                    score += weight
            else:
                # Single word: word-boundary match via regex
                if re.search(rf"\b{re.escape(kw)}\b", text_lower):
                    score += weight
        if score > 0:
            scores[work_type] = score
    if not scores:
        return "other"  # no keyword match — avoid biasing distribution
    return max(scores, key=scores.get)


def _extract_deliverables(daily_dir: Path) -> list[str]:
    """Extract **Delivered:** lines from ALL sessions in the most recent DailyActivity file.

    DailyActivity files are append-only with multiple sessions per day.
    Each session has its own **Delivered:** section. We collect from all of them
    so the learning loop sees the full day's work, not just the first session.
    """
    if not daily_dir.is_dir():
        return []
    da_files = sorted(
        [f for f in daily_dir.glob("*.md") if f.stem[:4].isdigit()],
        key=lambda f: f.stem,
        reverse=True,
    )
    if not da_files:
        return []

    deliverables: list[str] = []
    try:
        content = da_files[0].read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    in_delivered = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("**Delivered:**"):
            in_delivered = True
            continue
        if in_delivered:
            if stripped.startswith("- "):
                deliverables.append(stripped.removeprefix("- ").strip())
            elif stripped.startswith("**") or stripped.startswith("##"):
                in_delivered = False  # next section — will re-enter on next **Delivered:**
    return deliverables


def _normalize_history_key(title: str) -> str:
    """Normalize a suggestion title into a stable item_history key.

    Strips punctuation, collapses whitespace, lowercases, and truncates
    to 50 chars.  This prevents duplicate keys like "mcp servers not
    connecting in app" vs "mcp servers not connecting in-app".
    """
    key = re.sub(r"[^\w\s]", " ", title.lower())
    key = re.sub(r"\s+", " ", key).strip()
    return key[:50]


def _update_learning_from_activity(
    state: LearningState,
    daily_dir: Path,
) -> LearningState:
    """Compare last session's suggestions against actual deliverables.

    Updates skip/follow counts and work type distribution.
    Only runs if there's a previous briefing to compare against.

    Dedup guard: uses ``(file_stem, sessions_count)`` from DailyActivity
    frontmatter instead of mtime.  mtime changes on every append, but
    sessions_count only increments when a new session entry is written —
    preventing the same deliverables from being counted multiple times.
    """
    if not state.last_briefing_suggested:
        return state  # no previous suggestions to compare

    # --- Dedup guard: skip if DailyActivity hasn't gained new sessions ---
    if daily_dir.is_dir():
        da_files = sorted(
            [f for f in daily_dir.glob("*.md") if f.stem[:4].isdigit()],
            key=lambda f: f.stem,
            reverse=True,
        )
        if da_files:
            try:
                _content = da_files[0].read_text(encoding="utf-8")
                _sc = 0
                if _content.startswith("---"):
                    _end = _content.find("---", 3)
                    if _end != -1:
                        for _line in _content[3:_end].splitlines():
                            if _line.strip().startswith("sessions_count:"):
                                _sc = int(_line.split(":", 1)[1].strip())
                                break
                current_key = f"{da_files[0].stem}:{_sc}"
            except (OSError, ValueError):
                current_key = ""
            if current_key and current_key == state.last_processed_activity_key:
                return state  # already processed this version
            if current_key:
                state.last_processed_activity_key = current_key

    deliverables = _extract_deliverables(daily_dir)
    if not deliverables:
        return state  # no deliverables to compare

    # Classify the overall work type from deliverables
    combined = " ".join(deliverables)
    session_work_type = _classify_work_type(combined)
    state.work_type_distribution[session_work_type] = (
        state.work_type_distribution.get(session_work_type, 0) + 1
    )

    # Check each suggestion: was it followed or skipped?
    deliverables_lower = " ".join(d.lower() for d in deliverables)

    for suggested_title in state.last_briefing_suggested:
        key = _normalize_history_key(suggested_title)
        # Fuzzy match: any significant overlap between suggestion and deliverables
        title_words = set(key.split()) - {"the", "a", "an", "in", "on", "for", "and", "or", "to"}
        matched = sum(1 for w in title_words if w in deliverables_lower)
        followed = matched >= max(len(title_words) // 3, 1)

        # Update item history
        if key not in state.item_history:
            state.item_history[key] = {
                "suggested_count": 0, "followed_count": 0,
                "skipped_count": 0, "last_suggested": "", "last_worked": None,
            }
        history = state.item_history[key]
        history["suggested_count"] = history.get("suggested_count", 0) + 1
        if followed:
            history["followed_count"] = history.get("followed_count", 0) + 1
            history["last_worked"] = datetime.now().strftime("%Y-%m-%d")
        else:
            history["skipped_count"] = history.get("skipped_count", 0) + 1
        history["last_suggested"] = datetime.now().strftime("%Y-%m-%d")

    # Record observation
    state.observations.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "suggested_top": state.last_briefing_suggested[0] if state.last_briefing_suggested else "",
        "actual_work": deliverables[0] if deliverables else "",
        "work_type": session_work_type,
        "followed_suggestion": any(
            sum(1 for w in set(s[:50].lower().split()) - {"the", "a", "an", "in", "on", "for"}
                if w in deliverables_lower)
            >= max(len(set(s[:50].lower().split())) // 3, 1)
            for s in state.last_briefing_suggested[:1]
        ),
    })

    # Cap observations
    if len(state.observations) > _OBSERVATIONS_CAP:
        state.observations = state.observations[-_OBSERVATIONS_CAP:]

    return state


def _apply_learning(item: ScoredItem, state: LearningState) -> None:
    """Mutate a ScoredItem's score in-place based on learned patterns.

    Modifications (in-place):
    - Skip penalty: -10/skip (after threshold 2), capped at -30
    - Work type affinity: +15 for items matching preferred type
    """
    adjustment = 0

    # 1. Skip penalty
    history = state.get_item_history(item.title)
    if history:
        skip_count = history.get("skipped_count", 0)
        if skip_count >= _SKIP_THRESHOLD:
            penalty = min((skip_count - _SKIP_THRESHOLD + 1) * _SKIP_PENALTY_PER, _SKIP_PENALTY_CAP)
            adjustment -= penalty

    # 2. Work type affinity
    preferred = state.preferred_work_type()
    if preferred:
        item_type = _classify_work_type(f"{item.title} {item.status}")
        if item_type == preferred:
            adjustment += _AFFINITY_BONUS

    item.score = max(item.score + adjustment, 0)


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

        # ── Read-time staleness filter ──
        # Suppress threads whose topics appear in recent deliverables.
        # This is the safety net for when distillation hasn't run yet
        # (e.g. first session after a productive one).  See COE: memory
        # pipeline temporal lag gap (2026-03-19).
        threads = _filter_completed_threads(threads, daily_dir)

        # ── L3: Update learning state from previous session ──
        learning_state = _load_learning_state(workspace)
        learning_state = _update_learning_from_activity(learning_state, daily_dir)

        # ── Build briefing (L2: ranked suggestions + L3: learning adjustments) ──
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
