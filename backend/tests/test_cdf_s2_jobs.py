"""CDF S2 extraction jobs (core/ingestion/s2_jobs.py, worker.S2JobExecutor; design §12 r16.13 B).

What is tested:
- request queues one job per session; a second request while queued is a no-op; a
  request while running sets ``again`` so it runs once more; done/failed re-arm.
- A failing job is retried up to MAX_ATTEMPTS, then failed with its error; recover puts
  an interrupted running job back in the queue.
- The executor runs jobs in order, records failures on the job, and claims nothing
  while the session lane's admission says the host is loaded.
- The session-end hook and Save to Memory only queue: neither runs S2 inline.
- The sync transcript reader returns sent rows in order with JSON content decoded.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    from core.ingestion.api import connect_rw
    from core.ingestion import s2_jobs
    monkeypatch.setattr(s2_jobs, "_WAKER", None)
    value = connect_rw()
    yield value
    value.close()


def _state(conn, sid):
    row = conn.execute("SELECT state, attempts, again, error FROM ingestion_s2_jobs WHERE session_id=?",
                       (sid,)).fetchone()
    return tuple(row) if row else None


def test_request_lifecycle(conn) -> None:
    from core.ingestion import s2_jobs
    assert s2_jobs.request("s1", "session_end") == "queued"
    assert s2_jobs.request("s1", "save_button") == "queued"
    assert conn.execute("SELECT COUNT(*) FROM ingestion_s2_jobs").fetchone()[0] == 1
    job = s2_jobs.claim(conn)
    assert job["session_id"] == "s1" and job["attempts"] == 1
    assert s2_jobs.request("s1", "save_button") == "running"
    assert s2_jobs.finish(conn, "s1") == "queued"          # again: read once more
    assert _state(conn, "s1")[:3] == ("queued", 0, 0)
    s2_jobs.claim(conn)
    assert s2_jobs.finish(conn, "s1") == "done"
    assert s2_jobs.request("s1", "save_button") == "queued"  # done re-arms


def test_retry_then_fail_and_recover(conn, monkeypatch) -> None:
    from datetime import timedelta
    from core.ingestion import s2_jobs
    monkeypatch.setattr(s2_jobs, "RETRY_DELAY", timedelta(0))
    s2_jobs.request("s1", "session_end")
    for attempt in range(1, s2_jobs.MAX_ATTEMPTS + 1):
        assert s2_jobs.claim(conn)["attempts"] == attempt
        expected = "failed" if attempt == s2_jobs.MAX_ATTEMPTS else "queued"
        assert s2_jobs.finish(conn, "s1", error="boom") == expected
    assert _state(conn, "s1") == ("failed", s2_jobs.MAX_ATTEMPTS, 0, "boom")
    s2_jobs.request("s2", "session_end")
    s2_jobs.claim(conn)
    assert s2_jobs.recover(conn) == 1 and _state(conn, "s2")[0] == "queued"


def test_executor_runs_in_order_and_records_failures(conn, monkeypatch) -> None:
    from core.ingestion import s2_jobs, worker
    monkeypatch.setattr(worker, "ingestion_admission", lambda lane, **k: SimpleNamespace(run=True))
    seen = []

    def extract(sid):
        seen.append(sid)
        if sid == "bad":
            raise RuntimeError("model down")

    for sid in ("a", "bad", "b"):
        s2_jobs.request(sid, "session_end")
    executor = worker.S2JobExecutor(extract=extract)
    assert executor.drain(conn) == 3
    assert seen == ["a", "bad", "b"]
    assert _state(conn, "a")[0] == "done" and _state(conn, "b")[0] == "done"
    assert _state(conn, "bad")[0] == "queued" and "model down" in _state(conn, "bad")[3]
    # a failed job backs off instead of burning its attempts in the same drain
    assert executor.drain(conn) == 0 and _state(conn, "bad")[1] == 1


def test_executor_waits_while_the_host_is_loaded(conn, monkeypatch) -> None:
    from core.ingestion import s2_jobs, worker
    monkeypatch.setattr(worker, "ingestion_admission", lambda lane, **k: SimpleNamespace(run=False))
    s2_jobs.request("a", "session_end")
    assert worker.S2JobExecutor(extract=lambda sid: None).drain(conn) == 0
    assert _state(conn, "a")[0] == "queued"


def test_hook_only_queues(conn, monkeypatch) -> None:
    from core.ingestion.sources import s2_session
    monkeypatch.setattr(s2_session, "extract_session_sync",
                        lambda *a, **k: pytest.fail("the hook must not extract inline"))
    asyncio.run(s2_session.SessionExtractionHook().execute(
        SimpleNamespace(deleting=False, session_id="s9")))
    assert _state(conn, "s9")[0] == "queued"


def test_save_to_memory_only_queues(conn, monkeypatch) -> None:
    from routers import memory
    recorded = []
    monkeypatch.setattr("core.session_inputs.record_session_input",
                        lambda *a, **k: recorded.append(a) or "input:1")
    response = asyncio.run(memory.save_session_to_memory(
        memory.SaveSessionRequest(session_id="s7", since_message_idx=0)))
    assert response.status == "queued" and response.total_saved == 0
    assert recorded and recorded[0][:3] == ("s7", "user_message", "user")
    assert _state(conn, "s7")[0] == "queued"


def test_transcript_sync_reads_sent_rows_in_order(tmp_path, monkeypatch) -> None:
    from core.ingestion.sources import s2_session
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    db = sqlite3.connect(tmp_path / "data.db")
    db.execute("CREATE TABLE messages (id TEXT, session_id TEXT, role TEXT, content TEXT, "
               "created_at TEXT, sent INTEGER, content_seg TEXT)")
    db.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", [
        ("m2", "s", "assistant", json.dumps([{"type": "text", "text": "b"}]), "2", None, "x"),
        ("m1", "s", "user", "plain a", "1", 1, "x"),
        ("m3", "s", "user", "pending", "3", 0, "x"),
        ("o1", "other", "user", "x", "0", None, "x"),
    ])
    db.commit()
    db.close()
    rows = s2_session._transcript_sync("s")
    assert [r["id"] for r in rows] == ["m1", "m2"]
    assert rows[1]["content"] == [{"type": "text", "text": "b"}]
    assert "content_seg" not in rows[0]
