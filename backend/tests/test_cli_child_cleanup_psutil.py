"""The CLI child-process lookup works on every platform (R4.2).

``_get_children``, ``_snapshot_process_table`` and ``_snapshot_descendant_tree`` (the
snapshot-then-kill of a session's process tree) read the process table through psutil,
not by running ``pgrep`` / ``ps``, which Windows does not have. psutil is mocked here, and
running any command fails the test.
"""
from __future__ import annotations

import signal
from types import SimpleNamespace
from unittest.mock import patch

import psutil
import pytest

from core import session_unit as su

# a fake process table: 1 -> 10 -> (11, 12), 12 -> 13; 20 is unrelated
TABLE = {1: 0, 10: 1, 11: 10, 12: 10, 13: 12, 20: 1}


class _FakeProc:
    def __init__(self, pid: int):
        if pid not in TABLE:
            raise psutil.NoSuchProcess(pid)
        self.pid = pid
        self.info = {"pid": pid, "ppid": TABLE[pid]}

    def children(self, recursive: bool = False):
        direct = [_FakeProc(p) for p, pp in TABLE.items() if pp == self.pid]
        if not recursive:
            return direct
        out = []
        for child in direct:
            out.append(child)
            out.extend(child.children(recursive=True))
        return out


@pytest.fixture(autouse=True)
def no_commands():
    def refuse(*args, **kwargs):
        raise AssertionError(f"a command was run: {args[0] if args else kwargs}")
    with patch("core.session_unit.subprocess.run", side_effect=refuse):
        yield


@pytest.fixture
def fake_psutil(monkeypatch):
    monkeypatch.setattr(psutil, "Process", _FakeProc)
    monkeypatch.setattr(psutil, "process_iter",
                        lambda attrs=None: iter([_FakeProc(p) for p in TABLE]))


def test_children_via_psutil(fake_psutil):
    assert sorted(su._get_children(10)) == [11, 12]
    assert su._get_children(999) == []          # gone: no error, nothing


def test_process_table_via_psutil(fake_psutil):
    assert su._snapshot_process_table() == TABLE


def test_a_process_whose_parent_cannot_be_read_is_skipped(monkeypatch):
    rows = [SimpleNamespace(info={"pid": 5, "ppid": 1}), SimpleNamespace(info={"pid": 6, "ppid": None})]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(rows))
    assert su._snapshot_process_table() == {5: 1}


def test_descendant_tree_bottom_up(fake_psutil):
    tree = su._snapshot_descendant_tree(10)
    assert sorted(tree) == [11, 12, 13]
    assert tree.index(13) < tree.index(12)     # a child before its parent
    assert 20 not in tree and 10 not in tree


def test_descendant_tree_when_the_table_is_unreadable(fake_psutil, monkeypatch):
    def denied(attrs=None):
        raise psutil.AccessDenied()
    monkeypatch.setattr(psutil, "process_iter", denied)
    tree = su._snapshot_descendant_tree(10)
    assert sorted(tree) == [11, 12, 13] and tree.index(13) < tree.index(12)


def test_kill_uses_a_signal_every_platform_has(monkeypatch):
    sent = []
    monkeypatch.setattr(su.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.delattr(signal, "SIGKILL", raising=False)       # as on Windows
    with patch("core.kill_audit.record"):
        assert su._kill_pids([11, 12], "test") == 2
    assert [pid for pid, _ in sent] == [11, 12]
    assert all(sig == signal.SIGTERM for _, sig in sent)
