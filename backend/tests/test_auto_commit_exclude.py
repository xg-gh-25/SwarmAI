"""R29 regression: auto-commit must NOT sweep a sibling live session's in-flight edits.

Drives the REAL WorkspaceAutoCommitHook._smart_commit against a REAL temp git repo and
asserts the actual committed tree. Mutation-provable: removing the _unstage_paths
call (exclude) → the sibling file gets committed → these fail.
"""
import subprocess

import pytest

from hooks.auto_commit_hook import WorkspaceAutoCommitHook


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=10)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "ws"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t.co")
    _git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "seed")
    return r


def _committed_files(repo) -> set[str]:
    out = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


class TestAutoCommitExclude:
    def test_sibling_touched_file_is_not_committed(self, repo):
        # my work + an auto-generated file + a sibling's in-flight edit all dirty
        (repo / "my_change.py").write_text("mine\n")
        (repo / "DailyActivity.md").write_text("generated\n")  # not in any touched set
        sibling = repo / "sibling_wip.py"
        sibling.write_text("sibling in progress\n")

        hook = WorkspaceAutoCommitHook()
        hook._smart_commit(str(repo), exclude={str(sibling)})

        committed = _committed_files(repo)
        assert "my_change.py" in committed, "own work must be committed"
        assert "DailyActivity.md" in committed, "auto-generated files must still be committed"
        assert "sibling_wip.py" not in committed, "sibling in-flight edit must NOT be swept in"
        # and the sibling's change must survive on disk (working tree untouched)
        assert sibling.read_text() == "sibling in progress\n"

    def test_no_exclude_behaves_like_add_all(self, repo):
        # empty exclude → prior behavior: everything committed
        (repo / "a.py").write_text("a\n")
        (repo / "b.py").write_text("b\n")
        hook = WorkspaceAutoCommitHook()
        hook._smart_commit(str(repo), exclude=set())
        committed = _committed_files(repo)
        assert {"a.py", "b.py"} <= committed

    def test_exclude_outside_repo_is_ignored(self, repo, tmp_path):
        # a path outside the repo must not break the commit
        (repo / "c.py").write_text("c\n")
        outside = tmp_path / "elsewhere.py"
        hook = WorkspaceAutoCommitHook()
        hook._smart_commit(str(repo), exclude={str(outside)})
        assert "c.py" in _committed_files(repo)


class TestOverlapExcludeLogic:
    """Gate-2 finding B: a file edited by BOTH the committing session and a
    sibling must NOT be excluded (else the committer's own change is dropped)."""

    def test_shared_file_not_excluded(self, monkeypatch):
        from core import session_registry

        class _Unit:
            def __init__(self, touched):
                self._hook_session_context = {"_files_touched": set(touched)}

        class _Router:
            _units = {
                "me": _Unit(["/ws/shared.py", "/ws/mine_only.py"]),
                "sib": _Unit(["/ws/shared.py", "/ws/sib_only.py"]),
            }

        monkeypatch.setattr(session_registry, "session_router", _Router())
        exclude = WorkspaceAutoCommitHook._other_live_sessions_touched("me")
        # sibling-only file excluded; shared + mine-only NOT excluded
        assert "/ws/sib_only.py" in exclude
        assert "/ws/shared.py" not in exclude, "shared file must stay committable"
        assert "/ws/mine_only.py" not in exclude

    def test_registry_none_is_failsafe(self, monkeypatch):
        from core import session_registry
        monkeypatch.setattr(session_registry, "session_router", None)
        assert WorkspaceAutoCommitHook._other_live_sessions_touched("me") == set()
