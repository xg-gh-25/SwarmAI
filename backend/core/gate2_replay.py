"""Gate-2 under ``convergence = deterministic``: rebuild the finding ledger from its
sources, never from a stored state (Knowledge/Designs/2026-10-19-pipeline-
deterministic-convergence.md §2.1-§2.2; step 3a).

The ledger state is a pure function of three deterministic inputs, replayed in time
order into ``core.finding_ledger``:

- ``rows``   — the captured expert answers (sub-agent marker rows: ``expert_id``,
  ``ts``, ``reported_state``, ``reported``), as the Gate-2 findings check reads them;
- ``events`` — what the author declared (``fixed`` pass n, a break ``test`` for a
  finding, an ``untestable`` / ``not_reproduced`` declaration), each with its ``ts``;
- ``proofs`` — the runner's ``--mode prove`` verdicts per round
  (``{"R1": {nodeid: {"old": [...], "new": label}}}``).

Rebuilding each time makes the state idempotent: nothing drifts, a stored copy cannot
be edited, and the same inputs always give the same next step. No model verdict is an
input (D1): an expert's answer only contributes findings, and a finding closes only by
a valid proof.

Only blocking findings (``gate2_findings.must_record``: HIGH, or MED at confidence >= 7)
become ledger rows; the rest are returned as ``notes`` (listed, never push_ready).

Public: ``replay(roster, rows, events, proofs, prove_calls) -> (state, notes,
warnings)``; ``model_finding(expert, reported)``.
"""

from __future__ import annotations

from core import finding_ledger as fl
from core.gate2_findings import entry_id, must_record

EVENT_KINDS = ("fixed", "test", "declare")


def model_finding(expert: str, r: dict) -> dict:
    """A reported finding as the ledger's model input (``finding_ledger.MODEL_FIELDS``):
    the id is ``entry_id`` (per expert, stable across rounds)."""
    claim = r.get("summary") or r.get("finding") or r.get("description") or ""
    return {"id": entry_id(expert, r), "file": r.get("path") or r.get("file"),
            "claim": str(claim)[:500]}


def _ts(x) -> float:
    v = x.get("ts")
    return float(v) if isinstance(v, (int, float)) else 0.0


def replay(roster: "list[str]", rows: "list[dict]", events: "list[dict]",
           proofs: "dict[str, dict]", prove_calls: int = 0
           ) -> "tuple[dict, list[dict], list[str]]":
    """(ledger state, non-blocking notes, warnings). Rows from an expert outside the
    roster, or a spawn the ledger's budget does not allow, are ignored with a warning:
    they cannot add findings (no expert after R2, at most one respawn)."""
    state = fl.new_state(list(roster))
    notes: list[dict] = []
    warnings: list[str] = []
    seq = [(_ts(r), 0, i, "row", r) for i, r in enumerate(rows or []) if isinstance(r, dict)]
    seq += [(_ts(e), 1, i, "event", e) for i, e in enumerate(events or []) if isinstance(e, dict)]
    for _t, _o, _i, what, x in sorted(seq, key=lambda t: t[:3]):
        if what == "row":
            e = x.get("expert_id")
            if e not in roster:
                continue
            rnd = state["round"]
            if not fl.may_spawn(state, rnd, e):
                warnings.append(f"answer of {e} at ts {_t:.0f} ignored: R{rnd} allows no "
                                "spawn of it now (over budget, or before the fix was declared)")
                continue
            if x.get("reported_state") not in ("findings", "no_findings"):
                found = None  # unparsed, or a backend that captures no answer: not clean
            else:
                found = []
                for r in x.get("reported") or []:
                    if not isinstance(r, dict):
                        continue
                    (found if must_record(r) else notes).append(
                        model_finding(e, r) if must_record(r) else
                        {**model_finding(e, r), "round": rnd, "expert": e,
                         "severity": r.get("severity"), "confidence": r.get("confidence")})
            state = fl.record(state, {"kind": "review", "round": rnd, "expert": e,
                                      "findings": found})
            continue
        k = x.get("kind")
        if k not in EVENT_KINDS:
            warnings.append(f"event {k!r} ignored: only {', '.join(EVENT_KINDS)} are declared")
            continue
        try:
            if k == "fixed":
                state = fl.record(state, {"kind": "fixed", "pass": x.get("pass")})
            elif k == "test":
                state = fl.record(state, {"kind": "test", "finding": x.get("finding"),
                                          "test": x.get("test")})
            else:
                state = fl.record(state, {"kind": "declare", "finding": x.get("finding"),
                                          "declared": x.get("declared"),
                                          "reason": x.get("reason")})
        except (ValueError, KeyError, TypeError) as exc:
            warnings.append(f"{k} event ignored: {exc}")
    for row in state["findings"]:
        test = row.get("test")
        p = (proofs or {}).get(f"R{row.get('round')}", {}).get(test) if test else None
        if isinstance(p, dict):
            try:
                state = fl.record(state, {"kind": "proof", "finding": row["id"],
                                          "old": p.get("old") or [], "new": p.get("new")})
            except ValueError as exc:
                warnings.append(f"proof for {row['id']} ignored: {exc}")
    state["prove_calls"] = int(prove_calls)
    if state["phase"] == "review" and not fl._to_spawn(state, state["round"]) \
            and state["prove_calls"] and fl._needs_proof(state):
        state["phase"] = "prove"
    return state, notes, warnings
