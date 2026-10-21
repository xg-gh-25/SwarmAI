"""Deterministic DELIVER decisions: the finding ledger, the budget table, the landing
blockers and ``push_ready`` (design: Knowledge/Designs/2026-10-19-pipeline-
deterministic-convergence.md §2.2-§2.5, §5).

Models find, tests decide. Every function here is pure (no I/O, no clock, no model
call): it reads a plain-dict run state and returns either the next action or a new
state. The CLI (step 3 of the design) will persist the state in run.json and execute
the actions; the termination test drives the same functions with a scripted world.

Public surface:

- ``BUDGETS`` — the one budget table (§2.4). Every model loop is bounded by a counter
  in it; nothing waits for models to agree.
- ``proof_verdict(old, new)`` — I1: a break test proves a finding only when both
  old-side runs end in an assertion failure and the new side passes.
- ``finding_state(row)`` — one of ``FINDING_STATES``. Only ``proven`` is closed.
- ``new_state(experts)`` / ``next_action(state)`` / ``record(state, event)`` — the
  DELIVER state machine. ``next_action`` returns the single next step; ``record``
  applies what happened and returns a new state (the input is never mutated).
- ``landing_blockers(state)`` — the complete list of what blocks a LOCAL commit
  (§2.5). Not-proven findings are never on it.
- ``push_ready(state)`` / ``not_proven(state)`` / ``push_blocking(state)`` /
  ``residual(state)`` — what the human sees before push.

Flow of the state machine (phase order):

  review R1 → fix ① → review R2 → fix ② → prove → sync → test → land → landed
                                                      ↑        |      |
                                                      └─ main moved ──┘
  test red → fix_red (≤3 per test) → test; unfinished → test (≤3 per test set)

A round with no findings skips its fix pass (and R2 is skipped when R1 found
nothing). No expert is spawned after R2, ever. Every stop names an artifact and a
reason.
"""

from __future__ import annotations

import copy

FINDING_STATES = ("proven", "invalid_test", "untestable", "not_reproduced", "open")

BUDGETS = {
    "gate0_rounds": 2,          # Gate-0 skeptic rounds; then probe or advisory
    "gate1_rounds": 2,          # Gate-1 skeptic rounds; then exit test or advisory
    "gate2_rounds": 2,          # Gate-2 review rounds; no expert after R2
    "gate2_fix_passes": 2,      # fix ① and fix ②
    "red_fix_attempts": 3,      # fixes per red test before the stop
    "unparsable_respawns": 1,   # respawns of an expert whose output cannot be parsed
    "runner_continuations": 3,  # continuation calls per test set (and per prove set)
    "landing_attempts": 3,      # landing attempts while main keeps moving
}

# proof_verdict codes → (verdict, reason). Fixed set: a stop or a REPORT row quotes it.
PROOF_CODES = {
    "proven": ("valid", "FAILs by assertion on the snapshot (2 of 2) and PASSes now"),
    "old_pass": ("invalid", "PASSes on the snapshot: the test does not detect the defect"),
    "old_flaky": ("invalid", "flaky on the snapshot: one assertion failure, one pass"),
    "old_error": ("invalid", "errors on the snapshot before any assertion"),
    "old_timeout": ("invalid", "times out on the snapshot"),
    "old_skip": ("invalid", "is skipped on the snapshot"),
    "new_not_pass": ("invalid", "does not PASS on the current code"),
    "pending_old": ("pending", "snapshot runs not finished"),
    "pending_new": ("pending", "current-code run not finished"),
}
OLD_LABELS = ("assert_fail", "pass", "error", "timeout", "skip")
_OLD_INVALID = {"error": "old_error", "timeout": "old_timeout", "skip": "old_skip"}

TERMINAL = ("landed", "stopped")


def gate_rounds_left(gate: str, rounds_done: int) -> int:
    """Skeptic / expert rounds still allowed for ``gate`` (gate0 | gate1 | gate2)."""
    cap = BUDGETS.get(f"{gate}_rounds")
    if cap is None:
        raise ValueError(f"unknown gate {gate!r}")
    return max(0, cap - int(rounds_done))


