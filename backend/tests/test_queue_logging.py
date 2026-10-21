"""The daemon's log writes never run on the caller's thread (the asyncio loop).

What is tested (the logging main.py configures at import): records are handed to a
QueueHandler (the caller only enqueues); one listener thread owns the console and
rotating-file handlers and writes the record to the log file.

Why: on 2026-10-21 logging flush / RotatingFileHandler.shouldRollover on the loop
thread stalled the event loop 4-5s at a time; the SSE heartbeat stopped meanwhile.
"""
from __future__ import annotations

import logging
import time
from logging.handlers import QueueHandler, RotatingFileHandler


def test_records_go_through_the_queue_and_the_listener_writes_the_file():
    """(pytest owns the root logger's handlers here, so basicConfig is a no-op in a
    test run; in the daemon the root has none at import and gets the QueueHandler.)"""
    import main
    assert isinstance(main._queue_handler, QueueHandler)
    listener = main._log_listener
    assert listener._thread is not None and listener._thread.is_alive()
    assert any(isinstance(h, RotatingFileHandler) for h in listener.handlers)
    assert not isinstance(main._queue_handler, (logging.FileHandler, logging.StreamHandler))

    probe = logging.getLogger("test.queue_logging")
    probe.propagate = False
    probe.addHandler(main._queue_handler)
    try:
        marker = f"queue-logging-probe-{time.time_ns()}"
        t0 = time.perf_counter()
        probe.warning(marker)
        assert time.perf_counter() - t0 < 0.05  # the caller only enqueues
        path = main.file_handler.baseFilename
        for _ in range(100):
            with open(path, encoding="utf-8", errors="replace") as f:
                if marker in f.read():
                    return
            time.sleep(0.02)
        raise AssertionError("the listener did not write the record")
    finally:
        probe.removeHandler(main._queue_handler)
