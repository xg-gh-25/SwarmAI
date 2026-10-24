"""Commit 4 (r6 §2.3): every command tells the agent what comes next.

Methodology: the real CLI, runner and engine on the v1 rig. Invariants:
- every response carries ``state``, ``repos`` and ``next``; ``next`` carries the stage
  card's text when the call changed the stage;
- there is no ``stage-start`` or ``measure-usage``: the call that enters a code stage
  makes the worktrees; the daemon measures usage when REFLECT ends;
- ``--artifact -`` reads the artifact from stdin;
- a BUILD / TEST runner call whose results pass the stage's checks completes the stage;
  results that do not pass record no stage attempt and name the failing checks;
- a run with no transcript records ``metric(usage_not_available)``: Token Cost WARN,
  not BLOCK.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys

import pytest

from tests.pipeline_v1_rig import PROJECT, PV1, Rig, documents

pytestmark = pytest.mark.timeout(300)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    made: list[Rig] = []

    def make(profile: str = "direct", **kwargs) -> Rig:
        r = Rig(tmp_path / f"case{len(made)}", monkeypatch, profile=profile, **kwargs)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()


def test_create_enters_build_with_its_worktree_card_and_next(rig):
    r = rig()
    out = r.create()
    assert out["state"]["stage"] == "build"
    assert r.worktree is not None and r.worktree.is_dir() and r.worktree != r.repo
    step = out["next"]
    assert step["stage"] == "build" and "card_text" in step
    # S6-1: the agent tests natively and ends BUILD with its validation report
    assert step["action"] == "write_artifact" and "stage-complete --artifact -" in step["command"]
    assert '"reports"' in step["why"] and "pipeline_test" not in step["command"]
    status = r.v1("status")
    assert status["next"]["action"] == "write_artifact" and "card_text" not in status["next"]


def test_the_retired_commands_are_gone(rig):
    r = rig()
    r.create()
    for command in ("stage-start", "measure-usage"):
        proc = subprocess.run([sys.executable, str(PV1), command, "--project", PROJECT,
                               "--run-id", str(r.run_id)], env=r.env, capture_output=True,
                              text=True, timeout=60)
        assert proc.returncode == 2 and "invalid choice" in proc.stderr, command


def test_an_artifact_can_come_on_stdin(rig):
    """``--artifact -``: the CLI forwards its stdin to the daemon with the command."""
    r = rig("full")
    r.create()
    argv = [sys.executable, str(PV1), "stage-complete", "--project", PROJECT,
            "--run-id", str(r.run_id), "--artifact", "-"]
    body = json.dumps(documents("full")["evaluate"])
    out = r.bash(" ".join(map(shlex.quote, argv)) + f" <<'JSON'\n{body}\nJSON")
    assert out.get("ok") is True, out
    assert out["next"]["stage"] == "think" and "card_text" in out["next"]


def test_a_pipeline_command_without_the_hooks_token_is_refused(rig):
    """B-class B-b: run outside the session's hook (no token), nothing is executed."""
    r = rig()
    r.create()
    proc = subprocess.run([sys.executable, str(PV1), "abandon", "--project", PROJECT,
                           "--run-id", str(r.run_id), "--reason", "x"], env=r.env,
                          capture_output=True, text=True, timeout=60)
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert proc.returncode == 2 and out.get("code") == "no_token", out
    assert r.v1("status")["state"]["status"] == "active"