# ── proofs (I1) ──────────────────────────────────────────────────────────────
def proof_verdict(old: "list[str]", new: "str | None") -> "tuple[str, str]":
    """(verdict, code). ``old`` is the list of finished snapshot-side labels (in run
    order, each one of ``OLD_LABELS``); ``new`` is the current-code label or None
    while not run. Valid only for [assert_fail, assert_fail] + pass. A first old run
    that is not an assertion failure decides the verdict at once (no second run)."""
    old = list(old or [])
    for o in old:
        if o not in OLD_LABELS:
            raise ValueError(f"unknown snapshot outcome {o!r}")
    for o in old:
        if o in _OLD_INVALID:
            return "invalid", _OLD_INVALID[o]
    if "pass" in old:
        return ("invalid", "old_flaky") if "assert_fail" in old else ("invalid", "old_pass")
    if len(old) < 2:
        return "pending", "pending_old"
    if new is None:
        return "pending", "pending_new"
    if new != "pass":
        return "invalid", "new_not_pass"
    return "valid", "proven"


def old_runs_needed(old: "list[str]") -> int:
    """How many more snapshot-side runs the proof needs (0, 1 or 2)."""
    v, _ = proof_verdict(old, "pass")
    if v == "invalid":
        return 0
    return max(0, 2 - len(old or []))


# ── finding rows ─────────────────────────────────────────────────────────────
def finding_state(row: dict) -> str:
    """The row's ledger state. ``proof`` is ``{"old": [...], "new": label|None}``;
    ``declared`` is None | "untestable" | "not_reproduced" with ``reason``."""
    if row.get("kind") == "advisory":
        return "open"
    declared = row.get("declared")
    proof = row.get("proof") or {}
    old = proof.get("old") or []
    if declared == "untestable" and (row.get("reason") or "").strip():
        return "untestable"
    if declared == "not_reproduced" and row.get("test") and "pass" in old \
            and "assert_fail" not in old:
        return "not_reproduced"
    if row.get("test") and proof:
        v, _ = proof_verdict(old, proof.get("new"))
        if v == "valid":
            return "proven"
        if v == "invalid":
            return "invalid_test"
    return "open"


def not_proven(state: dict) -> "list[dict]":
    """Every row that is not ``proven``, with its state: REPORT lists each one."""
    out = []
    for row in state.get("findings") or []:
        s = finding_state(row)
        if s != "proven":
            out.append({"id": row["id"], "state": s, "round": row.get("round"),
                        "expert": row.get("expert"), "claim": row.get("claim"),
                        "test": row.get("test")})
    return out


# ── the DELIVER state machine ────────────────────────────────────────────────
def new_state(experts: "list[str]", r2_experts: "list[str] | None" = None) -> dict:
    """A fresh Gate-2/DELIVER state. ``experts`` is the R1 roster; R2 defaults to it."""
    if not experts:
        raise ValueError("Gate-2 needs at least one expert")
    return {
        "phase": "review", "round": 1,
        "roster": {"1": list(experts), "2": list(r2_experts or experts)},
        "outputs": {"1": {}, "2": {}},   # expert -> {"tries": n, "ok": bool}
        "spawns": [],                    # every expert spawn, in order
        "findings": [],
        "fix_passes": 0,
        "prove_calls": 0,
        "tests": {"continuations": 0},
        "red_attempts": {},              # nodeid -> fixes made while it was red
        "last_red": {},                  # nodeid -> last failure line
        "landing_attempts": 0,
        "gate0_falsified": None,
        "result": None,                  # {"state": landed|stopped, ...}
    }


def _stop(state: dict, kind: str, reason: str, artifact: str) -> dict:
    return {"action": "stop", "kind": kind, "reason": reason, "artifact": artifact}


def _to_spawn(state: dict, rnd: int) -> "list[str]":
    """Experts of round ``rnd`` that may (re)spawn now."""
    outs = state["outputs"][str(rnd)]
    cap = 1 + BUDGETS["unparsable_respawns"]
    return [e for e in state["roster"][str(rnd)]
            if e not in outs or (not outs[e]["ok"] and outs[e]["tries"] < cap)]


def may_spawn(state: dict, rnd: int, expert: str) -> bool:
    """The budget check a spawn gate applies: R1/R2 only, roster only, ≤1 respawn."""
    if rnd < 1 or rnd > BUDGETS["gate2_rounds"] or state["phase"] != "review" \
            or state["round"] != rnd:
        return False
    return expert in _to_spawn(state, rnd)


def _round_findings(state: dict, rnd: int) -> "list[dict]":
    return [f for f in state["findings"] if f.get("round") == rnd and f.get("kind") != "advisory"]


def _needs_proof(state: dict) -> "list[str]":
    out = []
    for f in state["findings"]:
        if f.get("kind") == "advisory" or not f.get("test") \
                or f.get("declared") == "untestable":
            continue
        p = f.get("proof") or {}
        if proof_verdict(p.get("old") or [], p.get("new"))[0] == "pending":
            out.append(f["id"])
    return out


