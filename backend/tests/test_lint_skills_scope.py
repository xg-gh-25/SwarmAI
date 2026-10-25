"""lint_skills' metadata pass covers exactly the skills that ship (closing guards L7).

Design: docs/design/2026-10-25-usable-by-anyone-closed-world-guards.md §3.1 K3 / L7. The
pass used to glob every ``s_*/SKILL.md`` on disk, so a user's gitignored local skill made
a local run red on files CI never sees and no installer carries. It now asks the
package's own list (backend/scripts/shipped_skills.py).

Methodology: a real git repo in tmp_path with a tracked, an untracked and an ignored skill.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lint_skills  # noqa: E402

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}


def _skill(root: Path, name: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(f"---\nname: {name[2:]}\ndescription: x\n---\n")


def test_metadata_pass_skips_ignored_skills_and_keeps_untracked_ones(tmp_path):
    repo = tmp_path / "repo"
    skills = repo / "skills"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=_ENV)
    (repo / ".gitignore").write_text("skills/s_local-*/\n")
    _skill(skills, "s_tracked")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=_ENV)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "c"], check=True, env=_ENV)
    _skill(skills, "s_new")          # being written, not committed yet: still ships
    _skill(skills, "s_local-mine")   # the user's own ignored skill: never ships

    names = [md.parent.name for md in lint_skills.metadata_targets(skills)]
    assert names == ["s_new", "s_tracked"]


def test_metadata_pass_without_git_lists_every_skill(tmp_path):
    _skill(tmp_path / "skills", "s_a")
    _skill(tmp_path / "skills", "s_local-b")
    names = [md.parent.name for md in lint_skills.metadata_targets(tmp_path / "skills")]
    assert names == ["s_a", "s_local-b"]
