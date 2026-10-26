"""Session-worktree files in the Canvas rail (design docs/design/2026-10-26-session-products.md §3.3).

A native session's own worktree lives under ``<app data>/session-worktrees/<name>/<repo>``,
inside ``~/.swarm-ai``, which ``canvas_noise`` treats as app plumbing. Tested here:

- a file in such a worktree gets the kind the same file gets in its main checkout
  (``needs_human_review._classify_kind`` with the repo): code and repo docs are
  ``source`` (Canvas Changes, never Artifacts / deliverables), a DDD doc is
  ``knowledge`` (Gawan 2026-10-26: repo code changes had shown under Artifacts);
- the tree-relative dot-segment rule (``needs_human_review._has_dot_segment``) declines
  ``.env``, ``.github/...``, ``.git/...`` inside it;
- a gitignored file and a noise shape (``node_modules``, ``.pyc``) decline;
- the rest of ``~/.swarm-ai`` (logs, state) is still noise.

Real git repos under tmp_path; ``SWARM_SESSION_WORKTREE_ROOT`` points the root at them
(the same env ``scripts.session_worktree.root`` reads), ``$HOME`` at tmp_path.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core import canvas_noise
from core.canvas_surface import is_canvas_surfaceable


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def wt(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    data = home / ".swarm-ai"
    root = data / "session-worktrees"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SWARM_SESSION_WORKTREE_ROOT", str(root))
    # tmp_path is under the OS temp root canvas_noise denies; drop only that root so
    # the test sees the ~/.swarm-ai rule, not the temp rule.
    monkeypatch.setattr(canvas_noise, "_noise_roots", lambda: tuple(canvas_noise._variants(data)))
    canvas_noise._root_exceptions.cache_clear()
    main = tmp_path / "repo"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t.co")
    _git(main, "config", "user.name", "t")
    (main / ".gitignore").write_text("secret.txt\n")
    (main / "README.md").write_text("hi\n")
    _git(main, "add", ".")
    _git(main, "commit", "-q", "-m", "init")
    path = root / "20261026-000000-abcd" / "repo"
    path.parent.mkdir(parents=True)
    _git(main, "worktree", "add", "-q", "-b", "session/k-20261026-000000-abcd", str(path), "HEAD")
    yield path
    canvas_noise._root_exceptions.cache_clear()


def _write(p: Path, text: str = "x\n") -> str:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return str(p)


@pytest.mark.parametrize("rel", ["backend/core/golden_set_store.py", "docs/design/plan.md", "README.md"])
def test_a_repo_file_in_a_session_worktree_is_source(wt: Path, tmp_path: Path, rel: str):
    """The same answer as in the main checkout: a change, not an artifact."""
    v = is_canvas_surfaceable(_write(wt / rel, "changed\n"), swarmws_root=tmp_path / "ws")
    assert (v.surfaceable, v.kind) == (True, "source")


def test_a_ddd_doc_in_a_session_worktree_is_knowledge(wt: Path, tmp_path: Path):
    v = is_canvas_surfaceable(_write(wt / "Projects" / "P" / "2-understanding" / "TECH.md"),
                              swarmws_root=tmp_path / "ws")
    assert (v.surfaceable, v.kind) == (True, "knowledge")


def test_a_source_kind_is_not_an_artifact_or_a_product():
    from core import session_outputs
    from core.turn_products import PRODUCT_FILE_KINDS
    assert "source" not in session_outputs.RAIL_KINDS
    assert "source" not in PRODUCT_FILE_KINDS


@pytest.mark.parametrize("rel", [".env", ".github/workflows/ci.yml", "config/.secrets/key.md"])
def test_dot_segment_inside_session_worktree_declines(wt: Path, tmp_path: Path, rel: str):
    assert is_canvas_surfaceable(_write(wt / rel), swarmws_root=tmp_path / "ws").surfaceable is False


def test_gitignored_file_declines(wt: Path, tmp_path: Path):
    assert is_canvas_surfaceable(_write(wt / "secret.txt"), swarmws_root=tmp_path / "ws").surfaceable is False


@pytest.mark.parametrize("rel", ["node_modules/x/index.js", "pkg/__pycache__/m.cpython-312.pyc"])
def test_noise_shape_inside_session_worktree_declines(wt: Path, tmp_path: Path, rel: str):
    assert is_canvas_surfaceable(_write(wt / rel), swarmws_root=tmp_path / "ws").surfaceable is False


def test_rest_of_swarm_state_is_still_noise(wt: Path, tmp_path: Path):
    data = Path(str(wt).split("/session-worktrees/")[0])
    assert is_canvas_surfaceable(_write(data / "logs" / "report.md"), swarmws_root=tmp_path / "ws").surfaceable is False
    # a directory directly under the root that is not a worktree root's file
    assert is_canvas_surfaceable(_write(data / "session-worktrees" / "notes.md"),
                                 swarmws_root=tmp_path / "ws").surfaceable is False
