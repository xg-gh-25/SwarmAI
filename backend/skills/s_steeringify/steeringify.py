"""Steeringify — Extract recurring corrections into STEERING.md standing rules.

3-stage pipeline:
  1. extract_rule_candidates() — parse EVOLUTION.md C-entries, extract bold rules
  2. cluster_and_filter() — group by keyword overlap, filter by recurrence + quality
  3. write_approved_rules() — append approved rules to STEERING.md

Public API:
  extract_rule_candidates(evolution_text: str) -> list[RuleCandidate]
  cluster_and_filter(candidates, min_recurrence=2) -> list[ProposedRule]
  write_approved_rules(rules, steering_path) -> int
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# ── Data models ──

@dataclass
class RuleCandidate:
    """A bold rule extracted from one C-entry's Pattern field."""
    rule_text: str
    source_ids: list[str] = field(default_factory=list)
    recurrence: int = 1
    first_seen: str = ""
    last_seen: str = ""


@dataclass
class ProposedRule:
    """A clustered, quality-gated rule ready for user review."""
    title: str
    rule_text: str
    source_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    already_in_steering: bool = False
    already_in_agent: bool = False


# ── Regex patterns ──

# Matches C-entry headers: ### C012 | 2026-04-25
_CENTRY_HEADER = re.compile(r"^### (C\d+)\s*\|?\s*(\d{4}-\d{2}-\d{2})?")

# Matches bold text in Pattern fields: **some rule text**
_BOLD_RULE = re.compile(r"\*\*([^*]{10,})\*\*")

# Quality gate: rule must contain prescriptive language
_PRESCRIPTIVE = re.compile(
    r"\b(must|should|always|never|don\'t|every|verify|check|run|exhaust|"
    r"ask|wait|before|after|triggers?|block(?:ing)?|require|ensure)\b",
    re.IGNORECASE,
)

# Descriptive (non-prescriptive) patterns to reject
_DESCRIPTIVE = re.compile(
    r"^(structural fix:|this is the same|same (?:as|root cause)|"
    r"three compounding|pattern:|root cause:)",
    re.IGNORECASE,
)


# ── Stage 1: Extract ──

def extract_rule_candidates(evolution_text: str) -> list[RuleCandidate]:
    """Parse EVOLUTION.md text, extract bold rules from active C-entry Pattern fields.

    Skips resolved entries. Returns one RuleCandidate per bold rule found.
    """
    candidates: list[RuleCandidate] = []
    current_id: str | None = None
    current_date: str = ""
    in_pattern: bool = False
    is_resolved: bool = False

    for line in evolution_text.splitlines():
        stripped = line.strip()

        # New C-entry header
        m = _CENTRY_HEADER.match(stripped)
        if m:
            current_id = m.group(1)
            current_date = m.group(2) or ""
            in_pattern = False
            is_resolved = False
            continue

        # Check for resolved status
        if current_id and stripped.startswith("- **Status**:"):
            if "resolved" in stripped.lower():
                is_resolved = True
            continue

        # Enter Pattern field
        if current_id and stripped.startswith("- **Pattern**:"):
            in_pattern = True
            # Pattern text may be on this same line
            pattern_text = stripped[len("- **Pattern**:"):].strip()
            if pattern_text and not is_resolved:
                _extract_bold_rules(pattern_text, current_id, current_date,
                                    candidates)
            continue

        # Continuation of Pattern field (indented lines)
        if in_pattern and current_id and not is_resolved:
            if stripped.startswith("- **") and not stripped.startswith("- **Pattern"):
                in_pattern = False
                continue
            _extract_bold_rules(stripped, current_id, current_date, candidates)

    return candidates


def _extract_bold_rules(
    text: str,
    source_id: str,
    date: str,
    candidates: list[RuleCandidate],
) -> None:
    """Extract bold rules from a line of text and append to candidates."""
    for m in _BOLD_RULE.finditer(text):
        rule_text = m.group(1).strip()
        # Quality gate: must be prescriptive, not just descriptive
        if not _PRESCRIPTIVE.search(rule_text):
            continue
        if _DESCRIPTIVE.match(rule_text):
            continue
        candidates.append(RuleCandidate(
            rule_text=rule_text,
            source_ids=[source_id],
            recurrence=1,
            first_seen=date,
            last_seen=date,
        ))


# ── Stage 2: Cluster and filter ──

