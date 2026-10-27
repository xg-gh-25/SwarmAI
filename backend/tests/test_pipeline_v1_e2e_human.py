"""Pipeline v1 real end-to-end tests: Gate-0, decisions and the person in the loop; ownership and hooks; approvals.

Split from test_pipeline_v1_real_e2e.py (design 2026-10-27-pipeline-hygiene §2);
shared fixture and helpers: tests/pipeline_v1_e2e_common.py. Invariant checked
after every case: the run's event log folds (``status`` succeeds).
"""
from __future__ import annotations

import pytest

from tests.pipeline_v1_rig import Rig, documents
from tests.pipeline_v1_e2e_common import (  # noqa: F401  (rig is a fixture)
    CODE_PROFILES, _decision, _landed_clean, _main, _not_landed_tests_red, _prepare,
    _reason, _request, rig)

pytestmark = pytest.mark.timeout(300)


# ── Gate-0, decisions, the person in the loop ───────────────────────────────

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




def test_user_stop_pauses_and_resume_continues(rig):
    r = rig("direct")
    r.create()
    assert r.stop_session() == [r.run_id]
    state = r.state()
    assert state["status"] == "paused", state
    assert r.v1("resume").get("ok") is True and r.state()["status"] == "active"


# ── A7 / C4: a landing must come through the hook; mode is a setting ────────

def test_a_land_from_a_session_that_does_not_own_the_run_changes_nothing(rig):
    """S6-3: no token, no daemon; a command acts only for the run's owner session."""
    from tests.pipeline_v1_rig import pv1_argv
    r = rig("direct")
    r.create()
    r.drive(until="deliver")
    base = _main(r)
    argv = [*pv1_argv(r.program), "land", *r._run_args(), "--message",
            "fix\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>"]
    out, _ = r._exec(argv, extra_env={"SWARM_APP_SESSION_ID": "someone-else"})
    assert out.get("code") == "not_owner", out
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
