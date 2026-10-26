"""Governance miner — turns mature GC candidates into proposals, and nothing else.

Reads the Governance Candidates table in EVOLUTION.md:

    | ID | Proposed Rule | Evidence | Count | Since | Checked |

A row becomes a proposal only when ALL of these hold (宁缺毋滥 — an empty or
unchecked proposal is worse than none; docs/design/2026-10-26-evolution-loop-admission.md §3.2):

  - Count reaches the threshold ("3/3"),
  - the Checked cell says what the rule was checked against and why it is new
    ("checked against: R27, P1 — new because …"). The agent writes it when it
    bumps the count; it has every governance file in its prompt, so the semantic
    check happens there. The miner only tests that the cell is filled,
  - the row carries no verdict yet (✅ PROMOTED / ✖ REJECTED in the Count cell).

``mark_gc_decision`` writes the human's verdict back into the row, so a decided
candidate is never mined again.

SAFETY INVARIANT: this module NEVER writes SOUL/AGENT/STEERING. It produces
proposal data, and edits only the Count cell of one GC row in EVOLUTION.md.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROMOTED = "✅ PROMOTED"
REJECTED = "✖ REJECTED"

# A Checked cell that is present but says nothing is treated as empty.
_EMPTY_CHECKED = {"", "-", "—", "–", "n/a", "na", "none", "tbd"}

_GC_ROW = re.compile(r"^\|\s*(GC\d+)\s*\|", re.MULTILINE)


@dataclass
class GCCandidate:
    """A parsed row of the Governance Candidates table."""

    gc_id: str
    proposed_rule: str
    evidence: list[str]
    evidence_count: int
    since: str = ""
    checked: str = ""


@dataclass
class GovernanceProposal:
    """A governance proposal — the miner's only output."""

    gc_id: str
    proposed_rule: str
    evidence: list[str] = field(default_factory=list)
    occurrence_count: int = 0
    checked: str = ""

    @property
    def title(self) -> str:
        """First sentence of the rule, for a card title."""
        text = self.proposed_rule.strip()
        for sep in ("。", ". ", " — ", "\n"):
            if sep in text:
                text = text.split(sep, 1)[0]
        return text[:120]

    def to_proposal_dict(self) -> dict:
        """Serialize to the format expected by .evolution_proposals.json."""
        return {
            "target": "governance",
            "gc_id": self.gc_id,
            "title": self.title,
            "proposed_rule": self.proposed_rule,
            "evidence": self.evidence,
            "occurrence_count": self.occurrence_count,
            "checked": self.checked,
        }


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def mine_gc_candidates(evolution_md: Path) -> list[GCCandidate]:
    """Parse every GC row that has no verdict yet (any count)."""
    if not evolution_md.exists():
        return []
    results: list[GCCandidate] = []
    for line in evolution_md.read_text(encoding="utf-8").splitlines():
        if not _GC_ROW.match(line):
            continue
        cells = _cells(line)
        if len(cells) < 5:
            continue
        gc_id, rule, evidence_str, count_str, since = cells[:5]
        checked = cells[5] if len(cells) > 5 else ""
        # A verdict (✅ PROMOTED / ✖ REJECTED) replaces the "N/M" count, so a
        # decided row fails this match and is never mined again.
        count_match = re.match(r"(\d+)/\d+", count_str)
        if not count_match:
            continue
        results.append(
            GCCandidate(
                gc_id=gc_id,
                proposed_rule=rule.strip('"'),
                evidence=[e.strip() for e in evidence_str.split(",") if e.strip()],
                evidence_count=int(count_match.group(1)),
                since=since,
                checked=checked,
            )
        )
    return results


def generate_governance_proposals(evolution_md: Path, threshold: int = 3) -> list[GovernanceProposal]:
    """Proposals for GC rows at the threshold with a filled Checked cell.

    SAFETY: never writes any file. Returns data only.
    """
    return [
        GovernanceProposal(
            gc_id=gc.gc_id,
            proposed_rule=gc.proposed_rule,
            evidence=gc.evidence[:5],
            occurrence_count=gc.evidence_count,
            checked=gc.checked,
        )
        for gc in mine_gc_candidates(evolution_md)
        if gc.evidence_count >= threshold and gc.checked.lower() not in _EMPTY_CHECKED
    ]


def mark_gc_decision(evolution_md: Path, gc_id: str, accepted: bool, today: date | None = None) -> bool:
    """Write the human's verdict into the GC row's Count cell, under EVOLUTION.md's lock.

    Returns True when the row was found and rewritten. Only that one cell of that
    one row changes; every other byte of the file is kept.
    """
    if not evolution_md.exists():
        return False
    from utils.file_lock import flock_exclusive, flock_unlock

    verdict = f"{PROMOTED if accepted else REJECTED} {(today or date.today()).isoformat()}"
    lock_path = evolution_md.with_suffix(evolution_md.suffix + ".lock")
    with open(lock_path, "w") as fd:
        flock_exclusive(fd)
        try:
            text = evolution_md.read_text(encoding="utf-8")
            lines = text.split("\n")
            changed = False
            for i, line in enumerate(lines):
                m = _GC_ROW.match(line)
                if not m or m.group(1) != gc_id:
                    continue
                parts = line.split("|")
                # parts: ['', ' GC14 ', rule, evidence, count, since, ...]
                if len(parts) < 6:
                    return False
                parts[4] = f" {verdict} "
                lines[i] = "|".join(parts)
                changed = True
                break
            if not changed:
                return False
            tmp = tempfile.NamedTemporaryFile(
                "w", dir=evolution_md.parent, suffix=".tmp", delete=False, encoding="utf-8"
            )
            try:
                tmp.write("\n".join(lines))
                tmp.close()
                os.replace(tmp.name, evolution_md)
            except Exception:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                raise
            return True
        finally:
            flock_unlock(fd)
