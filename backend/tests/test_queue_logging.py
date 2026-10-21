"""The daemon's log writes never run on the caller's thread (the asyncio loop).

What is tested (the logging main.py configures at import): records are handed to a
QueueHandler (the caller only enqueues); one listener thread owns the console and
rotating-file handlers and writes the record to the log file; each line carries
exactly one prefix (the QueueHandler has no formatter); Slack's network-outage
errors are demoted to one WARNING per message per window.

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


def test_the_queue_handler_has_no_formatter_so_each_line_has_one_prefix(tmp_path):
    """2026-10-21 regression: basicConfig gave the QueueHandler a formatter, so
    QueueHandler.prepare() put the formatted line into record.msg and the file
    handler prefixed it again. Wire a root the way main does (a fresh logger tree,
    since pytest owns the real root) and count the prefixes in the file."""
    import queue
    import re
    from logging.handlers import QueueListener
    import main

    root = logging.Logger("fresh-root")
    qh = QueueHandler(queue.SimpleQueue())
    main._install_root_queue_handler(root, qh, logging.INFO)
    assert root.handlers == [qh] and qh.formatter is None
    # an already-configured root is left alone, as basicConfig would
    main._install_root_queue_handler(root, QueueHandler(queue.SimpleQueue()), logging.INFO)
    assert root.handlers == [qh]

    fh = logging.FileHandler(tmp_path / "x.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(main.log_format))
    listener = QueueListener(qh.queue, fh)
    listener.start()
    try:
        root.info("probe-%s", "line")
    finally:
        listener.stop()
        fh.close()
    text = (tmp_path / "x.log").read_text(encoding="utf-8")
    assert len(re.findall(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \[\d+\]", text)) == 1, text
    assert text.rstrip().endswith("INFO - probe-line")


def test_network_outage_errors_from_slack_become_one_warning():
    import main
    f = main._NetworkErrorDemoteFilter(window_s=300)

    def rec(msg, level=logging.ERROR):
        return logging.LogRecord("slack_bolt.App", level, __file__, 1, msg, None, None)

    dns = "Failed to send a request to Slack API server: <urlopen error [Errno 8] nodename nor servname provided, or not known>"
    first = rec(dns)
    assert f.filter(first) and first.levelno == logging.WARNING and first.levelname == "WARNING"
    assert not f.filter(rec(dns))  # same message within the window: dropped
    other = rec("Slack AUTH_ERROR: invalid_auth")
    assert f.filter(other) and other.levelno == logging.ERROR  # a real error stays ERROR