def landing_blockers(state: dict) -> "list[dict]":
    """§2.5, the complete list. Each names {kind, artifact, reason}. Not-proven
    findings are deliberately absent: they block push_ready only (D6)."""
    out = []
    if state.get("gate0_falsified"):
        g = state["gate0_falsified"]
        out.append({"kind": "gate0_falsified", "artifact": g.get("probe", "gate0 probe"),
                    "reason": "Gate-0 probe reproduced a falsified premise: "
                              + str(g.get("detail", ""))})
    cap = BUDGETS["red_fix_attempts"]
    for nid in sorted(state.get("red_attempts") or {}):
        if state["red_attempts"][nid] >= cap and state["tests"].get("status") == "red" \
                and nid in (state["tests"].get("red") or []):
            out.append({"kind": "budget", "artifact": nid,
                        "reason": f"test {nid} red after {cap} attempts, last failure: "
                                  + str(state["last_red"].get(nid, "?"))})
    t = state["tests"]
    # the first call plus BUDGETS continuations; the one after that is the stop
    if t.get("status") == "unfinished" and t["continuations"] > BUDGETS["runner_continuations"]:
        out.append({"kind": "unfinished", "artifact": ", ".join(t.get("unfinished") or []) or "tests",
                    "reason": f"{len(t.get('unfinished') or [])} tests unfinished after "
                              f"{BUDGETS['runner_continuations']} continuations"
                              + (f" (load avg {t['load']})" if t.get("load") else "")})
    if state["landing_attempts"] >= BUDGETS["landing_attempts"] and state["phase"] != "landed":
        out.append({"kind": "budget", "artifact": "main",
                    "reason": f"main moved during {BUDGETS['landing_attempts']} landing attempts"})
    return out


def next_action(state: dict) -> dict:
    """The single next step. Actions:

    - ``{"action": "spawn", "round": r, "experts": [...]}`` — spawn these in parallel
    - ``{"action": "fix", "pass": p, "findings": [ids]}``
    - ``{"action": "prove", "findings": [ids]}``
    - ``{"action": "sync"}`` — rebase on main
    - ``{"action": "run_tests"}`` — the run's tests + break tests + regression
    - ``{"action": "fix_red", "test": nodeid, "attempt": n}``
    - ``{"action": "land"}``
    - ``{"action": "stop", "kind", "reason", "artifact"}`` / ``{"action": "done", ...}``
    """
    if state.get("result"):
        return {"action": "done", **state["result"]}
    blockers = landing_blockers(state)
    if blockers:
        b = blockers[0]
        return _stop(state, b["kind"], b["reason"], b["artifact"])
    phase = state["phase"]
    if phase == "review":
        rnd = state["round"]
        pending = _to_spawn(state, rnd)
        if pending:
            return {"action": "spawn", "round": rnd, "experts": pending}
        fs = _round_findings(state, rnd)
        if fs and state["fix_passes"] < BUDGETS["gate2_fix_passes"]:
            return {"action": "fix", "pass": state["fix_passes"] + 1,
                    "findings": [f["id"] for f in fs]}
        return {"action": "prove", "findings": _needs_proof(state)} if _needs_proof(state) \
            else {"action": "sync"}
    if phase == "prove":
        need = _needs_proof(state)
        if need and state["prove_calls"] <= BUDGETS["runner_continuations"]:
            return {"action": "prove", "findings": need}
        return {"action": "sync"}
    if phase == "sync":
        return {"action": "sync"}
    if phase == "test":
        t = state["tests"]
        st = t.get("status")
        if st is None or st == "unfinished":
            return {"action": "run_tests"}
        if st == "red":
            nid = sorted(t["red"])[0]
            return {"action": "fix_red", "test": nid,
                    "attempt": state["red_attempts"].get(nid, 0) + 1}
        return {"action": "land"}
    raise ValueError(f"unknown phase {phase!r}")


# What a model may say about a finding. Everything else on the row (round, expert, kind,
# proof) is the ledger's: an expert that sends ``proof`` or ``kind`` must not be able to
# mark its own finding proven or turn it into something else (D1).
MODEL_FIELDS = ("id", "file", "claim", "test", "declared", "reason")
_DECLARATIONS = ("untestable", "not_reproduced")


