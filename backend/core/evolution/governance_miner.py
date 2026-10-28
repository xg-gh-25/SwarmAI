"""Governance proposals — read straight from the GC table, decided in place.

The Governance Candidates table in EVOLUTION.md is the ONLY store of governance
proposals:

    ## Governance Candidates
    | ID | Proposed Rule | Evidence | Count | Since | Checked |

A row is a pending proposal while ALL of these hold (宁缺毋滥 — an empty or
unchecked proposal is worse than none; docs/design/2026-10-26-evolution-loop-admission.md §3.2):

  - its Count cell reads N/M with N at the threshold ("3/3"),
  - its Checked cell says what the rule was checked against and why it is new
    ("checked against: R27, P1 — new because …"). The agent writes it when it
    bumps the count; it has every governance file in its prompt, so the semantic
    check happens there. This module only tests that the cell is filled.

``mark_gc_decision`` replaces the Count cell with the human's verdict
(``✔ ACCEPTED <date>`` / ``✖ REJECTED <date>``); a verdict no longer parses as
N/M, so the row is never proposed again. There is no second copy of a proposal
to drift, race or go stale — readers derive the list from the table every time.

SAFETY INVARIANT: this module NEVER writes SOUL/AGENT/STEERING. It edits only the
Count cell of undecided GC rows in EVOLUTION.md.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ACCEPTED = "✔ ACCEPTED"
REJECTED = "✖ REJECTED"

_SECTION = "## Governance Candidates"
# A Checked cell that is present but says nothing is treated as empty.
_EMPTY_CHECKED = {"", "-", "—", "–", "n/a", "na", "none", "tbd"}
_GC_ROW = re.compile(r"^\|\s*(GC\d+)\s*\|")
_COUNT = re.compile(r"^\s*(\d+)/\d+")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")   # a markdown "\|" stays inside its cell


@dataclass
class GovernanceProposal:
    """A pending GC row, in the shape the dashboard / Need-You readers expect."""

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
        return {
            "target": "governance",
            "id": self.gc_id,
            "gc_id": self.gc_id,
            "title": self.title,
            "proposed_rule": self.proposed_rule,
            "evidence": self.evidence,
            "occurrence_count": self.occurrence_count,
            "checked": self.checked,
        }


def _section_lines(lines: list[str]) -> range:
    """Index range of the Governance Candidates section (empty when absent)."""
    start = next((i for i, l in enumerate(lines) if l.strip().startswith(_SECTION)), None)
    if start is None:
        return range(0)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return range(start + 1, end)


def _cells(line: str) -> list[str]:
    parts = _CELL_SPLIT.split(line.rstrip("\r"))
    return [c.strip() for c in parts[1:-1]] if len(parts) > 2 else []


def generate_governance_proposals(evolution_md: Path, threshold: int = 3) -> list[GovernanceProposal]:
    """The pending proposals: undecided GC rows at the threshold with Checked filled."""
    if not evolution_md.exists():
        return []
    lines = evolution_md.read_text(encoding="utf-8").split("\n")
    out: list[GovernanceProposal] = []
    for i in _section_lines(lines):
        if not _GC_ROW.match(lines[i]):
            continue
        cells = _cells(lines[i])
        if len(cells) < 5:
            continue
        gc_id, rule, evidence_str, count_str = cells[:4]
        checked = cells[5] if len(cells) > 5 else ""
        m = _COUNT.match(count_str)          # a verdict does not parse as N/M
        if not m or int(m.group(1)) < threshold or checked.lower() in _EMPTY_CHECKED:
            continue
        out.append(GovernanceProposal(
            gc_id=gc_id,
            proposed_rule=rule.strip('"'),
            evidence=[e.strip() for e in evidence_str.split(",") if e.strip()][:5],
            occurrence_count=int(m.group(1)),
            checked=checked,
        ))
    return out


def enqueue_governance_proposals(evolution_md: Path, threshold: int = 3, *,
                                 llm: Any | None = None) -> int:
    """Submit eligible cross-run governance measurements through S5."""
    from core.ingestion.sources.s5_metrics import extract_metrics

    proposals = generate_governance_proposals(evolution_md, threshold)
    if not proposals:
        return 0
    receipts = extract_metrics(
        "governance-candidates",
        {"candidate_count": len(proposals),
         "maximum_occurrences": max(p.occurrence_count for p in proposals),
         "inferred_candidates": [p.to_proposal_dict() for p in proposals]},
        run_count=max(p.occurrence_count for p in proposals),
        llm=llm,
    )
    return len(receipts)


def mark_gc_decision(evolution_md: Path, gc_id: str, accepted: bool, today: date | None = None) -> bool:
    """Retired compatibility API; CDF has no human approval mutation phase."""
    return False
