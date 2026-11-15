"""Governance proposals — read straight from the GC table, decided in place.

The Governance Candidates table in EVOLUTION.md is the ONLY store of governance
proposals:

    ## Governance Candidates
    | ID | Proposed Rule | Evidence | Count | Since | Checked |

A row is a pending proposal while ALL of these hold (宁缺毋滥 — an empty or
unchecked proposal is worse than none; docs/design/2026-10-26-evolution-loop-admission.md §3.2):

  - its count is at the threshold (3). For a row whose Evidence cell lists correction
    anchors (``<path>#<section>#<identity>``, e.g. ``.context/EVOLUTION.md#Corrections
    Captured#cand:cand-….unit``) the count is COMPUTED here, from the record (§9.1): the
    distinct sessions among the anchored corrections themselves and the corrections the
    after-session review recorded as repeating one of them (``queries.repeats_of``). One
    session counts once, so a mistake recorded twice in one conversation is one
    occurrence. A row without anchors, or when the CDF store cannot be read, keeps the
    hand-written N/M of its Count cell (no migration);
  - its Checked cell says what the rule was checked against and why it is new
    ("checked against: R27, P1 — new because …"). The agent writes it with the
    anchors; it has every governance file in its prompt, so the semantic check
    happens there. This module only tests that the cell is filled.

``accepted_outcomes`` reads whether an accepted rule worked: for each ``✔ ACCEPTED
<date>`` row with anchors, the repeats of its corrections in the time since acceptance
and in an equally long time before it.

``mark_gc_decision`` replaces the Count cell with the human's verdict
(``✔ ACCEPTED <date>`` / ``✖ REJECTED <date>``); a verdict no longer parses as
N/M, so the row is never proposed again. There is no second copy of a proposal
to drift, race or go stale — readers derive the list from the table every time.

SAFETY INVARIANT: this module NEVER writes SOUL/AGENT/STEERING. It edits only the
Count cell of undecided GC rows in EVOLUTION.md; it opens the CDF store read-only.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

ACCEPTED = "✔ ACCEPTED"
REJECTED = "✖ REJECTED"

_SECTION = "## Governance Candidates"
# A Checked cell that is present but says nothing is treated as empty.
_EMPTY_CHECKED = {"", "-", "—", "–", "n/a", "na", "none", "tbd"}
_GC_ROW = re.compile(r"^\|\s*(GC\d+)\s*\|")
_COUNT = re.compile(r"^\s*(\d+)/(\d+)")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")   # a markdown "\|" stays inside its cell
# a correction anchor as relations_v2 writes it: <path relative to the workspace>#<section>#<identity>
_ANCHOR = re.compile(r"^[^\s#]+\.md#[^#]+#\S+$")
_CAND = re.compile(r"#cand:(cand-[0-9a-f]+)\.unit$")
_ACCEPTED_ON = re.compile(r"^\s*✔ ACCEPTED (\d{4}-\d{2}-\d{2})")
_DECIDED = re.compile(r"ACCEPTED|REJECTED|PROMOTED")
DEFAULT_TARGET = 3


@dataclass(frozen=True)
class RepeatRecords:
    """What the record says about a set of anchors: the corrections recorded as repeating
    one (``queries.repeats_of`` rows) and the session of each anchored correction itself
    (an anchor whose candidate is not in the store is absent)."""

    repeats: list[dict]
    origins: dict[str, str]


RecordsLoader = Callable[[set], "RepeatRecords | None"]


def load_repeat_records(anchors: set) -> RepeatRecords | None:
    """The record for *anchors*, read-only; None when the CDF store cannot be read."""
    from core.ingestion.api import connect_ro
    from core.ingestion.queries import candidate_sessions, repeats_of

    cands = {a: m.group(1) for a in anchors if (m := _CAND.search(a))}
    try:
        with closing(connect_ro()) as conn:
            repeats = repeats_of(conn, anchors)
            sessions = candidate_sessions(conn, cands.values())
    except (OSError, sqlite3.Error) as exc:
        logger.info("governance miner: no correction record (%s); hand-written counts stay", exc)
        return None
    return RepeatRecords(repeats, {a: sessions[c] for a, c in cands.items() if c in sessions})


def workspace_gc_table() -> Path:
    """This workspace's EVOLUTION.md, the file holding the GC table."""
    from config import get_swarmws
    return Path(get_swarmws()) / ".context" / "EVOLUTION.md"


