"""core.finding_ledger: proof verdicts, finding states, the DELIVER state machine and
the termination test (Knowledge/Designs/2026-10-19-pipeline-deterministic-
convergence.md §2.2-§2.5, §5; run_600db8ac AC5-AC6).

Methodology: pure functions driven in-process. The termination test scripts the
design's worst case (conflicting rebase, an expert unparsable twice, an R2 finding,
an untestable finding, an import-error break test, a break test green on the
snapshot, a flaky snapshot run, a pre-existing red, an unfinished runner call, main
moving before landing) in two variants, then a seeded random-world sweep checks that
EVERY path ends landed or stopped within a step bound and that no expert is spawned
over budget or after R2.
Invariants: no model verdict is an input; not-proven findings never block landing,
only push_ready; every stop names an artifact and a reason.
"""
from __future__ import annotations

import random

import pytest

from core import finding_ledger as fl

EXPERTS = ["correctness", "security", "red-team"]
STEP_CAP = 200


# ── proof_verdict (I1) ───────────────────────────────────────────────────────
@pytest.mark.parametrize("old,new,verdict,code", [
    (["assert_fail", "assert_fail"], "pass", "valid", "proven"),
    (["assert_fail", "assert_fail"], "assert_fail", "invalid", "new_not_pass"),
    (["assert_fail", "assert_fail"], "error", "invalid", "new_not_pass"),
    (["assert_fail", "assert_fail"], None, "pending", "pending_new"),
    (["assert_fail"], "pass", "pending", "pending_old"),
    ([], None, "pending", "pending_old"),
    (["pass"], None, "invalid", "old_pass"),
    (["assert_fail", "pass"], "pass", "invalid", "old_flaky"),
    (["pass", "assert_fail"], "pass", "invalid", "old_flaky"),
    (["error"], "pass", "invalid", "old_error"),
    (["assert_fail", "error"], "pass", "invalid", "old_error"),
    (["timeout"], "pass", "invalid", "old_timeout"),
    (["skip"], "pass", "invalid", "old_skip"),
])
def test_proof_verdict(old, new, verdict, code):
    assert fl.proof_verdict(old, new) == (verdict, code)
    assert fl.PROOF_CODES[code][0] == verdict


def test_proof_verdict_rejects_an_unknown_label():
    with pytest.raises(ValueError):
        fl.proof_verdict(["RESOLVED"], "pass")


def test_old_runs_needed():
    assert fl.old_runs_needed([]) == 2
    assert fl.old_runs_needed(["assert_fail"]) == 1
    assert fl.old_runs_needed(["error"]) == 0  # decided by the first run
    assert fl.old_runs_needed(["pass"]) == 0
    assert fl.old_runs_needed(["assert_fail", "assert_fail"]) == 0


# ── finding_state ────────────────────────────────────────────────────────────
def _row(**kw):
    return {"id": "F", "round": 1, "kind": "finding", "test": None, "declared": None,
            "reason": None, "proof": None, **kw}


@pytest.mark.parametrize("row,state", [
    (_row(test="t", proof={"old": ["assert_fail", "assert_fail"], "new": "pass"}), "proven"),
    (_row(test="t", proof={"old": ["pass"], "new": None}), "invalid_test"),
    (_row(test="t", proof={"old": ["assert_fail", "pass"], "new": None}), "invalid_test"),
    (_row(test="t", proof={"old": ["error"], "new": None}), "invalid_test"),
    (_row(declared="untestable", reason="UI timing only"), "untestable"),
    (_row(declared="untestable", reason="  "), "open"),  # a declaration needs a reason
    (_row(declared="not_reproduced", test="t", proof={"old": ["pass"], "new": None}),
     "not_reproduced"),
    (_row(declared="not_reproduced"), "open"),  # no green old-side run behind it
    (_row(), "open"),
    (_row(test="t", proof={"old": ["assert_fail"], "new": None}), "open"),  # pending
    (_row(kind="advisory", claim="expert E gave no usable output"), "open"),
])
def test_finding_state(row, state):
    assert fl.finding_state(row) == state
    assert state in fl.FINDING_STATES