def test_a_build_report_completes_build_and_names_the_next_card(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    out = r.report_and_complete()
    assert out.get("ok") is True, out
    assert out["next"]["stage"] == "review" and "card_text" in out["next"]


def test_a_build_without_a_report_does_not_complete_and_names_bx3(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    out = r.v1("stage-complete")
    assert out.get("ok") is False and r.state()["stage"] == "build", out
    assert "BX-3" in json.dumps(out)


def test_reflect_without_a_transcript_records_usage_not_available(rig):
    r = rig()
    r.create()
    r.drive()
    events = r.events()
    missing = [e for e in events if e["kind"] == "metric"
               and e["payload"]["name"] == "usage_not_available"]
    assert len(missing) == 1 and missing[0]["payload"]["reason"]
    audit = json.loads((r.engine().run_dir / "run_audit.json").read_text())
    token = audit["summary"]["token_cost"] if "token_cost" in audit["summary"] \
        else audit["summary"]["token"]
    # the review expert this run spawned was not metered either: still BLOCK (§E5);
    # with only the main agent unknown it is WARN (test_unmetered_experts_…)
    assert token["verdict"] == "BLOCK" and token["not_available"]


def test_a_completion_decided_on_an_older_stage_is_refused(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    r.report_and_complete()                                     # BUILD → REVIEW
    out = r.v1("stage-complete", "--expect-stage", "build")
    assert out.get("ok") is False and "stale" in out.get("error", ""), out
    assert r.state()["stage"] == "review"


def test_a_failed_test_report_does_not_finish_test(rig):
    r = rig()
    r.create()
    r.drive(until="test")
    out = r.report_and_complete("fail")
    assert out.get("ok") is False and r.state()["stage"] == "test", out
    assert "failed command" in json.dumps(out)


def test_a_user_stop_waits_for_the_person(rig):
    r = rig()
    r.create()
    r.stop_session()
    step = r.v1("status")["next"]
    assert step["action"] == "wait" and "resume" not in step["command"], step


def test_review_dispatches_then_waits_then_completes(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    r.report_and_complete()
    started = r.v1("review-start")
    assert started.get("ok") is True and started["handouts"], started
    assert started["next"]["action"] == "dispatch", started["next"]
    assert r.v1("status")["next"]["action"] == "wait"
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], "NO FINDINGS", f"review-{i}")
    step = r.v1("status")["next"]
    assert step["action"] == "run" and " stage-complete " in step["command"] + " ", step


def test_unmetered_experts_still_block_token_cost():
    from core.run_protocol import audit
    base = [{"kind": "run_create", "at": "2026-10-23T00:00:00+00:00", "payload": {}},
            {"kind": "metric", "at": "2026-10-23T00:00:01+00:00", "producer": "cli",
             "payload": {"name": "usage_not_available", "value": 1, "reason": "none"}}]
    handout = {"kind": "handout", "at": "2026-10-23T00:00:02+00:00", "payload": {}}

    def verdict(events):
        return audit.build(
            run_id="run_x", projection={"run_id": "run_x", "status": "completed",
                                        "landing": "landed", "human_decisions": 0},
            events=events, final={}, findings=[], repos_isolated=1,
            contract={"run_id": "run_x", "mode": "isolated", "expected_repos": ["a"]},
            push={"push_ready": False, "reasons": {}},
            audit_through="COMPLETED")["summary"]["token_cost"]["verdict"]
    assert verdict(base) == "WARN"                     # only the main agent is unknown
    assert verdict([*base, handout]) == "BLOCK"        # an expert ran and nothing counts it


def test_an_unparsed_review_answer_routes_to_one_redispatch_not_to_address():
    """Re-audit 99f96949 MED: an expert answer that was not parsed produced no
    findings; `next` said review-address (nothing to address). It now says review-start
    while the one redispatch is still allowed, then stage-complete (RV checks decide)."""
    from types import SimpleNamespace
    from core.run_protocol import next_step as ns
    engine = SimpleNamespace(project="P", run_id="run_x")

    def h(eid, expert="correctness"):
        return {"kind": "handout", "stage": "review", "round": 1, "event_id": eid,
                "payload": {"expert_id": expert}}

    def a(hid, state):
        return {"kind": "answer", "stage": "review", "payload": {"handout_event_id": hid,
                                                                  "state": state}}
    blocking = [{"id": "RV-1a", "reason": "no usable answer"}]
    action, cmd, _ = ns._review_action(engine, [h("h1"), a("h1", "unparsed")], {}, blocking, None)
    assert action == "run" and "review-start" in cmd
    two = [h("h1"), a("h1", "unparsed"), h("h2"), a("h2", "unparsed")]
    action, cmd, _ = ns._review_action(engine, two, {}, blocking, None)
    assert action == "run" and "stage-complete" in cmd
    parsed = [h("h1"), a("h1", "parsed")]
    action, cmd, _ = ns._review_action(engine, parsed, {}, blocking, None)
    assert action == "fix" and "review-address" in cmd
