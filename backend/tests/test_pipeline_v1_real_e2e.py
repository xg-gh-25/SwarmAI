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
    pytest.param(p) for p in CODE_PROFILES])
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


def test_tests_red_ends_not_landed_tests_red(rig):
    r = rig("trivial")   # direct's BX-2 runs every importer in BUILD, so no TEST-only red
    r.create()
    base = _main(r)
    r.drive(until="build")
    r.edit(BREAK_OTHER)            # outside the BUILD exit test, inside regression scope
    state = r.drive()
    assert state["status"] == "completed", state
    assert state["landing"] == "not_landed" and _reason(state) == "tests_red", state
    assert _main(r) == base


def test_red_then_green_rerun_lands(rig):
    r = rig("trivial")
    r.create()
    r.drive(until="build")
    r.edit(BREAK_OTHER)
    r.drive(until="test")
    r.pt("regression")             # red
    r.edit(FIX_OTHER)
    r.pt("regression")             # green on the current tree
    _landed_clean(r.drive())


def test_main_moves_disjoint_lands_on_new_main(rig):
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    moved = r.commit_on_main({"docs/notes.md": "unrelated\n"})
    _landed_clean(r.drive())
    from tests.pipeline_v1_rig import _git
    assert _git(r.repo, "merge-base", "--is-ancestor", moved, "main") == ""


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


def test_decision_after_a_failed_attempt_then_stage_completes(rig):
    r = rig("trivial")
    r.create()
    r.drive(until="plan")
    r.edit(Rig.CHANGE)
    docs = documents("trivial")
    missing = [{"id": "X1", "kind": "test", "ref": "backend/tests/test_missing.py::test_m",
                "covers": ["R1.1"]}]                      # DS-2a fails as a check
    bad = r.v1("stage-complete", "--artifact",
               r.artifact("bad", {**docs["plan"], "exit_criteria": missing}))
    assert bad.get("ok") is False and "stage_attempt" in r.kinds(), bad
    asked = r.v1("decision-request", "--artifact", _decision(r), "--document", "DESIGN_SPEC")
    assert asked.get("ok") is True, asked
    assert r.answer_card("a — swap")
    out = r.v1("stage-complete", "--artifact", r.artifact("design", docs["plan"]))
    assert out.get("ok") is True and r.state()["stage"] == "build", out


def test_a_person_answers_and_the_agent_cannot(rig):
    r = rig("trivial")
    r.create()
    asked = r.v1("decision-request", "--artifact", _decision(r), "--document", "DESIGN_SPEC")
    assert asked.get("ok") is True and r.state()["status"] == "awaiting_human"
    agent = r.v1("decision-answer", "--request-id", "q1", "--option", "a")
    assert agent.get("_rc") == 2 and "human_answer" not in r.kinds()
    api = r.bash(f"curl -s -X POST http://127.0.0.1:1/api/pipelines/{r.run_id}/decision")
    assert api.get("denied"), api
    assert r.answer_card("b — rewrite")
    assert r.kinds().count("human_answer") == 1 and r.state()["status"] == "active"


def test_a_cli_process_cannot_sign_as_the_hook(rig):
    """The CLI's own entry narrows the process to the cli writer before it runs any
    command, so nothing it executes afterwards can sign as the hook (C3). This is a
    code-level split: a process that never enters the CLI and reads the key itself is
    the separate daemon-signing design (fixing spec §9)."""
    import subprocess, sys
    from tests.pipeline_v1_rig import PV1
    r = rig("direct")
    r.create()
    code = (
        "import runpy, sys\n"
        "sys.argv = [%r, 'status', '--project', %r, '--run-id', %r]\n"
        "try:\n"
        "    runpy.run_path(%r, run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        "from core.run_protocol.events import EventAuthority, allow_hook_writer\n"
        "try:\n"
        "    allow_hook_writer()\n"
        "except Exception:\n"
        "    pass\n"
        "EventAuthority(b'k' * 32).writer('hook')\n"
        % (str(PV1), "P", r.run_id, str(PV1)))
    proc = subprocess.run([sys.executable, "-c", code], env=r.env, capture_output=True,
                          text=True, cwd=str(PV1.parents[1]))
    assert proc.returncode != 0 and "may not write as 'hook'" in proc.stderr, proc.stderr[-500:]

