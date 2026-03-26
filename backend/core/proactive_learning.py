"""Proactive Intelligence — Level 3 cross-session learning.

Tracks suggestion outcomes (followed vs skipped), work type distribution,
and effectiveness scoring. Persists to proactive_state.json in SwarmWS.
All data is statistical — last-writer-wins concurrency is acceptable.

Key exports:
- LearningState             — persistent learning state dataclass
- load_learning_state()     — load from JSON (default on failure)
- save_learning_state()     — atomic write to JSON
- apply_learning()          — adjust ScoredItem score from learned patterns
- update_learning_from_activity() — compare suggestions vs deliverables
- update_effectiveness()    — compare suggestions vs actual (for distillation hook)
- classify_work_type()      — keyword-based work type classification
- extract_deliverables()    — parse Delivered: lines from DailyActivity

Split from proactive_intelligence.py (2026-03-25, Kiro feedback).
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
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


# ---------------------------------------------------------------------------
# LearningState dataclass
# ---------------------------------------------------------------------------

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
    # processed by update_learning_from_activity(). Prevents re-counting
    # the same deliverables across multiple session starts within the same
    # day.  Previous mtime-based guard was unreliable because DailyActivity
    # is append-only — mtime changes every session, causing double-counting.
    #
    # NOTE: Two tabs racing on the same DailyActivity file could both pass
    # the dedup check before either writes back, causing one observation to
    # be double-counted. Acceptable for statistical counters with last-writer-
    # wins persistence (counts converge quickly). Not worth adding file
    # locking for — revisit only if tab count exceeds ~4.
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


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _state_path(workspace_dir: Path) -> Path:
    """State file lives in Services/swarm-jobs/ alongside other job state."""
    new_path = workspace_dir / "Services" / "swarm-jobs" / "proactive_state.json"
    # Migration: move from old root-level location if it exists
    old_path = workspace_dir / "proactive_state.json"
    if old_path.exists() and not new_path.exists():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
        logger.info("Migrated proactive_state.json → Services/swarm-jobs/")
    return new_path


def load_learning_state(workspace_dir: Path) -> LearningState:
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


def save_learning_state(workspace_dir: Path, state: LearningState) -> None:
    """Atomically save learning state to JSON.

    NOTE: Concurrent tabs may each call this independently. Last writer wins.
    This is acceptable because learning data is statistical (counters, distributions),
    not precise — a lost increment is noise, not corruption. Do NOT add file locking
    here unless we move to precise counters that require transactional guarantees.
    """
    path = _state_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
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


# ---------------------------------------------------------------------------
# Work type classification
# ---------------------------------------------------------------------------

def classify_work_type(text: str) -> str:
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


# ---------------------------------------------------------------------------
# Deliverable extraction
# ---------------------------------------------------------------------------

def extract_deliverables(daily_dir: Path) -> list[str]:
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


# ---------------------------------------------------------------------------
# History key normalization
# ---------------------------------------------------------------------------

def _normalize_history_key(title: str) -> str:
    """Normalize a suggestion title into a stable item_history key.

    Strips punctuation, collapses whitespace, lowercases, and truncates
    to 50 chars.  This prevents duplicate keys like "mcp servers not
    connecting in app" vs "mcp servers not connecting in-app".
    """
    key = re.sub(r"[^\w\s]", " ", title.lower())
    key = re.sub(r"\s+", " ", key).strip()
    return key[:50]


# ---------------------------------------------------------------------------
# Effectiveness scoring (called by distillation hook)
# ---------------------------------------------------------------------------

def update_effectiveness(
    learning_state: LearningState,
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


# ---------------------------------------------------------------------------
# Learning update from DailyActivity
# ---------------------------------------------------------------------------

def update_learning_from_activity(
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

    deliverables = extract_deliverables(daily_dir)
    if not deliverables:
        return state  # no deliverables to compare

    # Classify the overall work type from deliverables
    combined = " ".join(deliverables)
    session_work_type = classify_work_type(combined)
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


# ---------------------------------------------------------------------------
# Learning score adjustment
# ---------------------------------------------------------------------------

def apply_learning(item: "ScoredItem", state: LearningState) -> None:
    """Mutate a ScoredItem's score in-place based on learned patterns.

    Modifications (in-place):
    - Skip penalty: -10/skip (after threshold 2), capped at -30
    - Work type affinity: +15 for items matching preferred type
    """
    # Import here to avoid circular dependency (ScoredItem is in proactive_scoring)
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
        item_type = classify_work_type(f"{item.title} {item.status}")
        if item_type == preferred:
            adjustment += _AFFINITY_BONUS

    item.score = max(item.score + adjustment, 0)
