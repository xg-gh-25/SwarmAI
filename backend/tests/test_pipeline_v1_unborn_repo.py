"""A repo with no commit yet ("unborn": just `git init`) is refused at create.

Spec 2026-10-24-swarmai-usable-by-anyone §5.2 (r29, XG 2026-10-25): unborn repos are
not supported. A run needs a base commit for its worktree, its landing checks and its
ff-only landing; before, create made the run and it paused at once with
``worktree_unavailable``, which told the user nothing. Now create changes nothing and
names the fix (an initial commit).

Tested through the real CLI (``tests/pipeline_v1_rig.py``): no run directory or event is
written; after one commit the same create works.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.pipeline_v1_rig import PROJECT, Rig, _git

pytestmark = pytest.mark.timeout(120)


def test_create_refuses_an_unborn_repo_and_names_the_fix(tmp_path, monkeypatch):
    r = Rig(tmp_path / "case", monkeypatch, profile="direct")
    try:
        unborn = tmp_path / "fresh"
        unborn.mkdir()
        subprocess.run(["git", "init", "-q", str(unborn)], check=True)
        out = r.v1("create", "--project", PROJECT, "--profile", "direct",
                   "--requirement", "add a readme", "--repo", f"fresh={unborn}")
        assert out.get("ok") is False, out
        assert "no commit" in out["error"] and "commit --allow-empty -m init" in out["error"], out
        assert not list((r.ws / "Projects").glob("*/.artifacts/runs/*")), "a run was made"

        _git(unborn, "commit", "-q", "--allow-empty", "-m", "init")
        again = r.v1("create", "--project", PROJECT, "--profile", "direct",
                     "--requirement", "add a readme", "--repo", f"fresh={unborn}")
        assert again.get("ok") is True, again
    finally:
        r.close()