def test_user_stop_pauses_and_resume_continues(rig):
    r = rig("direct")
    r.create()
    assert r.stop_session() == [r.run_id]
    state = r.state()
    assert state["status"] == "paused", state
    assert r.v1("resume").get("ok") is True and r.state()["status"] == "active"


# ── a person's run-land ─────────────────────────────────────────────────────

def _not_landed_tests_red(r: Rig) -> None:
    assert r.profile == "trivial"
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


def test_expired_approval_is_refused_and_a_fresh_one_lands(rig, monkeypatch):
    from core import hook_token
    r = rig("trivial")
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


def test_code_changed_after_prepare_cannot_land_even_with_edited_json(rig):
    import json
    from tests.pipeline_v1_rig import _git
    r = rig("trivial")
    _not_landed_tests_red(r)
    base = _main(r)
    prepared = _prepare(r)
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return 42\n"})   # untested tree
    path = Path(prepared["path"])
    value = json.loads(path.read_text())
    from core.run_protocol import git_tree, tree
    repos = {"repo": str(r.worktree)}
    report_sha = value["report_sha"]
    report = (r.data / "state" / "pipeline_agent_audit" / "runs" / r.run_id / "blobs"
              / report_sha).read_bytes()
    code_trees = tree.build_code_trees(repos, r.run_id, git_tree.write_tree,
                                       git_tree.list_reserved)
    forged = tree.build_delivery_trees(repos, r.run_id, code_trees, report, report_sha,
                                       git_tree.write_tree, git_tree.list_reserved)
    value["bindings"] = {name: {key: row[key] for key in ("code_tree", "delivery_tree",
                                                         "report_sha")}
                         for name, row in forged.items()}       # the JSON now "matches"
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


# ── B4: probe exits run through the §A2.5 executor ─────────────────────────

def _probe_design(r: Rig, expect: dict) -> dict:
    design = dict(documents("trivial")["plan"])
    design["exit_criteria"] = [*design["exit_criteria"], {
        "id": "X2", "kind": "probe", "covers": ["R1.1"], "expect": expect,
        "argv": ["git", "grep", "-n", "-e", "return b + a", "--", "backend/core/"]}]
    return design


def test_a_probe_exit_runs_and_counts_by_its_row_id(rig):
    r = rig("trivial")
    r.create()
    r.drive(until="plan")
    r.edit(Rig.CHANGE)
    out = r.v1("stage-complete", "--artifact",
               r.artifact("design", _probe_design(r, {"rc": 0, "stdout": "nonempty"})))
    assert out.get("ok") is True, out
    probed = r.pt("probe")
    assert probed.get("ok") is True and probed["probes"][0]["exit"] == "X2", probed
    r.pt("tdd", "--files", "backend/tests/test_x.py")
    assert r.v1("stage-complete").get("ok") is True     # DS-9b sees X2 pass
    assert "test_call" in r.kinds()


def test_a_probe_whose_expectation_fails_blocks_build(rig):
    r = rig("trivial")
    r.create()
    r.drive(until="plan")
    r.edit(Rig.CHANGE)
    r.v1("stage-complete", "--artifact",
         r.artifact("design", _probe_design(r, {"rc": 1, "stdout": "empty"})))
    assert r.pt("probe").get("ok") is False
    r.pt("tdd", "--files", "backend/tests/test_x.py")
    out = r.v1("stage-complete")
    assert out.get("ok") is False and "X2" in str(out.get("checks")), out


def test_the_old_free_argv_probe_mode_is_gone(rig):
    r = rig("direct")
    r.create()
    out = r.pt("probe", "--name", "x", "--", "sh", "-c", "exit 0")
    assert out.get("ok") is not True and out.get("_rc") == 2, out


# ── N1: direct has no PLAN, no documents, no exit criteria ──────────────────

def test_direct_starts_at_build_and_needs_the_users_words(rig):
    r = rig("direct")
    refused = r.v1("create", "--project", "P", "--profile", "direct",
                   "--repo", f"repo={r.repo}")
    assert refused.get("ok") is False and "--requirement" in refused.get("error", ""), refused
    out = r.create()
    assert "plan" not in out["state"]["stages"] and out["state"]["stage"] == "build"


