"""conftest's event-loop restore (``pytest_runtest_call``): a worker whose FIRST test
calls ``asyncio.run`` keeps pytest-asyncio's session loop for its later
``loop_scope="session"`` tests.

Methodology: a real pytest subprocess, sequential, whose first file ends with
asyncio.run (test_audit_marker_write_gate) and whose next uses the session loop
(test_chat_scenarios_e2e). With the old autouse-fixture guard (it read "before"
ahead of the session-loop fixture) all 15 of the latter failed with "There is no
current event loop in thread 'MainThread'"; under xdist that happened whenever such a
test was a worker's first.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.timeout(300)


def test_a_first_test_that_runs_asyncio_run_does_not_cost_the_session_loop(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-n", "0", "-p", "no:cacheprovider",
         "-W", "ignore", "--maxfail=1000", f"--basetemp={tmp_path / 'bt'}",
         "tests/test_audit_marker_write_gate.py::test_other_tools_pass",
         "tests/test_chat_scenarios_e2e.py::TestScenario1_FreshSend"],
        cwd=BACKEND, capture_output=True, text=True, timeout=280, stdin=subprocess.DEVNULL)
    tail = proc.stdout[-3000:]
    assert proc.returncode == 0, tail
    assert "no current event loop" not in proc.stdout, tail
