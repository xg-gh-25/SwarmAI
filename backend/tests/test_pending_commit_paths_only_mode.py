"""Tests for _pending_commit_paths on pathspec (--only) commits.

What is tested
--------------
``_pending_commit_paths`` (backend/core/security_hooks.py) computes the set of files a
``git commit`` command is about to commit, for the adversarial and active-run commit
gates. With pathspecs and no ``-a``/``-i``/``-p``, git commits ONLY the matched files and
leaves every other staged entry staged. These tests pin:

* the single-commit fast path: a command that is exactly one ``git commit [opts] -- <files>``
  is gated on those files only, so a file another session staged no longer causes a
  false DENY (the bug this file was written for);
* every other shape keeps the wide set (all staged files): ``cd``, subshells, command
  substitution, chained or wrapped commits, ``git -C``, env prefixes, abbreviated or
  unknown options, directories, globs, untracked paths;
* two widening fixes in the general parser: ``-u <path>`` no longer swallows the path,
  ``--pathspec-from-file`` sweeps tracked working-tree changes.

Methodology
-----------
Real git in tmp repos, no mocks. The differential test computes pending, then runs the
same command through bash and reads what git actually committed: committed must be a
subset of pending (never fail-open), and for fast-path commands the two must be equal
(no false DENY).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from core.security_hooks import _pending_commit_paths

_GC = "git" + " commit"  # split so this file's own text never reads as a commit
_HEREDOC_MSG = "-m \"$(cat <<'EOF'\nfix: subject\n\nbody with `code`\nEOF\n)\""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _abs(root: Path, *rels: str) -> set[str]:
    return {os.path.realpath(str(root / r)) for r in rels}


def _init(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")


@pytest.fixture
def repo(tmp_path):
    """A repo where another session has staged work and this session has unstaged work.

    Staged (the "other session"): foreign.py (modified), pkg/new.py (new), sub/x.py
    (modified). Unstaged (this session): mine.py, pkg/a.py, x.py; gone.py deleted.
    Untracked: untracked.py.
    """
    root = tmp_path / "repo"
    _init(root)
    for rel in ("mine.py", "foreign.py", "x.py", "gone.py", "pkg/a.py", "sub/x.py"):
        _write(root, rel, "base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _write(root, "foreign.py", "other session\n")
    _write(root, "pkg/new.py", "other session\n")
    _write(root, "sub/x.py", "other session\n")
    _git(root, "add", "foreign.py", "pkg/new.py", "sub/x.py")
    _write(root, "mine.py", "mine\n")
    _write(root, "pkg/a.py", "mine\n")
    _write(root, "x.py", "mine\n")
    (root / "gone.py").unlink()
    _write(root, "untracked.py", "untracked\n")
    return root


_STAGED = ("foreign.py", "pkg/new.py", "sub/x.py")


# ---------------------------------------------------------------------------
# The bug: a single pathspec commit is gated only on the files it commits.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd,files", [
    (_GC + ' -m "fix" -- mine.py', ("mine.py",)),
    (_GC + " -m fix mine.py", ("mine.py",)),
    (_GC + " -o mine.py -m fix", ("mine.py",)),
    (_GC + " -om fix mine.py", ("mine.py",)),
    (_GC + " -m fix -u mine.py", ("mine.py",)),
    (_GC + " -q -s -m fix --no-verify -- mine.py", ("mine.py",)),
    (_GC + " --trailer 'Ref: 1' -m fix -- mine.py", ("mine.py",)),
    (_GC + " " + _HEREDOC_MSG + " -- mine.py", ("mine.py",)),
    (_GC + " -m fix -- mine.py pkg/a.py gone.py", ("mine.py", "pkg/a.py", "gone.py")),
    (_GC + ' -m "feat(gate): narrow & fix" -- mine.py', ("mine.py",)),
    (_GC + " -m fix#1 -- mine.py", ("mine.py",)),
])
def test_single_pathspec_commit_excludes_files_other_sessions_staged(repo, cmd, files):
    pending = _pending_commit_paths(str(repo), cmd)
    assert pending == _abs(repo, *files), sorted(pending or [])


# ---------------------------------------------------------------------------
# Every other shape keeps the wide set: all staged files stay in pending.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    _GC + " -m x",
    _GC + " -a -m x",
    _GC + " -m x -i mine.py",
    _GC + " -p -m x -- mine.py",
    _GC + " -m x --tra mine.py",                       # abbreviated --trailer
    _GC + " -m x --frobnicate -- mine.py",             # unknown long option
    _GC + " -m x -Z -- mine.py",                       # unknown short flag
    _GC + " -m x -- pkg",                              # directory
    _GC + " -m x -- 'pkg/*.py'",                       # glob
    _GC + " -m x -- untracked.py",                     # unknown to git
    _GC + " -m x -- $F",                               # shell expansion
    "git -C sub commit -m x -- x.py",
    "GIT_LITERAL_PATHSPECS=1 " + _GC + " -m x -- mine.py",
    "nice -n 1 " + _GC + " -m x -- mine.py",
    "git add mine.py && " + _GC + " -m x -- mine.py",
    "echo evil > x.py && " + _GC + " -m x -- x.py",
    "cd sub && " + _GC + " -m x -- x.py",
    "(cd sub && " + _GC + " -m x -- x.py)",
    "x=$(cd sub && " + _GC + " -m m -- x.py; true)",
    "x=`cd sub && " + _GC + " -m m -- x.py; true`",
    "f() { cd sub; }; f && " + _GC + " -m x -- x.py",
    _GC + " -m x -- mine.py; nice -n 1 " + _GC + " -m b",
    _GC + " -m x -- mine.py | tail -1",
    _GC + ' -m "$(cd sub && ' + _GC + ' -m y)" -- mine.py',
    _GC + ' -m "fix `id`" -- mine.py',
    _GC + " -m \"$(cat <<EOF\nmsg $(cd sub)\nEOF\n)\" -- mine.py",       # unquoted delimiter
    _GC + " -m \"$(cat <<'EOF'\nmsg\nEOF\ncd sub\nEOF\n)\" -- mine.py",  # early terminator
    _GC + ' -m "unclosed -- mine.py',                                    # shlex failure
    _GC + " -F - -- mine.py <<EOF\n$(" + _GC + " -qam sneaky)\nEOF",       # heredoc body runs
    _GC + " -F - -- mine.py <<< \"$(" + _GC + " -qam sneaky)\"",          # here-string
    _GC + " -m x -- mine.py '>foreign.py'",                              # quoted file name
    _GC + " -m x -- mine.py > log.txt",
    _GC + " -m x -- mine.py 2>&1",
    _GC + " --amend --no-edit -- mine.py",
    _GC + ' -m "fix"& mine.py',                        # operator glued to a value
    _GC + " -m x&& mine.py",
    _GC + " -m x|mine.py x.py",
    _GC + " -m *.py mine.py",                          # glob in an option value
    _GC + " -m {x,foreign.py} mine.py",                # brace expansion in a value
])
def test_other_shapes_keep_every_staged_file(repo, cmd):
    pending = _pending_commit_paths(str(repo), cmd)
    assert pending is not None
    assert _abs(repo, *_STAGED) <= pending, sorted(pending)


def test_symlink_pathspec_is_not_narrowed(repo):
    os.symlink("mine.py", repo / "link.py")
    _git(repo, "add", "link.py")
    _git(repo, "commit", "-q", "-m", "link", "--", "link.py")
    pending = _pending_commit_paths(str(repo), _GC + " -m x -- link.py")
    assert _abs(repo, *_STAGED) <= pending


# ---------------------------------------------------------------------------
# Widening fixes in the general parser.
# ---------------------------------------------------------------------------

def test_dash_u_keeps_the_following_path_in_the_general_path(repo):
    pending = _pending_commit_paths(str(repo), "echo hi; " + _GC + " -u mine.py -m x")
    assert _abs(repo, "mine.py") <= pending


def test_pathspec_from_file_sweeps_tracked_working_tree_changes(repo):
    pending = _pending_commit_paths(str(repo), _GC + " -m x --pathspec-from-file=list.txt")
    assert _abs(repo, *_STAGED, "mine.py", "pkg/a.py", "x.py") <= pending


def test_unborn_head_falls_back_to_the_wide_set(tmp_path):
    root = tmp_path / "repo"
    _init(root)
    _write(root, "a.py", "a\n")
    _write(root, "b.py", "b\n")
    _git(root, "add", "a.py", "b.py")
    assert _abs(root, "a.py", "b.py") <= _pending_commit_paths(str(root), _GC + " -m x -- a.py")


def test_directory_to_file_type_change_is_not_narrowed(tmp_path):
    root = tmp_path / "repo"
    _init(root)
    _write(root, "p/x.py", "x\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "rm", "-q", "-r", "p")
    _write(root, "p", "now a file\n")
    _git(root, "add", "p")
    pending = _pending_commit_paths(str(root), _GC + " -m x -- p")
    assert _abs(root, "p/x.py") <= pending, sorted(pending)



# ---------------------------------------------------------------------------
# Differential: what real git commits is always inside pending.
# ---------------------------------------------------------------------------

_DIFFERENTIAL = [
    # (command, exact): exact → pending must EQUAL what git committed
    (_GC + ' -m "fix" -- mine.py', True),
    (_GC + " -m fix mine.py", True),
    (_GC + " -o mine.py -m fix", True),
    (_GC + " -om fix mine.py", True),
    (_GC + " -m fix -u mine.py", True),
    (_GC + " -m x -- mine.py pkg/a.py gone.py", True),
    (_GC + " --amend --no-edit -- mine.py", False),
    (_GC + " " + _HEREDOC_MSG + " -- mine.py", True),
    (_GC + " -q -s -m fix --no-verify -- mine.py", True),
    (_GC + " --trailer 'Ref: 1' -m fix -- mine.py", True),
    (_GC + " -m fix -- mine.py 2>&1", False),
    (_GC + ' -m "feat(gate): narrow & fix" -- mine.py', True),
    (_GC + ' -m "fix"& mine.py', False),
    (_GC + " -m x&& mine.py", False),
    (_GC + " -m *.py mine.py", False),
    (_GC + " -m {x,foreign.py} mine.py", False),
    (_GC + " -m x", True),
    (_GC + " -a -m x", False),
    (_GC + " -m x -i mine.py", False),
    (_GC + " -m x --tra mine.py", False),
    (_GC + " -m x -- pkg", False),
    (_GC + " -m x -- 'pkg/*.py'", False),
    ("git -C sub commit -m x -- x.py", False),
    ("git add mine.py && " + _GC + " -m x -- mine.py", False),
    ("echo evil > x.py && " + _GC + " -m x -- x.py", False),
    ("cd sub && " + _GC + " -m x -- x.py", False),
    ("(cd sub && " + _GC + " -m x -- x.py)", False),
    ("x=$(cd sub && " + _GC + " -m m -- x.py; true)", False),
    ("x=`cd sub && " + _GC + " -m m -- x.py; true`", False),
    ("f() { cd sub; }; f && " + _GC + " -m x -- x.py", False),
    (_GC + " -m x -- mine.py; nice -n 1 " + _GC + " -m b", False),
    (_GC + ' -m "$(cd sub && ' + _GC + ' -m y)" -- mine.py', False),
    ("GIT_LITERAL_PATHSPECS=1 " + _GC + " -m x -- 'pkg/*.py'", False),
    (_GC + " -m x --pathspec-from-file=list.txt", False),
    (_GC + " -m x -- untracked.py", False),
    # Not in the matrix: a commit nested inside a heredoc body or substitution
    # (`<<EOF\n$(git commit -a)`). The fast path rejects those shapes (unit tests above),
    # but the general path cannot see a nested commit's -a sweep either — a pre-existing
    # gap outside this change.
]


def test_quoted_redirect_shaped_file_name_is_not_dropped(repo):
    _write(repo, ">x.py", "base\n")
    _git(repo, "add", ">x.py")
    _git(repo, "commit", "-q", "-m", "odd name", "--", ">x.py")
    _write(repo, ">x.py", "changed\n")
    pending = _pending_commit_paths(str(repo), _GC + " -m x -- mine.py '>x.py'")
    assert _abs(repo, ">x.py") <= pending, sorted(pending)


def test_deleted_directory_prefix_is_not_narrowed(repo):
    import shutil
    shutil.rmtree(repo / "pkg")  # pkg/a.py tracked, now deleted with its directory
    pending = _pending_commit_paths(str(repo), _GC + " -m x -- pkg")
    assert _abs(repo, *_STAGED) <= pending


def test_fd_dup_named_file_disables_the_fast_path(repo):
    _write(repo, "2>&1", "base\n")
    pending = _pending_commit_paths(str(repo), _GC + " -m x -- mine.py '2>&1'")
    assert _abs(repo, *_STAGED) <= pending


@pytest.mark.parametrize("cmd,exact", _DIFFERENTIAL)
def test_differential_pending_contains_what_git_commits(repo, cmd, exact):
    (repo / "list.txt").write_text("mine.py\n")
    base = _git(repo, "rev-parse", "HEAD").strip()
    pending = _pending_commit_paths(str(repo), cmd)
    assert pending is not None

    subprocess.run(["bash", "-c", cmd], cwd=str(repo), capture_output=True, text=True,
                   stdin=subprocess.DEVNULL, timeout=30)
    head = _git(repo, "rev-parse", "HEAD").strip()
    committed: set[str] = set()
    if head != base:
        # `base` may have been amended away; diff against its tree either way.
        names = _git(repo, "diff", "--name-only", "--no-renames", base, head).split()
        committed = _abs(repo, *names)

    # A directory / glob pathspec enters pending as itself (e.g. ".../pkg"); review markers
    # list files, so such an entry can never be covered and the gate DENIES — the files
    # under it are therefore not a fail-open. Count them as seen.
    def _seen(f: str) -> bool:
        return f in pending or any(
            f.startswith(p + os.sep) or any(c in p for c in "*?[") for p in pending
        )

    unseen = sorted(f for f in committed if not _seen(f))
    assert not unseen, f"FAIL-OPEN: git committed files the gate did not see: {unseen}"
    if exact:
        assert pending == committed, (
            f"pending {sorted(pending)} != committed {sorted(committed)}"
        )


# ---------------------------------------------------------------------------
# Gate level: the adversarial commit gate approves a reviewed pathspec commit even
# though another session has staged unreviewed files, and still denies a commit that
# would take those files.
# ---------------------------------------------------------------------------

def test_adversarial_gate_approves_reviewed_pathspec_commit(repo, tmp_path, monkeypatch):
    import asyncio
    import json

    from core import security_hooks

    audit = tmp_path / "audit"
    audit.mkdir()
    monkeypatch.setattr(security_hooks, "_AGENT_AUDIT_DIR", audit)
    monkeypatch.delenv("SWARM_ADVERSARIAL_GATE_FORCE", raising=False)
    (audit / "session_sess-A_adv_1.marker").write_text(
        json.dumps({"adversarial": True, "reviewed_paths": sorted(_abs(repo, "mine.py"))}))
    gate = security_hooks.create_adversarial_commit_gate({"sdk_session_id": "sess-A"})

    def decide(cmd):
        out = asyncio.run(gate({"tool_name": "Bash", "cwd": str(repo),
                                "tool_input": {"command": cmd}}, None, None))
        return out.get("hookSpecificOutput", {}).get("permissionDecision", "approve")

    assert decide(_GC + " -m fix -- mine.py") != "deny"
    assert decide(_GC + " -m fix") == "deny"
    assert decide("cd sub && " + _GC + " -m fix -- x.py") == "deny"