def _finding_row(f, rnd: int, expert: str) -> "dict | None":
    """A ledger row from a model's finding dict: only MODEL_FIELDS are read, as strings;
    a finding with no usable id is dropped."""
    if not isinstance(f, dict):
        return None
    vals = {k: (str(f[k]) if f.get(k) is not None else None) for k in MODEL_FIELDS}
    if not vals["id"] or not vals["id"].strip():
        return None
    if vals["declared"] not in _DECLARATIONS:
        vals["declared"] = None
    return {**vals, "round": rnd, "expert": expert, "kind": "finding", "proof": None}


def _new_test_set(s: dict) -> None:
    s["phase"] = "test"
    s["tests"] = {"continuations": 0}


def record(state: dict, event: dict) -> dict:
    """Apply what happened; returns a new state. Events:

    - ``{"kind": "review", "round", "expert", "findings": [...] | None}`` — None =
      the output could not be parsed. Each finding: {id, file, claim, test?, declared?,
      reason?}; only these fields are read (``MODEL_FIELDS``), anything else the model
      sends is ignored; a known id keeps its first row within a round and becomes
      ``<id>-R<round>`` in a later one. Refused (ValueError) when the
      spawn was over budget.
    - ``{"kind": "fixed", "pass"}`` — fix pass done (a code change)
    - ``{"kind": "declare", "finding", "declared", "reason"}`` / ``{"kind": "test",
      "finding", "test"}`` — author declarations / a break test for a finding
    - ``{"kind": "proof", "finding", "old": [...], "new": label|None}`` — runner output
    - ``{"kind": "proved"}`` — one prove call finished (counts against continuations)
    - ``{"kind": "synced", "conflict": bool}`` — rebase done (a conflict resolution is
      a code change like any other: the tests re-run, no expert does)
    - ``{"kind": "tests", "status": green|red|unfinished, "red": [...],
      "pre_existing": [...], "unfinished": [...], "failures": {nodeid: line}, "load"}``
    - ``{"kind": "fixed_red", "test"}`` — one fix attempt for a red test
    - ``{"kind": "landed", "commit"}`` / ``{"kind": "main_moved"}`` — landing outcome
    - ``{"kind": "gate0_probe", "falsified": bool, "probe", "detail"}``
    """
    s = copy.deepcopy(state)
    if s.get("result"):
        raise ValueError(f"state is terminal ({s['result']['state']}); nothing more is recorded")
    k = event.get("kind")
    if k == "review":
        rnd, e = int(event["round"]), event["expert"]
        if not may_spawn(s, rnd, e):
            raise ValueError(f"expert {e} R{rnd} spawn is over budget or out of phase")
        out = s["outputs"][str(rnd)].setdefault(e, {"tries": 0, "ok": False})
        out["tries"] += 1
        s["spawns"].append({"round": rnd, "expert": e})
        fs = event.get("findings")
        if fs is None:
            if out["tries"] >= 1 + BUDGETS["unparsable_respawns"]:
                s["findings"].append({"id": f"{e}-R{rnd}-nooutput", "round": rnd, "expert": e,
                                      "kind": "advisory",
                                      "claim": f"expert {e} gave no usable output"})
            return s
        out["ok"] = True
        for f in fs:
            row = _finding_row(f, rnd, e)
            if row is None:
                continue
            # A known id from an EARLIER round is a new finding on the same site (R2
            # reviews the fix, which edits the R1 line): it gets its own row. A known id
            # in the same round is a repeat: the first row stands. Never an error:
            # raising here would leave this spawn uncounted, and next_action would ask
            # for it forever.
            known = next((x for x in s["findings"] if x["id"] == row["id"]), None)
            if known is not None and known.get("round") != rnd:
                row["id"] = f"{row['id']}-R{rnd}"
                known = next((x for x in s["findings"] if x["id"] == row["id"]), None)
            if known is not None:
                continue
            s["findings"].append(row)
        return s
    if k == "fixed":
        # Only when the ledger asks for exactly this pass: every expert of the round has
        # answered and the round has findings. A fix declared early would turn the late
        # R1 answers into R2 rows, or drop them as over budget.
        act = next_action(s)
        if act.get("action") != "fix" or int(event["pass"]) != act.get("pass"):
            raise ValueError(f"fix pass {event['pass']} out of order: the next step is "
                             f"{act.get('action')}")
        s["fix_passes"] += 1
        if s["round"] < BUDGETS["gate2_rounds"]:
            s["round"] += 1
            s["phase"] = "review"
        else:
            s["phase"] = "prove"
        return s
    if k in ("declare", "test", "proof"):
        row = next((f for f in s["findings"] if f["id"] == event["finding"]), None)
        if row is None:
            raise ValueError(f"unknown finding {event['finding']}")
        if k == "declare":
            if event["declared"] not in ("untestable", "not_reproduced"):
                raise ValueError(f"unknown declaration {event['declared']!r}")
            row["declared"], row["reason"] = event["declared"], event.get("reason")
        elif k == "test":
            row["test"], row["proof"] = event["test"], None
        else:
            proof_verdict(event.get("old") or [], event.get("new"))  # validates labels
            row["proof"] = {"old": list(event.get("old") or []), "new": event.get("new")}
        return s
    if k == "proved":
        if s["phase"] == "review":
            s["phase"] = "prove"
        s["prove_calls"] += 1
        return s
    if k == "synced":
        if s["phase"] not in ("review", "prove", "sync"):
            raise ValueError(f"sync out of phase {s['phase']}")
        _new_test_set(s)
        return s
    if k == "tests":
        if s["phase"] != "test":
            raise ValueError("tests recorded outside the test phase")
        st = event["status"]
        if st not in ("green", "red", "unfinished"):
            raise ValueError(f"unknown test status {st!r}")
        pre = set(event.get("pre_existing") or [])
        red = sorted(set(event.get("red") or []) - pre)
        t = s["tests"]
        t.update(status=("red" if red else "green") if st != "unfinished" else "unfinished",
                 red=red, pre_existing=sorted(pre),
                 unfinished=list(event.get("unfinished") or []), load=event.get("load"))
        if st == "unfinished":
            t["continuations"] += 1
        for nid, line in (event.get("failures") or {}).items():
            s["last_red"][nid] = line
        return s
    if k == "fixed_red":
        nid = event["test"]
        if s["phase"] != "test" or nid not in (s["tests"].get("red") or []):
            raise ValueError(f"{nid} is not red")
        s["red_attempts"][nid] = s["red_attempts"].get(nid, 0) + 1
        _new_test_set(s)  # a fix is a code change: a fresh test set decides
        return s
    if k == "landed":
        if s["phase"] != "test" or s["tests"].get("status") != "green":
            raise ValueError("landing needs a green test set")
        s["phase"] = "landed"
        s["landing_attempts"] += 1
        s["result"] = {"state": "landed", "commit": event.get("commit"),
                       "push_ready": push_ready(s)[0]}
        return s
    if k == "main_moved":
        s["landing_attempts"] += 1
        s["phase"] = "sync"
        return s
    if k == "gate0_probe":
        if event.get("falsified"):
            s["gate0_falsified"] = {"probe": event.get("probe"), "detail": event.get("detail")}
        return s
    raise ValueError(f"unknown event kind {k!r}")


