"""DDD 5-Dimensional Health Scoring (T3).

Per-section health scoring for DDD documents across 5 dimensions:
- D1 Staleness: days since last section update (from changelog/git)
- D2 Completeness: word count + placeholder/TODO detection
- D3 Usage: changelog entry count per section (last 30 days)
- D4 Decay: score direction since last measurement
- D5 Contradiction: placeholder (50) — deferred to LLM-based periodic job

Composite = weighted average → trust level (Full/High/Moderate/Low).
Computed at pipeline EVALUATE time, stored in run.json.
Consumed by T4 maturity promotion.

Public API:
    score_staleness(days_since_update) → int
    score_completeness(content) → int
    score_usage(changelog_entries_30d) → int
    score_decay(current_composite, last_composite) → int
    compute_composite(scores) → int
    derive_trust_level(composite) → str
    compute_section_health(project_dir) → dict
"""

import json
import os
import re
import tempfile as _tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.project_registry import DDD_CANONICAL_DOCS  # Run 0: single source of truth
from typing import Optional

# Weights for composite score
WEIGHTS = {
    "staleness": 0.25,
    "completeness": 0.20,
    "usage": 0.25,
    "decay": 0.15,
    "contradiction": 0.15,
}

# Trust level thresholds
TRUST_LEVELS = [
    (80, "full"),
    (60, "high"),
    (40, "moderate"),
    (0, "low"),
]

# Placeholder patterns that indicate incomplete content
PLACEHOLDER_RE = re.compile(
    r"\b(TODO|TBD|FIXME|placeholder|coming soon|to be added)\b",
    re.IGNORECASE,
)


def score_staleness(days_since_update: int) -> int:
    """D1: Staleness score. Fresh=100, decays 3 points per day, floors at 0."""
    return max(0, 100 - (days_since_update * 3))


def score_completeness(content: str) -> int:
    """D2: Completeness score based on word count + placeholder detection."""
    if not content or not content.strip():
        return 0

    score = 100

    # Penalty for placeholders (20 points each, max 5)
    placeholder_count = len(PLACEHOLDER_RE.findall(content))
    score -= min(placeholder_count, 5) * 20

    # Penalty for short content (< 50 words)
    word_count = len(content.split())
    if word_count < 50:
        score -= 30

    return max(0, score)


def score_usage(changelog_entries_30d: int) -> int:
    """D3: Usage score. 0 entries=0, 7+=100. Linear scale: entries * 15."""
    return min(100, changelog_entries_30d * 15)


def score_decay(current_composite: float, last_composite: Optional[float]) -> int:
    """D4: Decay score. Measures direction of health change.

    No history → neutral (50).
    Improving → above 50 (max 100).
    Declining → below 50 (min 0).
    """
    if last_composite is None:
        return 50

    delta = current_composite - last_composite
    raw = 50 + (delta * 5)
    return max(0, min(100, int(raw)))


def compute_composite(scores: dict) -> int:
    """Weighted average of all 5 dimensions → integer 0-100."""
    total = sum(scores[dim] * WEIGHTS[dim] for dim in WEIGHTS)
    return int(round(total))


def derive_trust_level(composite: int) -> str:
    """Map composite score to trust level string."""
    for threshold, level in TRUST_LEVELS:
        if composite >= threshold:
            return level
    return "low"


def _parse_sections(content: str) -> list[tuple[str, int, str]]:
    """Extract ## sections from markdown content.

    Returns list of (section_name, start_line, section_content).
    """
    lines = content.splitlines()
    sections = []
    current_name = None
    current_start = 0
    current_lines: list[str] = []

    for i, line in enumerate(lines, 1):
        if line.startswith("## ") and not line.startswith("### "):
            # Flush previous section
            if current_name is not None:
                sections.append((
                    current_name, current_start,
                    "\n".join(current_lines),
                ))
            # Strip any maturity annotation from header for section name
            header_text = line[3:].strip()
            # Remove [Sparse]/[Growing]/[Mature]/[Evergreen] if present
            header_text = re.sub(
                r"\s*\[(Sparse|Growing|Mature|Evergreen)\]\s*$", "",
                header_text,
            )
            current_name = header_text
            current_start = i
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    # Flush last section
    if current_name is not None:
        sections.append((
            current_name, current_start,
            "\n".join(current_lines),
        ))

    return sections


def _build_changelog_index(changelog_path: Path, days: int = 30) -> tuple[dict, dict]:
    """Parse changelog ONCE, return two lookup dicts.

    Returns:
        (usage_counts, latest_timestamps)
        - usage_counts: {(doc, section): entry_count_in_last_N_days}
        - latest_timestamps: {(doc, section): latest_datetime}

    F4 fix: single-pass instead of O(sections × entries).
    """
    usage_counts: dict[tuple[str, str], int] = {}
    latest_ts_map: dict[tuple[str, str], datetime] = {}

    if not changelog_path.is_file():
        return usage_counts, latest_ts_map

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        for line in changelog_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            doc = entry.get("target_doc", "")
            section = entry.get("target_section", "")
            if not doc or not section:
                continue

            key = (doc, section)

            # Parse timestamp
            ts_str = entry.get("timestamp", "")
            ts: Optional[datetime] = None
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                ts = None

            # Update latest timestamp (for staleness)
            if ts is not None:
                if key not in latest_ts_map or ts > latest_ts_map[key]:
                    latest_ts_map[key] = ts

            # Count entries within cutoff (for usage)
            if ts is None or ts >= cutoff:
                usage_counts[key] = usage_counts.get(key, 0) + 1
    except OSError:
        pass

    return usage_counts, latest_ts_map