def _evidence(cell: str) -> list[str]:
    return [e.strip() for e in cell.split(",") if e.strip()]


def anchors_of(cell: str) -> list[str]:
    """The correction anchors an Evidence cell lists (free-text evidence is not one)."""
    return [e for e in _evidence(cell) if _ANCHOR.match(e)]


def _occurrence(r: dict) -> tuple[str, str]:
    return ("session", r["session_id"]) if r.get("session_id") else ("unit", str(r.get("id")))


def computed_count(anchors: list[str], records: RepeatRecords) -> int:
    """The occurrences of the mistake the anchors name: the distinct sessions among the
    anchored corrections and the corrections recorded as repeating one of them. An anchor
    whose session is unknown counts once on its own; a repeat without a session once by
    its unit."""
    wanted = set(anchors)
    keys = {("session", records.origins[a]) if a in records.origins else ("anchor", a) for a in wanted}
    keys |= {_occurrence(r) for r in records.repeats if r.get("repeats_of") in wanted}
    return len(keys)


def _row_count(cells: list[str], records: RepeatRecords | None) -> tuple[int, int, str] | None:
    """``(count, target, "computed" | "hand")`` of an undecided row; None for a decided row
    or a hand-counted row whose Count cell is not N/M."""
    if len(cells) < 4 or _DECIDED.search(cells[3]):
        return None
    m = _COUNT.match(cells[3])
    target = int(m.group(2)) if m else DEFAULT_TARGET
    anchors = anchors_of(cells[2])
    if anchors and records is not None:
        return computed_count(anchors, records), target, "computed"
    return (int(m.group(1)), target, "hand") if m else None


def _rule_title(rule: str) -> str:
    text = rule.strip()
    for sep in ("。", ". ", " — ", "\n"):
        if sep in text:
            text = text.split(sep, 1)[0]
    return text[:120]


