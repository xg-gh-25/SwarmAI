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


def mark_gc_decision(evolution_md: Path, gc_id: str, accepted: bool, today: date | None = None) -> bool:
    """Write the verdict into the Count cell of every UNDECIDED row with this id.

    Runs under EVOLUTION.md's lock (shared with locked_write and the maintenance
    hook). A row that already carries a verdict is left alone. Line endings, the
    trailing newline and the file mode are preserved; nothing but those cells
    changes. Returns True when at least one row was marked.
    """
    if not evolution_md.exists():
        return False
    from utils.file_lock import flock_exclusive, flock_unlock

    verdict = f"{ACCEPTED if accepted else REJECTED} {(today or date.today()).isoformat()}"
    lock_path = evolution_md.with_suffix(evolution_md.suffix + ".lock")
    with open(lock_path, "w") as fd:
        flock_exclusive(fd)
        try:
            with open(evolution_md, encoding="utf-8", newline="") as f:
                text = f.read()
            lines = text.split("\n")
            changed = False
            for i in _section_lines(lines):
                m = _GC_ROW.match(lines[i])
                if not m or m.group(1) != gc_id:
                    continue
                spans = [s.start() for s in _CELL_SPLIT.finditer(lines[i])]
                if len(spans) < 6:
                    continue
                a, b = spans[3], spans[4]           # the Count cell lies between pipes 4 and 5
                if not _COUNT.match(lines[i][a + 1:b]):
                    continue                         # already decided, or not a count
                lines[i] = f"{lines[i][:a + 1]} {verdict} {lines[i][b:]}"
                changed = True
            if not changed:
                return False
            tmp = tempfile.NamedTemporaryFile(
                "w", dir=evolution_md.parent, suffix=".tmp", delete=False,
                encoding="utf-8", newline="",
            )
            try:
                tmp.write("\n".join(lines))
                tmp.close()
                shutil.copymode(evolution_md, tmp.name)
                os.replace(tmp.name, evolution_md)
            except Exception:
                tmp.close()
                Path(tmp.name).unlink(missing_ok=True)
                raise
            return True
        finally:
            flock_unlock(fd)