# ── budgets and spawning ─────────────────────────────────────────────────────
def test_budget_table_is_the_design_table():
    assert fl.BUDGETS == {"gate0_rounds": 2, "gate1_rounds": 2, "gate2_rounds": 2,
                          "gate2_fix_passes": 2, "red_fix_attempts": 3,
                          "unparsable_respawns": 1, "runner_continuations": 3,
                          "landing_attempts": 3}
    assert fl.gate_rounds_left("gate0", 0) == 2
    assert fl.gate_rounds_left("gate1", 2) == 0
    assert fl.gate_rounds_left("gate2", 5) == 0


def test_unparsable_expert_respawns_once_then_becomes_an_advisory():
    s = fl.new_state(["e"])
    s = fl.record(s, {"kind": "review", "round": 1, "expert": "e", "findings": None})
    assert fl.next_action(s) == {"action": "spawn", "round": 1, "experts": ["e"]}
    s = fl.record(s, {"kind": "review", "round": 1, "expert": "e", "findings": None})
    assert not fl.may_spawn(s, 1, "e")
    with pytest.raises(ValueError):
        fl.record(s, {"kind": "review", "round": 1, "expert": "e", "findings": []})
    assert [r["state"] for r in fl.not_proven(s)] == ["open"]
    assert "no usable output" in fl.not_proven(s)[0]["claim"]


def test_no_spawn_after_r2_and_none_outside_the_roster():
    s = fl.new_state(["e"])
    assert not fl.may_spawn(s, 1, "stranger")
    assert not fl.may_spawn(s, 2, "e")  # R2 has not started
    assert not fl.may_spawn(s, 3, "e")
    s = fl.record(s, {"kind": "review", "round": 1, "expert": "e",
                      "findings": [{"id": "F1", "claim": "c"}]})
    s = fl.record(s, {"kind": "fixed", "pass": 1})
    s = fl.record(s, {"kind": "review", "round": 2, "expert": "e",
                      "findings": [{"id": "F2", "claim": "c"}]})
    s = fl.record(s, {"kind": "fixed", "pass": 2})
    assert s["phase"] == "prove"
    for r in (1, 2, 3):
        assert not fl.may_spawn(s, r, "e")


def test_a_clean_r1_skips_fixing_and_r2():
    s = fl.new_state(["e"])
    s = fl.record(s, {"kind": "review", "round": 1, "expert": "e", "findings": []})
    assert fl.next_action(s) == {"action": "sync"}


def test_not_proven_never_blocks_landing_but_blocks_push_ready():
    s = fl.new_state(["e"])
    s = fl.record(s, {"kind": "review", "round": 1, "expert": "e",
                      "findings": [{"id": "F1", "claim": "c"}]})
    s = fl.record(s, {"kind": "fixed", "pass": 1})
    s = fl.record(s, {"kind": "review", "round": 2, "expert": "e", "findings": []})
    assert fl.next_action(s) == {"action": "sync"}  # F1 has no test: nothing to prove
    s = fl.record(s, {"kind": "synced", "conflict": False})
    s = fl.record(s, {"kind": "tests", "status": "green"})
    assert fl.landing_blockers(s) == [] and fl.next_action(s) == {"action": "land"}
    s = fl.record(s, {"kind": "landed", "commit": "abc"})
    assert s["result"] == {"state": "landed", "commit": "abc", "push_ready": False}
    assert fl.push_ready(s) == (False, ["F1: open"])


def test_landing_needs_a_green_test_set():
    s = fl.new_state(["e"])
    s = fl.record(s, {"kind": "review", "round": 1, "expert": "e", "findings": []})
    s = fl.record(s, {"kind": "synced", "conflict": False})
    with pytest.raises(ValueError):
        fl.record(s, {"kind": "landed"})
    s = fl.record(s, {"kind": "tests", "status": "red", "red": ["t"]})
    with pytest.raises(ValueError):
        fl.record(s, {"kind": "landed"})


