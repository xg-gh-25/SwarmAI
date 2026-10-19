"""FIXING_SPEC (docs/design/pipeline-anti-whack-a-mole.md §7) — the pure parts.

What is tested:
- ``core.gate2_findings.entry_id`` is the ledger's id (one implementation, I4)
- ``core.gate2_fixspec.round_items``: a round's open items from each reviewer's LATEST
  marker row (blocking findings by ledger id; NOT RESOLVED re-check answers by
  ``<expert>-R<n>`` carrying the cluster they re-checked); refuted / non-blocking out
- ``core.gate2_fixspec.next_step``: skeptic by default; root_cause on a reopen (a new
  finding on a site of an accepted cluster), a second ``revises`` of one cluster, >= 2
  new invariants, a ``design_impact: redesign``, or the 3rd submission of a round (I3, F6)
- ``design_spec.fixing_spec_errors``: coverage exactly once, recheck items through
  ``revises``, per-cluster fields, the degenerate-split guard, deferred, amend (I2, F1)
- ``core.gate2_convergence.outcome``: the root-cause verdict is consumed before the cap;
  an early trigger hands out root_cause; WRONG-APPROACH / a reopen after a verdict end
  the loop (sound) while MAX_REDESIGNS is 0 (I6, F4, F8)
- the daemon side: ``fixspec-skeptic`` verdicts are parsed, it is not a reviewer, and the
  round gate allows it 3 submissions per Gate-2 round
"""
import pytest

from core import gate2_convergence as gc
from core import gate2_findings as g2
from core import gate2_fixspec as fs
from scripts import design_spec

HIGH_A = {"severity": "HIGH", "confidence": 8, "path": "a.py", "line": 3, "summary": "leaks the token"}
HIGH_B = {"severity": "HIGH", "confidence": 9, "path": "b.py", "line": 7, "summary": "races on write"}
MED_C = {"severity": "MEDIUM", "confidence": 8, "path": "a.py", "line": 30, "summary": "stale cache"}
LOW_D = {"severity": "LOW", "confidence": 5, "path": "c.py", "summary": "naming"}


def _row(reported=(), answers=None, state="findings"):
    return {"reported": list(reported), "reported_state": state, "recheck_answers": answers or {}}


PLAN = {"design": {"invariants": [{"id": "I1", "statement": "one writer"},
                                  {"id": "I2", "statement": "tokens never logged"}],
                   "root_invariant": "I1"}}


def _cluster(cid, findings, inv="I2", **kw):
    c = {"id": cid, "findings": findings,
         "cause": {"invariant": inv, "why": "two writers build the args; nothing owns them"},
         "scope": {"sites": ["a.py::build"], "evidence": "grep -rn build_args backend -> 3 sites"},
         "solution": "every caller goes through the single builder function",
         "reuse": {"mechanism": "a.py::build", "decision": "reuse"},
         "anti_patch": "no new guard: the second writer is removed",
         "design_impact": "none",
         "break_test": "backend/tests/test_args.py::test_one_writer"}
    c.update(kw)
    return c


# ── identity ──────────────────────────────────────────────────────────────────
def test_entry_id_is_the_ledgers_id():
    from scripts import artifact_cli as cli
    assert cli._gate2_finding_key(HIGH_A) == g2.finding_key(HIGH_A)
    rid = g2.entry_id("correctness", HIGH_A)
    assert rid.startswith("correctness-") and len(rid) == len("correctness-") + 6
    assert g2.entry_id("correctness", dict(HIGH_A, confidence=3)) == rid   # key, not severity
    assert g2.entry_id("security", HIGH_A) != rid


# ── round items ───────────────────────────────────────────────────────────────
def test_round_items_read_each_reviewers_latest_row():
    rows = {"correctness": [_row([HIGH_A, LOW_D]), _row([MED_C], {"1": "NOT RESOLVED", "2": "RESOLVED"})],
            "security": [_row([HIGH_B])],
            "meta-review": [_row([HIGH_B])],                      # not a reviewer
            fs.FIXSPEC_SKEPTIC_ID: [_row([HIGH_B])]}               # not a reviewer
    out = fs.round_items(rows, {"correctness": {"1": "K1"}})
    assert out["round"] == 2
    ids = {i["id"]: i for i in out["items"]}
    assert ids[g2.entry_id("correctness", MED_C)]["kind"] == "finding"
    assert ids["correctness-R1"]["kind"] == "recheck" and ids["correctness-R1"]["cluster"] == "K1"
    assert g2.entry_id("security", HIGH_B) in ids
    assert g2.entry_id("correctness", HIGH_A) not in ids            # an earlier round
    assert not any(i["expert"] in ("meta-review", fs.FIXSPEC_SKEPTIC_ID) for i in out["items"])
    assert "correctness-R2" not in ids


