"""Tests for resolve_path_to_physical() — the extracted resolver helper.

Cycle 1 of run_e626e121 (Canvas-trigger unification, Extract≠Extend). The path
cascade (absolute → project-symlink → direct → bare-name recursive) was inline in
the /workspace/file/resolve HTTP handler; it is extracted into a callable module
helper so the streaming orchestrator can resolve a written file's PHYSICAL absolute
path once, without an HTTP round-trip. The endpoint delegates to the helper.

Behavior-preserving: same cascade, but returns BOTH the workspace-relative display
path AND the physical absolute path (the copy-path directive needs the absolute).
"""
import os
from pathlib import Path

import pytest

from routers.workspace_api import resolve_path_to_physical


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """A minimal workspace: a root file + a project symlink to an external repo."""
    (tmp_path / "Knowledge").mkdir()
    (tmp_path / "Knowledge" / "note.md").write_text("hi")
    # external "repo" that a project symlink points at (mirrors Projects/<X> → repo)
    ext = tmp_path.parent / "extrepo"
    ext.mkdir(exist_ok=True)
    (ext / "backend").mkdir(exist_ok=True)
    (ext / "backend" / "foo.py").write_text("x = 1")
    projects = tmp_path / "Projects"
    projects.mkdir()
    link = projects / "MyProj"
    if not link.exists():
        link.symlink_to(ext)
    return tmp_path


def test_direct_workspace_relative(ws: Path):
    r = resolve_path_to_physical("Knowledge/note.md", ws)
    assert r is not None
    assert r["relative"] == "Knowledge/note.md"
    # absolute must be the REAL physical path, not the relative string
    assert os.path.isabs(r["absolute"])
    assert Path(r["absolute"]).read_text() == "hi"


def test_via_project_symlink(ws: Path):
    # agent emits a source-repo-relative path; resolver finds it under Projects/*
    r = resolve_path_to_physical("backend/foo.py", ws)
    assert r is not None
    assert r["relative"] == "Projects/MyProj/backend/foo.py"
    assert os.path.isabs(r["absolute"])
    assert r["absolute"].endswith("/backend/foo.py")


def test_bare_filename_recursive(ws: Path):
    r = resolve_path_to_physical("note.md", ws)
    assert r is not None
    assert r["relative"] == "Knowledge/note.md"
    assert os.path.isabs(r["absolute"])


def test_absolute_path_inside_project_becomes_relative(ws: Path):
    abs_in = str((ws / "Projects" / "MyProj" / "backend" / "foo.py"))
    r = resolve_path_to_physical(abs_in, ws)
    assert r is not None
    assert r["relative"] == "Projects/MyProj/backend/foo.py"
    assert os.path.isabs(r["absolute"])


def test_nonexistent_returns_none(ws: Path):
    assert resolve_path_to_physical("does/not/exist.py", ws) is None


def test_null_byte_returns_none(ws: Path):
    # was HTTP 400; as a plain helper it fails safe → None (no raise on hot path)
    assert resolve_path_to_physical("foo\x00.py", ws) is None


def test_traversal_returns_none(ws: Path):
    assert resolve_path_to_physical("../etc/passwd", ws) is None


# ── Stage 5: governed-but-not-CONTAINED repo (bindings.yaml worktree) ────────
# run_1e791215: a DDD may GOVERN a code-repo whose source lives OUTSIDE the
# workspace and is NOT symlinked into Projects/<X>/ (the "GOVERNs, never CONTAINS"
# paradigm — Projects/SwarmAI is a real dir of DDD docs). A clickable file-link
# to that governed source (e.g. backend/routers/eval.py) missed all 4 legacy
# stages → 404. Stage 5 reads the bound worktree roots from Projects/*/bindings.yaml
# and resolves under them (allowlist-scoped).

@pytest.fixture(autouse=True)
def _clear_worktree_cache():
    """Stage 5 reads the SHARED needs_human_review._worktree_roots cache
    (lru_cache maxsize=1). Clear it around each test so a real-workspace entry
    from another test can't leak in, and each tmp-workspace computes fresh."""
    from core.needs_human_review import clear_worktree_cache
    clear_worktree_cache()
    yield
    clear_worktree_cache()