def test_a_doc_only_direct_run_lands_without_any_document(rig):
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    r.create(requirement="Fix the README typo.")
    base = _main(r)
    r.edit({"README.md": "# readme\n\nfixed\n"})
    assert r.v1("stage-complete").get("ok") is True          # BX-2: pure doc, no tdd
    _landed_clean(r.drive())
    assert _main(r) != base and "fixed" in (r.repo / "README.md").read_text()
    report = (r.repo / f".aidlc/deliverables/{r.run_id}/REPORT.md").read_text()
    assert "Fix the README typo." in report
    assert _git(r.repo, "log", "-1", "--format=%B").rstrip().endswith(
        "Co-Authored-By: Swarm <swarm@swarmai.dev>")


def test_a_direct_code_change_needs_passing_tdd_to_leave_build(rig):
    r = rig("direct")
    r.create()
    r.edit({"backend/core/mod.py": "def add(a, b):\n    return a - b\n"})   # red
    out = r.v1("stage-complete")
    assert out.get("ok") is False and "tdd" in str(out.get("checks")), out   # nothing run
    r.pt("tdd", "--files", "backend/tests/test_x.py")
    out = r.v1("stage-complete")
    assert out.get("ok") is False and "failed" in str(out.get("checks")), out
    r.edit(Rig.CHANGE)                                                       # green
    r.pt("tdd", "--files", "backend/tests/test_x.py")
    assert r.v1("stage-complete").get("ok") is True and r.state()["stage"] == "review"


def test_one_passing_node_does_not_stand_for_its_file(rig):
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    r.pt("tdd", "--files", "backend/tests/test_x.py::test_add")       # a single node
    out = r.v1("stage-complete")
    assert out.get("ok") is False and "not run whole" in str(out.get("checks")), out
    r.pt("tdd", "--files", "backend/tests/test_x.py")
    assert r.v1("stage-complete").get("ok") is True


def test_a_direct_run_cannot_ask_and_its_log_still_folds(rig):
    r = rig("direct")
    r.create()
    before = len(r.events())
    out = r.v1("decision-request", "--artifact", _decision(r), "--document", "REQUIREMENTS")
    assert out.get("ok") is False and "trivial" in out.get("error", ""), out
    assert len(r.events()) == before and r.state()["stage"] == "build"


# ── C1 / C6: execution reads the signed land_request event, once ────────────

def test_landing_reads_its_request_from_the_event_not_the_json(rig):
    r = rig("trivial")
    _not_landed_tests_red(r)
    base = _main(r)
    prepared = _prepare(r)
    Path(prepared["path"]).unlink()                 # the JSON is a display copy only
    landed = _request(r, prepared)
    assert landed.get("ok") is True and _main(r) != base, landed


def test_the_approval_card_shows_what_lands(rig):
    from core import security_hooks
    r = rig("trivial")
    _not_landed_tests_red(r)
    prepared = _prepare(r)
    request = prepared["prepared"]["request"]
    command = f"python backend/scripts/pipeline_v1.py run-land --project P --run-id {r.run_id} " \
              f"--request {request['request_id']} --message m"
    card = security_hooks.v1_land_card(command)
    row = request["bindings"]["repos"]["repo"]
    assert r.run_id in card and "skip tests: yes" in card, card
    assert row["code_tree"][:12] in card and row["delivery_tree"][:12] in card, card
    assert "accepted by XG" in card, card


def test_an_approval_is_consumed_once_and_a_second_try_is_integrity(rig):
    from core.run_protocol import landing
    r = rig("trivial")
    _not_landed_tests_red(r)
    prepared = _prepare(r)
    assert _request(r, prepared).get("ok") is True
    engine = r.engine()
    request_id = prepared["prepared"]["request"]["request_id"]
    with pytest.raises(landing.LandingError, match="already consumed"):
        landing.consume_authorization(
            engine.store, request_id, session_id=engine.owner_session(),
            bindings=prepared["prepared"]["request"]["bindings"]["repos"],
            at="2026-10-23T00:00:00Z", now=__import__("time").time())
    assert any(e["kind"] == "integrity" and e["payload"]["conflict_type"] == "auth_consumed_twice"
               for e in r.events())


# ── C5: manual landing without a final, and its errors are JSON ─────────────

