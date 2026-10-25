"""Tests for core.turn_changes — one turn's code changes for the "N files changed" card.

What is tested (real git repos in tmp_path, no daemon):
- a turn's edit / new file / deletion become rows with +/- counts and status; the
  diff is fetched on demand (diff_text) from the two change trees, never stored
- only files the turn wrote are listed: another session's edit elsewhere in the
  same repo never shows, including a glob-looking name (`[id].ts` vs `i.ts`)
- a commit made during the turn does not hide its change (content-based)
- the real index and working tree are never touched by the snapshots
- an Edit/Write tool path counts (a dot-directory file has no file_changed)
- a binding that is a subdirectory of its repo still yields rows (toplevel paths)
- a tree whose start snapshot failed is left off (no invented changes); a run
  worktree that went live mid-turn diffs from its HEAD; a tree that is not live is
  left off; hundreds of new files are added in one call
- a file written back to its start content is not a change; nothing touched → None
- a file over MAX_DIFF_LINES is not diffable; MAX_FILES caps rows
- one card per turn; finish() never waits past FINISH_BUDGET_S
- files outside the code trees (SwarmWS content) are not code changes
- diff_text refuses unknown repos, malformed ids and unsafe paths
- SessionRouter._persist_stream_event: tool paths and file_changed are noted; the
  block is persisted and delivered just before `result`, ahead of turn_deliverables
- context_injector keeps a turn_changes row out of a resumed prompt
- needs_human_review: Knowledge/DailyActivity is process, not a product
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core import turn_changes as tc
from core.turn_changes import TurnChanges


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _init(r: Path) -> Path:
    r.mkdir(parents=True)
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    return Path(os.path.realpath(r))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = _init(tmp_path / "repo")
    (r / "a.py").write_text("one\ntwo\nthree\n")
    (r / "b.py").write_text("keep\n")
    (r / "gone.py").write_text("bye\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    monkeypatch.setattr(tc, "code_roots", lambda: [str(r)])   # diff_text's allowlist
    return r


async def _started(roots) -> TurnChanges:
    t = TurnChanges.start(roots)
    await t.ready()
    return t


def _fc(path: Path, op: str = "written") -> dict:
    return {"type": "file_changed", "absolutePath": str(path), "path": path.name, "operation": op}


def _rows(block):
    return {f["path"]: f for f in block["files"]}


def _diff(row):
    return tc.diff_text(row["gitRoot"], row["start"], row["end"], row["path"])


@pytest.mark.asyncio
async def test_edit_new_and_delete_become_rows(repo):
    t = await _started([str(repo)])
    (repo / "a.py").write_text("one\nTWO\nthree\nfour\n")
    (repo / "new.py").write_text("x\ny\n")
    (repo / "gone.py").unlink()
    for p, op in ((repo / "a.py", "written"), (repo / "new.py", "written"), (repo / "gone.py", "deleted")):
        t.note(_fc(p, op))
    block = await t.finish()
    rows = _rows(block)
    assert block["type"] == "turn_changes"
    assert rows["a.py"]["status"] == "modified" and (rows["a.py"]["added"], rows["a.py"]["deleted"]) == (2, 1)
    assert rows["new.py"]["status"] == "added" and rows["new.py"]["added"] == 2
    assert rows["gone.py"]["status"] == "deleted" and rows["gone.py"]["deleted"] == 1
    assert all("diff" not in r for r in rows.values())          # never stored
    d = _diff(rows["a.py"])
    assert "+TWO" in d and "-two" in d and rows["a.py"]["diffable"]
    assert "+x" in _diff(rows["new.py"])
    assert rows["a.py"]["repo"] == "repo" and rows["a.py"]["absolutePath"] == str(repo / "a.py")


@pytest.mark.asyncio
async def test_another_sessions_edit_never_shows_even_for_a_glob_name(repo):
    (repo / "[id].ts").write_text("route\n")
    (repo / "i.ts").write_text("i\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "routes")
    t = await _started([str(repo)])
    (repo / "[id].ts").write_text("route\nmine\n")
    (repo / "i.ts").write_text("i\nsomeone else\n")   # not noted: not this turn's write
    (repo / "b.py").write_text("keep\nsomeone else\n")
    t.note(_fc(repo / "[id].ts"))
    rows = _rows(await t.finish())
    assert set(rows) == {"[id].ts"}
    assert rows["[id].ts"]["added"] == 1
    assert "someone else" not in _diff(rows["[id].ts"])


@pytest.mark.asyncio
async def test_a_commit_during_the_turn_still_shows(repo):
    t = await _started([str(repo)])
    (repo / "a.py").write_text("one\ntwo\nthree\nfour\n")
    _git(repo, "commit", "-qam", "turn commit")
    t.note(_fc(repo / "a.py"))
    assert _rows(await t.finish())["a.py"]["added"] == 1


@pytest.mark.asyncio
async def test_real_index_and_tree_untouched(repo):
    (repo / "b.py").write_text("keep\nstaged\n")
    _git(repo, "add", "b.py")
    index = repo / ".git" / "index"
    before = index.read_bytes()
    t = await _started([str(repo)])
    (repo / "a.py").write_text("changed\n")
    (repo / "untracked.py").write_text("u\n")
    t.note(_fc(repo / "a.py"))
    t.note(_fc(repo / "untracked.py"))
    await t.finish()
    assert index.read_bytes() == before
    status = _git(repo, "status", "--porcelain").splitlines()
    assert "M  b.py" in status and " M a.py" in status and "?? untracked.py" in status


@pytest.mark.asyncio
async def test_a_tool_path_counts_for_a_dot_directory_file(repo):
    (repo / ".github").mkdir()
    (repo / ".github" / "ci.yml").write_text("on: push\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ci")
    t = await _started([str(repo)])
    (repo / ".github" / "ci.yml").write_text("on: [push, pull_request]\n")
    t.note_tool({"type": "tool_use", "name": "Edit", "input": {"file_path": str(repo / ".github" / "ci.yml")}})
    t.note_tool({"type": "tool_use", "name": "Read", "input": {"file_path": str(repo / "a.py")}})  # a read
    assert set(_rows(await t.finish())) == {".github/ci.yml"}


@pytest.mark.asyncio
async def test_a_subdirectory_binding_uses_toplevel_paths(repo):
    (repo / "sub").mkdir()
    (repo / "sub" / "m.py").write_text("m\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "sub")
    t = await _started([str(repo / "sub")])
    (repo / "sub" / "m.py").write_text("m\nn\n")
    t.note(_fc(repo / "sub" / "m.py"))
    assert _rows(await t.finish())["sub/m.py"]["added"] == 1


@pytest.mark.asyncio
async def test_a_failed_start_leaves_the_tree_off(repo, monkeypatch):
    (repo / "a.py").write_text("uncommitted before the turn\n")   # must never show as this turn's
    real = tc._tree
    calls = {"n": 0}

    def flaky(top, add=()):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("start failed")
        return real(top, add)

    monkeypatch.setattr(tc, "_tree", flaky)
    t = await _started([str(repo)])
    (repo / "a.py").write_text("uncommitted before the turn\nand this turn\n")
    t.note(_fc(repo / "a.py"))
    assert await t.finish() is None


@pytest.mark.asyncio
async def test_a_run_worktree_first_seen_mid_turn_diffs_from_head(tmp_path, monkeypatch):
    home = tmp_path / "worktrees"
    wt = _init(home / "run_x" / "repo")
    (wt / "f.py").write_text("base\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "base")
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(home))
    t = await _started([])                # not live at start
    (wt / "f.py").write_text("base\nrun\n")
    t.note(_fc(wt / "f.py"))
    monkeypatch.setattr(tc, "code_roots", lambda: [str(wt)])   # live by the end
    assert _rows(await t.finish())["f.py"]["added"] == 1


@pytest.mark.asyncio
async def test_a_binding_added_mid_turn_is_left_off(repo, tmp_path, monkeypatch):
    other = _init(tmp_path / "other")
    (other / "f.py").write_text("base\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-qm", "base")
    (other / "f.py").write_text("earlier uncommitted work\n")
    monkeypatch.setenv("SWARM_WORKTREE_ROOT", str(tmp_path / "worktrees"))
    t = await _started([str(repo)])
    t.note(_fc(other / "f.py"))
    monkeypatch.setattr(tc, "code_roots", lambda: [str(repo), str(other)])  # live now, not a run worktree
    assert await t.finish() is None


def test_a_slow_root_cools_off(monkeypatch):
    monkeypatch.setattr(tc, "GIT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(tc, "_toplevel", lambda p: time.sleep(0.06) or None)  # a timeout looks like "no tree"
    monkeypatch.setattr(tc, "_cool_off", {})
    with pytest.raises(RuntimeError):
        tc._snapshot_root("/dead/mount")
    assert tc._cooling("/dead/mount")
    with pytest.raises(RuntimeError, match="cooling off"):
        tc._snapshot_root("/dead/mount")


@pytest.mark.asyncio
async def test_a_tree_that_is_not_live_is_left_off(tmp_path, monkeypatch):
    stale = _init(tmp_path / "stale")
    (stale / "f.py").write_text("base\n")
    _git(stale, "add", "-A")
    _git(stale, "commit", "-qm", "base")
    (stale / "f.py").write_text("earlier uncommitted work\n")
    t = await _started([])
    t.note_tool({"type": "tool_use", "name": "Edit", "input": {"file_path": str(stale / "f.py")}})
    monkeypatch.setattr(tc, "code_roots", lambda: [])
    assert await t.finish() is None


@pytest.mark.asyncio
async def test_hundreds_of_new_files_in_one_add(repo):
    t = await _started([str(repo)])
    for i in range(300):
        p = repo / "gen" / f"f{i}.py"
        p.parent.mkdir(exist_ok=True)
        p.write_text("x\n")
        t.note(_fc(p))
    t0 = time.monotonic()
    rows = _rows(await t.finish())
    assert len(rows) == tc.MAX_FILES and time.monotonic() - t0 < 5.0


@pytest.mark.asyncio
async def test_multiedit_and_notebook_paths(repo):
    t = TurnChanges([str(repo)])
    t.note_tool({"type": "tool_use", "name": "MultiEdit", "input": {"file_path": str(repo / "a.py")}})
    t.note_tool({"type": "tool_use", "name": "NotebookEdit", "input": {"notebook_path": str(repo / "n.ipynb")}})
    t.note_tool({"type": "tool_use", "name": "Write", "input": {"file_path": "relative.py"}})
    assert set(t._raw) == {str(repo / "a.py"), str(repo / "n.ipynb")}


@pytest.mark.asyncio
async def test_unchanged_and_untouched(repo):
    t = await _started([str(repo)])
    assert await t.finish() is None
    t = await _started([str(repo)])
    (repo / "a.py").write_text("x\n")
    (repo / "a.py").write_text("one\ntwo\nthree\n")
    t.note(_fc(repo / "a.py"))
    assert await t.finish() is None


@pytest.mark.asyncio
async def test_finish_is_computed_once_per_turn_and_the_budget_holds(repo, monkeypatch):
    """result and Stop can both finish a turn (design 2026-10-25-turn-result-card.md
    §5.1): every call gets the same block, in-place rows included, never a second
    card without them."""
    import asyncio as _a
    t = await _started([str(repo)])
    (repo / "a.py").write_text("z\n")
    t.note(_fc(repo / "a.py"))
    first, second = await _a.gather(t.finish(), t.finish())
    third = await t.finish()
    assert first is not None and first == second == third
    assert first is not second, "each caller gets its own copy"
    assert set(_rows(third)) == {"a.py"}

    t = await _started([str(repo)])
    (repo / "a.py").write_text("y\n")
    t.note(_fc(repo / "a.py"))
    monkeypatch.setattr(tc, "FINISH_BUDGET_S", 0.2)
    monkeypatch.setattr(tc, "_diff_top", lambda *a: time.sleep(1.0) or [])
    t0 = time.monotonic()
    assert await t.finish() is None
    assert time.monotonic() - t0 < 0.9


@pytest.mark.asyncio
async def test_a_cancelled_first_caller_does_not_take_the_card_away(repo):
    import asyncio as _a
    t = await _started([str(repo)])
    (repo / "a.py").write_text("q\n")
    t.note(_fc(repo / "a.py"))
    first = _a.ensure_future(t.finish())
    await _a.sleep(0)
    first.cancel()
    block = await t.finish()
    assert block is not None and set(_rows(block)) == {"a.py"}


@pytest.mark.asyncio
async def test_this_turns_landings_join_the_card(repo, monkeypatch):
    """The card's landings are read for this session's turn id (core/session_landings);
    a turn that wrote nothing in place but landed still gets a card."""
    seen = {}
    def _read(session_id, *, deadline, turn_id=None, limit=None, **kw):
        seen.update(session_id=session_id, turn_id=turn_id, limit=limit)
        return {"landed": [{"repo": "r", "branch": "session/x", "dest": "main", "count": 1,
                            "files": [{"path": "f.py", "added": 1, "deleted": 0}]}],
                "unverified": 1, "truncated": True, "unknown": [{"repo": "q", "reason": "no-reflog"}]}
    from core import session_landings
    monkeypatch.setattr(session_landings, "read_session", _read)
    t = TurnChanges.start([str(repo)], session_id="s1", turn_id="t" * 32)
    await t.ready()
    block = await t.finish()
    assert seen == {"session_id": "s1", "turn_id": "t" * 32, "limit": tc.MAX_LANDINGS}
    assert block["type"] == "turn_changes" and block["files"] == []
    assert block["turn_id"] == "t" * 32 and block["landed"][0]["branch"] == "session/x"
    assert block["landed_truncated"] is True and block["unverified"] == 1
    assert block["unknown"] == [{"repo": "q", "reason": "no-reflog"}]


@pytest.mark.asyncio
async def test_no_turn_id_reads_no_landings_and_nothing_is_no_card(repo, monkeypatch):
    from core import session_landings
    calls = []
    monkeypatch.setattr(session_landings, "read_session", lambda *a, **k: calls.append(k) or {
        "landed": [], "unverified": 0, "truncated": False, "unknown": []})
    t = TurnChanges.start([str(repo)], session_id="s1", turn_id=None)
    await t.ready()
    assert await t.finish() is None
    assert calls == []


@pytest.mark.asyncio
async def test_a_turn_that_handed_no_land_its_id_reads_no_landings(repo, monkeypatch):
    """No land of this turn means no stamp of this turn: no git call at turn end."""
    from core import session_landings
    calls = []
    monkeypatch.setattr(session_landings, "read_session", lambda *a, **k: calls.append(k) or {
        "landed": [], "unverified": 0, "truncated": False, "unknown": []})
    ctx = {"_turn_id": "t" * 32, "_turn_lands": 0}
    t = TurnChanges.start([str(repo)], session_id="s1", turn_id="t" * 32, context=ctx)
    await t.ready()
    assert await t.finish() is None
    assert calls == []


@pytest.mark.asyncio
async def test_the_landings_read_expects_the_turns_land_calls(repo, monkeypatch):
    from core import session_landings
    seen = {}
    monkeypatch.setattr(session_landings, "read_session", lambda sid, **k: seen.update(k) or {
        "landed": [], "unverified": 0, "truncated": False, "unknown": []})
    ctx = {"_turn_id": "t" * 32, "_turn_lands": 0}
    t = TurnChanges.start([str(repo)], session_id="s1", turn_id="t" * 32, context=ctx)
    await t.ready()
    ctx["_turn_lands"] = 2           # counted by run_owner_gate while the turn ran
    await t.finish()
    assert seen["expect"] == 2 and seen["turn_id"] == "t" * 32


@pytest.mark.asyncio
async def test_the_row_budget_spans_landings_and_in_place_rows(repo, monkeypatch):
    many = [{"path": f"f{i}.py", "added": 1, "deleted": 0} for i in range(tc.MAX_FILES + 5)]
    from core import session_landings
    monkeypatch.setattr(session_landings, "read_session", lambda *a, **k: {
        "landed": [{"repo": "r", "branch": "session/x", "files": many}],
        "unverified": 0, "truncated": False, "unknown": []})
    t = TurnChanges.start([str(repo)], session_id="s1", turn_id="t" * 32)
    await t.ready()
    (repo / "a.py").write_text("w\n")
    t.note(_fc(repo / "a.py"))
    block = await t.finish()
    shown = len(block["files"]) + sum(len(x["files"]) for x in block["landed"])
    assert shown == tc.MAX_FILES
    assert block["files"] and block["landed"][0]["omitted"] == 6


@pytest.mark.asyncio
async def test_two_landings_share_one_row_budget(repo, monkeypatch):
    half = [{"path": f"f{i}.py", "added": 1, "deleted": 0} for i in range(tc.MAX_FILES - 10)]
    from core import session_landings
    monkeypatch.setattr(session_landings, "read_session", lambda *a, **k: {
        "landed": [{"repo": "r", "branch": "session/a", "files": half},
                   {"repo": "r", "branch": "session/b", "files": list(half)}],
        "unverified": 0, "truncated": False, "unknown": []})
    t = TurnChanges.start([str(repo)], session_id="s1", turn_id="t" * 32)
    await t.ready()
    block = await t.finish()
    a, b = block["landed"]
    assert len(a["files"]) == tc.MAX_FILES - 10 and len(b["files"]) == 10
    assert b["omitted"] == tc.MAX_FILES - 20


@pytest.mark.asyncio
async def test_outside_the_code_trees_is_not_a_code_change(repo, tmp_path):
    other = _init(tmp_path / "ws")
    t = await _started([str(repo)])
    (other / "note.md").write_text("knowledge\n")
    t.note(_fc(other / "note.md"))
    assert await t.finish() is None


@pytest.mark.asyncio
async def test_big_file_not_diffable_and_rows_capped(repo, monkeypatch):
    monkeypatch.setattr(tc, "MAX_DIFF_LINES", 50)
    monkeypatch.setattr(tc, "MAX_FILES", 2)
    t = await _started([str(repo)])
    (repo / "a.py").write_text("".join(f"line {i}\n" for i in range(100)))
    (repo / "b.py").write_text("keep\nmore\n")
    (repo / "c.py").write_text("c\n")
    for n in ("a.py", "b.py", "c.py"):
        t.note(_fc(repo / n))
    block = await t.finish()
    rows = _rows(block)
    assert block["omitted"] == 1 and len(rows) == 2
    assert rows["a.py"]["diffable"] is False and rows["a.py"]["added"] == 100
    assert rows["b.py"]["diffable"] is True


@pytest.mark.asyncio
async def test_diff_text_refuses_what_it_should(repo, tmp_path):
    t = await _started([str(repo)])
    (repo / "a.py").write_text("q\n")
    t.note(_fc(repo / "a.py"))
    row = _rows(await t.finish())["a.py"]
    assert _diff(row)
    other = _init(tmp_path / "other")
    assert tc.diff_text(str(other), row["start"], row["end"], "a.py") is None   # not a code tree
    assert tc.diff_text(row["gitRoot"], "HEAD", row["end"], "a.py") is None    # not a tree id
    assert tc.diff_text(row["gitRoot"], row["start"], row["end"], "../x") is None
    assert tc.diff_text(row["gitRoot"], row["start"], row["end"], "/etc/passwd") is None
    assert tc.diff_text(row["gitRoot"], row["start"], row["end"], ".") is None      # whole tree
    assert tc.diff_text(row["gitRoot"], row["start"], row["end"], "sub/") is None
    (repo / "pkg").mkdir()
    (repo / "pkg" / "m.py").write_text("m\n")
    t = await _started([str(repo)])
    (repo / "pkg" / "m.py").write_text("m\nn\n")
    t.note(_fc(repo / "pkg" / "m.py"))
    prow = _rows(await t.finish())["pkg/m.py"]
    assert _diff(prow)
    assert tc.diff_text(prow["gitRoot"], prow["start"], prow["end"], "pkg") is None   # a directory


# ── router: noted during the turn, delivered just before `result` ────────────

class _Router:
    from core.session_router import SessionRouter as _SR
    _compact_tool_results = staticmethod(_SR._compact_tool_results)
    _persist_assistant_blocks = staticmethod(_SR._persist_assistant_blocks)
    _RESULT_FLUSH_EVENTS = _SR._RESULT_FLUSH_EVENTS
    _DELIVERABLE_KINDS = _SR._DELIVERABLE_KINDS
    _note_deliverable = classmethod(_SR._note_deliverable.__func__)
    _persist_stream_event = _SR._persist_stream_event
    _record_canvas_output = staticmethod(_SR._record_canvas_output)


class _FakeChanges:
    def __init__(self):
        self.noted, self.tools = [], []

    def note(self, event):
        self.noted.append(event["absolutePath"])

    def note_tool(self, block):
        self.tools.append(block.get("name"))

    async def finish(self):
        return {"type": "turn_changes", "files": [{"path": "x.py", "added": 1, "deleted": 0}]}


@pytest.mark.asyncio
async def test_router_notes_and_persists_changes_before_deliverables():
    put, router, pending, files, ch = AsyncMock(), _Router(), [], {}, _FakeChanges()
    tool = {"type": "tool_use", "id": "t", "name": "Edit", "input": {"file_path": "/r/.github/ci.yml"}}
    with patch("database.db.messages.put", put):
        await router._persist_stream_event("sid", {"type": "assistant", "content": [tool]},
                                           pending, files=files, changes=ch)
        for ev in ({"type": "file_changed", "path": "x.py", "absolutePath": "/r/x.py", "kind": "source"},
                   {"type": "file_changed", "path": "d.md", "absolutePath": "/ws/d.md", "kind": "content"}):
            assert await router._persist_stream_event("sid", ev, pending, files=files, changes=ch) == []
        before = await router._persist_stream_event("sid", {"type": "result"}, pending, files=files, changes=ch)
    assert ch.tools == ["Edit"] and ch.noted == ["/r/x.py", "/ws/d.md"]
    assert [b["content"][0]["type"] for b in before] == ["turn_changes", "turn_deliverables"]
    persisted = put.await_args.args[0]["content"]
    assert [b["type"] for b in persisted] == ["turn_changes", "turn_deliverables"]


def test_a_changes_row_never_reaches_a_resumed_prompt():
    from core.context_injector import _filter_tool_only_messages
    row = {"role": "assistant", "content": [
        {"type": "turn_changes", "files": [{"path": "x.py"}]},
        {"type": "turn_deliverables", "files": []}]}
    assert _filter_tool_only_messages([row]) == []


def test_daily_activity_is_process():
    from core.needs_human_review import _is_automated_job_output
    assert _is_automated_job_output("Knowledge/DailyActivity/2026-10-19.md")
    assert not _is_automated_job_output("Knowledge/Notes/2026-10-19.md")


@pytest.mark.asyncio
async def test_turn_diff_endpoint(repo):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.chat import router

    t = await _started([str(repo)])
    (repo / "a.py").write_text("served\n")
    t.note(_fc(repo / "a.py"))
    row = _rows(await t.finish())["a.py"]
    app = FastAPI()
    app.include_router(router, prefix="/api/chat")
    client = TestClient(app)
    q = {"git_root": row["gitRoot"], "start": row["start"], "end": row["end"], "path": "a.py"}
    ok = client.get("/api/chat/turn-diff", params=q)
    assert ok.status_code == 200 and "+served" in ok.json()["diff"]
    assert client.get("/api/chat/turn-diff", params={**q, "path": "../etc"}).status_code == 404

    def boom(*a):
        raise tc.DiffUnavailable("timeout")

    with patch.object(tc, "diff_text", boom):
        assert client.get("/api/chat/turn-diff", params=q).status_code == 503


@pytest.mark.asyncio
async def test_excludes_activity_context_and_deliverable_copies(repo):
    """Bookkeeping copies are not code changes: DailyActivity, .context and the run's
    committed deliverable copies stay off the card (chat turn spec v6 §7.4)."""
    t = await _started([str(repo)])
    skip = [repo / "Knowledge" / "DailyActivity" / "2026-10-19.md", repo / ".context" / "MEMORY.md",
            repo / ".aidlc" / "deliverables" / "run_x" / "REPORT.md"]
    # deeper paths with the same names are code: matching at any depth would let a
    # change hide from the card
    keep = [repo / "a.py", repo / "Knowledge" / "Notes" / "n.md", repo / "docs" / ".context.md",
            repo / "src" / ".context" / "payload.py", repo / "lib" / "Knowledge" / "DailyActivity" / "x.py"]
    for p in skip + keep:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("changed\n")
        t.note(_fc(p))
    rows = _rows(await t.finish())
    assert set(rows) == {"a.py", "Knowledge/Notes/n.md", "docs/.context.md",
                         "src/.context/payload.py", "lib/Knowledge/DailyActivity/x.py"}


def test_git_reads_never_take_optional_locks(tmp_path, monkeypatch):
    """Turn-end reads run while other sessions commit: no index.lock from a refresh."""
    seen = []
    real = subprocess.run
    real(["git", "init", "-q", str(tmp_path)], check=True)

    def run(cmd, *a, **kw):
        seen.append(kw.get("env") or {})
        return real(cmd, *a, **kw)
    monkeypatch.setattr(tc.subprocess, "run", run)
    tc._git(str(tmp_path), "rev-parse", "--git-dir")
    tc._git(str(tmp_path), "status", "--porcelain", env={"X": "1"})
    assert seen and all(e.get("GIT_OPTIONAL_LOCKS") == "0" for e in seen)


@pytest.mark.asyncio
async def test_rows_and_landings_share_one_finish_budget(repo, monkeypatch):
    """FINISH_BUDGET_S bounds the whole turn end: the landings read gets what the
    rows left, not a fresh budget of its own."""
    from core import session_landings
    monkeypatch.setattr(tc, "FINISH_BUDGET_S", 0.3)

    async def slow(self, raw):
        await asyncio.sleep(0.2)
    monkeypatch.setattr(TurnChanges, "_finish", slow)
    seen = {}
    monkeypatch.setattr(session_landings, "read_session", lambda sid, **k: seen.update(
        left=k["deadline"].left()) or {"landed": [], "unverified": 0, "truncated": False,
                                        "unknown": []})
    ctx = {"_turn_id": "t" * 32, "_turn_lands": 1}
    t = TurnChanges.start([str(repo)], session_id="s1", turn_id="t" * 32, context=ctx)
    await t.ready()
    (repo / "a.py").write_text("changed\n")
    t.note_tool({"type": "tool_use", "name": "Write", "input": {"file_path": str(repo / "a.py")}})
    await t.finish()
    assert seen["left"] < 0.15, seen
