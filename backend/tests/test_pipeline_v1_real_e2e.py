"""Pipeline v1 real end-to-end tests (fixing spec §2, group G).

Every run here is driven only through ``pipeline_v1.py`` / ``pipeline_test.py`` and the
real hook functions (``tests/pipeline_v1_rig.py``); no test appends an event. Each
negative path asserts the outcome the v13 design requires; a path the current code
gets wrong is ``xfail(strict=True)`` naming the fixing-spec item, and that item's
commit turns it into a required pass.

Invariant checked after every case: the run's event log folds (``status`` succeeds).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig, documents

pytestmark = pytest.mark.timeout(300)

CODE_PROFILES = ("direct", "trivial", "full", "bugfix", "goal")


def _xfail(item: str, why: str):
    return pytest.mark.xfail(strict=True, reason=f"fixing spec {item}: {why}")


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



def _main(r: Rig) -> str:
    from tests.pipeline_v1_rig import _git
    return _git(r.repo, "rev-parse", "main")


def _reason(state: dict) -> str | None:
    return state.get("reason")


def _landed_clean(state: dict) -> None:
    assert state["status"] == "completed" and state["landing"] == "landed", state
    assert all(v == "completed" for v in state["stages"].values()), state["stages"]


# ── happy paths ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("profile", [
    pytest.param(p, marks=_xfail("B1", "_v1_trace reads v1 run.json as legacy, BX-1 never passes"))
    for p in CODE_PROFILES])
def test_every_code_profile_lands(rig, profile):
    r = rig(profile)
    r.create()
    base = _main(r)
    state = r.drive()
    _landed_clean(state)
    assert _main(r) != base
    assert (r.repo / "backend/core/mod.py").read_text().endswith("return b + a\n")
    assert (r.repo / f".aidlc/deliverables/{r.run_id}/REPORT.md").is_file()


def test_research_completes_without_code(rig):
    r = rig("research", with_repo=False)
    r.create()
    state = r.drive()
    assert state["status"] == "completed" and state["landing"] == "no_code", state


# ── BUILD fails three times: the not-landed path, nothing reaches main ──────

@_xfail("A2", "after checks_exhausted, `land` commits untested code to main (G found it)")
def test_checks_exhausted_ends_not_landed_and_never_commits(rig):
    r = rig("direct")
    r.create()
    base = _main(r)
    r.drive(until="build")
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return a - b\n"})  # red
    for _ in range(3):
        r.pt("tdd", "--files", "backend/tests/test_x.py")
        r.v1("stage-complete")
    state = r.drive()
    assert state["status"] == "completed", state
    assert state["landing"] == "not_landed" and _reason(state) == "checks_exhausted", state
    assert _main(r) == base, "an exhausted run must not land"
    assert "render" in r.kinds(), "the not-landed REPORT is rendered by the engine"


# ── TEST red, a rerun green, main moving, merge conflict ────────────────────

BREAK_OTHER = {"backend/core/other.py": "def other():\n    return 2\n"}
FIX_OTHER = {"backend/core/other.py": "def other():\n    return 1\n"}


@_xfail("B1+A1/A2", "BUILD never passes; a red final has no not-landed producer")
def test_tests_red_ends_not_landed_tests_red(rig):
    r = rig("direct")
    r.create()
    base = _main(r)
    r.drive(until="build")
    r.edit(BREAK_OTHER)            # outside the BUILD exit test, inside regression scope
    state = r.drive()
    assert state["status"] == "completed", state
    assert state["landing"] == "not_landed" and _reason(state) == "tests_red", state
    assert _main(r) == base


@_xfail("B1+B2", "BUILD never passes; a later green run does not replace the red verdict")
def test_red_then_green_rerun_lands(rig):
    r = rig("direct")
    r.create()
    r.drive(until="build")
    r.edit(BREAK_OTHER)
    r.drive(until="test")
    r.pt("regression")             # red
    r.edit(FIX_OTHER)
    r.pt("regression")             # green on the current tree
    _landed_clean(r.drive())


@_xfail("B1+B3", "BUILD never passes; a disjoint main move is not carried")
def test_main_moves_disjoint_lands_on_new_main(rig):
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    moved = r.commit_on_main({"docs/notes.md": "unrelated\n"})
    _landed_clean(r.drive())
    from tests.pipeline_v1_rig import _git
    assert _git(r.repo, "merge-base", "--is-ancestor", moved, "main") == ""


@_xfail("B1+B3+A1", "BUILD never passes; an intersecting main move is neither rerun nor bounded")
def test_main_moves_into_dependency_scope_reruns_then_lands(rig):
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    base = _main(r)
    r.commit_on_main({"backend/tests/test_x.py": (
        "from core.mod import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\n"
        "def test_add_zero():\n    assert add(0, 0) == 0\n")})
    moved = _main(r)
    r.v1("land", "--message", "fix: swap\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    assert _main(r) == moved != base, "a final whose dependency scope moved must not land"
    assert r.state()["status"] == "active", "the attempt is retryable, not terminal"
    r.pt("regression")
    _landed_clean(r.drive())


@_xfail("B1+A1", "BUILD never passes; a merge conflict leaves the land attempt open")
def test_merge_conflict_three_times_ends_land_conflict(rig):
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    head = r.commit_on_main({"backend/core/mod.py": "def add(a, b):\n    return a + b + 0\n"})
    for _ in range(3):
        r.v1("land", "--message", "fix: swap\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    state = r.drive()
    assert state["status"] == "completed", state
    assert state["landing"] == "not_landed" and _reason(state) == "land_conflict", state
    assert _main(r) == head


# ── Gate-0, decisions, the person in the loop ───────────────────────────────

def _decision(r: Rig, request_id: str = "q1") -> str:
    return r.artifact(f"decision-{request_id}", {
        "request_id": request_id, "kind": "design_tradeoff",
        "question": "Swap or rewrite?", "why_evidence_insufficient": "both fit",
        "refs": [r.events()[0]["event_id"]],
        "options": [
            {"id": "a", "choice": "swap", "consequence": "small", "reversible": True,
             "risk": "low"},
            {"id": "b", "choice": "rewrite", "consequence": "large", "reversible": False,
             "risk": "high"}]})


@_xfail("A2", "a code profile's not-landed path (framing_rejected) cannot finish DELIVER")
def test_gate0_two_negative_rounds_end_framing_rejected(rig):
    r = rig("bugfix")
    r.create()
    docs = documents("bugfix")
    r.gate0("WRONG-FRAME", n=1)
    r.gate0("WRONG-FRAME", n=2)
    r.v1("stage-complete", "--artifact", r.artifact("requirements", docs["evaluate"]))
    state = r.drive()
    assert state["status"] == "completed", state
    assert state["landing"] == "not_landed" and _reason(state) == "framing_rejected", state


@_xfail("A4", "an answered decision does not override the earlier Gate-0 rejection")
def test_gate0_rejection_then_answered_decision_continues(rig):
    r = rig("bugfix")
    r.create()
    docs = documents("bugfix")
    r.gate0("WRONG-FRAME", n=1)
    r.gate0("WRONG-FRAME", n=2)
    asked = r.v1("decision-request", "--artifact", _decision(r), "--document", "REQUIREMENTS")
    assert asked.get("ok") is True, asked
    assert r.answer_card("a — swap")
    out = r.v1("stage-complete", "--artifact", r.artifact("requirements", docs["evaluate"]))
    assert out.get("ok") is True and r.state()["stage"] == "think", out


@_xfail("A5+A3", "a failing PLAN check writes no attempt; a decision after one breaks numbering")
def test_decision_after_a_failed_attempt_then_stage_completes(rig):
    r = rig("direct")
    r.create()
    r.v1("stage-start")
    r.edit(Rig.CHANGE)
    docs = documents("direct")
    missing = [{"id": "X1", "kind": "test", "ref": "backend/tests/test_missing.py::test_m",
                "covers": ["R1.1"]}]                      # DS-2a fails as a check
    failing = {"plan": {**docs["plan"], "design": {**docs["plan"]["design"],
                                                   "exit_criteria": missing}}}
    bad = r.v1("stage-complete", "--artifact", r.artifact("bad", failing["plan"]))
    assert bad.get("ok") is False and "stage_attempt" in r.kinds(), bad
    asked = r.v1("decision-request", "--artifact", _decision(r), "--document", "DESIGN_SPEC")
    assert asked.get("ok") is True, asked
    assert r.answer_card("a — swap")
    docs = documents("direct")
    out = r.v1("stage-complete", "--artifact", r.artifact("design", docs["plan"]))
    assert out.get("ok") is True and r.state()["stage"] == "build", out


def test_a_person_answers_and_the_agent_cannot(rig):
    r = rig("direct")
    r.create()
    asked = r.v1("decision-request", "--artifact", _decision(r), "--document", "DESIGN_SPEC")
    assert asked.get("ok") is True and r.state()["status"] == "awaiting_human"
    agent = r.v1("decision-answer", "--request-id", "q1", "--option", "a")
    assert agent.get("_rc") == 2 and "human_answer" not in r.kinds()
    api = r.bash(f"curl -s -X POST http://127.0.0.1:1/api/pipelines/{r.run_id}/decision")
    assert api.get("denied"), api
    assert r.answer_card("b — rewrite")
    assert r.kinds().count("human_answer") == 1 and r.state()["status"] == "active"


@_xfail("C3", "a CLI process can obtain the hook writer and sign a human_answer")
def test_a_cli_process_cannot_sign_as_the_hook(rig):
    import subprocess, sys
    from tests.pipeline_v1_rig import BACKEND, PROJECT
    r = rig("direct")
    r.create()
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from config import get_app_data_dir\n"
        "from core.run_protocol.engine import RunEngine\n"
        "import os\n"
        "e = RunEngine.load(data_dir=get_app_data_dir(), workspace=__import__('pathlib').Path(os.environ['SWARM_WORKSPACE']), project=%r, run_id=%r)\n"
        "e.authority.writer('hook')\n" % (str(BACKEND), PROJECT, r.run_id))
    proc = subprocess.run([sys.executable, "-c", code], env=r.env, capture_output=True, text=True)
    assert proc.returncode != 0, "the hook writer must not exist outside the daemon"


def test_user_stop_pauses_and_resume_continues(rig):
    r = rig("direct")
    r.create()
    assert r.stop_session() == [r.run_id]
    state = r.state()
    assert state["status"] == "paused", state
    assert r.v1("resume").get("ok") is True and r.state()["status"] == "active"


# ── a person's run-land ─────────────────────────────────────────────────────

def _not_landed_tests_red(r: Rig) -> None:
    r.create()
    r.drive(until="build")
    r.edit(BREAK_OTHER)
    state = r.drive()
    assert state["landing"] == "not_landed" and _reason(state) == "tests_red", state


def _prepare(r: Rig) -> dict:
    out = r.v1("run-land", "--prepare", "--skip-tests", "--note", "accepted by XG")
    assert out.get("ok") is True, out
    return out


def _request(r: Rig, prepared: dict) -> dict:
    request_id = prepared["prepared"]["request"]["request_id"]
    return r.v1("run-land", "--request", request_id, "--message", "fix: swap" + "\n\n"
                "Co-Authored-By: Swarm <swarm@swarmai.dev>")


@_xfail("B1+A1", "BUILD never passes; an expired approval leaves the land attempt open")
def test_expired_approval_is_refused_and_a_fresh_one_lands(rig, monkeypatch):
    from core import hook_token
    r = rig("direct")
    _not_landed_tests_red(r)
    base = _main(r)
    prepared = _prepare(r)
    monkeypatch.setattr(hook_token, "TOKEN_TTL_SECONDS", -1.0)
    refused = _request(r, prepared)
    assert refused.get("ok") is not True and _main(r) == base, refused
    assert r.v1("status").get("ok") is True, "the refused attempt must be closed"
    monkeypatch.setattr(hook_token, "TOKEN_TTL_SECONDS", 120.0)
    landed = _request(r, _prepare(r))
    assert landed.get("ok") is True and _main(r) != base, landed


@_xfail("B1+C1", "BUILD never passes; run-land trusts the editable land-request JSON")
def test_code_changed_after_prepare_cannot_land_even_with_edited_json(rig):
    import json
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    _not_landed_tests_red(r)
    base = _main(r)
    prepared = _prepare(r)
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return 42\n"})   # untested tree
    path = Path(prepared["path"])
    value = json.loads(path.read_text())
    from core.run_protocol import git_tree
    tree = git_tree.write_tree(str(r.worktree), (f".aidlc/deliverables/{r.run_id}/",), {})

    def retarget(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if key == "code_tree" and isinstance(item, str):
                    node[key] = tree
                else:
                    retarget(item)
        elif isinstance(node, list):
            for item in node:
                retarget(item)
    retarget(value)
    path.write_text(json.dumps(value))
    out = _request(r, prepared)
    assert out.get("ok") is not True and _main(r) == base, out


# ── invariant: whatever the command order, the log folds ────────────────────

_STEPS = ("stage-start", "complete-current", "stage-complete-empty", "tdd", "regression",
          "review-start", "land", "decision", "answer", "stop", "resume", "status")


def _step(r: Rig, step: str) -> None:
    if step == "complete-current":
        if r.state()["status"] == "active":
            r.complete_stage()
    elif step == "stage-complete-empty":
        r.v1("stage-complete")
    elif step in {"tdd", "regression"}:
        if r.worktree is not None:
            r.pt(step, *(["--files", "backend/tests/test_x.py"] if step == "tdd" else []))
    elif step == "land":
        r.v1("land", "--message", "fix: swap\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    elif step == "decision":
        r.v1("decision-request", "--artifact", _decision(r, f"q{len(r.events())}"),
             "--document", "DESIGN_SPEC")
    elif step == "answer":
        if r.v1("status").get("decision"):
            r.answer_card("a — swap")
    elif step == "stop":
        r.stop_session()
    else:
        r.v1(step)


@_xfail("A1-A9", "some command orders leave an open stage_attempt and the log stops folding")
def test_any_command_sequence_leaves_a_foldable_log(tmp_path, monkeypatch):
    import random
    rng = random.Random(20261022)
    failures = []
    for case in range(6):
        r = Rig(tmp_path / f"seq{case}", monkeypatch, profile="direct")
        try:
            r.create()
            sequence = [rng.choice(_STEPS) for _ in range(9)]
            for step in sequence:
                _step(r, step)
                if r.v1("status").get("ok") is not True:
                    failures.append((sequence, step))
                    break
        finally:
            r.close()
    assert not failures, failures
