"""A Hive releasing a Swarm Out task ends the pipeline runs its session owns
(core/hive_workspace.release with core/pipeline_human.end_session_runs).

When the Host discards a task it stops the session (a run waiting on a decision keeps
waiting) and then releases the order, which removes the task's clones. Before this, the
release never ended the run: it stayed ``awaiting_human`` for good, its decision stayed
listed, and a run worktree made from one of the removed clones could never be saved or
removed. Questions: (1) does a release end a run waiting in EVALUATE, with the person's
cancel reason, and remove the order; (2) does it end a run that has a run worktree while
the clone it was made from still exists, and remove that worktree; (3) when a run cannot
be ended, does the release refuse and keep everything.

Real objects: a v1 run driven through the shipped ``pipeline-v1`` CLI (tests/pipeline_v1_rig),
its repository a real git checkout where a work order keeps its writable clone. The order
file is written as ``prepare`` writes it (no mirror, no fetch: not what is tested here).
"""
from __future__ import annotations

import subprocess
import time

import pytest

from core import hive_workspace as hw, pipeline_human
from core.pipeline_metrics import USER_CANCEL_REASON
from tests.pipeline_v1_e2e_common import _decision
from tests.pipeline_v1_rig import SESSION, Rig

pytestmark = pytest.mark.timeout(300)

WID = "so_0123456789ab"


def _order_rig(tmp_path, monkeypatch) -> Rig:
    """A rig whose repository is the writable clone of work order WID, bound to the rig's
    session, with a deliverable in the order's outputs directory."""
    r = Rig(tmp_path, monkeypatch, profile="trivial")
    clone = r.ws / hw.repo_rel(WID, "repo")
    clone.parent.mkdir(parents=True)
    r.repo.rename(clone)
    r.repo = clone
    from core.repo_pipeline_config import SWARM_IDENTITY
    for key, value in zip(("user.name", "user.email"), SWARM_IDENTITY):  # as _clone_into sets
        subprocess.run(["git", "-C", str(clone), "config", key, value], check=True)
    (r.ws / hw.outputs_rel(WID)).mkdir(parents=True)
    (r.ws / hw.outputs_rel(WID) / "note.md").write_text("x")
    hw._write_order({"work_id": WID, "created_at": "2026-11-02T00:00:00Z",
                     "prepared_at": time.time(), "session_id": SESSION,
                     "repos": [{"name": "repo", "url": "file:///x", "ref": "main", "sha": "0" * 40,
                                "access": "write", "dir": hw.repo_rel(WID, "repo"),
                                "branch": hw.branch(WID)}],
                     "outputs": hw.outputs_rel(WID)})
    return r


def _ask(r: Rig, document: str) -> None:
    asked = r.v1("decision-request", "--artifact", _decision(r), "--document", document)
    assert asked.get("ok") is True, asked
    assert r.state()["status"] == "awaiting_human"
    # the Host's discard stops the session first: a waiting run keeps waiting
    assert pipeline_human.on_user_stop(SESSION) == []
    assert [p["run_id"] for p in pipeline_human.pending_decisions()] == [r.run_id]


def _order_gone(r: Rig) -> bool:
    return not (r.ws / "Hive" / "work" / WID).exists() \
        and not (r.ws / hw.outputs_rel(WID)).exists() and not hw._order_path(WID).exists()


def test_release_ends_a_run_waiting_in_evaluate(tmp_path, monkeypatch):
    r = _order_rig(tmp_path, monkeypatch)
    r.create()
    _ask(r, "REQUIREMENTS")
    assert hw.release(r.ws, WID, busy=lambda s: False,
                      end_runs=pipeline_human.end_session_runs) is True
    state = r.state()
    assert state["status"] == "abandoned" and state.get("reason") == USER_CANCEL_REASON, state
    assert pipeline_human.pending_decisions() == []
    assert _order_gone(r)


def test_release_ends_a_run_with_a_worktree(tmp_path, monkeypatch):
    r = _order_rig(tmp_path, monkeypatch)
    r.create()
    r.drive(until="plan")
    worktree = r.worktree
    assert worktree is not None and worktree.is_dir()
    _ask(r, "DESIGN_SPEC")
    clone_there = []

    def end_runs(session_id):
        clone_there.append((r.repo / ".git").is_dir())
        return pipeline_human.end_session_runs(session_id)

    assert hw.release(r.ws, WID, busy=lambda s: False, end_runs=end_runs) is True
    assert clone_there == [True]                   # ended while its main checkout existed
    state = r.state()
    assert state["status"] == "abandoned" and state.get("reason") == USER_CANCEL_REASON, state
    assert not worktree.exists()
    assert any(p.name == "worktree.bundle" for p in (tmp_path / "ledger").rglob("*")), \
        "abandon saved the worktree before removing it"
    assert pipeline_human.pending_decisions() == []
    assert _order_gone(r)


def test_release_keeps_everything_when_a_run_cannot_end(tmp_path, monkeypatch):
    r = _order_rig(tmp_path, monkeypatch)
    asked = []

    def end_runs(session_id):
        asked.append(session_id)
        raise RuntimeError("snapshot preservation failed")

    with pytest.raises(hw.WorkError) as err:
        hw.release(r.ws, WID, busy=lambda s: False, end_runs=end_runs)
    assert str(err.value) == hw.ERR_RUNS and asked == [SESSION]
    assert (r.repo / ".git").is_dir() and (r.ws / hw.outputs_rel(WID) / "note.md").is_file()
    assert hw.load(r.ws, WID) is not None
    # the sweep passes it through: the idle order stays too
    later = time.time() + (hw.RETENTION_DAYS + 1) * 86400
    assert hw.sweep(r.ws, lambda s: False, now=later, end_runs=end_runs) == 0
    assert hw.load(r.ws, WID) is not None and (r.repo / ".git").is_dir()
