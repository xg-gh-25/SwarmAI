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
    assert step["stage"] == "build" and "card_text" in step and step["action"] == "fix"
    assert "pipeline_test.py" in step["command"] and step["blocking"]
    status = r.v1("status")
    assert status["next"]["action"] == "fix" and "card_text" not in status["next"]


def test_the_retired_commands_are_gone(rig):
    r = rig()
    r.create()
    for command in ("stage-start", "measure-usage"):
        proc = subprocess.run([sys.executable, str(PV1), command, "--project", PROJECT,
                               "--run-id", str(r.run_id)], env=r.env, capture_output=True,
                              text=True, timeout=60)
        assert proc.returncode == 2 and "invalid choice" in proc.stderr, command


def test_an_artifact_can_come_on_stdin(rig):
    r = rig("full")
    r.create()
    proc = subprocess.run([sys.executable, str(PV1), "stage-complete", "--project", PROJECT,
                           "--run-id", str(r.run_id), "--artifact", "-"], env=r.env,
                          input=json.dumps(documents("full")["evaluate"]),
                          capture_output=True, text=True, timeout=120)
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out.get("ok") is True, out
    assert out["next"]["stage"] == "think" and "card_text" in out["next"]


def test_passing_build_results_complete_build_by_themselves(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    out = r.pt("tdd", "--files", "backend/tests/test_x.py")
    assert out.get("stage_complete", {}).get("ok") is True, out
    assert out["next"]["stage"] == "review" and "card_text" in out["next"]
    assert r.state()["stage"] == "review"


def test_failing_build_results_record_no_attempt_and_name_the_checks(rig):
    r = rig()
    r.create()
    r.edit({**Rig.CHANGE, "backend/tests/test_x.py":
            "from core.mod import add\n\n\ndef test_add():\n    assert add(1, 2) == 4\n"})
    before = [e for e in r.events() if e["kind"] == "stage_attempt"]
    out = r.pt("tdd", "--files", "backend/tests/test_x.py")
    assert "stage_complete" not in out, out
    assert out["next"]["action"] == "fix" and out["next"]["blocking"]
    assert [e for e in r.events() if e["kind"] == "stage_attempt"] == before
    assert r.state()["stage"] == "build"


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
    assert token["verdict"] == "WARN" and token["not_available"]


def test_a_completion_decided_on_an_older_stage_is_refused(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    r.pt("tdd", "--files", "backend/tests/test_x.py")           # BUILD → REVIEW
    out = r.v1("stage-complete", "--expect-stage", "build")
    assert out.get("ok") is False and "stale" in out.get("error", ""), out
    assert r.state()["stage"] == "review"


def test_another_sessions_test_call_completes_nothing(rig):
    r = rig()
    r.create()
    r.edit(Rig.CHANGE)
    out = r.pt("tdd", "--files", "backend/tests/test_x.py", session="someone-else")
    assert out["stage_complete"]["ok"] is False and r.state()["stage"] == "build", out


def test_a_red_regression_does_not_finish_test(rig):
    r = rig()
    r.create()
    r.drive(until="test")
    r.edit({"backend/core/other.py": "def other():\n    return 2\n"})
    out = r.pt("regression")
    assert "stage_complete" not in out and r.state()["stage"] == "test", out
    assert any(row["check"] == "final" for row in out["next"]["blocking"])


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
    r.pt("tdd", "--files", "backend/tests/test_x.py")
    started = r.v1("review-start")
    assert started.get("ok") is True and started["handouts"], started
    assert started["next"]["action"] == "dispatch", started["next"]
    assert r.v1("status")["next"]["action"] == "wait"
    for i, handout in enumerate(started["handouts"]):
        r.spawn_reviewer(handout["prompt"], "NO FINDINGS", f"review-{i}")
    step = r.v1("status")["next"]
    assert step["action"] == "run" and " stage-complete " in step["command"] + " ", step