def _tokenize(text: str) -> set[str]:
    """Extract content words (length >= 4) for Jaccard similarity.

    Applies basic stemming (strip trailing 's', 'ing', 'ed') to improve
    matching across singular/plural and verb forms.
    """
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    # Remove common stop words
    stops = {"this", "that", "with", "from", "have", "been", "must", "should",
             "when", "before", "after", "same", "also", "just", "instead",
             "because", "about", "every", "never", "always", "more", "than",
             "does", "doing", "only", "into", "each", "other", "very"}
    stemmed = set()
    for w in words:
        if w in stops:
            continue
        # Basic suffix stripping: alternatives→alternative, reporting→report
        if w.endswith("ing") and len(w) > 6:
            w = w[:-3]
        elif w.endswith("tion") and len(w) > 7:
            w = w[:-4]
        elif w.endswith("ed") and len(w) > 5:
            w = w[:-2]
        elif w.endswith("ves") and len(w) > 6:
            w = w[:-3]  # alternatives→alternati... fix below
        elif w.endswith("es") and len(w) > 5:
            w = w[:-2]
        elif w.endswith("s") and len(w) > 5:
            w = w[:-1]
        stemmed.add(w)
    return stemmed


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two word sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_and_filter(
    candidates: list[RuleCandidate],
    min_recurrence: int = 2,
    steering_text: str = "",
    agent_text: str = "",
) -> list[ProposedRule]:
    """Cluster candidates by keyword overlap, filter by recurrence and quality.

    Args:
        candidates: Raw candidates from extract_rule_candidates()
        min_recurrence: Minimum C-entry sources to qualify (default 2)
        steering_text: Current STEERING.md content for dedup
        agent_text: Current AGENT.md content for dedup

    Returns:
        List of ProposedRule objects ready for user review.
    """
    if not candidates:
        return []

    # Cluster by Jaccard similarity > 0.15
    # Threshold is intentionally low because bold rules are short text
    # (8-15 tokens) — even topically identical rules may share only 2-3 stems.
    # Quality gate + min_recurrence filter handle false positives.
    clusters: list[list[RuleCandidate]] = []
    assigned = [False] * len(candidates)

    for i, c in enumerate(candidates):
        if assigned[i]:
            continue
        cluster = [c]
        assigned[i] = True
        tokens_i = _tokenize(c.rule_text)

        for j in range(i + 1, len(candidates)):
            if assigned[j]:
                continue
            tokens_j = _tokenize(candidates[j].rule_text)
            if _jaccard(tokens_i, tokens_j) > 0.15:
                cluster.append(candidates[j])
                assigned[j] = True

        clusters.append(cluster)

    # Build ProposedRules from clusters
    proposals: list[ProposedRule] = []
    for cluster in clusters:
        # Merge source IDs, pick the longest rule text as representative
        all_sources = []
        for c in cluster:
            for sid in c.source_ids:
                if sid not in all_sources:
                    all_sources.append(sid)

        if len(all_sources) < min_recurrence:
            continue

        # Pick the most descriptive (longest) rule as representative
        representative = max(cluster, key=lambda c: len(c.rule_text))
        dates = [c.first_seen for c in cluster if c.first_seen] + \
                [c.last_seen for c in cluster if c.last_seen]
        dates = sorted(set(d for d in dates if d))

        # Generate title from first ~50 chars
        title = representative.rule_text[:60]
        if len(representative.rule_text) > 60:
            title = title.rsplit(" ", 1)[0] + "…"

        # Confidence: more sources + more specific = higher
        confidence = min(1.0, 0.4 + 0.15 * len(all_sources))

        # Dedup against STEERING.md and AGENT.md
        rule_lower = representative.rule_text.lower()
        # Check if a substantial substring (30+ chars) appears in existing rules
        in_steering = _text_contains_rule(steering_text, rule_lower)
        in_agent = _text_contains_rule(agent_text, rule_lower)

        proposals.append(ProposedRule(
            title=title,
            rule_text=representative.rule_text,
            source_ids=all_sources,
            confidence=confidence,
            already_in_steering=in_steering,
            already_in_agent=in_agent,
        ))

    # Sort by confidence descending
    proposals.sort(key=lambda p: p.confidence, reverse=True)
    return proposals


def _text_contains_rule(haystack: str, rule_lower: str) -> bool:
    """Check if a rule's key phrases already appear in a text body."""
    if not haystack:
        return False
    haystack_lower = haystack.lower()
    # Extract key phrases (5+ word sequences) from the rule
    words = rule_lower.split()
    for i in range(len(words) - 4):
        phrase = " ".join(words[i:i + 5])
        if phrase in haystack_lower:
            return True
    return False


# ── Stage 3: Write ──

MAX_ACTIVE_RULES = 10

def write_approved_rules(
    rules: list[ProposedRule],
    steering_path: Path,
) -> int:
    """Append approved rules to STEERING.md Standing Rules section.

    Returns count of rules written.
    """
    if not rules:
        return 0

    # Read existing content
    if steering_path.exists():
        content = steering_path.read_text(encoding="utf-8")
    else:
        content = "## Standing Rules\n\n"

    # Count existing steeringify rules (have Source: C prefix)
    existing_count = content.lower().count("> source: c")
    remaining_slots = MAX_ACTIVE_RULES - existing_count

    if remaining_slots <= 0:
        return 0

    rules_to_write = rules[:remaining_slots]

    # Build new rules text
    new_rules = []
    for r in rules_to_write:
        sources = ", ".join(r.source_ids)
        today = date.today().isoformat()
        block = (
            f"\n### {r.title}\n"
            f"> Source: {sources} | Added: {today} | "
            f"Confidence: {r.confidence:.2f}\n\n"
            f"{r.rule_text}\n"
        )
        new_rules.append(block)

    # Find insertion point: end of Standing Rules section
    insertion = _find_standing_rules_end(content)
    if insertion >= 0:
        new_content = content[:insertion] + "\n".join(new_rules) + "\n" + content[insertion:]
    else:
        # No Standing Rules section — append at end
        new_content = content.rstrip() + "\n\n## Standing Rules\n" + "\n".join(new_rules) + "\n"

    steering_path.write_text(new_content, encoding="utf-8")
    return len(rules_to_write)


def _find_standing_rules_end(content: str) -> int:
    """Find the end of the Standing Rules section in STEERING.md.

    Returns character index of insertion point, or -1 if section not found.
    """
    lines = content.split("\n")
    in_standing = False
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("## standing rules"):
            in_standing = True
            continue
        if in_standing and line.strip().startswith("## ") and "standing" not in line.lower():
            # Start of next section — insert before it
            return sum(len(l) + 1 for l in lines[:i])

    if in_standing:
        # Standing Rules is the last section — append at end
        return len(content)
    return -1
