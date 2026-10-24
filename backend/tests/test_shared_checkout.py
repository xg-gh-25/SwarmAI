"""Tests for core.shared_checkout and scripts/session_worktree (design
2026-10-24-swarmai-usable-by-anyone §3.8): native work in a checkout another live
session has uncommitted edits in.

Methodology: real git repos under tmp_path; a fake session router holding hook
contexts (the file tracker's ``_files_written`` is driven through the real
PostToolUse hook). Invariants:
  - another session's written + still-uncommitted file → one note per repo, never a deny;
  - its reads, its committed files, and this session's own edits → no note;
  - a lone session runs no git (no subprocess);
  - session_worktree create → edit+commit → land (ff into the main branch, the other
    session's uncommitted edit untouched) → remove; remove refuses unmerged work.
"""
import asyncio
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import repo_guidance as rg
from core import shared_checkout as sc
from core.runtime_hooks import create_file_tracker


def _g(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    sc._dirty_cache.clear()
    rg._root_cache.clear()
    rg._excluded_cache.clear()
    data = tmp_path / "appdata"
    ws = data / "SwarmWS"
    ws.mkdir(parents=True)
    monkeypatch.setattr("config.get_app_data_dir", lambda: data)
    monkeypatch.setattr("core.project_registry.get_swarmws", lambda: ws)
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    yield {"data": data}


@pytest.fixture
def router(monkeypatch):
    units = {}
    from core import session_registry
    monkeypatch.setattr(session_registry, "session_router",
                        SimpleNamespace(_units=units), raising=False)

    def add(sid):
        ctx = {"sdk_session_id": sid}
        units[sid] = SimpleNamespace(_hook_session_context=ctx)
        return ctx
    return add


def _repo(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "a.py").write_text("a = 1\n")
    (path / "b.py").write_text("b = 1\n")
    _g(path, "add", "-A")
    _g(path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "init")
    return path


def _track(ctx, tool, **ti):
    hook = create_file_tracker(ctx)
    asyncio.run(hook({"tool_name": tool, "tool_input": ti, "cwd": ti.pop("cwd", "")},
                     None, MagicMock()))


def _note(ctx, event, tool, **ti):
    hook = sc.create_shared_checkout_hook(ctx, event)
    out = asyncio.run(hook({"tool_name": tool, "tool_input": ti, "cwd": ti.pop("cwd", "")},
                           None, MagicMock()))
    assert "permissionDecision" not in json.dumps(out)        # inject-only, never a deny
    return (out.get("hookSpecificOutput") or {}).get("additionalContext", "")


def test_sibling_uncommitted_write_is_noted_once(tmp_path, router):
    repo = _repo(tmp_path / "proj")
    other, me = router("other-session-1"), router("me")
    (repo / "a.py").write_text("a = 2\n")
    _track(other, "Edit", file_path=str(repo / "a.py"))
    text = _note(me, "PostToolUse", "Read", file_path=str(repo / "b.py"))
    assert "Another live session has uncommitted changes" in text
    assert "a.py" in text and "other-se" in text
    assert "scripts.session_worktree create" in text
    assert _note(me, "PreToolUse", "Edit", file_path=str(repo / "b.py")) == ""  # once per repo


def test_reads_committed_files_and_own_edits_are_not_noted(tmp_path, router):
    repo = _repo(tmp_path / "proj")
    other, me = router("other"), router("me")
    _track(other, "Read", file_path=str(repo / "a.py"))            # a read is not a write
    (repo / "b.py").write_text("b = 2\n")
    _track(other, "Write", file_path=str(repo / "b.py"))
    _g(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qam", "other committed")
    (repo / "a.py").write_text("mine\n")
    _track(me, "Edit", file_path=str(repo / "a.py"))               # my own edit
    assert _note(me, "PreToolUse", "Edit", file_path=str(repo / "a.py")) == ""


def test_bash_write_target_counts_and_untracked_new_file(tmp_path, router):
    repo = _repo(tmp_path / "proj")
    other, me = router("other"), router("me")
    _track(other, "Bash", command="echo hi > new.txt", cwd=str(repo))
    (repo / "new.txt").write_text("hi\n")
    assert "new.txt" in _note(me, "PostToolUse", "Bash", command="ls", cwd=str(repo))


def test_lone_session_runs_no_git(tmp_path, router, monkeypatch):
    repo = _repo(tmp_path / "proj")
    me = router("me")
    called = []
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: called.append(a))
    assert _note(me, "PreToolUse", "Edit", file_path=str(repo / "a.py")) == ""
    assert called == []


def test_swarmws_is_skipped(tmp_path, router, _iso):
    ws = _iso["data"] / "SwarmWS"
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    other, me = router("other"), router("me")
    (ws / "x.md").write_text("x\n")
    _track(other, "Write", file_path=str(ws / "x.md"))
    assert _note(me, "PreToolUse", "Edit", file_path=str(ws / "y.md")) == ""


# ── scripts/session_worktree ─────────────────────────────────────────────────

def _cli(*args):
    from scripts import session_worktree as sw
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sw.main(list(args))
    return rc, json.loads(buf.getvalue())


def test_create_land_remove_keeps_the_other_sessions_edit(tmp_path):
    repo = _repo(tmp_path / "proj")
    (repo / "b.py").write_text("other session, uncommitted\n")     # someone else's edit
    rc, out = _cli("create", str(repo), "--name", "t1")
    assert rc == 0 and out["branch"] == "session/t1" and out["base_branch"] == "main"
    wt = out["path"]
    assert wt.startswith(str(tmp_path / "appdata" / "session-worktrees"))
    assert (open(os.path.join(wt, "b.py")).read() == "b = 1\n")    # not carried over
    with open(os.path.join(wt, "a.py"), "w") as f:
        f.write("a = 3\n")
    rc, out = _cli("land", wt)
    assert rc == 1 and "uncommitted" in out["error"]               # commit first
    _g(wt, "-c", "core.hooksPath=/dev/null", "commit", "-qam", "mine")
    rc, out = _cli("land", wt)
    assert rc == 0, out
    assert (repo / "a.py").read_text() == "a = 3\n"
    assert (repo / "b.py").read_text() == "other session, uncommitted\n"
    rc, out = _cli("remove", wt)
    assert rc == 0 and not os.path.exists(wt)
    assert "session/t1" not in _g(repo, "branch", "--list")


def test_remove_refuses_unmerged_work(tmp_path):
    repo = _repo(tmp_path / "proj")
    rc, out = _cli("create", str(repo), "--name", "t2")
    wt = out["path"]
    with open(os.path.join(wt, "a.py"), "w") as f:
        f.write("unlanded\n")
    _g(wt, "-c", "core.hooksPath=/dev/null", "commit", "-qam", "unlanded")
    rc, out = _cli("remove", wt)
    assert rc == 1 and "not merged" in out["error"] and os.path.exists(wt)


def test_land_refuses_a_squash_that_undoes_the_base(tmp_path):
    """The 2026-10-24 incident: `reset --soft main` from an old tree, then land."""
    repo = _repo(tmp_path / "proj")
    rc, out = _cli("create", str(repo), "--name", "t3")
    wt = out["path"]
    (repo / "b.py").write_text("b = 2  # landed by another session\n")
    _g(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qam", "other session")
    with open(os.path.join(wt, "a.py"), "w") as f:
        f.write("a = 9\n")
    _g(wt, "add", "-A")
    _g(wt, "reset", "-q", "--soft", "main")
    _g(wt, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "squashed")
    rc, out = _cli("land", wt)
    assert rc == 1 and "b.py" in out["error"] and "merge-base" in out["error"]
    assert (repo / "b.py").read_text() == "b = 2  # landed by another session\n"


def test_land_and_remove_only_touch_session_worktrees(tmp_path):
    repo = _repo(tmp_path / "proj")
    rc, out = _cli("remove", str(repo))
    assert rc == 1 and "not a session worktree" in out["error"]
