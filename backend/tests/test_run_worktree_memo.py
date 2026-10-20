"""Tests for the per-process rev-parse memo in core/run_worktree.py and core/session_ids.py.

- _common_dir / _toplevel answer from the memo for an unchanged repo root, and never
  return a stale answer once the root's `.git` entry is replaced (re-init, removed)
- a failure is never memoised; a non-root cwd is never memoised
- session_ids.valid_session_id keeps the transcript pattern; artifact_cli's
  _record_transcript_id returns before any import when no session id is set
"""
import subprocess

import pytest

from core import run_worktree as rw


def _init(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


@pytest.fixture(autouse=True)
def _fresh_memo():
    rw._REV_MEMO.clear()
    yield
    rw._REV_MEMO.clear()


def _count_git(monkeypatch):
    calls = []
    real = rw.git

    def spy(cwd, *a, **k):
        calls.append(a[:2])
        return real(cwd, *a, **k)
    monkeypatch.setattr(rw, "git", spy)
    return calls


def test_root_answers_are_memoised(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    _init(repo)
    calls = _count_git(monkeypatch)
    first = (rw._common_dir(repo), rw._toplevel(repo))
    n = len(calls)
    assert (rw._common_dir(repo), rw._toplevel(repo)) == first
    assert len(calls) == n            # second time: no git process


def test_a_replaced_git_entry_is_never_answered_from_the_memo(tmp_path):
    import shutil
    repo = tmp_path / "r"
    _init(repo)
    assert rw._toplevel(repo)
    shutil.rmtree(repo / ".git")
    assert rw._toplevel(repo) is None          # not a repo any more
    _init(repo)
    assert rw._toplevel(repo) == str(repo.resolve())


def test_failures_and_non_roots_are_not_memoised(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert rw._toplevel(plain) is None and rw._REV_MEMO == {}
    repo = tmp_path / "r"
    _init(repo)
    (repo / "sub").mkdir()
    assert rw._toplevel(repo / "sub") == str(repo.resolve())
    assert rw._REV_MEMO == {}                  # sub/ has no .git of its own


def test_session_ids_pattern():
    from core.session_ids import MAX_TRANSCRIPT_IDS, valid_session_id
    from core.transcript_usage import valid_session_id as reexported
    assert reexported is valid_session_id and MAX_TRANSCRIPT_IDS == 32
    assert valid_session_id("c40ebd5a-38fc-4726-9464-882a8599668b")
    assert not valid_session_id("abc") and not valid_session_id("c40ebd5a-38fc\n")
    assert not valid_session_id("../../etc") and not valid_session_id(None)


def test_record_transcript_id(monkeypatch):
    from scripts.artifact_cli import _record_transcript_id
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    st = {}
    assert _record_transcript_id(st) is False and st == {}
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "c40ebd5a-38fc-4726-9464-882a8599668b")
    assert _record_transcript_id(st) is True
    assert st["transcripts"] == ["c40ebd5a-38fc-4726-9464-882a8599668b"]
    assert _record_transcript_id(st) is False   # once


def _git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e.com",
                        "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e.com",
                        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                        "HOME": str(path)})


def _repo_with_commit(tmp_path):
    repo = (tmp_path / "r").resolve()
    _init(repo)
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "i")
    return repo


def test_main_checkout_memo_survives_a_git_write(tmp_path, monkeypatch):
    """A git write in the main checkout (index.lock moves .git's mtime) must not
    invalidate the root answers: they do not depend on it."""
    repo = _repo_with_commit(tmp_path)
    first = (rw._common_dir(repo), rw._toplevel(repo), rw._symbolic_head(repo))
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "-A")
    calls = _count_git(monkeypatch)
    assert (rw._common_dir(repo), rw._toplevel(repo), rw._symbolic_head(repo)) == first
    assert calls == []