def test_round_items_unparsed_answer_is_no_spec_item_and_none_is_empty():
    out = fs.round_items({"correctness": [_row([], state="unparsed")]}, {})
    assert out["items"] == []                    # a re-ask, not a fix by cause
    assert gc.open_items(_row([], state="unparsed"))   # still open for convergence
    assert fs.round_items({}, {}) == {"round": 0, "items": []}


def test_a_reask_of_an_unparsed_answer_stays_in_its_round():
    rows = {"correctness": [dict(_row([], state="unparsed"), ts=1), dict(_row([HIGH_A]), ts=2)],
            "security": [dict(_row([HIGH_B]), ts=1.5)]}
    assert fs.round_items(rows, {})["round"] == 1
    assert fs.rechecked_rounds(rows) == []
    rows["correctness"].append(dict(_row([]), ts=3))                 # a real re-check
    assert fs.round_items(rows, {})["round"] == 2 and fs.rechecked_rounds(rows) == [1]


def test_a_refuted_finding_is_never_new():
    items = _items(("security", HIGH_A, 1))
    it = items[0]
    spec = {"round": 2, "clusters": [_cluster("K2", ["correctness-R1"], revises="K1")]}
    prior = [_cluster("K1", ["x"])]                                   # owns a.py
    assert fs.next_step(spec, prior, [dict(it, reviewer_round=1)], 1, PLAN)[0] == "root_cause"
    assert fs.next_step(spec, prior, [dict(it, reviewer_round=1)], 1, PLAN, {it["id"]})[0] == "skeptic"


def test_validator_cluster_ids_are_unique_across_the_cycle():
    items = _items(("correctness", HIGH_A, 2))
    spec = {"round": 2, "clusters": [_cluster("K1", [items[0]["id"]])]}
    errs = design_spec.fixing_spec_errors(spec, items, PLAN, [_cluster("K1", ["x"])], set())
    assert any("already the id of an accepted cluster" in e for e in errs)


# ── next step ─────────────────────────────────────────────────────────────────
def _items(*fs_):
    return [{"id": g2.entry_id(e, f), "expert": e, "kind": "finding", "path": f.get("path"),
             "first_round": r} for e, f, r in fs_]


def test_next_step_defaults_to_the_skeptic():
    items = _items(("correctness", HIGH_A, 1))
    spec = {"round": 1, "clusters": [_cluster("K1", [items[0]["id"]])]}
    assert fs.next_step(spec, [], items, 1, PLAN)[0] == "skeptic"


@pytest.mark.parametrize("case", ["reopen", "revised_twice", "new_invariants", "redesign",
                                  "spec_not_agreed"])
def test_next_step_root_cause_triggers(case):
    items = _items(("correctness", HIGH_A, 2))
    spec = {"round": 2, "clusters": [_cluster("K2", [items[0]["id"]])]}
    prior = []
    subs = 1
    if case == "reopen":           # a NEW round-2 finding on a site an accepted cluster owned
        prior = [_cluster("K1", ["x"], scope={"sites": ["a.py::build"], "evidence": "e" * 25})]
    elif case == "revised_twice":
        prior = [_cluster("K1", ["x"]), _cluster("K3", ["y"], revises="K1")]
        spec["clusters"][0]["revises"] = "K1"
        spec["clusters"][0]["scope"] = {"sites": ["z.py"], "evidence": "e" * 25}
    elif case == "new_invariants":
        spec["design_updates"] = [{"id": "I8", "statement": "s" * 20, "why_missed": "w" * 12},
                                  {"id": "I9", "statement": "s" * 20, "why_missed": "w" * 12}]
    elif case == "redesign":
        spec["clusters"][0]["design_impact"] = "redesign"
    else:
        subs = fs.MAX_SUBMISSIONS
    nxt, trigger = fs.next_step(spec, prior, items, subs, PLAN)
    assert nxt == "root_cause" and trigger


