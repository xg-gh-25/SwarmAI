"""Daemon log identity: which process wrote a line, and did backend.json change.

What is tested:
- ``main.log_format`` carries ``%(process)d`` right after the timestamp, so
  lines from the old daemon, the new one and any other instance writing the
  same backend-daemon.log can be told apart, while log readers that anchor on
  the leading timestamp (core/session_health_probe.py ``_LOG_TS_RE``) still match.
- ``write_backend_json`` reports whether it wrote (True) or the conflict guard
  skipped (False), so the startup log no longer claims "written" for a skip.

Methodology: format a real LogRecord with the configured format; call
write_backend_json against a tmp path with a live listener owning the file.
"""
from __future__ import annotations

import json
import logging
import os
import socket
from pathlib import Path


def test_log_format_has_pid_after_timestamp_and_stays_ts_anchored():
    import main
    from core.session_health_probe import _LOG_TS_RE

    assert main.log_format.startswith("%(asctime)s [%(process)d] - ")
    rec = logging.LogRecord("core.x", logging.WARNING, __file__, 1, "hello", None, None)
    line = logging.Formatter(main.log_format).format(rec)
    assert f"[{os.getpid()}] - core.x - WARNING - hello" in line
    assert _LOG_TS_RE.match(line)


def test_write_backend_json_reports_written(tmp_path):
    from main import write_backend_json

    path = tmp_path / "backend.json"
    assert write_backend_json(port=18321, mode="daemon", path=str(path)) is True
    assert json.loads(path.read_text())["pid"] == os.getpid()


def test_write_backend_json_reports_skip_when_owned_by_live_backend(tmp_path):
    from main import write_backend_json

    path = tmp_path / "backend.json"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        # pid 1 is always alive and never us → a live owner on a listening port
        path.write_text(json.dumps({"pid": 1, "port": port, "mode": "daemon"}))
        assert write_backend_json(port=9999, mode="subprocess", path=str(path)) is False
        assert json.loads(path.read_text())["port"] == port
    finally:
        sock.close()