@dataclass
class GovernanceProposal:
    """A pending GC row, in the shape the dashboard / Need-You readers expect."""

    gc_id: str
    proposed_rule: str
    evidence: list[str] = field(default_factory=list)
    occurrence_count: int = 0
    checked: str = ""
    target: int = DEFAULT_TARGET
    count_source: str = "hand"      # "computed" from the record, or the row's hand-written N/M

    @property
    def title(self) -> str:
        """First sentence of the rule, for a card title."""
        return _rule_title(self.proposed_rule)

    def to_proposal_dict(self) -> dict:
        return {
            "target": "governance",
            "id": self.gc_id,
            "gc_id": self.gc_id,
            "title": self.title,
            "proposed_rule": self.proposed_rule,
            "evidence": self.evidence,
            "occurrence_count": self.occurrence_count,
            "target": self.target,
            "count_source": self.count_source,
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


def _records_for(lines: list[str], rows: range, loader: RecordsLoader, gc_id: str | None = None
                 ) -> RepeatRecords | None:
    """The record for the anchors of the section's undecided rows (of *gc_id* only, when
    given); None without anchors (nothing to read) or without a readable store."""
    anchors: set = set()
    for i in rows:
        m = _GC_ROW.match(lines[i])
        if m and (gc_id is None or m.group(1) == gc_id):
            cells = _cells(lines[i])
            if len(cells) >= 4 and not _DECIDED.search(cells[3]):
                anchors.update(anchors_of(cells[2]))
    return loader(anchors) if anchors else None


def generate_governance_proposals(evolution_md: Path, threshold: int = 3,
                                  records_loader: RecordsLoader = load_repeat_records
                                  ) -> list[GovernanceProposal]:
    """The pending proposals: undecided GC rows at the threshold with Checked filled; an
    anchored row's count computed from the record (``_row_count``)."""
    if not evolution_md.exists():
        return []
    lines = evolution_md.read_text(encoding="utf-8").split("\n")
    rows = _section_lines(lines)
    records = _records_for(lines, rows, records_loader)
    out: list[GovernanceProposal] = []
    for i in rows:
        if not _GC_ROW.match(lines[i]):
            continue
        cells = _cells(lines[i])
        if not _pending(cells, threshold, records):
            continue
        gc_id, rule, evidence_str = cells[:3]
        count, target, source = _row_count(cells, records)
        out.append(GovernanceProposal(
            gc_id=gc_id,
            proposed_rule=rule.strip('"'),
            evidence=_evidence(evidence_str)[:5],
            occurrence_count=count,
            checked=cells[5] if len(cells) > 5 else "",
            target=target,
            count_source=source,
        ))
    return out


def accepted_outcomes(evolution_md: Path, now: datetime | None = None,
                      records_loader: RecordsLoader = load_repeat_records) -> list[dict]:
    """Whether each accepted rule stopped its mistake recurring: for every ``✔ ACCEPTED
    <date>`` row, ``{gc_id, title, accepted_on, anchors, measured, days, before, after,
    reason}``. ``after`` counts the occurrences (distinct sessions) of corrections recorded
    as repeating one of the row's anchors from the start of the acceptance day (local) to
    *now*; ``before`` the same over an equally long time just before it, so the two are
    comparable; ``days`` that length. A row without anchors, or without a readable record,
    is listed with ``measured: False`` and the reason."""
    if not evolution_md.exists():
        return []
    now = now or datetime.now(timezone.utc)
    lines = evolution_md.read_text(encoding="utf-8").split("\n")
    accepted = []
    for i in _section_lines(lines):
        if not _GC_ROW.match(lines[i]):
            continue
        cells = _cells(lines[i])
        m = _ACCEPTED_ON.match(cells[3]) if len(cells) >= 4 else None
        if m:
            accepted.append((cells, date.fromisoformat(m.group(1)), anchors_of(cells[2])))
    wanted = {a for _c, _d, anchors in accepted for a in anchors}
    records = records_loader(wanted) if wanted else None
    out = []
    for cells, on, anchors in accepted:
        entry = {"gc_id": cells[0], "title": _rule_title(cells[1].strip('"')), "accepted_on": on.isoformat(),
                 "anchors": len(anchors), "measured": False, "days": None, "before": None, "after": None,
                 "reason": None}
        if not anchors:
            entry["reason"] = "no correction anchors in Evidence"
        elif records is None:
            entry["reason"] = "no correction record"
        else:
            start = datetime.combine(on, time()).astimezone()
            span = max(now - start, timedelta(0))
            reps = [r for r in records.repeats if r.get("repeats_of") in set(anchors)]
            entry.update(measured=True, days=span.days,
                         before=len({_occurrence(r) for r in reps if start - span <= r["at"] < start}),
                         after=len({_occurrence(r) for r in reps if start <= r["at"] < now}))
        out.append(entry)
    return out


class GovernanceWriteRefused(Exception):
    """The governed edit path refused a verdict; the message says why."""


def _pending(cells: list[str], threshold: int, records: RepeatRecords | None = None) -> bool:
    """Whether a GC row's cells make it a pending proposal: an undecided count (computed
    for an anchored row, hand-written N/M otherwise) at the threshold and a Checked cell
    that says something."""
    if len(cells) < 5:
        return False
    counted = _row_count(cells, records)     # a verdict has no count
    checked = cells[5] if len(cells) > 5 else ""
    return counted is not None and counted[0] >= threshold and checked.lower() not in _EMPTY_CHECKED


def mark_gc_decision(evolution_md: Path, gc_id: str, accepted: bool, today: date | None = None,
                     threshold: int = 3, records_loader: RecordsLoader = load_repeat_records) -> bool:
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
    rows = _section_lines(lines)
    records = _records_for(lines, rows, records_loader, gc_id)
    changed = False
    for i in rows:
        m = _GC_ROW.match(lines[i])
        if not m or m.group(1) != gc_id or not _pending(_cells(lines[i]), threshold, records):
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
