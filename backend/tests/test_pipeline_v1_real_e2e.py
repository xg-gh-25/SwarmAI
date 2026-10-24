"""Pipeline v1 real end-to-end tests (fixing spec §2, group G).

Every run here is driven only through ``pipeline_v1.py`` and the
real hook functions (``tests/pipeline_v1_rig.py``); no test appends an event. Each
negative path asserts the outcome the v13 design requires; every fixing-spec item
has landed, so no case is marked as expected to fail.

Invariant checked after every case: the run's event log folds (``status`` succeeds).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig, documents

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
    r.edit(Rig.CHANGE)
    for _ in range(3):
        r.v1("stage-complete")          # no report: BX-3 fails each attempt
    state = r.drive()
    assert state["status"] == "completed", state
    assert state["landing"] == "not_landed" and _reason(state) == "checks_exhausted", state
    assert _main(r) == base, "an exhausted run must not land"
    assert "render" in r.kinds(), "the not-landed REPORT is rendered by the engine"


# ── TEST red, a rerun green, main moving, merge conflict ────────────────────



def test_a_failed_test_report_ends_not_landed(rig):
    r = rig("trivial")
    base = _main(r)
    _not_landed_tests_red(r)
    assert r.state()["status"] == "completed"
    assert _main(r) == base


def test_main_moves_disjoint_lands_on_new_main(rig):
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    moved = r.commit_on_main({"docs/notes.md": "unrelated\n"})
    _landed_clean(r.drive())
    from tests.pipeline_v1_rig import _git
    assert _git(r.repo, "merge-base", "--is-ancestor", moved, "main") == ""



def test_main_moves_and_the_synced_tree_is_tested_again_before_it_lands(rig):
    """S6-1: the landing rebases the worktree onto the moved main; the TEST report is
    for the old tree, so LG-1 fails and next asks for report-tests on the new tree."""
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"lessons": []}))
    moved = r.commit_on_main({"docs/notes.md": "unrelated\n"})
    out = r.v1("land", "--message", "fix: swap\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    assert _main(r) == moved and r.state()["status"] == "active", out
    assert "report-tests" in out["next"]["command"], out["next"]
    r.v1("report-tests", "--artifact", r.artifact("report", r.report()))
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


def test_the_cli_process_holds_no_key_and_no_engine(rig):
    """B-class B-b: the CLI only sends its command to the daemon. Its module imports
    no engine, store or key code, so nothing in the agent's process signs (C3 by
    construction, no longer by narrowing the process)."""
    import subprocess, sys
    from tests.pipeline_v1_rig import PV1
    code = ("import runpy, sys\n"
            "sys.argv = [%r, '--help']\n"
            "try:\n"
            "    runpy.run_path(%r, run_name='not_main')\n"
            "except SystemExit:\n"
            "    pass\n"
            "bad = [m for m in sys.modules if m in ('core.run_protocol.engine', "
            "'core.run_protocol.store', 'core.run_protocol.keys')]\n"
            "print(bad)\n" % (str(PV1), str(PV1)))
    proc = subprocess.run([sys.executable, "-c", code], env=rig("direct").env,
                          capture_output=True, text=True, cwd=str(PV1.parents[1]))
    assert proc.stdout.strip().splitlines()[-1] == "[]", proc.stdout + proc.stderr


def test_user_stop_pauses_and_resume_continues(rig):
    r = rig("direct")
    r.create()
    assert r.stop_session() == [r.run_id]
    state = r.state()
    assert state["status"] == "paused", state
    assert r.v1("resume").get("ok") is True and r.state()["status"] == "active"


# ── a person's run-land ─────────────────────────────────────────────────────

def _not_landed_tests_red(r: Rig) -> None:
    """The agent reports a failing test in TEST three times (S6-1): the stage's checks
    are exhausted and the run ends not_landed, nothing committed."""
    assert r.profile == "trivial"
    r.create()
    r.drive(until="test")
    for _ in range(3):
        r.report_and_complete("fail")
    state = r.drive()
    assert state["landing"] == "not_landed" and _reason(state) == "checks_exhausted", state


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
    monkeypatch.setattr(hook_token, "TOKEN_TTL_SECONDS", 120.0)
    assert r.v1("status").get("ok") is True, "the refused attempt must be closed"
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

_STEPS = ("push-status", "complete-current", "stage-complete-empty", "report", "report-fail",
          "review-start", "land", "decision", "answer", "stop", "resume", "status")


def _step(r: Rig, step: str) -> None:
    if step == "complete-current":
        if r.state()["status"] == "active":
            r.complete_stage()
    elif step == "stage-complete-empty":
        r.v1("stage-complete")
    elif step in {"report", "report-fail"}:
        if r.worktree is not None:
            r.report_and_complete("fail" if step == "report-fail" else "pass")
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
    nothing = {"reports": [{"repo": "repo", "commands": [], "tests_changed": [],
                            "rationale": "a README typo: nothing to test"}]}
    assert r.v1("stage-complete", "--artifact", r.artifact("doc", nothing)).get("ok") is True
    _landed_clean(r.drive())
    assert _main(r) != base and "fixed" in (r.repo / "README.md").read_text()
    report = (r.repo / f".aidlc/deliverables/{r.run_id}/REPORT.md").read_text()
    assert "Fix the README typo." in report
    assert _git(r.repo, "log", "-1", "--format=%B").rstrip().endswith(
        "Co-Authored-By: Swarm <swarm@swarmai.dev>")


def test_a_direct_code_change_needs_a_build_report_on_the_current_tree(rig):
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    out = r.v1("stage-complete")
    assert out.get("ok") is False and "BX-3" in str(out.get("checks")), out   # no report
    assert r.report_and_complete().get("ok") is True and r.state()["stage"] == "review"


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



def test_a_tested_prepare_on_an_untested_tree_asks_for_a_test_report(rig):
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    assert r.v1("abandon", "--reason", "stop").get("ok") is True
    out = r.v1("run-land", "--prepare", "--note", "n")
    assert out.get("ok") is False and "report-tests" in out.get("error", ""), out


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
    # no token: the daemon runs nothing at all (B-class B-b), so the run is untouched
    assert out.get("code") == "no_token", out
    assert _main(r) == base and r.state()["stage"] == "deliver"


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
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    _git(r.worktree, "add", "-A"); _git(r.worktree, "commit", "-qm", "wip")
    engine = r.engine()
    assert engine._fork_base("repo", str(r.worktree)) == _git(r.repo, "rev-parse", "main")
    # the committed change is still the run's change
    assert "backend/core/mod.py" in engine.check_context().run["changed_paths"]
    assert r.report_and_complete().get("ok") is True     # BX-3 sees the committed change


# ── B8: DS-2b asks pytest what it collects ──────────────────────────────────

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


def test_the_base_follows_the_recorded_branch_not_the_main_checkout(rig):
    from tests.pipeline_v1_rig import _git
    r = rig("direct")
    r.create()
    r.edit(Rig.CHANGE)
    base = _git(r.repo, "rev-parse", "main")
    _git(r.repo, "checkout", "-q", "-b", "elsewhere")          # the person switches branch
    (r.repo / "elsewhere.txt").write_text("x\n")
    _git(r.repo, "add", "."); _git(r.repo, "commit", "-qm", "unrelated")
    engine = r.engine()
    assert engine._fork_base("repo", str(r.worktree)) == base
    assert "elsewhere.txt" not in engine.check_context().run["changed_paths"]
    _git(r.repo, "checkout", "-q", "main")


# ── D4: an approved dangerous command is a required approval of the run ─────

def test_an_approved_dangerous_command_counts_as_a_required_approval(rig):
    from core import security_hooks
    from tests.pipeline_v1_rig import SESSION
    r = rig("direct")
    r.create()
    security_hooks._count_required_approval(SESSION)
    rows = [e for e in r.events() if e["kind"] == "metric"
            and e["payload"]["name"] == "required_approval"]
    assert len(rows) == 1 and rows[0]["producer"] == "hook"
    audit = r.engine().build_audit(through="DELIVER", persist=False)
    assert audit["summary"]["autonomy"]["required_approvals"] == 1


def test_create_records_the_sessions_own_transcript(rig, monkeypatch):
    r = rig("direct")
    r.env["CLAUDE_CODE_SESSION_ID"] = "0123abcd-0000-4000-8000-00000000abcd"
    r.create()
    assert "0123abcd-0000-4000-8000-00000000abcd" in r.engine().transcript_ids()


# ── D1: REFLECT is the daemon's background work ─────────────────────────────

def test_the_agent_cannot_complete_reflect_and_the_daemon_does(rig):
    from core.run_protocol import reflect_job
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"lessons": []}))
    r.v1("land", "--message", "fix: swap the operands\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    assert r.state()["status"] == "reflecting"
    out = r.v1("reflect-complete")
    assert out.get("ok") is not True                       # the command is gone
    reflect_job.process_run(r.engine())
    state = r.state()
    assert state["status"] == "completed" and state.get("reflect") in ("ok", None), state
    complete = [e for e in r.events() if e["kind"] == "reflect_complete"]
    assert len(complete) == 1 and complete[0]["producer"] == "hook"


def test_lessons_cite_events_listed_by_the_cli_and_are_routed_with_an_outcome(rig):
    import json
    from core.run_protocol import reflect_job
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    ids = [e["event_id"] for e in r.v1("events")["events"]]
    lesson = {"id": "L1", "type": "pitfall", "scope": "instance",
              "statement": "Run the whole test file, not one node, before leaving BUILD.",
              "tell": "BX-2 refused a single node", "evidence": ids[:1],
              "relation": {"kind": "new", "entry": None}}
    out = r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"lessons": [lesson]}))
    assert out.get("ok") is True, out
    r.v1("land", "--message", "fix: swap the operands\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    reflect_job.process_run(r.engine())
    doc = json.loads((r.engine().run_dir / "artifacts" / "reflect.json").read_text())
    outcome = doc["lessons"][0]["outcome"]
    assert outcome["status"] in ("applied", "noise", "suspect"), outcome


def test_measuring_after_the_daemon_completed_reflect_refreshes_the_final_audit(rig, monkeypatch):
    import json
    from core.run_protocol import reflect_job, usage
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"lessons": []}))
    r.v1("land", "--message", "fix: swap the operands\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>")
    reflect_job.process_run(r.engine())
    engine = r.engine()
    audit = json.loads((engine.run_dir / "run_audit.json").read_text())
    token = audit["summary"]["token_cost"]
    # the daemon found no transcript when REFLECT ended (r6 §2.3), and the review
    # expert it spawned was not metered either: sub-agent tokens are uncounted (§E5)
    assert token["verdict"] == "BLOCK" and token["not_available"], token
    monkeypatch.setattr(usage.transcript_usage, "resolve_transcripts", lambda ids: ["t"])
    monkeypatch.setattr(usage.transcript_usage, "usage_in_window", lambda *a, **k: {
        "input_tokens": 10, "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "output_tokens": 5, "responses": 2})
    engine.measure_usage()
    audit = json.loads((engine.run_dir / "run_audit.json").read_text())
    assert audit["summary"]["token_cost"]["verdict"] != "BLOCK", audit["summary"]["token_cost"]
    assert audit["summary"]["token_cost"]["not_available"] is None


def test_a_manual_landing_resumes_after_a_crash_past_auth_consumed(rig):
    """Re-audit ca91762f HIGH: auth_consumed recorded, refs moved, then the process
    died before the land event. The retry of the same request must finish the
    landing (append the missing land event), not stop at "already consumed"."""
    r = rig("trivial")
    _not_landed_tests_red(r)
    prepared = _prepare(r)
    assert _request(r, prepared).get("ok") is True
    landed_main = _main(r)
    log = r.data / "state" / "pipeline_agent_audit" / "runs" / r.run_id / "events.jsonl"
    lines = log.read_text().splitlines(keepends=True)
    kinds = [json.loads(x)["kind"] for x in lines]
    cut = max(i for i, k in enumerate(kinds) if k == "land")
    assert "auth_consumed" in kinds[:cut]
    log.write_text("".join(lines[:cut]))           # the crash: no land event, refs moved
    again = _request(r, prepared)
    assert again.get("ok") is True, again
    assert _main(r) == landed_main                  # nothing landed twice
    assert [e["kind"] for e in r.events()].count("land") == 1
