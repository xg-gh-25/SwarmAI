"""Steeringify v2 — Extract recurring corrections into STEERING.md standing rules.

Structured Pattern-field extraction with C-entry cross-reference graph.
Replaces keyword clustering with explicit reference detection.

3-stage pipeline:
  1. extract_corrections() — parse EVOLUTION.md C-entries, detect cross-refs
  2. group_and_propose() — group by cross-ref graph, filter, format for STEERING.md
  3. write_approved_rules() — append approved rules to STEERING.md

Public API:
  extract_corrections(evolution_text: str) -> list[CorrectionEntry]
  group_and_propose(entries, ...) -> list[ProposedRule]
  write_approved_rules(rules, steering_path) -> int
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


# ── Data models ──


@dataclass
class CorrectionEntry:
    """Parsed C-entry from EVOLUTION.md."""

    id: str                                    # "C012"
    date: str = ""                             # "2026-04-25"
    correction: str = ""                       # The Correction field text
    pattern: str = ""                          # The Pattern field text (full)
    bold_rules: list[str] = field(default_factory=list)  # Bold prescriptive rules
    cross_refs: list[str] = field(default_factory=list)  # Referenced C-IDs
    status: str = "active"


@dataclass
class ProposedRule:
    """A rule ready for user review, formatted for STEERING.md."""

    title: str
    body: str                                  # Full rule text for STEERING.md
    source_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    already_in_steering: bool = False
    already_in_agent: bool = False
    violates_existing: str | None = None       # Existing STEERING rule violated


# ── Regex patterns ──

# C-entry header: ### C012 | 2026-04-25
_CENTRY_HEADER = re.compile(r"^### (C\d+)\s*\|?\s*(\d{4}-\d{2}-\d{2})?")

# Bold text: **some rule text** (min 10 chars)
_BOLD_RULE = re.compile(r"\*\*([^*]{10,})\*\*")

# Prescriptive language (rule must contain)
_PRESCRIPTIVE = re.compile(
    r"\b(must|should|always|never|don't|every|verify|check|run|exhaust|"
    r"ask|wait|before|after|triggers?|block(?:ing)?|require|ensure|start)\b",
    re.IGNORECASE,
)

# Descriptive patterns to reject
_DESCRIPTIVE = re.compile(
    r"^(structural fix:|this is the same|same (?:as|root cause)|"
    r"three compounding|pattern:|root cause:)",
    re.IGNORECASE,
)

# Cross-reference patterns: explicit phrases first, bare C\d+ catch-all last.
# Explicit phrasings ("same as C007", "Related: C008") get full confidence.
# Bare C-refs within Pattern fields ("see C007", "cf. C007") are still caught.
_CROSS_REF = re.compile(
    r"(?:"
    r"same (?:as |rule as |pattern as |root cause as )(C\d+)"
    r"|Related:\s*(C\d+)"
    r"|(C\d+)(?:'s|s)\s+\d+(?:st|nd|rd|th)"
    r"|(C\d+) was a specific"
    r"|(?:see |cf\.? |per |from )(C\d+)"
    r"|\b(C\d{3,})\b"
    r")",
    re.IGNORECASE,
)


# ── Stage 1: Extract ──


def extract_corrections(evolution_text: str) -> list[CorrectionEntry]:
    """Parse EVOLUTION.md, extract C-entries with bold rules and cross-refs.

    Only returns entries that are active AND have at least one bold
    prescriptive rule in their Pattern field.
    """
    entries: list[CorrectionEntry] = []
    current: CorrectionEntry | None = None
    in_field: str = ""  # "correction", "pattern", or ""

    for line in evolution_text.splitlines():
        stripped = line.strip()

        # New C-entry header
        m = _CENTRY_HEADER.match(stripped)
        if m:
            _finalize_entry(current, entries)
            current = CorrectionEntry(id=m.group(1), date=m.group(2) or "")
            in_field = ""
            continue

        if current is None:
            continue

        # Status field
        if stripped.startswith("- **Status**:"):
            status_text = stripped.split(":", 1)[1].strip().lower()
            if "resolved" in status_text:
                current.status = "resolved"
            in_field = ""
            continue

        # Correction field
        if stripped.startswith("- **Correction**:"):
            in_field = "correction"
            current.correction = stripped[len("- **Correction**:"):].strip()
            continue

        # Pattern field
        if stripped.startswith("- **Pattern**:"):
            in_field = "pattern"
            text = stripped[len("- **Pattern**:"):].strip()
            if text:
                current.pattern = text
                _extract_from_pattern_line(text, current)
            continue

        # Continuation of current field (indented or non-field-start lines)
        if stripped.startswith("- **") and not stripped.startswith("- **Pattern"):
            in_field = ""
            continue

        if in_field == "pattern" and stripped:
            current.pattern += " " + stripped
            _extract_from_pattern_line(stripped, current)
        elif in_field == "correction" and stripped:
            current.correction += " " + stripped

    # Finalize last entry
    _finalize_entry(current, entries)

    return entries


def _extract_from_pattern_line(text: str, entry: CorrectionEntry) -> None:
    """Extract bold rules and cross-references from a pattern line."""
    # Bold prescriptive rules
    for m in _BOLD_RULE.finditer(text):
        rule_text = m.group(1).strip()
        if not _PRESCRIPTIVE.search(rule_text):
            continue
        if _DESCRIPTIVE.match(rule_text):
            continue
        if rule_text not in entry.bold_rules:
            entry.bold_rules.append(rule_text)

    # Cross-references
    for m in _CROSS_REF.finditer(text):
        ref_id = next(g for g in m.groups() if g is not None)
        if ref_id != entry.id and ref_id not in entry.cross_refs:
            entry.cross_refs.append(ref_id)


def _finalize_entry(
    entry: CorrectionEntry | None,
    entries: list[CorrectionEntry],
) -> None:
    """Add entry to list if active and has a Pattern field.

    Entries without bold rules are still included — they can join groups
    via cross-references. Only groups need at least one bold rule.
    """
    if entry is None:
        return
    if entry.status == "resolved":
        return
    if not entry.pattern:
        return
    entries.append(entry)


# ── Stage 2: Group and propose ──


def group_and_propose(
    entries: list[CorrectionEntry],
    min_group_size: int = 2,
    steering_text: str = "",
    agent_text: str = "",
) -> list[ProposedRule]:
    """Group entries by cross-reference graph, produce STEERING.md proposals.

    Uses explicit cross-references (not keyword similarity) to form groups.
    Connected components in the reference graph become one proposal each.

    Args:
        entries: Parsed C-entries from extract_corrections()
        min_group_size: Minimum entries in a group to qualify (default 2)
        steering_text: Current STEERING.md content for dedup + violation detection
        agent_text: Current AGENT.md content for dedup
    """
    if not entries:
        return []

    # Build adjacency graph from cross-references
    all_ids = {e.id for e in entries}

    # Union-Find for connected components
    parent: dict[str, str] = {e.id: e.id for e in entries}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb  # No rank — O(n) worst case, fine for <100 entries

    # Connect entries via cross-references
    for entry in entries:
        for ref in entry.cross_refs:
            if ref in all_ids:
                union(entry.id, ref)

    # Build groups from connected components
    groups: dict[str, list[CorrectionEntry]] = {}
    for entry in entries:
        root = find(entry.id)
        groups.setdefault(root, []).append(entry)

    # Build proposals
    proposals: list[ProposedRule] = []
    for group in groups.values():
        source_ids = sorted({e.id for e in group}, key=lambda x: int(x[1:]))

        if len(source_ids) < min_group_size:
            continue

        # Pick the best rule text: longest bold rule from any entry in group
        # Groups without any bold rules are skipped — we need a prescriptive rule
        group_sorted = sorted(group, key=lambda e: e.date, reverse=True)
        best_rule = ""
        for entry in group_sorted:
            for rule in entry.bold_rules:
                if len(rule) > len(best_rule):
                    best_rule = rule

        if not best_rule:
            # No bold prescriptive rules in this group — skip
            continue

        # Title: first 60 chars
        title = best_rule[:60]
        if len(best_rule) > 60:
            title = title.rsplit(" ", 1)[0] + "…"

        # Confidence: multi-signal scoring
        # - Base: 0.3
        # - Source count: +0.1 per source (more C-entries = more recurrence)
        # - Rule specificity: +0.1 if rule text > 60 chars (specific > vague)
        # - Recency: +0.1 if any source is from last 30 days
        # - Cross-ref density: +0.1 if group has explicit cross-references
        confidence = 0.3
        confidence += 0.1 * len(source_ids)
        if len(best_rule) > 60:
            confidence += 0.1
        if group_sorted and group_sorted[0].date:
            try:
                newest = date.fromisoformat(group_sorted[0].date)
                if (date.today() - newest).days <= 30:
                    confidence += 0.1
            except (ValueError, TypeError):
                pass
        if any(e.cross_refs for e in group):
            confidence += 0.1
        confidence = min(1.0, confidence)

        # Build body: bold principle + context from entries
        body = f"**{best_rule}**"

        # Dedup against STEERING.md and AGENT.md
        in_steering = _text_contains_rule(steering_text, best_rule.lower())
        in_agent = _text_contains_rule(agent_text, best_rule.lower())

        # Effectiveness: check if this group's rules overlap with existing STEERING
        violation = _detect_violation(source_ids, group, steering_text)

        proposals.append(ProposedRule(
            title=title,
            body=body,
            source_ids=source_ids,
            confidence=confidence,
            already_in_steering=in_steering,
            already_in_agent=in_agent,
            violates_existing=violation,
        ))

    proposals.sort(key=lambda p: p.confidence, reverse=True)
    return proposals


def _text_contains_rule(haystack: str, rule_lower: str) -> bool:
    """Check if a rule's key phrases already appear in a text body.

    Uses a sliding window of min(5, len(words)) to catch short rules
    that would otherwise slip through a fixed 5-word window.
    """
    if not haystack:
        return False
    haystack_lower = haystack.lower()
    words = rule_lower.split()
    window = min(5, len(words))
    if window < 2:
        return rule_lower in haystack_lower
    for i in range(len(words) - window + 1):
        phrase = " ".join(words[i : i + window])
        if phrase in haystack_lower:
            return True
    return False


def _detect_violation(
    source_ids: list[str],
    group: list[CorrectionEntry],
    steering_text: str,
) -> str | None:
    """Detect if a correction group re-raises an issue already in STEERING.md.

    If the STEERING text already contains rules about the same topic (matched
    by key phrases from the group's bold rules), the newest correction in the
    group is evidence that the existing rule isn't strong enough.
    """
    if not steering_text:
        return None

    steering_lower = steering_text.lower()
    # Check if any bold rule's key phrases appear in existing STEERING
    for entry in group:
        for rule in entry.bold_rules:
            words = rule.lower().split()
            window = min(4, len(words))
            if window < 2:
                continue
            for i in range(len(words) - window + 1):
                phrase = " ".join(words[i : i + window])
                if phrase in steering_lower:
                    return f"Rule about '{phrase}' already in STEERING.md but {entry.id} re-raised the issue"

    return None


# ── Stage 3: Write ──


MAX_ACTIVE_RULES = 10


def write_approved_rules(
    rules: list[ProposedRule],
    steering_path: Path,
    max_rules: int = MAX_ACTIVE_RULES,
) -> int:
    """Append approved rules to STEERING.md Standing Rules section.

    Matches existing STEERING.md format:
      ### Title
      > Source: C-IDs | Added: date | Confidence: score

      **Bold principle.**

      Explanation text.

    Returns count of rules written.
    """
    if not rules:
        return 0

    content = ""
    if steering_path.exists():
        content = steering_path.read_text(encoding="utf-8")
    else:
        content = "## Standing Rules\n\n"

    # Count existing steeringify rules
    existing_count = content.lower().count("> source: c")
    remaining_slots = max_rules - existing_count

    if remaining_slots <= 0:
        return 0

    rules_to_write = rules[:remaining_slots]

    new_rules = []
    for r in rules_to_write:
        sources = ", ".join(r.source_ids)
        today = date.today().isoformat()
        block = (
            f"\n### {r.title}\n"
            f"> Source: {sources} | Added: {today} | "
            f"Confidence: {r.confidence:.2f}\n\n"
            f"{r.body}\n"
        )
        new_rules.append(block)

    insertion = _find_insertion_point(content)
    if insertion >= 0:
        new_content = (
            content[:insertion] + "\n".join(new_rules) + "\n" + content[insertion:]
        )
    else:
        new_content = (
            content.rstrip()
            + "\n\n## Standing Rules\n"
            + "\n".join(new_rules)
            + "\n"
        )

    steering_path.write_text(new_content, encoding="utf-8")
    return len(rules_to_write)


def _find_insertion_point(content: str) -> int:
    """Find where to insert new rules in Standing Rules section.

    Inserts before the `---` separator or before the next `## ` section,
    whichever comes first after Standing Rules header.
    """
    lines = content.split("\n")
    in_standing = False
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## standing rules"):
            in_standing = True
            continue
        if in_standing:
            stripped = line.strip()
            # Stop at separator or next top-level section
            if stripped == "---":
                return sum(len(ln) + 1 for ln in lines[:i])
            if stripped.startswith("## ") and "standing" not in stripped.lower():
                return sum(len(ln) + 1 for ln in lines[:i])

    if in_standing:
        return len(content)
    return -1
