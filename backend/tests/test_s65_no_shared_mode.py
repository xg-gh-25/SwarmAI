"""S6-5b (design 2026-10-24-swarmai-usable-by-anyone.md §7): the shared mode is gone.
Every pipeline run's code is in its own git worktree per repo; nothing turns that off.

What is tested:
- ``run_worktree.mode_enabled`` and ``$SWARM_WORKTREE_MODE`` are gone: no product
  module reads the variable, so setting it changes nothing;
- ``RunEngine.create`` takes no mode; every contract says ``isolated``; a code
  profile without a repo is refused (a shared run used to allow that);
- the landing has no in-place path (``in_place``, ``_land_in_place``) and the engine
  no shared-run reservation (``_live_shared_holder``).
"""
from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

from core import run_worktree
from core.run_protocol import landing
from core.run_protocol.engine import EngineError, RunEngine

BACKEND = Path(__file__).resolve().parents[1]


def test_no_switch_is_left():
    assert not hasattr(run_worktree, "mode_enabled")
    readers = subprocess.run(["git", "grep", "-l", "SWARM_WORKTREE_MODE", "--", "."],
                             cwd=BACKEND, capture_output=True, text=True).stdout.split()
    assert [p for p in readers if not p.startswith("tests/")] == []


def test_create_takes_no_mode_and_every_contract_is_isolated(tmp_path, monkeypatch):
    assert "mode" not in inspect.signature(RunEngine.create).parameters
    monkeypatch.setenv("SWARM_WORKTREE_MODE", "off")      # no effect any more
    engine = RunEngine.create(data_dir=tmp_path / "d", workspace=tmp_path / "w",
                              project="P", profile="research", session_id="s1")
    assert engine.contract["mode"] == "isolated"
    with pytest.raises(EngineError, match="at least one repo"):
        RunEngine.create(data_dir=tmp_path / "d", workspace=tmp_path / "w", project="P",
                         profile="direct", requirement="x", session_id="s1")


def test_no_in_place_landing_or_shared_reservation():
    for fn in (landing.commit_repos, landing.execute_request, landing._prepare_commit):
        assert "in_place" not in inspect.signature(fn).parameters, fn.__name__
    assert not hasattr(landing, "_land_in_place")
    assert not hasattr(RunEngine, "_live_shared_holder")