def _load_last_scores(project_dir: Path) -> dict:
    """Load previously computed section scores from state file."""
    state_path = project_dir / ".artifacts" / "section_health.json"
    if not state_path.is_file():
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data.get("docs", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _get_section_age_days(
    project_dir: Path, doc_name: str, sec_name: str,
    changelog_index: dict,
) -> int:
    """Get days since last modification of a specific section.

    Uses pre-built changelog_index for O(1) lookup per section.
    Fallback: file mtime (coarse — same age for all sections in a doc).

    Args:
        changelog_index: {(doc_name, section_name): latest_timestamp}
    """
    # F1 fix: look up THIS section specifically, not any section in the doc
    key = (doc_name, sec_name)
    latest_ts = changelog_index.get(key)
    if latest_ts:
        now = datetime.now(timezone.utc)
        return (now - latest_ts).days

    # Fallback: file mtime (all sections get same age — coarse but safe)
    doc_path = project_dir / doc_name
    if doc_path.exists():
        mtime = datetime.fromtimestamp(doc_path.stat().st_mtime, tz=timezone.utc)
        return (datetime.now(timezone.utc) - mtime).days
    return 999  # Unknown → very stale


def compute_section_health(project_dir: Path) -> dict:
    """Compute per-section health scores for all DDD docs in a project.

    Returns:
        {
            "project": str,
            "computed_at": ISO timestamp,
            "docs": {
                "TECH.md": {
                    "sections": {
                        "Architecture": {
                            "staleness": int, "completeness": int,
                            "usage": int, "decay": int, "contradiction": int,
                            "composite": int, "trust": str
                        }
                    }
                }
            }
        }
    """
    last_scores = _load_last_scores(project_dir)
    changelog_path = project_dir / ".artifacts" / "ddd-changelog.jsonl"

    # F4 fix: parse changelog ONCE, build lookup dicts for all sections
    usage_counts, latest_ts_map = _build_changelog_index(changelog_path, days=30)

    result = {
        "project": project_dir.name,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "docs": {},
    }

    for doc_name in DDD_CANONICAL_DOCS:
        doc_path = project_dir / doc_name
        if not doc_path.exists():
            continue

        try:
            content = doc_path.read_text(encoding="utf-8")
        except OSError:
            continue

        sections = _parse_sections(content)
        if not sections:
            continue

        doc_sections = {}
        for sec_name, sec_start, sec_content in sections:
            # D1: Staleness — F1 fix: pass sec_name + pre-built index
            age_days = _get_section_age_days(
                project_dir, doc_name, sec_name, latest_ts_map
            )
            staleness = score_staleness(age_days)

            # D2: Completeness
            completeness = score_completeness(sec_content)

            # D3: Usage — F4 fix: O(1) lookup from pre-built index
            entries = usage_counts.get((doc_name, sec_name), 0)
            usage = score_usage(entries)

            # D5: Contradiction (placeholder)
            contradiction = 50

            # Compute composite WITHOUT decay (D4 excluded from comparison base)
            scores_no_decay = {
                "staleness": staleness,
                "completeness": completeness,
                "usage": usage,
                "decay": 50,  # neutral placeholder for base composite
                "contradiction": contradiction,
            }
            composite_no_decay = compute_composite(scores_no_decay)

            # D4: Decay — F2 fix: compare composite_no_decay to STORED composite_no_decay
            # This prevents the decay dimension from feeding back into itself.
            last_composite_no_decay = None
            if doc_name in last_scores:
                last_sec = last_scores[doc_name].get("sections", {}).get(sec_name)
                if last_sec:
                    # Use stored composite_no_decay if available, else composite as fallback
                    last_composite_no_decay = last_sec.get(
                        "composite_no_decay", last_sec.get("composite")
                    )
            decay = score_decay(composite_no_decay, last_composite_no_decay)

            # Final composite with real decay
            scores = {
                "staleness": staleness,
                "completeness": completeness,
                "usage": usage,
                "decay": decay,
                "contradiction": contradiction,
            }
            composite = compute_composite(scores)
            trust = derive_trust_level(composite)

            doc_sections[sec_name] = {
                **scores,
                "composite": composite,
                "composite_no_decay": composite_no_decay,  # F2: stored for next run's decay calc
                "trust": trust,
            }

        result["docs"][doc_name] = {"sections": doc_sections}

    # Persist scores for next decay calculation (atomic write)
    state_path = project_dir / ".artifacts" / "section_health.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = _tempfile.mkstemp(
        dir=str(state_path.parent), suffix=".tmp"
    )
    try:
        os.write(tmp_fd, json.dumps(result, indent=2, ensure_ascii=False).encode())
        os.close(tmp_fd)
        os.replace(tmp_path, str(state_path))
    except Exception:
        try:
            os.close(tmp_fd)
        except OSError:
            pass
        if Path(tmp_path).exists():
            os.unlink(tmp_path)
        # Don't fail the scoring — state persistence is best-effort
        pass

    return result
