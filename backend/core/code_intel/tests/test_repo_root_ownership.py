"""repo_root ownership validation (run_1950e67e).

A project's code_intel.db carries a stored ``repo_root``. Nothing used to check
that path belonged to THAT project, so IVTHub's db carried the SwarmAI source
path and the startup watcher re-indexed SwarmAI's files into IVTHub's brain every
save (self-perpetuating content contamination — Principle-1 violation).

``resolve_owned_repo_root`` is the ownership oracle: a project owns only the repo
its OWN TECH.md declares (local + is_dir), else None. ``repo_root_is_owned`` is
the gate both index trigger sites apply before trusting a stored repo_root.
"""
import os
import tempfile
from pathlib import Path

from core.code_intel import resolve_owned_repo_root, repo_root_is_owned


def _mk_project(tmp: Path, name: str, tech_body: str) -> Path:
    """Create a DDD project dir with a TECH.md at the migrated (2-understanding) path."""
    pd = tmp / name
    (pd / "2-understanding").mkdir(parents=True)
    (pd / "2-understanding" / "TECH.md").write_text(tech_body, encoding="utf-8")
    return pd


def test_owned_repo_resolves_when_tech_declares_a_local_dir():
    """A project whose TECH.md declares a real local repo path OWNS it."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        real_repo = tmp / "myrepo"
        real_repo.mkdir()
        pd = _mk_project(tmp, "Owned", f"# TECH\n\n**Local:** `{real_repo}`\n")
        owned = resolve_owned_repo_root(pd)
        assert owned == str(real_repo.resolve())
        assert repo_root_is_owned(pd, str(real_repo)) is True


def test_no_local_repo_is_not_owned():
    """A DDD whose TECH declares only remote/internal repos (extract_repo_path None,
    e.g. worktree:null code.amazon.com packages) owns NOTHING — must index nothing."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pd = _mk_project(tmp, "Internal",
                         "# TECH\n\n## Codebase Location\n"
                         "- Service code: Brazil packages Foo / Bar (code.amazon.com)\n")
        assert resolve_owned_repo_root(pd) is None
        # even if a foreign repo_root is stored, it is NOT owned
        assert repo_root_is_owned(pd, str(tmp)) is False


def test_foreign_stored_repo_root_is_rejected():
    """The IVTHub contamination case: TECH declares no local repo (or a different
    one) but the db stored a FOREIGN repo_root → gate rejects it."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        foreign = tmp / "swarmai_source"
        foreign.mkdir()
        # IVTHub-like: internal-only TECH, but db somehow stored the SwarmAI path
        pd = _mk_project(tmp, "IVTHubLike",
                         "# TECH\n\n## Codebase Location\n- code.amazon.com/packages/IVTHub\n")
        assert repo_root_is_owned(pd, str(foreign)) is False, \
            "foreign repo_root accepted — contamination guard is vacuous"


def test_different_local_repo_rejected():
    """TECH declares repo A, db stored repo B (both local dirs) → not owned."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo_a = tmp / "a"; repo_a.mkdir()
        repo_b = tmp / "b"; repo_b.mkdir()
        pd = _mk_project(tmp, "Proj", f"# TECH\n\n**Local:** `{repo_a}`\n")
        assert repo_root_is_owned(pd, str(repo_a)) is True
        assert repo_root_is_owned(pd, str(repo_b)) is False


def test_empty_or_missing_stored_root_is_not_owned():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        real_repo = tmp / "r"; real_repo.mkdir()
        pd = _mk_project(tmp, "P", f"# TECH\n\n**Local:** `{real_repo}`\n")
        assert repo_root_is_owned(pd, None) is False
        assert repo_root_is_owned(pd, "") is False


def test_nonexistent_declared_path_is_not_owned():
    """TECH declares a path that isn't a real dir → owns nothing (no false trust)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pd = _mk_project(tmp, "Gone", "# TECH\n\n**Local:** `/nonexistent/path/xyz`\n")
        assert resolve_owned_repo_root(pd) is None


def test_tilde_declared_path_expands(monkeypatch, tmp_path):
    """Gate-2 MED: a `~/...` repo declaration must expanduser, else a legit project
    is silently un-indexed (Path('~/x').resolve() -> <cwd>/~/x, is_dir False)."""
    home = tmp_path / "home"; (home / "repos" / "x").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    pd = _mk_project(tmp_path, "Tilde", "# TECH\n\n**Local:** `~/repos/x`\n")
    owned = resolve_owned_repo_root(pd)
    assert owned == str((home / "repos" / "x").resolve()), f"~ not expanded: {owned}"
    assert repo_root_is_owned(pd, "~/repos/x") is True


def test_case_and_symlink_variants_match_same_dir(tmp_path):
    """Gate-2 MED: a stored root that differs by case (case-insensitive FS) or via a
    symlink but points at the SAME dir must be accepted (samefile inode identity),
    not rejected by a raw string ==."""
    import os
    real = tmp_path / "RealRepo"; real.mkdir()
    pd = _mk_project(tmp_path, "P", f"# TECH\n\n**Local:** `{real}`\n")
    # symlink pointing at the same dir → samefile True → owned
    link = tmp_path / "linkrepo"
    try:
        os.symlink(real, link)
    except OSError:
        return  # platform without symlink perms — skip
    assert repo_root_is_owned(pd, str(link)) is True