def test_gate0_falsified_premise_stops_with_the_probe_named():
    s = fl.new_state(["e"])
    s = fl.record(s, {"kind": "gate0_probe", "falsified": True, "probe": "evidence/p1.json",
                      "detail": "the guard already exists"})
    a = fl.next_action(s)
    assert a["action"] == "stop" and a["kind"] == "gate0_falsified"
    assert a["artifact"] == "evidence/p1.json" and "already exists" in a["reason"]


def test_record_never_mutates_its_input_and_terminal_is_final():
    s = fl.new_state(["e"])
    snap = repr(s)
    fl.record(s, {"kind": "review", "round": 1, "expert": "e", "findings": None})
    assert repr(s) == snap
    s = fl.stop(s, {"action": "stop", "kind": "budget", "reason": "r", "artifact": "a"})
    assert fl.next_action(s)["action"] == "done"
    with pytest.raises(ValueError):
        fl.record(s, {"kind": "synced"})


# ── the termination test (§5) ────────────────────────────────────────────────
class WorstCase:
    """The design's worst case, all at once. ``variant`` A: test X stays red through
    every fix attempt; B: X is fixed on attempt 2."""

    X = "backend/tests/test_feature.py::test_x"
    PRE = "backend/tests/test_old.py::test_already_red"

    def __init__(self, variant: str):
        self.variant = variant
        self.prove_calls = 0
        self.test_calls = 0
        self.x_fixes = 0
        self.land_calls = 0
        self.syncs = []

    def respond(self, a: dict, s: dict) -> list:
        kind = a["action"]
        if kind == "spawn":
            return [self._review(a["round"], e) for e in a["experts"]]
        if kind == "fix":
            if a["pass"] == 1:
                return [{"kind": "fixed", "pass": 1},
                        {"kind": "test", "finding": "F1", "test": "t::f1"},
                        {"kind": "declare", "finding": "F2", "declared": "untestable",
                         "reason": "only visible in a live Slack thread"},
                        {"kind": "test", "finding": "F3", "test": "t::f3_imports_fix"},
                        {"kind": "test", "finding": "F4", "test": "t::f4_green_on_old"},
                        {"kind": "test", "finding": "F5", "test": "t::f5_flaky"}]
            return [{"kind": "fixed", "pass": 2},
                    {"kind": "test", "finding": "F6", "test": "t::f6_r2"}]
        if kind == "prove":
            self.prove_calls += 1
            first = self.prove_calls == 1
            proofs = {"F1": (["assert_fail"] if first else ["assert_fail", "assert_fail"],
                             None if first else "pass"),  # unfinished on the first call
                      "F3": (["error"], None),
                      "F4": (["pass"], None),
                      "F5": (["assert_fail", "pass"], None),
                      "F6": (["assert_fail", "assert_fail"], "pass")}
            return [{"kind": "proof", "finding": f, "old": o, "new": n}
                    for f, (o, n) in proofs.items() if f in a["findings"]] + [{"kind": "proved"}]
        if kind == "sync":
            # the first rebase conflicts on the same function; the author resolves it
            self.syncs.append(len(self.syncs) == 0)
            return [{"kind": "synced", "conflict": self.syncs[-1]}]
        if kind == "run_tests":
            self.test_calls += 1
            if self.test_calls == 1:
                return [{"kind": "tests", "status": "unfinished",
                         "unfinished": ["backend/tests/test_slow.py"], "load": 9.1}]
            x_red = self.variant == "A" or self.x_fixes < 2
            return [{"kind": "tests", "status": "red" if x_red else "green",
                     "red": ([self.X] if x_red else []) + [self.PRE],
                     "pre_existing": [self.PRE],
                     "failures": {self.X: "assert 3 == 4"} if x_red else {}}]
        if kind == "fix_red":
            assert a["test"] == self.X
            self.x_fixes += 1
            return [{"kind": "fixed_red", "test": a["test"]}]
        if kind == "land":
            self.land_calls += 1
            if self.land_calls == 1:  # main moves again just before landing
                return [{"kind": "main_moved"}]
            return [{"kind": "landed", "commit": "c0ffee"}]
        raise AssertionError(f"unexpected action {a}")

    def _review(self, rnd: int, e: str) -> dict:
        if rnd == 1:
            if e == "security":
                return {"kind": "review", "round": 1, "expert": e, "findings": None}
            fs = {"correctness": [{"id": "F1", "file": "a.py", "claim": "off by one"},
                                  {"id": "F2", "file": "b.py", "claim": "race in UI"},
                                  {"id": "F3", "file": "a.py", "claim": "missing guard"}],
                  "red-team": [{"id": "F4", "file": "c.py", "claim": "bypass"},
                               {"id": "F5", "file": "c.py", "claim": "timing"}]}[e]
            return {"kind": "review", "round": 1, "expert": e, "findings": fs}
        fs = [{"id": "F6", "file": "a.py", "claim": "fix 1 broke the empty case"}] \
            if e == "correctness" else []
        return {"kind": "review", "round": 2, "expert": e, "findings": fs}


