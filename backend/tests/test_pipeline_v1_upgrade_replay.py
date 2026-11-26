"""A run an older release recorded is read, cancelled and continued by this code.

The fixture ``fixtures/pipeline_v1_v2_5_5_run/`` is a bugfix run that the v2.5.5 backend
itself drove past TEST (``fixtures/pipeline_v1_v2_5_5_run_make.py``): its TEST attempt
names TS-1 and TS-4, the roster 2.5.5 had, and no roster version. 2.6.0 added FM-1 to
the TEST roster and compared every replayed attempt with today's roster, so every
command on such a run failed. The run's repo and worktree are rebuilt here as 2.5.5 had
them (the rig's files, the same base commit, the rig's change in the worktree); the
event log, blobs and contract are 2.5.5's own.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.pipeline_v1_rig import Rig, _git, write

pytestmark = pytest.mark.timeout(300)

FIXTURE = Path(__file__).parent / "fixtures" / "pipeline_v1_v2_5_5_run"


def _upgraded(tmp_path: Path, monkeypatch) -> Rig:
    """A rig holding the 2.5.5 run as an upgraded install finds it on disk."""
    meta = json.loads((FIXTURE / "fixture.json").read_text(encoding="utf-8"))
    run_id = meta["run_id"]
    # the same commit date as the fixture's: the rebuilt base is the commit the log names
    # and the rig's own identity, so landing needs no git config on the machine
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_DATE", meta["git_date"])
        monkeypatch.setenv(f"GIT_{who}_NAME", "t")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.invalid")
    rig = Rig(tmp_path, monkeypatch, profile="bugfix")
    audit = rig.data / "state" / "pipeline_agent_audit"
    shutil.copytree(FIXTURE / "audit", audit)
    run_dir = rig.ws / "Projects" / "P" / ".artifacts" / "runs" / run_id
    shutil.copytree(FIXTURE / "workspace_run", run_dir)
    # the repo and worktree paths, recorded under the generator's root, are this rig's
    for path in (audit / "runs" / run_id / "contract.json", run_dir / "run.json"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("<ROOT>", str(tmp_path)), encoding="utf-8")
    rig.run_id = run_id
    events = rig.events()
    bases = next(e["payload"]["bases"] for e in events if e["kind"] == "handout"
                 and e["stage"] == "review")
    assert _git(rig.repo, "rev-parse", "HEAD") == bases["repo"], \
        "the rebuilt repo is not the base the 2.5.5 log names (rig files or git date changed)"
    from core import run_worktree
    worktree = run_worktree.wt_path(run_id, "repo")
    _git(rig.repo, "worktree", "add", "-q", "-b", run_worktree.branch_name(run_id),
         str(worktree), "main")
    run_worktree.stamp_workspace(run_id, rig.ws)
    write(worktree, rig.CHANGE)
    rig.worktree = worktree
    # what 2.5.5 recorded: a TEST attempt without FM-1 and without a roster version
    test_attempt = next(e for e in events if e["kind"] == "stage_attempt"
                        and e["stage"] == "test")
    assert test_attempt["payload"] == {"check_ids": ["TS-1", "TS-4"]}
    return rig


def test_a_v2_5_5_run_past_test_is_read(tmp_path, monkeypatch):
    rig = _upgraded(tmp_path, monkeypatch)
    out = rig.v1("status")
    assert out.get("ok") is True, out
    state = out["state"]
    assert (state["status"], state["stage"]) == ("active", "deliver")
    assert state["stages"]["test"] == "completed"


def test_a_v2_5_5_run_past_test_is_cancelled(tmp_path, monkeypatch):
    rig = _upgraded(tmp_path, monkeypatch)
    out = rig.v1("abandon", "--reason", "upgraded mid-run")
    assert out.get("ok") is True, out
    assert rig.state()["status"] == "abandoned"


def test_a_v2_5_5_run_past_test_continues_and_lands(tmp_path, monkeypatch):
    rig = _upgraded(tmp_path, monkeypatch)
    base = _git(rig.repo, "rev-parse", "main")
    state = rig.drive()
    assert (state["status"], state["landing"]) == ("completed", "landed"), state
    assert _git(rig.repo, "rev-parse", "main") != base
    assert (rig.repo / "backend/core/mod.py").read_text().endswith("return b + a\n")