def test_a_revision_of_a_revision_is_revised_twice():
    items = [{"id": "correctness-R1", "expert": "correctness", "kind": "recheck", "cluster": "K2"}]
    prior = [_cluster("K1", ["x"]), _cluster("K2", ["y"], revises="K1",
                                              scope={"sites": ["q.py"], "evidence": "e" * 25})]
    spec = {"round": 3, "clusters": [_cluster("K3", ["correctness-R1"], revises="K2",
                                              scope={"sites": ["r.py"], "evidence": "e" * 25})]}
    nxt, trigger = fs.next_step(spec, prior, items, 1, PLAN)
    assert nxt == "root_cause" and trigger.startswith("revised_twice")


def test_a_late_reviewers_new_finding_on_an_owned_site_is_a_reopen():
    rows = {"correctness": [_row([HIGH_B]), _row([])], "red-team": [_row([HIGH_A])]}
    items = fs.round_items(rows, {})["items"]
    it = next(i for i in items if i["expert"] == "red-team")
    assert it["reviewer_round"] == 1
    it["first_round"] = 1                       # the ledger: red-team's own first row
    spec = {"round": 2, "clusters": [_cluster("K2", [it["id"]])]}
    prior = [_cluster("K1", ["x"])]             # owns a.py::build
    assert fs.next_step(spec, prior, [it], 1, PLAN)[0] == "root_cause"


def test_open_items_and_round_items_share_one_definition():
    row = _row([HIGH_A, LOW_D], {"1": "NOT RESOLVED", "2": "RESOLVED"})
    assert len(gc.open_items(row)) == len(fs.round_items({"correctness": [row]}, {})["items"]) == 2
    assert gc.open_entries({"reported": [HIGH_A]}) is None          # an old daemon: not judged


def _ts(row, t):
    return dict(row, ts=t)


def test_rechecked_rounds_count_a_late_reviewer_in_the_round_it_joined():
    rows = {"correctness": [_ts(_row([HIGH_A]), 1), _ts(_row([HIGH_A]), 20), _ts(_row([]), 40)],
            "red-team": [_ts(_row([HIGH_B]), 21)]}            # joined in round 2
    assert fs.rechecked_rounds(rows) == [1, 2]
    rows["red-team"] = [_ts(_row([HIGH_B]), 15)]              # answered before correctness' 2nd row
    assert fs.rechecked_rounds(rows) == [1, 2]
    assert fs.rechecked_rounds({"correctness": [_ts(_row([]), 1), _ts(_row([]), 2)]}) == []
    assert fs.rechecked_rounds({"correctness": [_ts(_row([HIGH_A]), 1)]}) == []   # never re-checked
    late = {"correctness": [_ts(_row([]), 1), _ts(_row([]), 20)],
            "red-team": [_ts(_row([HIGH_B]), 21), _ts(_row([]), 40)]}   # round 3: only the Red Team
    assert fs.rechecked_rounds(late) == [2] and fs.round_items(late, {})["round"] == 3


def test_an_old_finding_an_accepted_cluster_lists_is_no_reopen():
    rows = {"a": [_row([HIGH_B]), _row([])], "b": [_row([HIGH_A])]}   # b's re-check not back yet
    items = fs.round_items(rows, {})["items"]
    it = next(i for i in items if i["expert"] == "b")
    it["first_round"] = 1
    spec = {"round": 2, "clusters": [_cluster("K2", [it["id"]])]}
    prior = [_cluster("K1", [it["id"]])]                               # K1 owns a.py and lists it
    assert fs.next_step(spec, prior, [it], 1, PLAN)[0] == "skeptic"


def test_validator_more_than_two_single_clusters_need_unrelated_even_beside_a_group():
    singles = [dict(HIGH_B, path=f"x{i}.py") for i in range(3)]
    items = _items(("correctness", HIGH_A, 1), ("security", MED_C, 1),
                   *[("correctness", f, 1) for f in singles])
    group = _cluster("K1", [items[0]["id"], items[1]["id"]])
    ks = [_cluster(f"K{i + 2}", [items[i + 2]["id"]],
                   scope={"sites": [f"x{i}.py::f"], "evidence": "e" * 25}) for i in range(3)]
    errs = design_spec.fixing_spec_errors({"round": 1, "clusters": [group] + ks}, items, PLAN, [], set())
    assert any("look like one cause split up" in e for e in errs)