def test_skip_tests_lands_a_run_that_never_ran_tests(rig):
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    assert r.v1("abandon", "--reason", "XG takes it from here").get("ok") is True
    base = _main(r)
    landed = _request(r, _prepare(r))
    assert landed.get("ok") is True and _main(r) != base, landed


def test_a_guard_failure_on_prepare_is_json_not_a_traceback(rig):
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    assert r.v1("abandon", "--reason", "stop").get("ok") is True
    (r.repo / "other.txt").write_text("moved\n")
    _git(r.repo, "add", "other.txt"); _git(r.repo, "commit", "-qm", "main moves")
    out = r.v1("run-land", "--prepare", "--skip-tests", "--note", "n")
    assert out.get("ok") is False and "LG-4" in out.get("error", ""), out


def test_a_tested_prepare_on_an_untested_tree_names_the_runner(rig):
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    assert r.v1("abandon", "--reason", "stop").get("ok") is True
    out = r.v1("run-land", "--prepare", "--note", "n")
    assert out.get("ok") is False and "--mode regression" in out.get("error", ""), out


# ── A7 / C4: a landing must come through the hook; mode is a setting ────────

def test_a_land_without_the_hook_ends_not_landed_needs_hooked_session(rig):
    import subprocess, sys
    from tests.pipeline_v1_rig import PV1
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    base = _main(r)
    argv = [sys.executable, str(PV1), "land", *r._run_args(), "--message",
            "fix\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>"]
    out, _ = r._exec(argv)                                      # straight, no hook
    assert out.get("reason") == "needs_hooked_session", out
    assert _main(r) == base and r.state()["landing"] == "not_landed"


def test_create_has_no_mode_option(rig):
    r = rig("direct")
    out = r.v1("create", "--project", "P", "--profile", "direct", "--mode", "shared",
               "--requirement", "x", "--repo", f"repo={r.repo}")
    assert out.get("ok") is not True, out


def test_an_agent_cannot_write_the_runs_deliverables(rig):
    from core import worktree_write_gate as wg
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    own = wg._Own(r.run_id, [("repo", str(r.worktree))])
    def verdict(path):
        out = wg.judge({"tool_name": "Write", "tool_input": {"file_path": str(path)}}, own)
        return ((out or {}).get("hookSpecificOutput") or {}).get("permissionDecision")
    assert verdict(r.worktree / ".aidlc" / "deliverables" / r.run_id / "REPORT.md") == "deny"
    assert verdict(r.worktree / "backend" / "core" / "mod.py") is None


