"""Governance pattern miner — L1 evolution that detects recurring judgment failures.

Parses EVOLUTION.md correction classes and Governance Candidates table to identify
patterns meeting the promotion threshold (3+ occurrences). Generates proposals
that surface in session briefing via .evolution_proposals.json.

SAFETY INVARIANT: This module NEVER writes to SOUL/AGENT/STEERING files.
It only produces proposal data structures. Human gate is non-negotiable.

Key public symbols:
    mine_correction_classes — parse CLASS sections from EVOLUTION.md
    mine_gc_candidates — parse GC table for mature candidates
    generate_governance_proposals — full pipeline: mine + filter + propose
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CorrectionClass:
    """A parsed correction class from EVOLUTION.md."""

    name: str
    occurrence_count: int
    pattern: str = ""
    structural_fix: str = ""
    evidence_chain: list[str] = field(default_factory=list)


@dataclass
class GCCandidate:
    """A parsed Governance Candidate from the GC table."""

    gc_id: str
    proposed_rule: str
    evidence: list[str]
    evidence_count: int
    since: str = ""


@dataclass
class GovernanceProposal:
    """A governance rule proposal — output of the mining pipeline."""

    target: str = "governance"
    source_class: str = ""
    gc_id: str = ""
    occurrence_count: int = 0
    proposed_rule: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    # v3 Phase 2: distinguishes a rule proposal from a gate proposal so the
    # rule->gate escalation for the SAME class does not dedup-collide (the
    # dedup identity must include kind — see evolution_optimizer dedup).
    proposal_kind: str = "rule"

    def to_proposal_dict(self) -> dict:
        """Serialize to the format expected by .evolution_proposals.json."""
        return {
            "target": self.target,
            "source_class": self.source_class,
            "gc_id": self.gc_id,
            "occurrence_count": self.occurrence_count,
            "proposed_rule": self.proposed_rule,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "proposal_kind": self.proposal_kind,
        }


# --- Mining Functions ---


def mine_correction_classes(evolution_md: Path) -> list[CorrectionClass]:
    """Parse EVOLUTION.md for correction classes with occurrence counts.

    Looks for headers like:
        ### CLASS A: Confidence -> Skip Process [12 occurrences, ...]
    Extracts the class name, count, pattern description, and structural fix.
    Skips 'Standalone' and 'Resolved' sections.
    """
    if not evolution_md.exists():
        return []

    text = evolution_md.read_text(encoding="utf-8")
    results: list[CorrectionClass] = []

    # Match CLASS headers: ### CLASS X: Description [N occurrences/entries...]
    class_pattern = re.compile(
        r"^###\s+(CLASS\s+\w+:\s*[^\[]+)\[(\d+)\s+(?:occurrences|entries)",
        re.MULTILINE,
    )

    for match in class_pattern.finditer(text):
        name = match.group(1).strip()
        count = int(match.group(2))

        # Skip standalone/resolved
        if "standalone" in name.lower() or "resolved" in name.lower():
            continue

        # Extract the section content (until next ### or ## header)
        section_start = match.end()
        next_header = re.search(r"^#{2,3}\s+", text[section_start:], re.MULTILINE)
        section_end = section_start + next_header.start() if next_header else len(text)
        section_text = text[section_start:section_end]

        # Extract pattern (line starting with **Pattern**:)
        pattern_match = re.search(
            r"\*\*Pattern\*\*:\s*(.+?)(?:\n|$)", section_text
        )
        pattern_str = pattern_match.group(1).strip().strip('"') if pattern_match else ""

        # Extract structural fix
        fix_match = re.search(
            r"\*\*Structural fix\*\*:\s*(.+?)(?:\n|$)", section_text
        )
        fix_str = fix_match.group(1).strip() if fix_match else ""

        # Extract chain entries as evidence
        chain_match = re.search(r"\*\*Chain\*\*:\s*(.+?)(?:\n|$)", section_text)
        evidence = []
        if chain_match:
            evidence = [e.strip() for e in chain_match.group(1).split("→")]
        else:
            # Fallback: extract individual correction IDs (e.g. **C034** (05-29))
            correction_ids = re.findall(r"\*\*(C\d+)\*\*\s*\([^)]+\)", section_text)
            evidence = correction_ids

        results.append(
            CorrectionClass(
                name=name,
                occurrence_count=count,
                pattern=pattern_str,
                structural_fix=fix_str,
                evidence_chain=evidence,
            )
        )

    return results


def mine_gc_candidates(evolution_md: Path) -> list[GCCandidate]:
    """Parse GC table for candidates with evidence meeting promotion threshold.

    Filters:
    - Skips already PROMOTED entries (✅ PROMOTED in Count column)
    - Only returns candidates with evidence_count >= 3
    """
    if not evolution_md.exists():
        return []

    text = evolution_md.read_text(encoding="utf-8")
    results: list[GCCandidate] = []

    # Find the GC table rows: | GC05 | "rule text" | evidence | 1/3 | date |
    # Also matches: | GC12 | "rule text" | evidence | ✅ PROMOTED | date |
    row_pattern = re.compile(
        r"^\|\s*(GC\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )

    for match in row_pattern.finditer(text):
        gc_id = match.group(1).strip()
        rule = match.group(2).strip().strip('"')
        evidence_str = match.group(3).strip()
        count_str = match.group(4).strip()
        since = match.group(5).strip()

        # Skip promoted
        if "PROMOTED" in count_str.upper():
            continue

        # Parse evidence count: "1/3" → 1, "3/3" → 3
        count_match = re.match(r"(\d+)/\d+", count_str)
        if not count_match:
            continue
        evidence_count = int(count_match.group(1))

        # Only include if meets threshold (3+)
        if evidence_count < 3:
            continue

        evidence_list = [e.strip() for e in evidence_str.split(",")]

        results.append(
            GCCandidate(
                gc_id=gc_id,
                proposed_rule=rule,
                evidence=evidence_list,
                evidence_count=evidence_count,
                since=since,
            )
        )

    return results


def generate_governance_proposals(
    evolution_md: Path,
    steering_md: Path,
    threshold: int = 3,
) -> list[GovernanceProposal]:
    """Generate governance proposals from mature patterns.

    Pipeline:
    1. Mine correction classes from EVOLUTION.md
    2. Mine GC candidates from EVOLUTION.md
    3. Filter out classes that already have STEERING/AGENT rules
    4. Generate proposals for qualifying patterns

    SAFETY: Never writes to any file. Returns data only.
    """
    proposals: list[GovernanceProposal] = []

    # Read steering to check for existing rules
    steering_text = ""
    if steering_md.exists():
        steering_text = steering_md.read_text(encoding="utf-8").lower()

    # --- Mine correction classes ---
    classes = mine_correction_classes(evolution_md)
    for cls in classes:
        if cls.occurrence_count < threshold:
            continue

        # Check if already ruled: look for STEERING reference in structural_fix
        if _has_existing_rule(cls, steering_text):
            continue

        # Calculate confidence based on occurrence count and evidence
        confidence = min(0.9, 0.5 + (cls.occurrence_count - threshold) * 0.05)

        # Canonical key so a miner proposal ("CLASS A: ...") and a tracker-ladder
        # proposal ("CLASS_A") for the SAME logical class dedup together, not twice
        # (adversarial HIGH). The human-readable name is preserved in proposed_rule.
        from core.evolution.class_key import canonical_class_key, is_cognitive_class

        ckey = canonical_class_key(cls.name)
        # Defense-in-depth axis guard (run_685db747 Gate-2 MED): today the header
        # regex only matches "CLASS X" (all cognitive by construction), so this is
        # unreachable — but if that regex is ever loosened, a non-cognitive class
        # must NOT reach the governance queue. Mirrors escalation_ladder:111 and the
        # eval_service consumer guard so all three layers agree.
        if not is_cognitive_class(ckey):
            continue

        # Admission root-fix (run_97519f7c): a class with NO real rule text (empty
        # pattern) must NOT surface a contentless "Address recurring X pattern"
        # meta-instruction — that is not an approvable rule, just queue noise a human
        # can't action. Skip it; it re-surfaces only once a real structural_fix/pattern
        # is extracted. (The escalation ladder handles bare recurrence separately.)
        if not (cls.pattern or "").strip():
            continue

        proposals.append(
            GovernanceProposal(
                target="governance",
                source_class=ckey,
                occurrence_count=cls.occurrence_count,
                proposed_rule=cls.pattern,
                evidence=cls.evidence_chain[:5],  # Cap at 5
                confidence=confidence,
            )
        )

    # --- Mine GC candidates ---
    gc_candidates = mine_gc_candidates(evolution_md)
    for gc in gc_candidates:
        if gc.evidence_count < threshold:
            continue

        confidence = min(0.85, 0.6 + (gc.evidence_count - threshold) * 0.1)

        proposals.append(
            GovernanceProposal(
                target="governance",
                gc_id=gc.gc_id,
                occurrence_count=gc.evidence_count,
                proposed_rule=gc.proposed_rule,
                evidence=gc.evidence,
                confidence=confidence,
            )
        )

    return proposals


def _has_existing_rule(cls: CorrectionClass, steering_text: str) -> bool:
    """Check if a correction class already has a STEERING/AGENT rule.

    Two checks:
    1. structural_fix field mentions "STEERING R" or "STEERING #" → already ruled
    2. steering_text contains key phrases from the class pattern → already addressed
    """
    fix_lower = cls.structural_fix.lower()

    # Direct mentions of existing rules in the structural_fix field
    if re.search(r"steering\s+r\d+", fix_lower):
        return True
    if re.search(r"steering\s+#\d+", fix_lower):
        return True

    # Check if STEERING.md already addresses this pattern's core concept
    # Extract the key phrase after the colon (e.g., "Confidence → Skip Process")
    if cls.name and ":" in cls.name:
        pattern_key = cls.name.split(":", 1)[-1].strip().lower()[:40]
        # Only match if substantial phrase (>10 chars to avoid false positives)
        if len(pattern_key) > 10 and pattern_key in steering_text:
            return True

    return False