def test_validator_a_recheck_of_a_refuted_finding_may_be_refuted_again():
    item = {"id": "correctness-R1", "expert": "correctness", "kind": "recheck", "cluster": "refuted"}
    spec = {"round": 2, "clusters": [], "refuted": [{"id": "correctness-R1", "evidence": "x" * 30}]}
    assert design_spec.fixing_spec_errors(spec, [item], PLAN, [], set()) == []
    for c in ("K1", None, ""):                   # a cluster's re-check, or a lost / edited map
        item["cluster"] = c
        assert design_spec.fixing_spec_errors(spec, [item], PLAN, [], set())


def test_reopen_matches_an_absolute_finding_path_to_a_relative_site():
    it = dict(_items(("correctness", dict(HIGH_A, path="/w/run_x/swarmai/a.py"), 2))[0], reviewer_round=2)
    spec = {"round": 2, "clusters": [_cluster("K2", [it["id"]])]}
    assert fs.next_step(spec, [_cluster("K1", ["x"])], [it], 1, PLAN)[0] == "root_cause"
    it["path"] = "/w/run_x/swarmai/aa.py"                               # a different file
    assert fs.next_step(spec, [_cluster("K1", ["x"])], [it], 1, PLAN)[0] == "skeptic"


def test_a_new_finding_elsewhere_is_no_reopen():
    items = _items(("correctness", HIGH_B, 2))      # b.py, not in K1's sites
    spec = {"round": 2, "clusters": [_cluster("K2", [items[0]["id"]], scope={"sites": ["b.py"], "evidence": "e" * 25})]}
    prior = [_cluster("K1", ["x"])]
    assert fs.next_step(spec, prior, items, 1, PLAN)[0] == "skeptic"


# ── validator ─────────────────────────────────────────────────────────────────
def _errs(spec, items, prior=(), known=()):
    return design_spec.fixing_spec_errors(spec, items, PLAN, list(prior), set(known))


def test_validator_accepts_a_full_spec():
    items = _items(("correctness", HIGH_A, 1), ("security", HIGH_B, 1))
    spec = {"round": 1, "clusters": [_cluster("K1", [items[0]["id"], items[1]["id"]])]}
    assert _errs(spec, items) == []


def test_validator_coverage_missing_twice_unknown():
    items = _items(("correctness", HIGH_A, 1), ("security", HIGH_B, 1))
    spec = {"round": 1, "clusters": [_cluster("K1", [items[0]["id"]]),
                                     _cluster("K2", [items[0]["id"], "ghost-000000"])]}
    e = " | ".join(_errs(spec, items))
    assert items[1]["id"] in e and "not in any cluster" in e
    assert "more than one cluster" in e
    assert "ghost-000000" in e


def test_validator_per_cluster_fields():
    items = _items(("correctness", HIGH_A, 1))
    bad = _cluster("K1", [items[0]["id"]], inv="I7", solution="short", anti_patch="",
                   break_test="test_x", design_impact="maybe",
                   reuse={"decision": "none-exists"}, scope={"sites": [], "evidence": "x"})
    e = " | ".join(_errs({"round": 1, "clusters": [bad]}, items))
    for word in ("invariant", "solution", "anti_patch", "break_test", "design_impact",
                 "reuse", "scope.sites", "scope.evidence"):
        assert word in e, word


def test_validator_degenerate_split_needs_unrelated():
    items = _items(("correctness", HIGH_A, 1), ("security", HIGH_B, 1), ("api", MED_C, 1))
    spec = {"round": 1, "clusters": [_cluster(f"K{i + 1}", [it["id"]]) for i, it in enumerate(items)]}
    assert any("single-finding clusters" in x for x in _errs(spec, items))
    spec["unrelated"] = [{"a": "K1", "b": "K2", "why": "different files, different invariants"},
                         {"a": "K2", "b": "K3", "why": "the cache is not the writer path at all"}]
    assert not any("single-finding clusters" in x for x in _errs(spec, items))


def test_validator_recheck_items_need_revises_and_deferred_is_non_blocking():
    items = [{"id": "correctness-R1", "expert": "correctness", "kind": "recheck", "cluster": "K1"}]
    spec = {"round": 2, "clusters": [_cluster("K2", ["correctness-R1"])]}
    prior = [_cluster("K1", ["x"])]
    assert any("revises" in x for x in _errs(spec, items, prior))
    spec["clusters"][0]["revises"] = "K1"
    assert _errs(spec, items, prior) == []
    hi = _items(("correctness", HIGH_A, 1))
    spec2 = {"round": 1, "clusters": [], "deferred": [{"id": hi[0]["id"], "reason": "later"}]}
    assert any("deferred" in x for x in _errs(spec2, hi))