def drive(world, experts=EXPERTS):
    s = fl.new_state(experts)
    for _ in range(STEP_CAP):
        a = fl.next_action(s)
        if a["action"] == "done":
            return s
        if a["action"] == "stop":
            assert a["reason"] and a["artifact"]  # D5: a stop names what and why
            return fl.stop(s, a)
        for ev in world.respond(a, s):
            s = fl.record(s, ev)
    raise AssertionError(f"no terminal state within {STEP_CAP} steps: {fl.next_action(s)}")


def _spawn_budget_ok(s, experts):
    per = {}
    for sp in s["spawns"]:
        assert 1 <= sp["round"] <= fl.BUDGETS["gate2_rounds"], sp
        key = (sp["round"], sp["expert"])
        per[key] = per.get(key, 0) + 1
    assert all(n <= 1 + fl.BUDGETS["unparsable_respawns"] for n in per.values()), per
    assert len(s["spawns"]) <= fl.BUDGETS["gate2_rounds"] * len(experts) * 2


def test_worst_case_variant_b_lands_with_push_ready_false():
    w = WorstCase("B")
    s = drive(w)
    assert s["result"]["state"] == "landed" and s["result"]["commit"] == "c0ffee"
    assert s["result"]["push_ready"] is False
    _spawn_budget_ok(s, EXPERTS)
    assert max(sp["round"] for sp in s["spawns"]) == 2
    assert sum(sp["expert"] == "security" and sp["round"] == 1 for sp in s["spawns"]) == 2
    states = {r["id"]: r["state"] for r in fl.not_proven(s)}
    assert states == {"F2": "untestable", "F3": "invalid_test", "F4": "invalid_test",
                      "F5": "invalid_test", "security-R1-nooutput": "open"}
    proven = [f["id"] for f in s["findings"] if fl.finding_state(f) == "proven"]
    assert proven == ["F1", "F6"]
    assert w.prove_calls == 2 and w.x_fixes == 2 and w.syncs == [True, False]
    ok, reasons = fl.push_ready(s)
    assert not ok and len(reasons) == 5


def test_worst_case_variant_a_stops_on_the_red_test():
    w = WorstCase("A")
    s = drive(w)
    r = s["result"]
    assert r["state"] == "stopped" and r["kind"] == "budget"
    assert r["artifact"] == WorstCase.X
    assert r["reason"] == f"test {WorstCase.X} red after 3 attempts, last failure: assert 3 == 4"
    assert w.x_fixes == 3 and w.land_calls == 0
    _spawn_budget_ok(s, EXPERTS)


def test_the_pre_existing_red_alone_never_blocks():
    s = fl.new_state(["e"])
    s = fl.record(s, {"kind": "review", "round": 1, "expert": "e", "findings": []})
    s = fl.record(s, {"kind": "synced"})
    s = fl.record(s, {"kind": "tests", "status": "red", "red": ["p"], "pre_existing": ["p"]})
    assert fl.next_action(s) == {"action": "land"}