def stop(state: dict, action: dict) -> dict:
    """Record a stop returned by ``next_action`` as the terminal result."""
    if action.get("action") != "stop":
        raise ValueError("not a stop action")
    s = copy.deepcopy(state)
    s["phase"] = "stopped"
    s["result"] = {"state": "stopped", "kind": action["kind"], "reason": action["reason"],
                   "artifact": action["artifact"]}
    return s


# An expert whose output stayed unparsable after its respawn leaves an advisory row.
# Only correctness's silence lowers push_ready (no correctness review = no review);
# any other expert's is a coverage gap REPORT lists as residual risk
# (docs/design/pipeline-gate-expert-simplification.md E5).
NO_OUTPUT_BLOCKS = frozenset({"correctness"})


def push_blocking(state: dict) -> "list[dict]":
    """The ``not_proven`` rows that lower push_ready: every one except the no-output
    advisory row of an expert outside ``NO_OUTPUT_BLOCKS``."""
    return [r for r in not_proven(state) if not _residual_only(r, state)]


def residual(state: dict) -> "list[dict]":
    """The ``not_proven`` rows that do NOT lower push_ready (REPORT: residual risk)."""
    return [r for r in not_proven(state) if _residual_only(r, state)]


def _residual_only(r: dict, state: dict) -> bool:
    row = next((f for f in state.get("findings") or [] if f.get("id") == r["id"]), {})
    return row.get("kind") == "advisory" and r.get("expert") not in NO_OUTPUT_BLOCKS


def push_ready(state: dict) -> "tuple[bool, list[str]]":
    """(ready, reasons). Ready only when landed and every ``push_blocking`` row is
    proven (``residual`` rows are listed, not blocking)."""
    reasons = []
    if state.get("phase") != "landed":
        reasons.append("not landed")
    for r in push_blocking(state):
        reasons.append(f"{r['id']}: {r['state']}")
    return not reasons, reasons
