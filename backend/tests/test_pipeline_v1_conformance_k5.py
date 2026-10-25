"""Conformance K5: v13 §D4 acceptance, end to end (conformance spec §3 K5).

Methodology: runs are driven only through ``pipeline-v1``
and the real hooks (``tests/pipeline_v1_rig.py``); assertions read the run's signed
event log, its sealed finals and git. Covered:

- every code profile lands (F9; every run is isolated since S6-5 removed the shared
  mode), and the TEST final of each repo is bound to the tree that landed;
- §D4 items 1, 4, 5, 6 on two real isolated runs of one repo, asserting finals, not
  only trees (F9);
- the §D4 end-to-end scenarios: TEST red -> not_landed(tests_red) and
  push_ready false, the circuit breaker, a person's run-land (human_override), a
  decision with awaiting_human, abandon/restore;
- a person's run-land after a partial_land keeps the landed repo and lands the rest.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig, _git

pytestmark = pytest.mark.timeout(300)

CODE_PROFILES = ("direct", "trivial", "full", "bugfix", "goal")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    rigs: list[Rig] = []

    def make(profile: str = "direct", **kwargs) -> Rig:
        r = Rig(tmp_path / f"case{len(rigs)}", monkeypatch, profile=profile, **kwargs)
        rigs.append(r)
        return r
    yield make
    for r in rigs:
        if r.run_id:
            assert r.v1("status").get("ok") is True, "the event log no longer folds"
        r.close()


def _final_tree(r: Rig) -> str:
    """The tree the run's last TEST report was submitted on (S6-1)."""
    tree = r.tested_tree()
    assert tree, "no TEST validation report"
    return tree


def _every_stage_checked_and_completed(r: Rig) -> None:
    """§D4: each stage's signed chain is stage_attempt -> a passing check for EVERY
    check the registry requires there -> stage_complete -> transition, and the land
    attempt passed every land check. The expectation comes from the check registry,
    not from the roster the run wrote."""
    from core.run_protocol import checks
    from core.run_protocol.lifecycle import PROFILE_STAGES
    events = r.events()
    by_id = {e["event_id"]: e for e in events}
    passed = {(e["payload"]["stage_attempt_event_id"], e["payload"]["check_id"])
              for e in events if e["kind"] == "check" and e["payload"]["passed"]}
    completed = {e["payload"]["stage_attempt_event_id"] for e in events
                 if e["kind"] == "stage_complete"}
    triggers = {e["payload"]["trigger_event_id"] for e in events if e["kind"] == "transition"}
    for stage in PROFILE_STAGES[r.profile]:
        if stage in ("deliver", "reflect"):
            continue
        variant = ("lite" if stage == "plan" and r.profile == "trivial"
                   else "full" if stage in {"evaluate", "plan"} else None)
        required = {spec.check_id for spec in checks.stage_checks(r.profile, stage, variant)}
        attempts = [e for e in events if e["kind"] == "stage_attempt" and e["stage"] == stage]
        assert attempts, f"{stage}: no stage_attempt"
        last = attempts[-1]
        assert required <= set(last["payload"]["check_ids"]), (stage, required)
        assert all((last["event_id"], cid) in passed for cid in required), stage
        assert last["event_id"] in completed, f"{stage}: no stage_complete"
        done = next(e for e in events if e["kind"] == "stage_complete"
                    and e["payload"]["stage_attempt_event_id"] == last["event_id"])
        assert done["event_id"] in triggers, f"{stage}: completion moved no transition"
    land = next(e for e in reversed(events) if e["kind"] == "land")
    attempt = by_id[land["payload"]["stage_attempt_event_id"]]
    for spec in checks.land_checks(r.profile):
        assert (attempt["event_id"], spec.check_id) in passed, spec.check_id


def _landed_is_final_plus_report(r: Rig, head: str) -> None:
    """The WHOLE landed tree is the tested code tree plus the rendered REPORT."""
    code_tree = _final_tree(r)
    changed = _git(r.repo, "diff", "--name-only", code_tree, head).splitlines()
    assert changed == [f".aidlc/deliverables/{r.run_id}/REPORT.md"], changed