def test_unfinished_stops_after_three_continuations():
    s = fl.new_state(["e"])
    s = fl.record(s, {"kind": "review", "round": 1, "expert": "e", "findings": []})
    s = fl.record(s, {"kind": "synced"})
    for _ in range(1 + fl.BUDGETS["runner_continuations"]):
        assert fl.next_action(s) == {"action": "run_tests"}
        s = fl.record(s, {"kind": "tests", "status": "unfinished", "unfinished": ["u1", "u2"],
                          "load": 12.0})
    a = fl.next_action(s)
    assert a["action"] == "stop" and a["kind"] == "unfinished"
    assert a["reason"] == "2 tests unfinished after 3 continuations (load avg 12.0)"


def test_main_moving_three_times_stops():
    s = fl.new_state(["e"])
    s = fl.record(s, {"kind": "review", "round": 1, "expert": "e", "findings": []})
    for _ in range(fl.BUDGETS["landing_attempts"]):
        s = fl.record(s, {"kind": "synced"})
        s = fl.record(s, {"kind": "tests", "status": "green"})
        assert fl.next_action(s) == {"action": "land"}
        s = fl.record(s, {"kind": "main_moved"})
    a = fl.next_action(s)
    assert a["action"] == "stop" and a["reason"] == "main moved during 3 landing attempts"


class RandomWorld:
    """Every response drawn at random: unparsable outputs, any number of findings,
    every proof label, any test status, red sets from a finite pool, main moving."""

    POOL = ["t::a", "t::b", "t::c"]

    def __init__(self, rng):
        self.rng = rng
        self.n = 0

    def respond(self, a, s):
        r = self.rng
        k = a["action"]
        if k == "spawn":
            evs = []
            for e in a["experts"]:
                if r.random() < 0.3:
                    evs.append({"kind": "review", "round": a["round"], "expert": e,
                                "findings": None})
                    continue
                fs = []
                for _ in range(r.randint(0, 3)):
                    self.n += 1
                    fs.append({"id": f"F{self.n}", "claim": "c"})
                evs.append({"kind": "review", "round": a["round"], "expert": e, "findings": fs})
            return evs
        if k == "fix":
            evs = [{"kind": "fixed", "pass": a["pass"]}]
            for f in a["findings"]:
                c = r.random()
                if c < 0.6:
                    evs.append({"kind": "test", "finding": f, "test": f"t::{f}"})
                elif c < 0.8:
                    evs.append({"kind": "declare", "finding": f, "declared": "untestable",
                                "reason": "r"})
            return evs
        if k == "prove":
            evs = []
            for f in a["findings"]:
                old = [r.choice(fl.OLD_LABELS) for _ in range(r.randint(0, 2))]
                evs.append({"kind": "proof", "finding": f, "old": old,
                            "new": r.choice([None, "pass", "assert_fail", "error"])})
            return evs + [{"kind": "proved"}]
        if k == "sync":
            return [{"kind": "synced", "conflict": r.random() < 0.5}]
        if k == "run_tests":
            st = r.choice(["green", "red", "unfinished"])
            red = r.sample(self.POOL, r.randint(1, 2)) if st == "red" else []
            pre = [x for x in red if r.random() < 0.3]
            return [{"kind": "tests", "status": st, "red": red, "pre_existing": pre,
                     "unfinished": ["u"] if st == "unfinished" else [],
                     "failures": {x: "boom" for x in red}}]
        if k == "fix_red":
            return [{"kind": "fixed_red", "test": a["test"]}]
        if k == "land":
            return [{"kind": "main_moved"}] if r.random() < 0.4 else [{"kind": "landed"}]
        raise AssertionError(a)


@pytest.mark.parametrize("seed", range(300))
def test_every_random_world_terminates_within_budget(seed):
    rng = random.Random(seed)
    experts = EXPERTS[:rng.randint(1, 3)]
    s = drive(RandomWorld(rng), experts)
    assert s["result"]["state"] in fl.TERMINAL
    _spawn_budget_ok(s, experts)
    if s["result"]["state"] == "landed":
        assert s["tests"]["status"] == "green"
        assert s["result"]["push_ready"] == all(
            fl.finding_state(f) == "proven" for f in s["findings"])
    else:
        assert s["result"]["reason"] and s["result"]["artifact"]
