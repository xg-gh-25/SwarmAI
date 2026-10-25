"""T1 (design 2026-10-25-pipeline-standalone §4.6): REFLECT and the worktree cleanup
happen in the CLI, right after the landing, with no daemon.

What is tested (real CLI and hooks, ``tests/pipeline_v1_rig.py``; REFLECT is never run
by the test itself, unlike the rig's happy path):
- ``land`` ends the run ``completed`` in the same command: REFLECT recorded once, the
  lessons routed, the run's worktree and ``run/<id>`` branch gone;
- a run left ``reflecting`` (its CLI died after the landing) is finished by the next
  ``resume``, and ``next`` points there;
- REFLECT processing holds the run's operation lock, so a second processor (the
  daemon's worker, the next CLI call) never routes the same lessons again.
"""
from __future__ import annotations

import json
import threading

import pytest

from tests.pipeline_v1_rig import SWARM, Rig, _git

pytestmark = pytest.mark.timeout(300)



@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = Rig(tmp_path, monkeypatch, profile="direct")
    yield r
    r.close()


def _to_deliver(r: Rig) -> None:
    r.create()
    r.drive(until="deliver")
    ids = [e["event_id"] for e in r.v1("events")["events"]]
    lesson = {"id": "L1", "type": "pitfall", "scope": "instance",
              "statement": "Write the failing test before swapping operands.",
              "tell": "the swapped add passed review only with a test", "evidence": ids[:1],
              "relation": {"kind": "new", "entry": None}}
    out = r.v1("reflect-prepare", "--artifact", r.artifact("reflect", {"lessons": [lesson]}))
    assert out.get("ok") is True, out


def _reflect_events(r: Rig) -> list[dict]:
    return [e for e in r.events() if e["kind"] in ("reflect_complete", "reflect_exhausted")]


def test_land_completes_reflect_and_releases_the_worktree(rig):
    r = rig
    _to_deliver(r)
    worktree = r.worktree
    assert worktree is not None and worktree.is_dir()
    out = r.v1("land", "--message", "fix: swap the operands" + SWARM)
    assert out.get("ok") is True, out
    state = r.state()
    assert (state["status"], state["landing"]) == ("completed", "landed"), state
    assert len(_reflect_events(r)) == 1
    reflect = json.loads((r.engine().run_dir / "artifacts" / "reflect.json").read_text())
    assert [row["id"] for row in reflect["lessons"]] == ["L1"]
    assert (reflect["lessons"][0].get("outcome") or {}).get("status"), reflect
    assert not worktree.exists()
    assert f"run/{r.run_id}" not in _git(r.repo, "branch", "--list", f"run/{r.run_id}")
    assert len(_git(r.repo, "worktree", "list", "--porcelain").split("worktree ")) == 2
    assert out["next"]["why"] == "the run is completed", out["next"]


def test_a_run_left_reflecting_is_finished_by_resume(rig):
    r = rig
    _to_deliver(r)
    # the CLI died right after the landing: land in-process, without the CLI's REFLECT
    r.engine().normal_land(message="fix: swap the operands" + SWARM)
    assert r.state()["status"] == "reflecting"
    status = r.v1("status")
    assert "resume" in str(status["next"].get("command")), status["next"]
    out = r.v1("resume")
    assert out.get("ok") is True, out
    assert r.state()["status"] == "completed" and len(_reflect_events(r)) == 1


def test_reflect_processing_holds_the_runs_operation_lock(rig, monkeypatch):
    r = rig
    _to_deliver(r)
    r.engine().normal_land(message="fix: swap the operands" + SWARM)
    from core.run_protocol import oplock, reflect_job
    engine = r.engine()
    seen: list[str] = []
    real_route = reflect_job.route

    def route(*args, **kwargs):
        def other():
            try:
                with oplock.operation(engine.audit_root, engine.run_id, timeout=0.2):
                    seen.append("got the lock")
            except oplock.RunBusy:
                seen.append("busy")
        t = threading.Thread(target=other)
        t.start()
        t.join()
        return real_route(*args, **kwargs)
    monkeypatch.setattr(reflect_job, "route", route)
    assert reflect_job.process_run(engine) is not None
    assert seen == ["busy"], seen
    assert reflect_job.process_run(r.engine()) is None       # already completed
    assert len(_reflect_events(r)) == 1


def test_an_edit_after_the_landing_is_kept_in_a_snapshot(rig):
    r = rig
    _to_deliver(r)
    r.engine().normal_land(message="fix: swap the operands" + SWARM)
    worktree = r.worktree
    (worktree / "late.txt").write_text("written after the landing\n")
    out = r.v1("resume")
    assert out.get("ok") is True and r.state()["status"] == "completed", out
    assert not worktree.exists()
    from core.run_worktree import ledger_root
    snapshots = list(ledger_root().glob(f"{r.run_id}/**/*"))
    assert snapshots, "the late edit was removed without a snapshot"
