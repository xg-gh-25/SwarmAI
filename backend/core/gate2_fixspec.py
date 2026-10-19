"""Gate-2 FIXING_SPEC: what is open this round, and what comes after a spec (pure functions).

docs/design/pipeline-anti-whack-a-mole.md §7. Between the parallel Gate-2 review and any
fix there is one fixing spec: the round's blocking findings grouped by CAUSE, every site
of that cause in scope, one solution per group (validated by
``scripts/design_spec.fixing_spec_errors``, attacked by the ``fixspec-skeptic``). These
functions only read what the reviewers SAID (their SubagentStop marker rows) and the
specs already accepted; the CLI (``artifact_cli gate2-fixspec``) does the I/O.

- ``round_items(rows_by_expert, recheck_maps)`` → ``{"round", "items"}``: the open items
  of each reviewer's LATEST row. A blocking finding is identified by its ledger id
  (``core.gate2_findings.entry_id``, the same function the ledger uses); a re-check item
  answered NOT RESOLVED is ``<expert>-R<n>`` and carries the cluster it re-checked (the
  ``<expert>-recheck.map.json`` gate2-recheck wrote). An unparsed answer is NOT an item:
  it needs the reviewer to answer again (``_gate2_findings_errors`` asks for the re-spawn),
  not a fix by cause — and that re-spawn stays in its round.
  ``round`` is the GLOBAL round (``_rounds``). A row carrying ``wave`` (the CLI stamps
  it: 1 + the gate2-recheck hand-outs, run.json ``gate2_waves``, at or before the
  sub-agent's ``started_at``) belongs to the round its batch was handed out in, so the
  order in which a batch's answers COMPLETE never moves one into another round (the
  Red Team spawned with the round-2 re-checks used to land in round 1 when it answered
  first, and gate2-recheck then refused every spec). Within a wave, or for rows with no
  ``wave``, the rows replay in time order and a new round starts when a reviewer that
  already answered in the current round answers again — unless that answer replaces
  its unparsed one (a re-ask, not a re-check); a re-check spawned by hand, outside
  gate2-recheck, still starts a round.
- ``rechecked_rounds(rows_by_expert)`` → the global rounds whose final state had open
  items and that a later round followed (re-checked).
- ``next_step(spec, accepted, items, submissions, plan)`` → ``(next, trigger)``:
  ``skeptic`` by default; ``root_cause`` when the class did not close: a NEW finding of
  this round on a site an accepted cluster owned (reopen), a cluster that ``revises`` one
  already revised once, two or more invariants new to the design, a cluster that says the
  design must be redone (``design_impact: redesign`` — there is no redesign budget while
  ``gate2_convergence.MAX_REDESIGNS`` is 0), or the 3rd submission of one round (two
  skeptic BLOCKs: a spec that cannot be agreed means the approach is unclear).

The loop stays bounded (I6): at most ``MAX_SUBMISSIONS`` specs per round, at most
``MAX_ROUNDS`` rounds, one root-cause review per cycle (``gate2_convergence.outcome``).

Public symbols: ``FIXSPEC_SKEPTIC_ID``, ``MAX_SKEPTIC_BLOCKS``, ``MAX_SUBMISSIONS``,
``round_items``, ``rechecked_rounds``, ``next_step``, ``site_path``.
"""
from __future__ import annotations

import re
from typing import Any

from core.gate2_convergence import NOT_REVIEWERS, open_entries
from core.gate2_findings import entry_id

FIXSPEC_SKEPTIC_ID = "fixspec-skeptic"
MAX_SKEPTIC_BLOCKS = 2
MAX_SUBMISSIONS = MAX_SKEPTIC_BLOCKS + 1
_INV = re.compile(r"^I\d+$")


def _l(v: Any) -> list:
    return v if isinstance(v, list) else []


def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _reviewer_rows(rows_by_expert: "dict[str, list[dict]]") -> "dict[str, list[dict]]":
    return {e: list(r) for e, r in _d(rows_by_expert).items()
            if r and e not in NOT_REVIEWERS and e != FIXSPEC_SKEPTIC_ID}


def _rounds(rows: "dict[str, list[dict]]") -> "list[tuple[int, str, int]]":
    """(global round, reviewer, row index) per row: by hand-out wave, then time order."""
    def _wave(r: Any) -> int:
        w = _d(r).get("wave")
        return w if isinstance(w, int) and not isinstance(w, bool) else 0
    seq = sorted(((_wave(r), float(_d(r).get("ts") or 0.0), e, i) for e, rs in rows.items()
                  for i, r in enumerate(rs)), key=lambda t: (t[0], t[1], t[2], t[3]))
    out: list[tuple[int, str, int]] = []
    rnd, members, wave = 1, set(), None
    for w, _ts, e, i in seq:
        reask = i > 0 and _d(rows[e][i - 1]).get("reported_state") == "unparsed"
        if wave is not None and w > wave:
            rnd, members = rnd + 1, set()   # a new gate2-recheck batch: a new round
        elif e in members and not reask:
            rnd, members = rnd + 1, set()
        wave = w
        members.add(e)
        out.append((rnd, e, i))
    return out


def rechecked_rounds(rows_by_expert: "dict[str, list[dict]]") -> "list[int]":
    """Global rounds whose final state had open items and that a later round followed."""
    rows = _reviewer_rows(rows_by_expert)
    order = _rounds(rows)
    last = max((r for r, _e, _i in order), default=0)
    out: list[int] = []
    for n in range(1, last):
        state: dict[str, int] = {}
        for r, e, i in order:
            if r <= n:
                state[e] = i
        if any(k != "unparsed" for e, i in state.items() for k, _n, _f in open_entries(rows[e][i]) or []):
            out.append(n)
    return out


