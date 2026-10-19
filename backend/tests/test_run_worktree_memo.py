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