@pytest.mark.parametrize("profile", CODE_PROFILES)
def test_every_code_profile_lands(rig, profile):
    r = rig(profile)
    r.create()
    base = _git(r.repo, "rev-parse", "main")
    state = r.drive()
    assert state["status"] == "completed" and state["landing"] == "landed", state
    engine = r.engine()
    assert engine.contract["mode"] == "isolated"
    head = _git(r.repo, "rev-parse", "main")
    assert head != base and _git(r.repo, "rev-parse", "main^") == base
    landed_tree = _git(r.repo, "rev-parse", "main^{tree}")
    assert engine.landed_commits() == {"repo": head}
    assert landed_tree
    _landed_is_final_plus_report(r, head)
    _every_stage_checked_and_completed(r)
    # the main checkout ends clean: the landed commit is what is checked out
    assert _git(r.repo, "status", "--porcelain") == ""


# ── v13 §D4 concurrent isolation acceptance: items 1, 4, 5, 6 with finals ───

def _second_run(r: Rig) -> Rig:
    """A second run on the SAME repo, data dir and hook oracle as *r*."""
    import copy
    other = copy.copy(r)
    other.env = dict(r.env)
    other.ctx = dict(r.ctx)
    other.run_id = None
    other.worktree = None
    return other


def _to_test_done(r: Rig, change: str) -> None:
    r.create()
    r.drive(until="build")
    r.edit({"backend/core/mod.py": change})
    assert r.report_and_complete().get("ok") is True
    r.drive(until="deliver")


def _mod_in(r: Rig, tree: str) -> str:
    return _git(r.repo, "cat-file", "-p", f"{tree}:backend/core/mod.py") + "\n"


A_CHANGE = "def add(a, b):\n    return b + a\n"
B_CHANGE = "def add(a, b):\n    return sum((a, b))\n"


def test_d4_1_two_isolated_runs_on_one_file_have_their_own_tree_and_final(rig):
    a = rig("direct")
    b = _second_run(a)
    _to_test_done(a, A_CHANGE)
    _to_test_done(b, B_CHANGE)
    ta, tb = a.tested_tree(), b.tested_tree()
    assert ta != tb
    assert _mod_in(a, ta) == A_CHANGE and _mod_in(b, tb) == B_CHANGE
    assert (a.repo / "backend/core/mod.py").read_text() == "def add(a, b):\n    return a + b\n"
    assert {e["run_id"] for e in a.events()} == {a.run_id}
    assert {e["run_id"] for e in b.events()} == {b.run_id}