def round_items(rows_by_expert: "dict[str, list[dict]]",
                recheck_maps: "dict[str, dict[str, str]]") -> dict:
    """The round's open items (module docstring). ``rows_by_expert``: reviewer id →
    its marker rows of this cycle, oldest first."""
    rows = _reviewer_rows(rows_by_expert)
    items: list[dict] = []
    for eid in sorted(rows):
        ents = open_entries(rows[eid][-1])   # the same "open" gate2_convergence judges
        if ents is None:
            continue   # a daemon that does not record answers: not judged
        rr = len(rows[eid])
        for kind, n, f in ents:
            if kind == "recheck":
                items.append({"id": f"{eid}-R{n}", "expert": eid, "kind": "recheck",
                              "cluster": _d(_d(recheck_maps).get(eid)).get(str(n))})
            elif kind == "finding":
                items.append({"id": entry_id(eid, f), "expert": eid, "kind": "finding",
                              "path": f.get("path") or f.get("file"), "line": f.get("line"),
                              "reviewer_round": rr,
                              "text": str(f.get("summary") or f.get("finding") or "")[:300]})
    return {"round": max((r for r, _e, _i in _rounds(rows)), default=0), "items": items}


def site_path(site: Any) -> str:
    """The file a scope site names (``path``, ``path::symbol`` or ``path:line``)."""
    s = str(site or "").strip()
    s = s.split("::", 1)[0]
    m = re.match(r"^(.*?):\d+$", s)
    return (m.group(1) if m else s).strip()


def _invariants(plan: Any) -> set:
    d = _d(_d(plan).get("design"))
    return {str(i.get("id")) for i in _l(d.get("invariants")) if isinstance(i, dict) and i.get("id")}


def _same_file(a: str, b: str) -> bool:
    """One file named two ways: a reviewer often writes the absolute worktree path, a spec
    the repo-relative one — equal, or one ends with ``/`` + the other."""
    a, b = a.removeprefix("./"), b.removeprefix("./")
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def next_step(spec: dict, accepted: "list[dict]", items: "list[dict]", submissions: int,
              plan: Any, settled: "set[str] | None" = None) -> "tuple[str, str | None]":
    """(``skeptic`` | ``root_cause``, trigger) for a VALID spec (module docstring).
    ``accepted``: the clusters of this cycle's earlier accepted specs; ``submissions``:
    the attempt of this spec in its round, 1 + the skeptic BLOCKs the round already got
    (the CLI counts them); ``items``: the round's open items, a finding item may carry
    ``first_round`` (from the ledger). A finding an accepted cluster already lists, or
    that an accepted spec refuted (``settled``), is never NEW (a reviewer whose re-check
    has not answered still shows its old row)."""
    spec = _d(spec)
    rnd = int(spec.get("round") or 0)
    if submissions >= MAX_SUBMISSIONS:
        return "root_cause", (f"spec_not_agreed: submission {submissions} of round {rnd} after "
                              f"{MAX_SKEPTIC_BLOCKS} skeptic BLOCKs")
    clusters = [c for c in _l(spec.get("clusters")) if isinstance(c, dict)]
    if any(c.get("design_impact") == "redesign" for c in clusters):
        return "root_cause", "redesign_impact: the fixing spec says the design must be redone"
    known = _invariants(plan)
    new = {str(u.get("id")) for u in _l(spec.get("design_updates"))
           if isinstance(u, dict) and _INV.match(str(u.get("id") or "")) and str(u.get("id")) not in known}
    if len(new) >= 2:
        return "root_cause", f"new_invariants: {', '.join(sorted(new))} are new to the design"
    owned: dict = {}
    listed = {str(f) for c in accepted or [] if isinstance(c, dict) for f in _l(c.get("findings"))}
    listed |= {str(x) for x in settled or ()}
    for c in accepted or []:
        for s in _l(_d(c.get("scope")).get("sites")):
            owned.setdefault(site_path(s), c.get("id"))
    for it in items or []:
        # NEW in this round = first reported in the reviewer's own latest row (a reviewer
        # that joined late, e.g. the Red Team in round 2, counts its rows from 1)
        fr, rr = it.get("first_round"), it.get("reviewer_round")
        if (it.get("kind") != "finding" or rnd < 2 or str(it.get("id")) in listed
                or (fr is not None and fr != (rr or rnd))):
            continue
        p = site_path(it.get("path"))
        hit = next((k for k in owned if p and _same_file(p, k)), None)
        if hit:
            return "root_cause", (f"reopen: {it['id']} is a new round-{rnd} finding in {p}, a site "
                                  f"cluster {owned[hit]} owned")
    # a class revised twice: the target was revised already, or is itself a revision
    # (K1 -> K2 -> K3: gate2-recheck maps a re-check to the NEWEST cluster of the chain)
    rev_of = {str(c.get("id")): str(c.get("revises")) for c in accepted or []
              if isinstance(c, dict) and c.get("revises")}
    for c in clusters:
        r = c.get("revises")
        if r and (str(r) in set(rev_of.values()) or str(r) in rev_of):
            return "root_cause", f"revised_twice: {c.get('id')} revises {r}, already a revision of this class"
    return "skeptic", None
