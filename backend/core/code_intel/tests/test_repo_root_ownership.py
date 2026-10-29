"""repo_root ownership validation (run_1950e67e).

A project's graph carries a stored ``repo_root``. Nothing used to check that the
path belonged to THAT project, so IVTHub's db carried the SwarmAI source path and
SwarmAI's files were re-indexed into IVTHub's brain (content contamination).

``repo_root_is_owned`` is the gate a caller applies before trusting a stored
``repo_root``: true only for a repo the project owns (``owned_repo_roots`` — a
confirmed local root or a bind-managed clone). A TECH.md path grants nothing any
more; ``_tech_md_repo_root`` only reads it to seed a pending suggestion.
"""
import os
from pathlib import Path

from core.code_intel import _tech_md_repo_root, owned_repo_roots, repo_root_is_owned


def _mk_project(tmp: Path, name: str, tech_body: str = "# TECH\n") -> Path:
    """A DDD project dir with a TECH.md at the migrated (2-understanding) path."""
    pd = tmp / name
    (pd / "2-understanding").mkdir(parents=True)
    (pd / "2-understanding" / "TECH.md").write_text(tech_body, encoding="utf-8")
    return pd


def test_a_confirmed_root_is_owned(tmp_path, own_local_root):
    real_repo = tmp_path / "myrepo"
    real_repo.mkdir()
    pd = _mk_project(tmp_path, "Owned")
    own_local_root("Owned", real_repo)
    assert owned_repo_roots(pd) == [str(real_repo.resolve())]
    assert repo_root_is_owned(pd, str(real_repo)) is True


def test_a_tech_md_path_alone_owns_nothing(tmp_path):
    """The old declaration: TECH.md names a real local repo. It owns nothing now."""
    real_repo = tmp_path / "myrepo"
    real_repo.mkdir()
    pd = _mk_project(tmp_path, "Doc", f"# TECH\n\n**Local:** `{real_repo}`\n")
    assert owned_repo_roots(pd) == []
    assert repo_root_is_owned(pd, str(real_repo)) is False
    assert _tech_md_repo_root(pd) == str(real_repo.resolve())   # still read as a suggestion


def test_foreign_stored_repo_root_is_rejected(tmp_path):
    """The IVTHub contamination case: the project owns nothing local, but the db
    stored a foreign repo_root → the gate rejects it."""
    foreign = tmp_path / "swarmai_source"
    foreign.mkdir()
    pd = _mk_project(tmp_path, "IVTHubLike",
                     "# TECH\n\n## Codebase Location\n- code.amazon.com/packages/IVTHub\n")
    assert repo_root_is_owned(pd, str(foreign)) is False, \
        "foreign repo_root accepted — contamination guard is vacuous"


def test_different_local_repo_rejected(tmp_path, own_local_root):
    repo_a = tmp_path / "a"; repo_a.mkdir()
    repo_b = tmp_path / "b"; repo_b.mkdir()
    pd = _mk_project(tmp_path, "Proj")
    own_local_root("Proj", repo_a)
    assert repo_root_is_owned(pd, str(repo_a)) is True
    assert repo_root_is_owned(pd, str(repo_b)) is False


def test_empty_or_missing_stored_root_is_not_owned(tmp_path, own_local_root):
    real_repo = tmp_path / "r"; real_repo.mkdir()
    pd = _mk_project(tmp_path, "P")
    own_local_root("P", real_repo)
    assert repo_root_is_owned(pd, None) is False
    assert repo_root_is_owned(pd, "") is False


def test_a_confirmed_root_that_was_deleted_owns_nothing(tmp_path, own_local_root):
    lost = tmp_path / "lost_checkout"; lost.mkdir()
    pd = _mk_project(tmp_path, "Proj")
    own_local_root("Proj", lost)
    lost.rmdir()
    assert owned_repo_roots(pd) == []


def test_tilde_tech_md_path_expands(monkeypatch, tmp_path):
    """A `~/...` TECH.md path must expanduser, else the suggestion is silently lost
    (Path('~/x').resolve() -> <cwd>/~/x, is_dir False)."""
    home = tmp_path / "home"; (home / "repos" / "x").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    pd = _mk_project(tmp_path, "Tilde", "# TECH\n\n**Local:** `~/repos/x`\n")
    assert _tech_md_repo_root(pd) == str((home / "repos" / "x").resolve())


def test_nonexistent_tech_md_path_is_no_suggestion(tmp_path):
    pd = _mk_project(tmp_path, "Gone", "# TECH\n\n**Local:** `/nonexistent/path/xyz`\n")
    assert _tech_md_repo_root(pd) is None


def test_case_and_symlink_variants_match_same_dir(tmp_path, own_local_root):
    """A stored root that differs by a symlink but points at the SAME dir is accepted
    (samefile inode identity), not rejected by a raw string ==."""
    real = tmp_path / "RealRepo"; real.mkdir()
    pd = _mk_project(tmp_path, "P")
    own_local_root("P", real)
    link = tmp_path / "linkrepo"
    try:
        os.symlink(real, link)
    except OSError:
        return  # platform without symlink perms
    assert repo_root_is_owned(pd, str(link)) is True