def test_d4_4_test_auth_abandon_restore_never_cross_runs(rig):
    from tests.test_pipeline_v1_real_e2e import _not_landed_tests_red, _prepare
    a = rig("trivial")
    b = _second_run(a)
    b.profile = "direct"
    _not_landed_tests_red(a)                          # A: completed(not_landed)
    a_tree = a.tested_tree()
    b.create()
    b.drive(until="build")
    b.edit({"backend/core/mod.py": B_CHANGE})
    # test: a report through A's run id records only on A's own tree, never B's
    assert a.tested_tree() == a_tree
    # auth: A's prepared landing request cannot be executed by B
    request_id = _prepare(a)["prepared"]["request"]["request_id"]
    base = _git(a.repo, "rev-parse", "main")
    stolen = b.v1("run-land", "--request", request_id, "--message", "fix: steal" +
                  "\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    assert stolen.get("ok") is not True, stolen
    assert _git(a.repo, "rev-parse", "main") == base
    assert not any(e["kind"] in ("auth", "auth_consumed", "land") for e in b.events())
    # abandon / restore: B's lifecycle never touches A
    before = [e["event_id"] for e in a.events()]
    assert b.v1("abandon", "--reason", "cross-run probe").get("ok") is True
    assert b.v1("restore").get("ok") is True
    assert [e["event_id"] for e in a.events()] == before


def test_d4_4_a_persons_answer_reaches_only_the_run_that_asked(rig):
    from tests.test_pipeline_v1_real_e2e import _decision
    a = rig("bugfix")
    b = _second_run(a)
    b.profile = "direct"
    a.create()
    b.create()
    a.gate0("WRONG-FRAME", n=1)
    a.gate0("WRONG-FRAME", n=2)
    asked = a.v1("decision-request", "--artifact", _decision(a), "--document", "REQUIREMENTS")
    assert asked.get("ok") is True, asked
    assert a.answer_card("a — swap")
    assert any(e["kind"] == "human_answer" for e in a.events())
    assert not any(e["kind"] in ("decision", "human_answer") for e in b.events())


# ── §D4 end-to-end scenarios ─────────────────────────────────────────────────


def test_tests_red_ends_not_landed_and_never_push_ready(rig):
    from tests.test_pipeline_v1_real_e2e import _not_landed_tests_red
    r = rig("trivial")
    base = _git(r.repo, "rev-parse", "main")
    _not_landed_tests_red(r)
    assert _git(r.repo, "rev-parse", "main") == base
    assert r.engine().push_status()["push_ready"] is False


def test_a_persons_run_land_lands_as_human_override(rig):
    from tests.test_pipeline_v1_real_e2e import _not_landed_tests_red, _prepare, _request
    r = rig("trivial")
    _not_landed_tests_red(r)
    base = _git(r.repo, "rev-parse", "main")
    out = _request(r, _prepare(r))
    assert out.get("ok") is True, out
    head = _git(r.repo, "rev-parse", "main")
    assert _git(r.repo, "rev-parse", "main^") == base
    land = [e for e in r.events() if e["kind"] == "land"][-1]
    assert land["payload"]["landing"] == "human_override"
    assert land["payload"]["commits"] == {"repo": head}
    assert r.engine().landed_commits() == {"repo": head}
    assert _git(r.repo, "status", "--porcelain") == ""


def test_a_decision_waits_for_the_person_then_continues(rig):
    from tests.test_pipeline_v1_real_e2e import _decision
    from tests.pipeline_v1_rig import documents
    r = rig("bugfix")
    r.create()
    r.gate0("WRONG-FRAME", n=1)
    r.gate0("WRONG-FRAME", n=2)
    asked = r.v1("decision-request", "--artifact", _decision(r), "--document", "REQUIREMENTS")
    assert asked.get("ok") is True, asked
    assert r.state()["status"] == "awaiting_human"
    assert r.answer_card("a — swap")
    out = r.v1("stage-complete", "--artifact",
               r.artifact("requirements", documents("bugfix")["evaluate"]))
    assert out.get("ok") is True and r.state()["stage"] == "think", out


def test_abandon_then_restore_continues_to_a_bound_landing(rig):
    r = rig("direct")
    r.create()
    r.drive(until="review")
    assert r.v1("abandon", "--reason", "rehearse").get("ok") is True
    assert r.state()["status"] == "abandoned"
    assert r.v1("restore").get("ok") is True
    assert r.state()["status"] == "paused"
    assert r.v1("resume").get("ok") is True
    state = r.drive()
    assert state["landing"] == "landed", state
    _landed_is_final_plus_report(r, _git(r.repo, "rev-parse", "main"))


# ── a person's landing after a partial land ──────────────────────────────────


def test_a_run_land_after_a_partial_land_keeps_the_landed_repo(tmp_path):
    from core.run_protocol import landing
    from tests.test_pipeline_v1_conformance_k2 import _repos
    roots, bases, bindings = _repos(tmp_path)
    first = landing.commit_repos(run_id="run_one", integration_roots={"a": roots["a"]},
                                 bases={"a": bases["a"]}, bindings={"a": bindings["a"]},
                                 message="normal land", progress_path=tmp_path / "p1.json")
    # the person's landing renders another REPORT: repo a's delivery tree differs now
    other = dict(bindings, a={"delivery_tree": _git(Path(roots["a"]), "rev-parse",
                                                     f"{bases['a']}^{{tree}}")})
    commits = landing.commit_repos(run_id="run_one", integration_roots=roots, bases=bases,
                                   bindings=other, message="human land",
                                   progress_path=tmp_path / "p2.json", adopted=first)
    assert commits["a"] == first["a"] and _git(Path(roots["b"]), "rev-parse", "HEAD") == commits["b"]
    with pytest.raises(landing.LandingError, match="not on its branch"):
        landing.commit_repos(run_id="run_one", integration_roots=roots, bases=bases,
                             bindings=other, message="human land",
                             progress_path=tmp_path / "p3.json", adopted={"a": "f" * 40})


# ── review round 3 ───────────────────────────────────────────────────────────
