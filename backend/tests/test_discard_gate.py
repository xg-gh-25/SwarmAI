"""Acceptance tests for the "discards another session's uncommitted work" rule of
dangerous_command_gate (replaces the history-rewrite rule).

What git can lose: a ref move (rebase, amend, reset to another commit, branch -f,
update-ref) keeps the old commits in the reflog, so it is recoverable. Discarding the
working tree (reset --hard/--merge, checkout -f / -- <path>, restore, switch
--discard-changes, clean -f, worktree remove --force) is not. The gate asks only when
such a discard runs in a repo where ANOTHER session wrote files that git still reports
as uncommitted (shared_checkout.sibling_uncommitted, the file tracker's facts).

Methodology: real git repos under tmp_path (main checkout + a linked worktree), a fake
session router holding hook contexts with ``_files_written``, the real gate. The
approval wait is faked to "deny", so an ask shows up as a deny result and an approve
as ``{"decision": "approve"}``.

Invariants:
  - the commands that prompted on 2026-10-24/25 (commit + rebase + ff-merge in a
    linked worktree, reset --hard in it) approve while a sibling has uncommitted
    edits in the main checkout;
  - a discard where the sibling's uncommitted files are → ask;
  - this session's own uncommitted files, a sibling's committed files, no sibling → approve;
  - a discard whose directory cannot be followed → judged against every repo a
    sibling wrote in; git failing → ask.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from core import repo_guidance as rg
from core import shared_checkout as sc

ME, SIB = "sess-me", "sess-sib"


def _g(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    sc._dirty_cache.clear()
    rg._root_cache.clear()
    # Where the repos live must not matter: a user's checkout is not under a temp dir,
    # so the test's own temp dir must not read as one either (run with --basetemp
    # outside /tmp, e.g. under $KIROCREW_SCRATCH).
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", "/nonexistent-temp-root")
    monkeypatch.setattr("core.security_hooks.get_app_data_dir", lambda: tmp_path / "appdata")
    (tmp_path / "appdata").mkdir()
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def repos(tmp_path):
    main = tmp_path / "repo"
    main.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(main)], check=True)
    for n in ("a.py", "b.py"):
        (main / n).write_text(n + "\n")
    _g(main, "add", "-A")
    _g(main, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "init")
    wt = tmp_path / "scratch" / "wt-topic"
    _g(main, "worktree", "add", "-q", "-b", "kiro/topic", str(wt), "main")
    return SimpleNamespace(main=main.resolve(), wt=wt.resolve())


@pytest.fixture
def router(monkeypatch):
    """Both the shape shared_checkout reads (_units + _hook_session_context) and the
    one the old rule read (list_units + is_alive), so the old code sees a live sibling."""
    from core import session_registry
    units = {}

    def add(sid):
        ctx = {"sdk_session_id": sid, "_files_written": set()}
        units[sid] = SimpleNamespace(_hook_session_context=ctx, session_id=sid, is_alive=True)
        return ctx
    monkeypatch.setattr(session_registry, "session_router",
                        SimpleNamespace(_units=units, list_units=lambda: list(units.values())),
                        raising=False)
    return add


def _gate(ctx):
    from core.permission_manager import PermissionManager
    from core.security_hooks import create_dangerous_command_gate
    pm = PermissionManager()

    async def _deny(request_id, timeout=300):
        return "deny"
    pm.wait_for_permission_decision = _deny  # type: ignore[method-assign]
    return create_dangerous_command_gate(ctx, ME, pm, enable_human_approval=True)


async def _run(ctx, command, cwd):
    return await _gate(ctx)({"tool_name": "Bash", "cwd": str(cwd),
                             "tool_input": {"command": command}}, "tu-1", None)


def _asked(result) -> bool:
    return result != {"decision": "approve"}


def _sibling_dirty(router, path, text="edited\n"):
    """A sibling session wrote *path* and left it uncommitted."""
    path.write_text(text)
    router(SIB)["_files_written"].add(str(path.resolve()))


# ── the commands that prompted: worktree flow while a sibling edits main ─────
@pytest.mark.asyncio
@pytest.mark.parametrize("make_cmd", [
    lambda r: (f"cd {r.wt} && echo x > c.py && git add c.py && git commit -qm c "
               f"&& git rebase -q main && git -C {r.main} merge --ff-only kiro/topic"),
    lambda r: f"cd {r.wt} && git reset -q --hard HEAD",
    lambda r: f"git -C {r.wt} reset -q --hard main",
    lambda r: f"cd {r.wt} && git commit -q --amend --allow-empty --no-edit",
])
async def test_worktree_flow_is_not_asked_while_a_sibling_edits_main(repos, router, make_cmd):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    assert not _asked(await _run(me, make_cmd(repos), repos.wt))
    assert (repos.main / "a.py").read_text() == "edited\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", [
    "git rebase main",
    "git commit --amend --no-edit --allow-empty",
    "git reset --soft HEAD",
    "git branch -f other HEAD",
    "git update-ref refs/heads/other HEAD",
    "git checkout main",
    "git restore --staged a.py",
    "git reset -q -- a.py",
    "git clean -n",
])
async def test_a_ref_move_or_non_discard_in_main_is_not_asked(repos, router, cmd):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    assert not _asked(await _run(me, cmd, repos.main))


# ── discards that would destroy the sibling's uncommitted file ───────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", [
    "git reset --hard",
    "git reset --hard HEAD~0",
    "git reset --merge",
    "git checkout -- a.py",
    "git checkout .",
    "git checkout a.py",
    "git checkout -f main",
    "git restore a.py",
    "git restore --staged --worktree a.py",
    "git switch --discard-changes main",
    "echo hi && git reset --hard",
    "bash <<'EOF'\ngit reset --hard\nEOF",
])
async def test_a_discard_over_a_siblings_uncommitted_file_is_asked(repos, router, cmd):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    assert _asked(await _run(me, cmd, repos.main))


@pytest.mark.asyncio
async def test_clean_over_a_siblings_new_untracked_file_is_asked(repos, router):
    me = router(ME)
    _sibling_dirty(router, repos.main / "new.py")
    assert _asked(await _run(me, "git clean -fd", repos.main))


@pytest.mark.asyncio
async def test_worktree_remove_force_over_a_siblings_edit_is_asked(repos, router, tmp_path):
    me = router(ME)
    _sibling_dirty(router, repos.wt / "a.py")
    assert _asked(await _run(me, f"git worktree remove --force {repos.wt}", repos.main))
    # the sibling's edit is in the worktree, not in main: discarding main is fine
    assert not _asked(await _run(me, "git reset --hard", repos.main))


@pytest.mark.asyncio
async def test_a_discard_from_another_directory_is_judged_where_it_runs(repos, router, tmp_path):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    assert _asked(await _run(me, f"git -C {repos.main} reset --hard", tmp_path))
    assert _asked(await _run(me, f"cd {repos.main} && git reset --hard", tmp_path))


# ── nothing of anyone else's at stake ────────────────────────────────────────
@pytest.mark.asyncio
async def test_own_uncommitted_files_are_not_asked(repos, router):
    me = router(ME)
    router(SIB)
    (repos.main / "a.py").write_text("mine\n")
    me["_files_written"].add(str((repos.main / "a.py").resolve()))
    assert not _asked(await _run(me, "git reset --hard", repos.main))


@pytest.mark.asyncio
async def test_a_siblings_committed_file_is_not_asked(repos, router):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    _g(repos.main, "-c", "core.hooksPath=/dev/null", "commit", "-qam", "sib")
    assert not _asked(await _run(me, "git reset --hard", repos.main))


@pytest.mark.asyncio
async def test_a_solo_session_is_not_asked(repos, router):
    me = router(ME)
    (repos.main / "a.py").write_text("hand edit\n")
    assert not _asked(await _run(me, "git reset --hard", repos.main))


# ── cannot tell ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", [
    "cd $X && git reset --hard",
    'eval "cd $X" && git reset --hard',
    "GIT_DIR=/elsewhere/.git git reset --hard",
    "git --git-dir=/elsewhere/.git reset --hard",
    'git reset --hard "unbalanced',
])
async def test_an_unfollowed_discard_is_judged_against_every_sibling_repo(repos, router,
                                                                          tmp_path, cmd):
    me = router(ME)
    assert not _asked(await _run(me, cmd, tmp_path))      # no sibling edits anywhere
    _sibling_dirty(router, repos.main / "a.py")
    assert _asked(await _run(me, cmd, tmp_path))


@pytest.mark.asyncio
async def test_git_failing_is_asked(repos, router, monkeypatch):
    me = router(ME)
    _sibling_dirty(router, repos.main / "a.py")
    monkeypatch.setattr(sc, "_git_status", lambda root, rels: None)
    assert _asked(await _run(me, "git reset --hard", repos.main))
    # the inject-only note treats the same failure as "nothing to say"
    assert sc.sibling_uncommitted(str(repos.main), me) == []