def test_a_second_landing_of_the_same_run_is_refused_while_one_runs(rig):
    import fcntl
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    base = _main(r)
    lock = r.data / "state" / "pipeline_agent_audit" / "runs" / r.run_id / "landing.lock"
    with open(lock, "a+") as held:
        fcntl.flock(held, fcntl.LOCK_EX)                 # another process is landing
        out = r.v1("land", "--message", "fix\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    assert out.get("ok") is False and "in progress" in out.get("error", ""), out
    assert _main(r) == base


def test_the_cli_has_no_way_to_answer_a_decision(rig):
    """C2 (fc6d2ab2): only the daemon records a person's answer."""
    r = rig("trivial")
    r.create()
    asked = r.v1("decision-request", "--artifact", _decision(r), "--document", "REQUIREMENTS")
    assert asked.get("ok") is True, asked
    out = r.v1("decision-answer", "--request-id", "q1", "--option", "a")
    assert out.get("ok") is not True
    assert "human_answer" not in r.kinds() and r.state()["status"] == "awaiting_human"


# ── B6: the run's base, not the worktree HEAD ───────────────────────────────

def test_a_commit_in_the_worktree_does_not_move_the_base_or_hide_the_change(rig):
    import json
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    # the agent commits its change and drops the validation rule in the same commit
    (r.worktree / "validations.json").write_text(json.dumps({"schema": 1, "entries": []}))
    _git(r.worktree, "add", "-A"); _git(r.worktree, "commit", "-qm", "wip")
    out = r.pt("tdd", "--files", "backend/tests/test_x.py")
    final = json.loads(Path(out["v1"]["final"]).read_text())
    base = _git(r.repo, "rev-parse", "main")
    assert final.get("base") == base, (final.get("base"), base)
    # the committed change is still the run's change: QX-2 sees the rule edit
    changed = r.engine().check_context().run["changed_paths"]
    assert {"validations.json", "backend/core/mod.py"} <= set(changed), changed
    from core.run_protocol import checks
    assert checks.evaluate("QX-2", r.engine().check_context())[0] is False
    assert r.v1("stage-complete").get("ok") is True   # BX-2 sees the committed change


# ── B8: DS-2b asks pytest what it collects ──────────────────────────────────

@pytest.mark.parametrize("ref,passes", [
    ("backend/tests/test_other.py::test_other", True),     # exists, never run in tdd
    ("backend/tests/test_x.py::test_missing", False),      # named, does not exist
])
def test_build_collects_the_designs_failure_mode_tests(rig, ref, passes):
    r = rig("full")
    r.create()
    r.drive(until="plan")
    assert r.v1("stage-start").get("ok") is True
    r.edit(Rig.CHANGE)
    design = documents("full")["plan"]
    design = {**design, "failure_modes": [{**design["failure_modes"][0], "test": ref}]}
    out = r.v1("stage-complete", "--artifact", r.artifact("design", design))
    assert out.get("ok") is True, out                    # DS-2b is a BUILD check
    r.pt("tdd", "--files", "backend/tests/test_x.py")
    out = r.v1("stage-complete")
    checks_out = {c["id"]: c for c in out.get("checks") or []}
    assert (out.get("ok") is True) is passes, out
    if not passes:
        assert checks_out["DS-2b"]["passed"] is False, checks_out.get("DS-2b")


# ── F-6: an expert with no usable output is dispatched once more, never twice ──

def test_an_unparsed_review_answer_is_redispatched_once(rig):
    r = rig("trivial")
    r.create()
    r.drive(until="review")
    started = r.v1("review-start")
    handouts = started.get("handouts") or []
    assert handouts, started
    r.spawn_reviewer(handouts[0]["prompt"], "I looked around.", "review-a")   # no envelope
    again = r.v1("review-start")
    assert [h["event"]["attempt"] for h in again["handouts"]] == [2], again
    r.spawn_reviewer(again["handouts"][0]["prompt"], "still nothing", "review-b")
    assert r.v1("review-start")["handouts"] == []        # no third dispatch


def test_a_committed_or_new_file_is_in_the_review_diff(rig):
    from tests.pipeline_v1_rig import _git
    r = rig("trivial")
    r.create()
    r.drive(until="review")
    (r.worktree / "backend" / "core" / "new_mod.py").write_text("def n():\n    return 2\n")
    _git(r.worktree, "add", "backend/core/mod.py"); _git(r.worktree, "commit", "-qm", "wip")
    started = r.v1("review-start")
    prompt = "\n".join(h["prompt"] for h in started.get("handouts") or [])
    roster = [row["id"] for row in started.get("reviewers") or []]
    assert started.get("ok") is True, started
    assert roster, "a new code file always gets a reviewer"
    assert "new_mod.py" in prompt and "return b + a" in prompt, prompt[-800:]


def test_the_final_records_the_rule_versions_it_selected_with(rig):
    import json
    from core.run_protocol.risk_selection import RISK_RULES_VERSION
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    out = r.pt("tdd", "--files", "backend/tests/test_x.py")
    final = json.loads(Path(out["v1"]["final"]).read_text())
    assert final["provenance"]["rules_version"].endswith("/" + RISK_RULES_VERSION), \
        final["provenance"].get("rules_version")


def test_the_base_follows_the_recorded_branch_not_the_main_checkout(rig):
    import json
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    base = _git(r.repo, "rev-parse", "main")
    _git(r.repo, "checkout", "-q", "-b", "elsewhere")          # the person switches branch
    (r.repo / "elsewhere.txt").write_text("x\n")
    _git(r.repo, "add", "."); _git(r.repo, "commit", "-qm", "unrelated")
    out = r.pt("tdd", "--files", "backend/tests/test_x.py")
    assert json.loads(Path(out["v1"]["final"]).read_text())["base"] == base
    changed = r.engine().check_context().run["changed_paths"]
    assert "elsewhere.txt" not in changed, changed
    _git(r.repo, "checkout", "-q", "main")
