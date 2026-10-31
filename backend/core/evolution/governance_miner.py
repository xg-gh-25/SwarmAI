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

import re
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
        if not _pending(cells, threshold):
            continue
        gc_id, rule, evidence_str, count_str = cells[:4]
        out.append(GovernanceProposal(
            gc_id=gc_id,
            proposed_rule=rule.strip('"'),
            evidence=[e.strip() for e in evidence_str.split(",") if e.strip()][:5],
            occurrence_count=int(_COUNT.match(count_str).group(1)),
            checked=cells[5] if len(cells) > 5 else "",
        ))
    return out


class GovernanceWriteRefused(Exception):
    """The governed edit path refused a verdict; the message says why."""


def _pending(cells: list[str], threshold: int) -> bool:
    """Whether a GC row's cells make it a pending proposal: an undecided N/M count at
    the threshold and a Checked cell that says something."""
    if len(cells) < 5:
        return False
    m = _COUNT.match(cells[3])               # a verdict does not parse as N/M
    checked = cells[5] if len(cells) > 5 else ""
    return bool(m) and int(m.group(1)) >= threshold and checked.lower() not in _EMPTY_CHECKED


def mark_gc_decision(evolution_md: Path, gc_id: str, accepted: bool, today: date | None = None,
                     threshold: int = 3) -> bool:
    """Write the human's verdict into the Count cell of every UNDECIDED row with this id.

    EVOLUTION.md is a governed file: the edit goes through the governed path
    (``apply_user_edit``: archived first, optimistic on the file's revision, the
    baseline revised with it), so CDF never sees it as a change made outside it. Only a
    row that is a pending proposal (the filter ``generate_governance_proposals`` uses)
    is marked, so a decision on one id never gives a verdict to a row nobody was shown.
    The edit changes those Count cells; like every edit of a governed file, the path
    also redacts a secret or strips an invisible character found elsewhere in the file.

    Returns True when a row was marked; False when none was, or when the file changed
    after it was read (nothing is written then). Raises ``GovernanceWriteRefused`` with
    the reason when the governed path refuses the edit (e.g. the row quotes a phrase
    the memory guard blocks).
    """
    if not evolution_md.is_file():
        return False
    import hashlib

    from core.ingestion.governed_edit import StaleRevision, apply_user_edit
    from core.memory_guard import MemoryGuardError

    verdict = f"{ACCEPTED if accepted else REJECTED} {(today or date.today()).isoformat()}"
    data = evolution_md.read_bytes()
    revision = hashlib.sha256(data).hexdigest()    # the revision of the bytes parsed here
    lines = data.decode("utf-8").split("\n")
    changed = False
    for i in _section_lines(lines):
        m = _GC_ROW.match(lines[i])
        if not m or m.group(1) != gc_id or not _pending(_cells(lines[i]), threshold):
            continue
        spans = [s.start() for s in _CELL_SPLIT.finditer(lines[i])]
        if len(spans) < 6:
            continue
        a, b = spans[3], spans[4]           # the Count cell lies between pipes 4 and 5
        lines[i] = f"{lines[i][:a + 1]} {verdict} {lines[i][b:]}"
        changed = True
    if not changed:
        return False
    try:
        apply_user_edit(evolution_md, "\n".join(lines).encode("utf-8"),
                        expected_revision=revision, actor="user:governance")
    except StaleRevision:
        return False
    except MemoryGuardError as exc:
        raise GovernanceWriteRefused(
            f"{gc_id} holds a phrase the memory guard blocks ({exc}); edit the row's text "
            "in EVOLUTION.md, then decide again") from exc
    except (ValueError, PermissionError, RuntimeError) as exc:
        raise GovernanceWriteRefused(f"{gc_id} could not be written: {exc}") from exc
    return True
