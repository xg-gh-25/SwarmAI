"""Tests for core.session_landings — a session's landings read from the stamp
``session_worktree land`` writes (design 2026-10-25-turn-result-card.md §4, v9).

What is tested (real git repos in tmp_path; landings made by the real CLI):
- a landing is read with its branch, onto..new, count, subjects, files and the
  on-<dest> chip; with a turn id, only that turn's landings;
- another session's landings, and unrelated reflog entries, are not read;
- reset back and forth and a deleted middle reflog entry do not change a landing;
- a forged stamp (onto unknown / not an ancestor) is dropped and counted
  ``unverified``; a malformed one is not a landing;
- the branch reset below a landing: chip off; a destination not called ``main``;
- unknown: detached HEAD (``no-branch``), no reflog (``no-reflog``);
- a read returning exactly the cap is ``truncated`` even with a match;
- an exhausted deadline starts no git call (``timeout``).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess

import pytest

from core import app_session_id as asi
from core import session_landings as sl

_REAL_BOUND_CHECKOUTS = sl._bound_checkouts    # conftest pins it to [] per test

SID = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"
T1 = "0123456789abcdef0123456789abcdef"
T2 = "fedcba9876543210fedcba9876543210"


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


def _repo(path, branch="main"):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    (path / "a.py").write_text("a = 1\n")
    _g(path, "add", "-A")
    _g(path, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "init")
    return path


def _land(monkeypatch, repo, name, files, *, sid=SID, turn=T1):
    from scripts import session_worktree as sw
    monkeypatch.setenv("SWARM_APP_SESSION_ID", sid)
    monkeypatch.setenv("SWARM_TURN_ID", turn)

    def cli(*args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sw.main(list(args))
        return json.loads(buf.getvalue())
    wt = cli("create", str(repo), "--name", name)["path"]
    for fname, text in files.items():
        with open(os.path.join(wt, fname), "w") as f:
            f.write(text)
        _g(wt, "add", "-A")
        _g(wt, "-c", "core.hooksPath=/dev/null", "commit", "-qm", f"add {fname}")
    out = cli("land", wt)
    assert out["landed"], out
    return out


def _read(repo, **kw):
    return sl.read_repo(str(repo), asi.session_key(SID), deadline=sl.Deadline(10), **kw)


def test_a_landing_is_read_from_its_stamp(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    out = _land(monkeypatch, repo, "t1", {"b.py": "b\n", "c.py": "c = 1\nc = 2\n"})
    res = _read(repo)
    assert res["unknown"] is None and not res["truncated"] and res["unverified"] == 0
    [land] = res["landed"]
    assert land["branch"] == out["landed_branch"] and land["dest"] == "main"
    assert (land["onto"], land["new"]) == (out["onto"], out["new"])
    assert land["count"] == 2 and land["subjects"][0]["subject"] == "add c.py"
    assert land["turn"] == T1 and land["state"] == "on"
    rows = {f["path"]: f for f in land["files"]}
    assert set(rows) == {"b.py", "c.py"} and rows["c.py"]["added"] == 2
    assert rows["b.py"]["status"] == "added" and rows["b.py"]["gitRoot"] == str(repo)


def test_turn_filter_and_other_sessions(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    _land(monkeypatch, repo, "t1", {"b.py": "b\n"}, turn=T1)
    _land(monkeypatch, repo, "t2", {"c.py": "c\n"}, turn=T2)
    _land(monkeypatch, repo, "t3", {"d.py": "d\n"}, sid=OTHER, turn=T2)
    _g(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", "plain")
    assert [x["turn"] for x in _read(repo)["landed"]] == [T2, T1]
    [only] = _read(repo, turn_id=T2)["landed"]
    assert [f["path"] for f in only["files"]] == ["c.py"]


def test_reset_back_and_forth_and_a_deleted_entry_change_nothing(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    first = _land(monkeypatch, repo, "t1", {"b.py": "b\n"})
    tip = _g(repo, "rev-parse", "HEAD").strip()
    _g(repo, "reset", "-q", "--hard", first["onto"])
    _g(repo, "reset", "-q", "--hard", tip)
    _g(repo, "reflog", "delete", "main@{1}")
    [land] = _read(repo)["landed"]
    assert (land["onto"], land["new"], land["count"]) == (first["onto"], first["new"], 1)


def test_forged_and_malformed_stamps(tmp_path):
    repo = _repo(tmp_path / "proj")
    key = asi.session_key(SID)
    head = _g(repo, "rev-parse", "HEAD").strip()
    (repo / "z.py").write_text("z\n")
    _g(repo, "add", "-A")
    _g(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "z")
    tip = _g(repo, "rev-parse", "HEAD").strip()
    for msg in (f"swarm-land session/x session={key} turn=none onto={'0' * 40}: Fast-forward",
                f"swarm-land session/x session={key} turn=none onto={tip}: Fast-forward",
                f"swarm-land session/x session={key} turn=BAD onto={head}: Fast-forward"):
        _g(repo, "update-ref", "-m", msg, "refs/heads/main", head)
        _g(repo, "update-ref", "-m", msg, "refs/heads/main", tip)
    # each message is written twice (new=head, new=tip): onto unknown x2; onto=tip is
    # not an ancestor of head, and equals tip (an empty range); turn=BAD is no stamp
    res = _read(repo)
    assert res["landed"] == [] and res["unverified"] == 4


def test_chip_and_destination_name(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj", branch="trunk")
    out = _land(monkeypatch, repo, "t1", {"b.py": "b\n"})
    _g(repo, "reset", "-q", "--hard", out["onto"])
    [land] = _read(repo)["landed"]
    assert land["dest"] == "trunk" and land["state"] == "off"


def test_unknown_reasons(tmp_path):
    repo = _repo(tmp_path / "proj")
    _g(repo, "checkout", "-q", "--detach")
    assert _read(repo)["unknown"] == "no-branch"
    repo2 = _repo(tmp_path / "proj2")
    _g(repo2, "reflog", "expire", "--expire=now", "--all")
    subprocess.run(["rm", "-f", str(repo2 / ".git" / "logs" / "refs" / "heads" / "main")], check=True)
    assert _read(repo2)["unknown"] == "no-reflog"


def test_exactly_the_cap_is_truncated(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    _land(monkeypatch, repo, "t1", {"b.py": "b\n"})
    for _ in range(3):
        _g(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", "x")
    # main's reflog, newest first: 3 commits, the landing, init (5 entries)
    res = sl.read_repo(str(repo), asi.session_key(SID), deadline=sl.Deadline(10), cap=3)
    assert res["truncated"] and len(res["landed"]) == 0
    res = sl.read_repo(str(repo), asi.session_key(SID), deadline=sl.Deadline(10), cap=5)
    assert res["truncated"] and len(res["landed"]) == 1     # a match, still maybe more
    res = sl.read_repo(str(repo), asi.session_key(SID), deadline=sl.Deadline(10), cap=6)
    assert not res["truncated"] and len(res["landed"]) == 1


def test_an_exhausted_deadline_starts_no_git(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    called = []
    monkeypatch.setattr(sl.subprocess, "run", lambda *a, **k: called.append(a))
    res = sl.read_repo(str(repo), asi.session_key(SID), deadline=sl.Deadline(0))
    assert res["unknown"] == "timeout" and called == []


def test_read_session_spans_the_bound_checkouts(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    other = _repo(tmp_path / "other")
    _g(other, "checkout", "-q", "--detach")
    _land(monkeypatch, repo, "t1", {"b.py": "b\n"})
    monkeypatch.setattr(sl, "_bound_checkouts", lambda: [str(repo), str(other)])
    res = sl.read_session(SID, deadline=sl.Deadline(10))
    assert [x["branch"] for x in res["landed"]] == [f"session/{asi.session_key(SID)}-t1"]
    assert res["unknown"] == [{"repo": "other", "reason": "no-branch"}]
    assert sl.read_session("bad/id", deadline=sl.Deadline(10))["landed"] == []


def test_bound_checkouts_are_main_checkouts_only(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    wt = tmp_path / "wt"
    _g(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    monkeypatch.setattr(sl, "_bound_checkouts", _REAL_BOUND_CHECKOUTS)   # undo the conftest pin
    monkeypatch.setattr("core.needs_human_review._bound_worktree_roots",
                        lambda ws: ((str(repo), "proj"), (str(wt), "proj-wt")))
    assert sl._bound_checkouts() == [os.path.realpath(repo)]


def test_a_limit_keeps_the_newest_and_says_there_are_more(tmp_path, monkeypatch):
    repo = _repo(tmp_path / "proj")
    _land(monkeypatch, repo, "t1", {"b.py": "b\n"})
    _land(monkeypatch, repo, "t2", {"c.py": "c\n"})
    res = _read(repo, limit=1)
    assert [x["files"][0]["path"] for x in res["landed"]] == ["c.py"] and res["truncated"]
    assert not _read(repo, limit=2)["truncated"]
