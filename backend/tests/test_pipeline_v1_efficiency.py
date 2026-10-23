"""Commit 2 (r6 §2.1): one command reads, verifies and computes each fact once.

Methodology: real AuditStore/RunEngine on temporary repos (the v1 rig) and a git
spy. Invariants:
- verifying the signed log is NOT cached: measured at 1.2 ms for a 56-event run, it
  is never worth trading integrity for (review f18dbe20);
- CodeFacts are computed once per command for the same place of the code and again
  after the command itself moves it.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.pipeline_v1_rig import Rig

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








def test_code_facts_are_computed_once_per_place_of_the_code(rig, monkeypatch):
    r = rig()
    r.create()
    r.drive(until="review")
    engine = r.engine()
    spawned = []
    real = subprocess.Popen.__init__

    def spy(self, args, *a, **k):
        if isinstance(args, (list, tuple)) and args and str(args[0]).endswith("git"):
            spawned.append(args)
        return real(self, args, *a, **k)
    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    first = engine.check_context()
    once = len(spawned)
    second = engine.check_context()
    assert len(spawned) == once                            # no second git round
    assert first.run["code_trees"] == second.run["code_trees"]
    engine._code_facts = None                              # as after the command moves code
    engine.check_context()
    assert len(spawned) > once


def test_one_diff_against_the_code_tree_sees_every_kind_of_change(rig):
    r = rig()
    r.create()
    r.drive(until="review")
    root = r.worktree
    (root / "backend" / "new_module.py").write_text("x = 1\n")          # untracked
    victim = root / "backend" / "core" / "other.py"
    victim.unlink()                                                    # deleted
    engine = r.engine()
    changed = engine.check_context().run["changed_paths"]
    assert "backend/new_module.py" in changed
    assert str(victim.relative_to(root)) in changed
    assert "backend/core/mod.py" in changed                            # the run's own change


def test_a_base_manifest_is_read_from_git_once_per_commit(tmp_path, monkeypatch):
    from core.run_protocol import validations
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "validations.json").write_text('{"schema": 1, "entries": []}')
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b", "-c", "user.name=a",
                    "commit", "-qm", "m"], check=True)
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                         stdout=subprocess.PIPE, text=True).stdout.strip()
    runs = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: runs.append(a[0]) or real(*a, **k))
    first = validations.load_manifest(str(repo), sha)
    first["entries"].append("mutated by a caller")
    assert validations.load_manifest(str(repo), sha) == {"schema": 1, "entries": []}
    assert len(runs) == 2                                             # ls-tree + show, once
