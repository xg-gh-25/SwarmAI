"""Tests for the landing stamp ``session_worktree land`` writes into git
(design docs/design/2026-10-25-turn-result-card.md §3, v9).

What is tested (real git repos in tmp_path, the CLI as the agent runs it):
- the destination branch's reflog entry is exactly
  ``swarm-land <branch> session=<key> turn=<id> onto=<target>: Fast-forward``,
  its sha is the branch head, and ``onto..new`` is exactly the branch's commits;
- a repo with ``core.logAllRefUpdates=false`` still gets the entry;
- "Already up to date" writes no entry and reports ``landed: false``;
- a missing / invalid session id or turn id becomes ``none`` and is reported;
- ``create`` names the branch ``session/<key>-<name>`` when it has a session id;
- core.app_session_id: one grammar, the same as RunEngine's.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess

import pytest

from core import app_session_id as asi

SID = "11111111-2222-3333-4444-555555555555"
TURN = "0123456789abcdef0123456789abcdef"


def _g(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                          text=True).stdout


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    data = tmp_path / "appdata"
    (data / "SwarmWS").mkdir(parents=True)
    monkeypatch.setattr("config.get_app_data_dir", lambda: data)
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SWARM_APP_SESSION_ID", raising=False)
    monkeypatch.delenv("SWARM_TURN_ID", raising=False)


def _repo(path, *, reflog=True):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    if not reflog:
        _g(path, "config", "core.logAllRefUpdates", "false")
    (path / "a.py").write_text("a = 1\n")
    _g(path, "add", "-A")
    _g(path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "init")
    return path


def _cli(*args):
    from scripts import session_worktree as sw
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = sw.main(list(args))
    return rc, json.loads(buf.getvalue())


def _commit(wt, name, text):
    with open(os.path.join(wt, name), "w") as f:
        f.write(text)
    _g(wt, "add", "-A")
    _g(wt, "-c", "core.hooksPath=/dev/null", "commit", "-qm", name)


def _reflog(repo, ref="main"):
    out = _g(repo, "reflog", "show", "--format=%H%x00%gs", ref)
    return [tuple(line.split("\0")) for line in out.splitlines()]


def test_land_stamps_the_destination_reflog(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_APP_SESSION_ID", SID)
    monkeypatch.setenv("SWARM_TURN_ID", TURN)
    repo = _repo(tmp_path / "proj")
    rc, out = _cli("create", str(repo), "--name", "t1")
    assert rc == 0 and out["branch"] == f"session/{asi.session_key(SID)}-t1"
    wt, branch = out["path"], out["branch"]
    _commit(wt, "b.py", "b\n")
    _commit(wt, "c.py", "c\n")
    target = _g(repo, "rev-parse", "main").strip()
    rc, out = _cli("land", wt)
    assert rc == 0, out
    head = _g(wt, "rev-parse", "HEAD").strip()
    new, msg = _reflog(repo)[0]
    assert new == head
    assert msg == (f"swarm-land {branch} session={asi.session_key(SID)} turn={TURN} "
                   f"onto={target}: Fast-forward")
    assert _g(repo, "rev-list", "--count", f"{target}..{new}").strip() == "2"
    assert out["landed"] is True and out["onto"] == target and out["new"] == head
    assert out["attribution"] == {"session": asi.session_key(SID), "turn": TURN}
    assert out["record"] == "ok"


def test_land_writes_the_entry_when_reflogs_are_off(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_APP_SESSION_ID", SID)
    repo = _repo(tmp_path / "proj", reflog=False)
    rc, out = _cli("create", str(repo), "--name", "t2")
    wt = out["path"]
    _commit(wt, "b.py", "b\n")
    rc, out = _cli("land", wt)
    assert rc == 0 and out["record"] == "ok", out
    assert _reflog(repo)[0][1].startswith(f"swarm-land {out['landed_branch']} session=")


def test_up_to_date_is_no_landing(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_APP_SESSION_ID", SID)
    repo = _repo(tmp_path / "proj")
    rc, out = _cli("create", str(repo), "--name", "t3")
    before = _reflog(repo)
    rc, out = _cli("land", out["path"])
    assert rc == 0 and out["landed"] is False
    assert _reflog(repo) == before


@pytest.mark.parametrize("sid,turn", [(None, None), ("bad/id", "nothex"), (SID, None)])
def test_missing_or_invalid_ids_become_none(tmp_path, monkeypatch, sid, turn):
    if sid:
        monkeypatch.setenv("SWARM_APP_SESSION_ID", sid)
    if turn:
        monkeypatch.setenv("SWARM_TURN_ID", turn)
    repo = _repo(tmp_path / "proj")
    rc, out = _cli("create", str(repo), "--name", "t4")
    good = sid == SID
    assert out["branch"] == (f"session/{asi.session_key(SID)}-t4" if good else "session/t4")
    wt = out["path"]
    _commit(wt, "b.py", "b\n")
    rc, out = _cli("land", wt)
    want_s = asi.session_key(SID) if good else "none"
    assert out["attribution"] == {"session": want_s, "turn": "none"}
    assert f" session={want_s} turn=none onto=" in _reflog(repo)[0][1]


def test_one_app_session_grammar():
    assert asi.valid_app_session_id(SID) and asi.valid_app_session_id("a:b.c_d-1")
    for bad in ("", "a/b", "x" * 129, "a b", None, "a\nb"):
        assert not asi.valid_app_session_id(bad)
    assert asi.valid_turn_id(TURN) and not asi.valid_turn_id(TURN.upper())


def test_run_engine_and_owner_gate_use_the_shared_grammar():
    import inspect
    from core import run_owner_gate
    from core.run_protocol import engine
    for mod in (engine, run_owner_gate):
        src = inspect.getsource(mod)
        assert "valid_app_session_id" in src and "[A-Za-z0-9_.:-]{1,128}" not in src
