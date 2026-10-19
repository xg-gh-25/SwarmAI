"""Gate-2 convergence: is the review loop done, and what happens next (pure functions).

再一再二不再三, fully autonomous (docs/design/pipeline-review-convergence.md §3,
docs/design/pipeline-review-n3-implementation.md). The verdict is computed from what
the reviewers SAID — the SubagentStop marker rows the hook wrote (``reported``,
``reported_state``, ``recheck_answers``, ``verdict``) — never from the orchestrator's
own record of the findings.

``outcome(rows_by_expert, root_rows, redesigns_used, early_ts=None)`` returns ``action``:

- ``none``       no Gate-2 expert has answered in this cycle yet
- ``converged``  every expert's latest round has nothing open
- ``recheck``    something is open, rounds are left, and either no root-cause review is
                 due or it answered SOUND before the cap (fix as the spec says)
- ``root_cause`` no verdict yet and either an expert with open items used all
                 MAX_ROUNDS or ``gate2-fixspec`` recorded an early trigger (``early_ts``:
                 the class reopened, core.gate2_fixspec.next_step): hand out ONE
                 fresh-context root-cause review — one per cycle
- ``sound``      the root-cause review has answered and the loop must stop: at the cap,
                 WRONG-APPROACH with no redesign budget, or a trigger recorded after the
                 verdict, or an answer with no verdict line (``UNPARSED``: one root-cause
                 spawn per cycle, so it is never asked again); the verdict, its reasons
                 and every open item go to REPORT §10;
                 the code commits locally with push_ready false (再一再二不再三)
- ``redesign``   only while ``MAX_REDESIGNS`` > 0 (it is 0 since 2026-10-19: a redesign
                 restarted the three rounds, so a run could review six times)

An expert's latest round is "open" when it answered ``NOT RESOLVED`` to a re-check
item, reported a blocking finding (``core.gate2_findings.is_blocking``), or its answer
could not be parsed (``reported_state == "unparsed"``: fail closed; the round cap
bounds the cost). A row from a daemon that does not record answers (no
``reported_state`` key) is not judged.

Public symbols: ``MAX_ROUNDS``, ``ROOT_CAUSE_ID``, ``NOT_REVIEWERS``, ``is_blocking``,
``open_entries``, ``open_items``, ``outcome``.
"""
from __future__ import annotations

from typing import Any, Optional

MAX_ROUNDS = 3
MAX_REDESIGNS = 0  # WRONG-APPROACH after round 3 is recorded, not redesigned
ROOT_CAUSE_ID = "root-cause"
# Spawns that are not Gate-2 review rounds of a reviewer.
NOT_REVIEWERS = frozenset({ROOT_CAUSE_ID, "meta-review", "gate0-skeptic", "gate1-skeptic",
                           "fixspec-skeptic"})  # core.gate2_fixspec.FIXSPEC_SKEPTIC_ID
SOUND, WRONG, UNPARSED = "SOUND", "WRONG-APPROACH", "UNPARSED"


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


def open_entries(row: dict) -> "Optional[list[tuple]]":
    """The ONE definition of what a round left open (``open_items`` and
    core.gate2_fixspec.round_items both read it): ``("recheck", n, None)`` per NOT RESOLVED
    answer, ``("finding", None, f)`` per blocking reported finding, ``("unparsed", None,
    None)`` for an answer that could not be parsed; None when the row cannot be judged."""
    if not isinstance(row, dict) or "reported_state" not in row:
        return None
    out: list[tuple] = [("recheck", str(n), None)
                        for n, w in sorted((row.get("recheck_answers") or {}).items())
                        if w == "NOT RESOLVED"]
    out += [("finding", None, f) for f in row.get("reported") or [] if is_blocking(f)]
    if row.get("reported_state") == "unparsed" and not out:
        out.append(("unparsed", None, None))
    return out


def open_items(row: dict) -> "Optional[list[str]]":
    """What is still open after this round, as labels; None when the row cannot be judged."""
    ents = open_entries(row)
    if ents is None:
        return None
    return [f"R{n}: NOT RESOLVED" if kind == "recheck" else _label(f) if kind == "finding"
            else "answer not parsed (no findings JSON, no NO FINDINGS line)" for kind, n, f in ents]


def outcome(rows_by_expert: "dict[str, list[dict]]", root_rows: "list[dict]",
            redesigns_used: int, max_rounds: int = MAX_ROUNDS,
            early_ts: "float | None" = None) -> dict:
    """The loop's state for one DELIVER review cycle (see module docstring).
    ``rows_by_expert``: reviewer id → its marker rows in this cycle, oldest first.
    ``early_ts``: when ``gate2-fixspec`` recorded an early root-cause trigger in this
    cycle (a class reopened, core.gate2_fixspec.next_step). The verdict is consumed
    BEFORE the cap: SOUND before the cap → keep fixing (recheck); at the cap, a
    WRONG-APPROACH with no redesign budget, or a trigger recorded after the verdict →
    ``sound`` (one root-cause review per cycle). No verdict: the cap or an early trigger
    → ``root_cause``."""
    rounds = {e: len(rs) for e, rs in rows_by_expert.items() if rs and e not in NOT_REVIEWERS}
    open_: dict[str, list[str]] = {}
    unjudged: list[str] = []
    for e in rounds:
        items = open_items(rows_by_expert[e][-1])
        if items is None:
            unjudged.append(e)
        elif items:
            open_[e] = items
    roots = [r for r in root_rows or [] if isinstance(r, dict)]
    verdict = next((r.get("verdict") for r in reversed(roots)
                    if r.get("verdict") in (SOUND, WRONG)), None)
    verdict_text = next((str(r.get("verdict_text") or "") for r in reversed(roots)
                         if r.get("verdict") in (SOUND, WRONG)), "")
    if verdict is None and roots:
        # It answered, but with no verdict line: it cannot be asked again (one per
        # cycle), so the loop ends here, fail closed (push_ready stays false).
        verdict = UNPARSED
        verdict_text = str(roots[-1].get("verdict_text") or "")
    verdict_ts = next((float(r.get("ts") or 0.0) for r in reversed(roots)
                       if r.get("verdict") in (SOUND, WRONG)), 0.0)
    if verdict == UNPARSED:
        verdict_ts = float(roots[-1].get("ts") or 0.0)
    capped = sorted(e for e in open_ if rounds[e] >= max_rounds)
    if not rounds:
        action = "none"
    elif not open_:
        action = "converged"
    elif verdict is not None:
        if verdict == WRONG and redesigns_used < MAX_REDESIGNS:
            action = "redesign"
        elif capped or verdict in (WRONG, UNPARSED) or (early_ts is not None and early_ts > verdict_ts):
            action = "sound"     # at the cap, no redesign budget, unparsed, or a reopen after it
        else:
            action = "recheck"   # SOUND before the cap: fix as the spec says
    elif capped or early_ts is not None:
        action = "root_cause"
    else:
        action = "recheck"
    return {"action": action, "converged": action == "converged", "rounds": rounds,
            "open": open_, "capped": capped, "unjudged": sorted(unjudged),
            "root_cause": verdict, "root_cause_text": verdict_text[:1500],
            "redesigns_used": redesigns_used}
