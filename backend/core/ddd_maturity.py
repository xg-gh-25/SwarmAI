"""DDD Maturity Annotations (T4) — Evidence-Based Trust Calibration.

Per-section maturity tracking with 4 levels:
- Sparse: single source, unverified. AI asks before relying.
- Growing: 2-3 sources, partially validated. AI references with annotation.
- Mature: multi-source verified, production-tested. Full trust.
- Evergreen: auto-maintained, contradiction-free 90d+. Manual promotion only.

Evidence tracked per section:
- source_count: distinct source_stages in changelog (reflect, correction, decision)
- verified_by_production: pipeline DELIVER succeeded while relying on this section
- used_in_decision: pipeline EVALUATE/THINK loaded this section
- days_at_level: how long at current maturity level

Storage: HTML comment immediately after ## header in DDD .md files.
Format: <!-- maturity: level | sources: N | verified: bool | used: bool | days: N | promoted: ISO-date -->

Public API:
    MaturityState        — dataclass holding per-section state
    parse_maturity(content) → dict[section_name, MaturityState]
    inject_maturity(content, states) → str
    evaluate_promotion(state) → Optional[str]  (new level or None)
    promote_section(project_dir, doc_name, section_name, new_level) → bool
    evaluate_all_promotions(project_dir) → list[dict]
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.project_registry import DDD_CANONICAL_DOCS  # Run 0: single source of truth

logger = logging.getLogger(__name__)


# Valid maturity levels (ordered)
LEVELS = ("sparse", "growing", "mature", "evergreen")

# Regex for maturity HTML comment (supports optional trust: field)
_MATURITY_RE = re.compile(
    r"^<!--\s*maturity:\s*(\w+)\s*\|"
    r"\s*sources:\s*(\d+)\s*\|"
    r"\s*verified:\s*(true|false)\s*\|"
    r"\s*used:\s*(true|false)\s*\|"
    r"\s*days:\s*(\d+)\s*\|"
    r"(?:\s*trust:\s*\w+\s*\|)?"  # Optional trust field (new)
    r"\s*promoted:\s*([\w\-]+)\s*-->$"
)

# Regex for ## section headers (not ###)
_SECTION_RE = re.compile(r"^##\s+(.+)$")


@dataclass
class MaturityState:
    """Per-section maturity evidence state.

    The ``trust`` field in the annotation output is derived at write-time
    from source_count + verified_by_production. It may become stale if
    these values change between annotation writes. This is acceptable
    because annotations are recomputed on each health hook run (daily).
    PE-8: staleness window = max 24 hours between health hook cycles.
    """

    level: str = "sparse"
    source_count: int = 0
    verified_by_production: bool = False
    used_in_decision: bool = False
    days_at_level: int = 0
    last_promoted: Optional[datetime] = None

    def __post_init__(self):
        if self.level not in LEVELS:
            # Fail-loud: an illegal level is doc pollution (a hand-written value
            # the engine's vocabulary doesn't contain). Warn before coercing so
            # it's visible, not silently swallowed. The guard ensures the legal
            # default ("sparse") never warns — no warn-storm.
            logger.warning(
                "DDD maturity: illegal level %r (not in %s) — coercing to 'sparse'. "
                "Likely a hand-edited annotation; fix the source doc.",
                self.level, LEVELS,
            )
            self.level = "sparse"

    def to_comment(self) -> str:
        """Serialize to HTML comment string."""
        promoted_str = (
            self.last_promoted.strftime("%Y-%m-%d")
            if self.last_promoted
            else "none"
        )
        # Derive trust level from health score (if available)
        trust = "unknown"
        if self.source_count >= 3 and self.verified_by_production:
            trust = "full"
        elif self.source_count >= 2 or self.used_in_decision:
            trust = "high"
        elif self.source_count >= 1:
            trust = "moderate"
        else:
            trust = "low"  # ⚠️ Agent should confirm before relying

        return (
            f"<!-- maturity: {self.level} | "
            f"sources: {self.source_count} | "
            f"verified: {'true' if self.verified_by_production else 'false'} | "
            f"used: {'true' if self.used_in_decision else 'false'} | "
            f"days: {self.days_at_level} | "
            f"trust: {trust} | "
            f"promoted: {promoted_str} -->"
        )


def parse_maturity(content: str) -> dict[str, MaturityState]:
    """Parse maturity annotations from DDD markdown content.

    Returns dict mapping section_name → MaturityState.
    Sections without annotations default to Sparse with zeroed evidence.
    """
    if not content or not content.strip():
        return {}

    lines = content.splitlines()
    result: dict[str, MaturityState] = {}
    current_section: Optional[str] = None
    found_annotation_for_current = False

    for i, line in enumerate(lines):
        # Check for ## header
        m = _SECTION_RE.match(line)
        if m:
            # If previous section had no annotation, mark as Sparse
            if current_section and not found_annotation_for_current:
                result[current_section] = MaturityState()

            # Strip any existing maturity annotation text from header
            header_text = m.group(1).strip()
            # Remove [Sparse]/[Growing] etc. if in header
            header_text = re.sub(
                r"\s*\[(Sparse|Growing|Mature|Evergreen)\]\s*$", "",
                header_text,
            )
            current_section = header_text
            found_annotation_for_current = False
            continue

        # Check for maturity comment (only valid immediately after ## header)
        if current_section and not found_annotation_for_current:
            stripped = line.strip()
            if not stripped:
                continue  # skip blank lines between header and comment
            am = _MATURITY_RE.match(stripped)
            if am:
                state = _parse_annotation_match(am)
                result[current_section] = state
                found_annotation_for_current = True
            else:
                # First non-blank, non-annotation line → no annotation present
                result[current_section] = MaturityState()
                found_annotation_for_current = True

    # Flush last section
    if current_section and not found_annotation_for_current:
        result[current_section] = MaturityState()

    return result


def _parse_annotation_match(m: re.Match) -> MaturityState:
    """Parse a regex match into MaturityState."""
    level = m.group(1).lower()
    if level not in LEVELS:
        # Fail-loud: the annotation matched the regex but carries an illegal
        # level (e.g. a hand-written 'seeded'). This is the primary evidence-loss
        # site — we discard the parsed sources/verified/used and return a zeroed
        # default. Warn naming the illegal level so the pollution is visible
        # instead of silently zeroing the section's evidence.
        logger.warning(
            "DDD maturity: illegal level %r in annotation (not in %s) — "
            "discarding this section's parsed evidence, returning default. "
            "Fix the source doc's maturity annotation.",
            level, LEVELS,
        )
        return MaturityState()

    source_count = int(m.group(2))
    verified = m.group(3).lower() == "true"
    used = m.group(4).lower() == "true"
    days = int(m.group(5))
    promoted_str = m.group(6)

    last_promoted: Optional[datetime] = None
    if promoted_str and promoted_str != "none":
        try:
            last_promoted = datetime.strptime(promoted_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            last_promoted = None

    return MaturityState(
        level=level,
        source_count=source_count,
        verified_by_production=verified,
        used_in_decision=used,
        days_at_level=days,
        last_promoted=last_promoted,
    )


def inject_maturity(content: str, states: dict[str, MaturityState]) -> str:
    """Write maturity annotations into DDD markdown content.

    For sections in `states`: adds or replaces the HTML comment.
    Sections NOT in `states` are left untouched.
    """
    if not content or not states:
        return content

    trailing_newline = content.endswith("\n")
    lines = content.splitlines()
    result_lines: list[str] = []
    current_section: Optional[str] = None
    skip_old_annotation = False
    injected_for_current = False

    for i, line in enumerate(lines):
        # Check for ## header
        m = _SECTION_RE.match(line)
        if m:
            # New section — inject annotation for previous if needed
            # (shouldn't happen if previous was handled, but safety)
            current_section = m.group(1).strip()
            current_section = re.sub(
                r"\s*\[(Sparse|Growing|Mature|Evergreen)\]\s*$", "",
                current_section,
            )
            injected_for_current = False
            skip_old_annotation = current_section in states
            result_lines.append(line)

            # Inject new annotation immediately after header
            if current_section in states:
                result_lines.append(states[current_section].to_comment())
                injected_for_current = True
            continue

        # Skip old annotation line if we're replacing
        if skip_old_annotation:
            stripped = line.strip()
            if _MATURITY_RE.match(stripped):
                # Skip old annotation — we already injected the new one
                skip_old_annotation = False
                continue
            elif not stripped:
                # Blank line between header and content — keep it
                result_lines.append(line)
                continue
            else:
                # Content line — stop looking for old annotation
                skip_old_annotation = False
                result_lines.append(line)
                continue

        result_lines.append(line)

    result = "\n".join(result_lines)
    if trailing_newline and not result.endswith("\n"):
        result += "\n"
    return result


def evaluate_promotion(state: MaturityState) -> Optional[str]:
    """Evaluate if a section should be promoted.

    Returns new level string or None if no promotion.
    Mature→Evergreen is NEVER auto-promoted (manual only).
    """
    if state.level == "sparse":
        if state.source_count >= 2 and state.verified_by_production:
            return "growing"

    elif state.level == "growing":
        if (
            state.source_count >= 3
            and state.days_at_level > 30
            and state.used_in_decision
        ):
            return "mature"

    # mature → evergreen: MANUAL ONLY
    # evergreen: no promotion possible
    return None


def evaluate_demotion(state: MaturityState, health_score: int = 50) -> Optional[str]:
    """Evaluate if a section should be demoted due to staleness.

    Demotion is the inverse of promotion — triggered by declining health
    and absence of new evidence. Evergreen sections are immune.

    Args:
        state: Current maturity state of the section
        health_score: Composite health score (0-100) from ddd_health.py

    Returns:
        New (lower) level string, or None if no demotion needed.
    """
    if state.level == "evergreen":
        return None  # Immune — manually curated eternal truths

    if state.level == "mature":
        # Mature → Growing: 180 days without new sources + declining health
        if state.days_at_level > 180 and health_score < 40:
            return "growing"

    elif state.level == "growing":
        # Growing → Sparse: 90 days without sources + poor health
        if state.days_at_level > 90 and health_score < 30:
            return "sparse"

    # sparse: already bottom, can't demote
    return None


def promote_section(
    project_dir: Path, doc_name: str, section_name: str, new_level: str
) -> bool:
    """Promote a section's maturity level in the actual .md file.

    Reads the doc, updates the annotation, writes back.
    Returns True on success. Returns False for invalid level.
    """
    if new_level not in LEVELS:
        return False

    doc_path = project_dir / doc_name
    if not doc_path.is_file():
        return False

    try:
        content = doc_path.read_text(encoding="utf-8")
    except OSError:
        return False

    states = parse_maturity(content)
    if section_name not in states:
        return False

    state = states[section_name]
    state.level = new_level
    state.last_promoted = datetime.now(timezone.utc)
    state.days_at_level = 0  # Reset on promotion

    updated = inject_maturity(content, {section_name: state})
    try:
        doc_path.write_text(updated, encoding="utf-8")
    except OSError:
        return False

    return True


def evaluate_all_promotions(project_dir: Path) -> list[dict]:
    """Evaluate all sections across all DDD docs for promotion eligibility.

    Returns list of promotion records (for changelog logging).
    Does NOT write to files — caller decides whether to apply.
    """
    promotions: list[dict] = []

    for doc_name in DDD_CANONICAL_DOCS:
        doc_path = project_dir / doc_name
        if not doc_path.is_file():
            continue

        try:
            content = doc_path.read_text(encoding="utf-8")
        except OSError:
            continue

        states = parse_maturity(content)
        for section_name, state in states.items():
            new_level = evaluate_promotion(state)
            if new_level:
                promotions.append({
                    "doc": doc_name,
                    "section": section_name,
                    "from_level": state.level,
                    "to_level": new_level,
                    "evidence": {
                        "source_count": state.source_count,
                        "verified_by_production": state.verified_by_production,
                        "used_in_decision": state.used_in_decision,
                        "days_at_level": state.days_at_level,
                    },
                })

    return promotions


def compute_evidence_from_changelog(project_dir: Path) -> dict[tuple[str, str], dict]:
    """Scan changelog to compute evidence for each section.

    Returns {(doc_name, section_name): {"source_count": N, ...}}
    source_count = number of DISTINCT source_stages that have written to this section.
    """
    changelog_path = project_dir / ".artifacts" / "ddd-changelog.jsonl"
    if not changelog_path.is_file():
        return {}

    # Track distinct sources per section
    section_sources: dict[tuple[str, str], set[str]] = {}

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
            source = entry.get("source_stage", "")

            if not doc or not section or not source:
                continue

            # Fix #3: Internal events don't count as external evidence
            if source == "maturity_promotion":
                continue

            key = (doc, section)
            if key not in section_sources:
                section_sources[key] = set()
            section_sources[key].add(source)
    except OSError:
        return {}

    return {
        key: {"source_count": len(sources)}
        for key, sources in section_sources.items()
    }


def update_evidence_from_changelog(project_dir: Path) -> dict[str, int]:
    """Update maturity evidence in DDD docs from changelog data.

    Scans changelog for distinct source_stages per section,
    updates source_count AND refreshes days_at_level from last_promoted.

    Returns {"updated": N, "unchanged": M} counts.
    """
    evidence = compute_evidence_from_changelog(project_dir)

    updated = 0
    unchanged = 0

    # Process ALL DDD docs (not just those with changelog evidence)
    # because days_at_level needs refreshing for ALL sections.
    now = datetime.now(timezone.utc)

    for doc_name in DDD_CANONICAL_DOCS:
        doc_path = project_dir / doc_name
        if not doc_path.is_file():
            continue

        try:
            content = doc_path.read_text(encoding="utf-8")
        except OSError:
            continue

        states = parse_maturity(content)
        if not states:
            continue

        changed = False

        for section_name, state in states.items():
            # Update source_count from changelog evidence
            key = (doc_name, section_name)
            if key in evidence:
                new_count = evidence[key]["source_count"]
                if state.source_count != new_count:
                    state.source_count = new_count
                    changed = True
                    updated += 1
                else:
                    unchanged += 1

            # Fix #2: Refresh days_at_level from last_promoted timestamp
            if state.last_promoted:
                computed_days = (now - state.last_promoted).days
                if state.days_at_level != computed_days:
                    state.days_at_level = computed_days
                    changed = True

        if changed:
            new_content = inject_maturity(content, states)
            # PE-1: Only write if content actually differs — prevents daily git churn
            if new_content != content:
                try:
                    doc_path.write_text(new_content, encoding="utf-8")
                except OSError:
                    pass

    return {"updated": updated, "unchanged": unchanged}