def test_validator_refuted_covers_a_finding_with_evidence_only():
    items = _items(("correctness", HIGH_A, 1), ("security", HIGH_B, 1))
    spec = {"round": 1, "clusters": [_cluster("K1", [items[1]["id"]])],
            "refuted": [{"id": items[0]["id"], "evidence": "a.py:3 never sees a token: masked by the caller"}]}
    assert _errs(spec, items) == []
    spec["refuted"][0]["evidence"] = "no"
    assert any("evidence" in x for x in _errs(spec, items))
    rc = [{"id": "correctness-R1", "expert": "correctness", "kind": "recheck", "cluster": "K1"}]
    spec2 = {"round": 2, "clusters": [], "refuted": [{"id": "correctness-R1", "evidence": "e" * 30}]}
    assert any("cluster that revises it" in x for x in _errs(spec2, rc, [_cluster("K1", ["x"])]))


def test_validator_amend_needs_design_updates():
    items = _items(("correctness", HIGH_A, 1))
    spec = {"round": 1, "clusters": [_cluster("K1", [items[0]["id"]], design_impact="amend")]}
    assert any("design_updates" in x for x in _errs(spec, items))


# ── convergence ───────────────────────────────────────────────────────────────
def _open(n):
    return {"correctness": [_row([HIGH_A]) for _ in range(n)]}


def test_outcome_early_trigger_hands_out_root_cause_before_the_cap():
    assert gc.outcome(_open(1), [], 0)["action"] == "recheck"
    assert gc.outcome(_open(1), [], 0, early_ts=10.0)["action"] == "root_cause"


def test_outcome_consumes_the_verdict_before_the_cap():
    sound = [{"verdict": "SOUND", "ts": 20.0}]
    wrong = [{"verdict": "WRONG-APPROACH", "ts": 20.0}]
    assert gc.outcome(_open(2), sound, 0, early_ts=10.0)["action"] == "recheck"   # SOUND: keep fixing
    assert gc.outcome(_open(3), sound, 0, early_ts=10.0)["action"] == "sound"     # at the cap
    assert gc.outcome(_open(1), wrong, 0, early_ts=10.0)["action"] == "sound"     # no redesign budget
    # a reopen after the verdict ends the loop: one root-cause review per cycle
    assert gc.outcome(_open(2), sound, 0, early_ts=30.0)["action"] == "sound"


def test_outcome_without_trigger_is_unchanged():
    assert gc.outcome(_open(3), [], 0)["action"] == "root_cause"
    assert gc.outcome(_open(3), [{"verdict": "SOUND", "ts": 1.0}], 0)["action"] == "sound"
    assert gc.outcome({"correctness": [_row([])]}, [], 0)["action"] == "converged"


# ── daemon side ───────────────────────────────────────────────────────────────
def test_fixspec_skeptic_is_not_a_reviewer_and_its_verdict_parses():
    from core import runtime_hooks as rh
    from scripts import artifact_cli as cli
    assert fs.FIXSPEC_SKEPTIC_ID in gc.NOT_REVIEWERS
    assert fs.FIXSPEC_SKEPTIC_ID in cli._GATE_SKEPTIC_IDS
    assert rh._gate_verdict(fs.FIXSPEC_SKEPTIC_ID, "FIXSPEC VERDICT: PASS\nok") == "PASS"
    assert rh._gate_verdict(fs.FIXSPEC_SKEPTIC_ID, "FIXSPEC VERDICT: BLOCK\nmerge K1+K2") == "BLOCK"
    assert rh._gate_verdict(fs.FIXSPEC_SKEPTIC_ID, "FIXSPEC VERDICT: PASS | BLOCK") is None
    assert gc.outcome({fs.FIXSPEC_SKEPTIC_ID: [_row([HIGH_A])]}, [], 0)["action"] == "none"


def test_round_gate_allows_three_submissions_per_round():
    from core import review_round_gate as rg
    assert rg.cap_for(fs.FIXSPEC_SKEPTIC_ID) == fs.MAX_SUBMISSIONS * (gc.MAX_ROUNDS + 1)
    assert "gate2-fixspec" in rg._deny_reason(fs.FIXSPEC_SKEPTIC_ID,
                                              {"used": 12, "stage": "deliver", "run_id": "r"})
    assert rg.cap_for(gc.ROOT_CAUSE_ID) == 1
    assert rg.cap_for("correctness") == gc.MAX_ROUNDS
