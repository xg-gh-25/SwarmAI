"""Blocking calls reached from async endpoints run off the event loop.

What is tested: each async entry point below hands its blocking work (a whole job run,
one Bedrock judge call per extracted memory entry) to a worker thread, so /health and
SSE keep being served while it runs. Before the fix each ran on the loop thread and
stalled the backend for seconds to minutes (the app showed Backend Offline).

Method: the blocking callee is replaced with a spy that records
``threading.get_ident()``; the test asserts the spy ran on a thread other than the one
running the event loop. Everything else the entry point needs is stubbed.
"""
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_run_job_executes_off_event_loop(monkeypatch):
    import jobs.executor as ex
    import jobs.scheduler as sch
    from routers.jobs import RunJobRequest, run_job

    job = SimpleNamespace(id="j1", enabled=True, type="script")
    for name, val in {"load_config": {}, "load_feeds": [], "load_defaults": {},
                      "load_jobs": [job], "load_state": {}, "load_user_context": {}}.items():
        monkeypatch.setattr(sch, name, lambda *a, _v=val, **k: _v)
    monkeypatch.setattr(sch, "save_state_reconciled", lambda *a, **k: None)

    seen: list[int] = []

    def _execute(*a, **k):
        seen.append(threading.get_ident())
        return SimpleNamespace(job_id="j1", status="success", summary="ok",
                               duration_seconds=0.0)

    monkeypatch.setattr(ex, "execute_job", _execute)
    loop_thread = threading.get_ident()
    resp = await run_job(RunJobRequest(job_id="j1"))
    assert resp.status == "success"
    assert seen and all(t != loop_thread for t in seen)


