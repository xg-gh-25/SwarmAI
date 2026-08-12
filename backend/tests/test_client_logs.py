"""Tests for the frontend log-forwarding endpoint (POST /api/system/client-logs).

Verifies:
- A batch of entries is appended to <app_data>/logs/frontend.log
- Level + source + message are formatted into each line
- Oversized files are truncated from the head (size cap)
- The handler never raises (best-effort observability path)
"""
import asyncio
from pathlib import Path
from unittest.mock import patch

from routers.system import (
    ingest_client_logs,
    ClientLogBatch,
    ClientLogEntry,
    _FRONTEND_LOG_MAX_BYTES,
)


def _run(coro):
    # asyncio.run (fresh loop per call) — NOT get_event_loop().run_until_complete:
    # the latter raises "no current event loop" on Py3.12 once a prior test clears
    # the thread-default loop (e.g. test_community_api._run's set_event_loop(None)).
    return asyncio.run(coro)


def test_appends_entries(tmp_path):
    with patch("routers.system.get_app_data_dir", return_value=tmp_path):
        batch = ClientLogBatch(entries=[
            ClientLogEntry(level="error", message="boom", source="App.tsx:10:5"),
            ClientLogEntry(level="warn", message="careful"),
        ])
        result = _run(ingest_client_logs(batch))

    assert result == {"status": "ok", "written": 2}
    log_file = tmp_path / "logs" / "frontend.log"
    content = log_file.read_text()
    assert "[ERROR] (App.tsx:10:5) boom" in content
    assert "[WARN] careful" in content


def test_truncates_oversized_file(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "frontend.log"
    # Pre-fill beyond the cap.
    log_file.write_bytes(b"x" * (_FRONTEND_LOG_MAX_BYTES + 1000))

    with patch("routers.system.get_app_data_dir", return_value=tmp_path):
        _run(ingest_client_logs(ClientLogBatch(entries=[
            ClientLogEntry(level="error", message="after-truncate"),
        ])))

    # File was truncated to ~half the cap, then the new line appended.
    assert log_file.stat().st_size < _FRONTEND_LOG_MAX_BYTES
    assert "after-truncate" in log_file.read_text(errors="ignore")


def test_empty_batch_is_ok(tmp_path):
    with patch("routers.system.get_app_data_dir", return_value=tmp_path):
        result = _run(ingest_client_logs(ClientLogBatch(entries=[])))
    assert result == {"status": "ok", "written": 0}


def test_never_raises_on_bad_dir():
    # get_app_data_dir points at a path that can't be created (a file, not dir).
    with patch("routers.system.get_app_data_dir", return_value=Path("/dev/null/nope")):
        result = _run(ingest_client_logs(ClientLogBatch(entries=[
            ClientLogEntry(level="error", message="x"),
        ])))
    # Best-effort: returns error status, does not raise.
    assert result["status"] in ("ok", "error")