def test_symbolic_head_follows_a_branch_switch_and_never_stores_detached(tmp_path):
    repo = _repo_with_commit(tmp_path)
    assert rw._symbolic_head(repo) == "refs/heads/master" or rw._symbolic_head(repo).startswith("refs/heads/")
    _git(repo, "checkout", "-q", "-b", "other")
    assert rw._symbolic_head(repo) == "refs/heads/other"
    _git(repo, "checkout", "-q", "--detach")
    assert rw._symbolic_head(repo) is None
    assert not any(k[0] == "head" and v is None for k, v in rw._REV_MEMO.items())
    _git(repo, "checkout", "-q", "other")
    assert rw._symbolic_head(repo) == "refs/heads/other"


def test_worktree_list_is_memoised_and_follows_add_remove_and_branch(tmp_path, monkeypatch):
    repo = _repo_with_commit(tmp_path)
    wt = (tmp_path / "wt").resolve()
    _git(repo, "worktree", "add", "-q", "-b", "run/x", str(wt))
    first = rw._worktree_list(repo)
    assert [w["path"] for w in first] == [str(repo), str(wt)]
    assert first[1]["branch"] == "refs/heads/run/x"
    calls = _count_git(monkeypatch)
    assert rw._worktree_list(repo) == first and calls == []      # memo hit
    (wt / "c.txt").write_text("c\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "c")                           # moves no listed field
    assert rw._worktree_list(repo) == first
    _git(wt, "checkout", "-q", "-b", "run/y")
    assert rw._worktree_list(repo)[1]["branch"] == "refs/heads/run/y"
    _git(wt, "checkout", "-q", "--detach")
    assert "branch" not in rw._worktree_list(repo)[1]
    wt2 = (tmp_path / "wt2").resolve()
    _git(repo, "worktree", "add", "-q", "-b", "run/z", str(wt2))
    assert [w["path"] for w in rw._worktree_list(repo)] == [str(repo), str(wt), str(wt2)]
    _git(repo, "worktree", "remove", "--force", str(wt))
    assert [w["path"] for w in rw._worktree_list(repo)] == [str(repo), str(wt2)]


def test_busy_state_uses_one_git_call_and_sees_each_state(tmp_path, monkeypatch):
    repo = _repo_with_commit(tmp_path)
    wt = (tmp_path / "wt").resolve()
    _git(repo, "worktree", "add", "-q", "-b", "run/x", str(wt))
    calls = _count_git(monkeypatch)
    assert rw.busy_state(repo) is None and rw.busy_state(wt) is None
    assert len(calls) == 2
    (repo / ".git" / "CHERRY_PICK_HEAD").write_text("x\n")
    assert rw.busy_state(repo) == "CHERRY_PICK_HEAD"
    assert rw.busy_state(wt) is None                     # per-worktree state
    admin = repo / ".git" / "worktrees" / "wt"
    (admin / "rebase-merge").mkdir()
    assert rw.busy_state(wt) == "rebase-merge"


def test_reftable_repo_follows_branch_switches(tmp_path):
    """In a reftable repo HEAD is a fixed stub: HEAD answers must not be memoised."""
    repo = (tmp_path / "rt").resolve()
    repo.mkdir()
    p = subprocess.run(["git", "init", "-q", "--ref-format=reftable", str(repo)],
                       capture_output=True)
    if p.returncode != 0 or not (repo / ".git" / "reftable").is_dir():
        pytest.skip("git without reftable support")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "i")
    wt = (tmp_path / "rtwt").resolve()
    _git(repo, "worktree", "add", "-q", "-b", "run/x", str(wt))
    before = rw._symbolic_head(repo)
    assert rw._worktree_list(repo)[1]["branch"] == "refs/heads/run/x"
    _git(repo, "checkout", "-q", "-b", "other")
    _git(wt, "checkout", "-q", "-b", "run/y")
    assert rw._symbolic_head(repo) == "refs/heads/other" != before
    assert rw._worktree_list(repo)[1]["branch"] == "refs/heads/run/y"
    _git(repo, "checkout", "-q", "--detach")
    assert rw._symbolic_head(repo) is None