@pytest.fixture
def ws_governed(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace whose Projects/Gov/ is a REAL dir (DDD docs) holding a
    bindings.yaml that declares an ABSOLUTE worktree at an external repo NOT
    symlinked into the workspace — the govern-not-contain shape."""
    (tmp_path / "Knowledge").mkdir()
    # external governed repo — lives OUTSIDE the workspace, no symlink into it
    repo = tmp_path.parent / "governed_repo"
    (repo / "backend" / "routers").mkdir(parents=True, exist_ok=True)
    (repo / "backend" / "routers" / "eval.py").write_text("x = 1")
    (repo / "desktop").mkdir(exist_ok=True)
    (repo / "desktop" / "eslint.config.js").write_text("module.exports = {}")
    # Projects/Gov = a REAL directory (NOT a symlink) holding bindings.yaml
    gov = tmp_path / "Projects" / "Gov"
    gov.mkdir(parents=True)
    (gov / "bindings.yaml").write_text(
        "bindings:\n"
        "  - repo: govrepo\n"
        "    kind: external\n"
        "    clone: https://example.com/govrepo.git\n"
        f"    worktree: {repo}\n"
        "    delivery_contract:\n"
        "      remote_kind: self-hosted-main\n"
        "      build_system: local-script\n"
        "      branch: main\n"
        "      review_path: s_autonomous-pipeline\n"
        "      auto_send: manual-push\n"
    )
    return tmp_path, repo


def test_governed_multisegment_resolves_via_bindings_worktree(ws_governed):
    """AC1: a governed-source multi-segment path (missed by all 4 legacy stages)
    resolves to the absolute file under the bound worktree (was None → 404)."""
    ws, repo = ws_governed
    r = resolve_path_to_physical("backend/routers/eval.py", ws)
    assert r is not None, "governed path must resolve via bindings worktree (Stage 5)"
    assert os.path.isabs(r["absolute"])
    assert Path(r["absolute"]) == (repo / "backend" / "routers" / "eval.py").resolve()
    assert Path(r["absolute"]).read_text() == "x = 1"
    # a file outside the workspace is returned with the absolute path as display too
    assert os.path.isabs(r["relative"])


def test_governed_second_file_resolves(ws_governed):
    """AC1: a different governed multi-segment path also resolves."""
    ws, repo = ws_governed
    r = resolve_path_to_physical("desktop/eslint.config.js", ws)
    assert r is not None
    assert Path(r["absolute"]) == (repo / "desktop" / "eslint.config.js").resolve()


def test_governed_allowlist_not_widened(ws_governed):
    """AC2: a path that does NOT exist under any declared worktree still None —
    the fix does not widen resolution to arbitrary outside paths."""
    ws, repo = ws_governed
    # exists on disk but under NO declared worktree (sibling of the repo)
    outside = repo.parent / "not_governed" / "secret.py"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("secret = 1")
    assert resolve_path_to_physical("not_governed/secret.py", ws) is None
    # and a path that simply doesn't exist anywhere
    assert resolve_path_to_physical("backend/routers/nope.py", ws) is None


def test_governed_traversal_cannot_escape_worktree(ws_governed):
    """AC2/AC4: a relative path with .. is rejected before Stage 5 (never escapes)."""
    ws, repo = ws_governed
    # a sibling secret next to the worktree — must NOT be reachable via ..
    (repo.parent / "outside_secret.py").write_text("secret")
    assert resolve_path_to_physical("../outside_secret.py", ws) is None


def test_malformed_bindings_does_not_raise(tmp_path: Path):
    """AC4: a project whose bindings.yaml is malformed must be skipped (fail-safe
    to None), never raise — the resolver runs on the streaming hot path."""
    (tmp_path / "Knowledge").mkdir()
    bad = tmp_path / "Projects" / "Bad"
    bad.mkdir(parents=True)
    (bad / "bindings.yaml").write_text("this: is not: valid bindings\n  - broken")
    # must not raise; just returns None for an unresolvable path
    assert resolve_path_to_physical("backend/routers/eval.py", tmp_path) is None


def test_legacy_stages_unaffected_by_stage5(ws_governed):
    """AC3: the pre-existing stages still work with a bindings.yaml present —
    a real workspace file resolves via Stage 1, not Stage 5."""
    ws, repo = ws_governed
    (ws / "Knowledge" / "note.md").write_text("hi")
    r = resolve_path_to_physical("Knowledge/note.md", ws)
    assert r is not None
    assert r["relative"] == "Knowledge/note.md"  # workspace-relative, NOT absolute
