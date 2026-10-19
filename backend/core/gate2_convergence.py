"""Gate-2 convergence: is the review loop done, and what happens next (pure functions).

再一再二不再三, fully autonomous (docs/design/pipeline-review-convergence.md §3,
docs/design/pipeline-review-n3-implementation.md). The verdict is computed from what
the reviewers SAID — the SubagentStop marker rows the hook wrote (``reported``,
``reported_state``, ``recheck_answers``, ``verdict``) — never from the orchestrator's
own record of the findings.

``outcome(rows_by_expert, root_rows, redesigns_used)`` returns ``action``:

- ``none``       no Gate-2 expert has answered in this cycle yet
- ``converged``  every expert's latest round has nothing open
- ``recheck``    something is open and every expert with open items has rounds left
- ``root_cause`` an expert with open items used all MAX_ROUNDS and no root-cause
                 verdict exists yet: hand out ONE fresh-context root-cause review
- ``sound``      the root-cause review said the approach is right (or a second miss
                 after a redesign): stop fixing; open items go to REPORT §10; the code
                 commits locally with push_ready false
- ``redesign``   the root-cause review said WRONG-APPROACH and this run has not been
                 redesigned yet: back to PLAN once

An expert's latest round is "open" when it answered ``NOT RESOLVED`` to a re-check
item, reported a blocking finding (``core.gate2_findings.is_blocking``), or its answer
could not be parsed (``reported_state == "unparsed"``: fail closed; the round cap
bounds the cost). A row from a daemon that does not record answers (no
``reported_state`` key) is not judged.

Public symbols: ``MAX_ROUNDS``, ``ROOT_CAUSE_ID``, ``NOT_REVIEWERS``, ``is_blocking``,
``open_items``, ``outcome``.
"""
from __future__ import annotations

from typing import Any, Optional

MAX_ROUNDS = 3
ROOT_CAUSE_ID = "root-cause"
# Spawns that are not Gate-2 review rounds of a reviewer.
NOT_REVIEWERS = frozenset({ROOT_CAUSE_ID, "meta-review", "gate0-skeptic", "gate1-skeptic"})
SOUND, WRONG = "SOUND", "WRONG-APPROACH"


def is_blocking(f: Any) -> bool:
    """The one Gate-2 blocking rule (core.gate2_findings once it lands; the same rule
    inline until then: HIGH/CRITICAL/BLOCK/BLOCKER always, MED/MEDIUM at confidence
    >= 7 or missing)."""
    if not isinstance(f, dict):
        return False
    try:
        from core.gate2_findings import is_blocking as _ib
        return bool(_ib(f))
    except ImportError:
        pass
    sev = str(f.get("severity") or "").strip().upper()
    if sev in ("HIGH", "CRITICAL", "BLOCK", "BLOCKER"):
        return True
    if sev in ("MED", "MEDIUM"):
        raw = f.get("confidence")
        try:
            return raw is None or float(str(raw).split("/")[0]) >= 7
        except ValueError:
            return True
    return False


def _label(f: dict) -> str:
    where = f.get("file") or f.get("path") or f.get("location") or ""
    line = f.get("line")
    loc = f"{where}:{line}" if where and line else str(where)
    what = str(f.get("finding") or f.get("summary") or f.get("title") or f.get("issue") or "")[:160]
    sev = str(f.get("severity") or "").upper()
    return " — ".join(p for p in (f"[{sev}]" if sev else "", loc, what) if p)


def open_items(row: dict) -> "Optional[list[str]]":
    """What is still open after this round; None when the row cannot be judged."""
    if not isinstance(row, dict) or "reported_state" not in row:
        return None
    out = [f"R{n}: NOT RESOLVED" for n, w in sorted((row.get("recheck_answers") or {}).items())
           if w == "NOT RESOLVED"]
    out += [_label(f) for f in row.get("reported") or [] if is_blocking(f)]
    if row.get("reported_state") == "unparsed" and not out:
        out.append("answer not parsed (no findings JSON, no NO FINDINGS line)")
    return out


def outcome(rows_by_expert: "dict[str, list[dict]]", root_rows: "list[dict]",
            redesigns_used: int, max_rounds: int = MAX_ROUNDS) -> dict:
    """The loop's state for one DELIVER review cycle (see module docstring).
    ``rows_by_expert``: reviewer id → its marker rows in this cycle, oldest first."""
    rounds = {e: len(rs) for e, rs in rows_by_expert.items() if rs and e not in NOT_REVIEWERS}
    open_: dict[str, list[str]] = {}
    unjudged: list[str] = []
    for e in rounds:
        items = open_items(rows_by_expert[e][-1])
        if items is None:
            unjudged.append(e)
        elif items:
            open_[e] = items
    verdict = next((r.get("verdict") for r in reversed(root_rows or [])
                    if isinstance(r, dict) and r.get("verdict") in (SOUND, WRONG)), None)
    verdict_text = next((str(r.get("verdict_text") or "") for r in reversed(root_rows or [])
                         if isinstance(r, dict) and r.get("verdict") in (SOUND, WRONG)), "")
    capped = sorted(e for e in open_ if rounds[e] >= max_rounds)
    if not rounds:
        action = "none"
    elif not open_:
        action = "converged"
    elif not capped:
        action = "recheck"
    elif verdict is None:
        action = "root_cause"
    elif verdict == WRONG and redesigns_used < 1:
        action = "redesign"
    else:
        action = "sound"
    return {"action": action, "converged": action == "converged", "rounds": rounds,
            "open": open_, "capped": capped, "unjudged": sorted(unjudged),
            "root_cause": verdict, "root_cause_text": verdict_text[:1500],
            "redesigns_used": redesigns_used}
