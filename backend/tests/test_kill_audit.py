"""core/kill_audit.py + the startup-sweep owner rule (F6).

What is tested: record() appends one line per pid with reason, caller and our
pid; killed_by_us() reflects a recent record; a session's _kill_pids records
before it signals; classify_failure marks an unrecorded SIGKILL (pressure ok) as
killed_by=external and a recorded one as swarmai; main._other_live_backend
returns another live backend's pid (so a test / probe / second instance skips
the sweep) and None for our own pid, a dead pid, or no file.
"""
from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from core import kill_audit


@pytest.fixture
def audit(tmp_path, monkeypatch):
    monkeypatch.setattr(kill_audit, "_path", lambda: tmp_path / "state" / "kills.jsonl")
    kill_audit._reset_for_tests()
    yield tmp_path / "state" / "kills.jsonl"
    kill_audit._reset_for_tests()


def test_record_writes_reason_caller_and_our_pid(audit):
    def release_session():
        kill_audit.record([111, 222], "session s-1")

    release_session()
    rows = [json.loads(l) for l in audit.read_text().splitlines()]
    assert [r["pid"] for r in rows] == [111, 222]
    assert rows[0]["reason"] == "session s-1" and rows[0]["by"] == os.getpid()
    assert "test_kill_audit.py:release_session" in rows[0]["caller"]
    assert kill_audit.killed_by_us(5)


def test_kill_pids_records_before_signalling(audit):
    from core import session_unit
    with patch.object(session_unit.os, "kill") as k:
        session_unit._kill_pids([4242], "tree")
    k.assert_called_once()
    assert json.loads(audit.read_text())["pid"] == 4242


@pytest.mark.parametrize("ours,expected", [(False, "external"), (True, "swarmai")])
def test_unrecorded_sigkill_is_external(audit, ours, expected):
    from core import session_utils
    if ours:
        kill_audit.record([1], "x")
    with patch.object(session_utils, "_memory_pressure", return_value=("ok", 50.0)):
        ft, meta = session_utils.classify_failure("Command failed with exit code -9")
    assert ft == session_utils.FailureType.KILLED
    assert meta["killed_by"] == expected


def test_only_the_backend_json_owner_sweeps(tmp_path):
    import main
    bj = tmp_path / "backend.json"
    assert main._other_live_backend(str(bj)) is None                 # no file
    bj.write_text(json.dumps({"pid": os.getpid(), "port": 1}))
    assert main._other_live_backend(str(bj)) is None                 # ourselves
    bj.write_text(json.dumps({"pid": os.getppid(), "port": 1}))
    assert main._other_live_backend(str(bj)) == os.getppid()         # another live one
    import subprocess, sys
    p = subprocess.Popen([sys.executable, "-c", "pass"]); p.wait()
    bj.write_text(json.dumps({"pid": p.pid, "port": 1}))
    assert main._other_live_backend(str(bj)) is None                 # dead: we succeed it


def test_stream_reader_logs_our_own_kill_at_info(audit, caplog):
    """The CLI stream of a session we SIGKILLed ends with exit -9: INFO, not the
    WARNING stream_error (5 a day, all intentional, buried the real ones)."""
    import asyncio
    import logging
    from core.cli_stream_reader import CliStreamReader

    class _Client:
        def __init__(self, err):
            self.err = err

        async def receive_messages(self):
            raise self.err
            yield  # pragma: no cover

    def run(sid, err):
        asyncio.run(CliStreamReader(_Client(err), sid)._run())

    kill_audit.record([1], "session s-ours")
    with caplog.at_level(logging.INFO, logger="core.cli_stream_reader"):
        for sid in ("s-ours", "s-other"):
            run(sid, RuntimeError("Command failed with exit code -9"))
    recs = {r.getMessage().split()[1]: r.levelno for r in caplog.records
            if "stream_error" in r.getMessage()}
    assert recs == {"session_id=s-ours:": logging.INFO, "session_id=s-other:": logging.WARNING}
