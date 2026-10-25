"""conftest closes a function-loop test's DB pools on that test's own loop, after its leftover tasks.

The hang this guards (2026-10-25, test_steer_send after test_t18): a task the test left
running (``session_state.publish``, started by another task as it ended) had a read in
flight when pytest-asyncio closed the test's loop. aiosqlite's worker then failed to
hand the result back (``Event loop is closed``) and died, and ``reset_database``'s
teardown, on the session loop, queued ``close`` on that dead worker and waited forever.

Methodology: a real pytest subprocess on a generated two-test file, written into
tests/ under a name no run collects, so it loads this conftest. Test one leaves a task that, one hop later, starts a second task whose read
takes about a second, and returns while that read runs. Test two only exists so the
next ``reset_database`` cycle runs. Before the fix the subprocess hits pytest-timeout
(or the outer timeout); after it, both pass.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.timeout(120)

_PROBE = textwrap.dedent('''
    import asyncio

    from database.sqlite import _get_pool
    import database

    # A read that takes about a second on the worker thread.
    SLOW = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c "
            "WHERE x < 4000000) SELECT count(*) FROM c")

    async def _slow_read():
        async with _get_pool(str(database.db.db_path)).borrow(readonly=True) as conn:
            await conn.execute(SLOW)

    async def _spawns_the_read():
        await asyncio.sleep(0)
        asyncio.get_running_loop().create_task(_slow_read())  # one hop: not in a first snapshot

    async def test_leaves_a_read_running():
        asyncio.get_running_loop().create_task(_spawns_the_read())
        await asyncio.sleep(0.05)   # the read is on the worker now; return while it runs

    async def test_next():
        pass
''')


def test_a_read_left_running_by_a_test_does_not_hang_the_next_db_reset(tmp_path):
    # Inside tests/ so this conftest loads as a conftest (as a -p plugin pytest-asyncio
    # binds its function-loop fixtures differently). The name does not match
    # test_*.py, so no other run collects it; an explicit path is collected anyway.
    probe = BACKEND / "tests" / f"_probe_loop_teardown_{uuid.uuid4().hex[:8]}.py"
    probe.write_text(_PROBE)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-n", "0", "-p", "no:cacheprovider",
             "-W", "ignore", "--maxfail=1000", "--timeout=20",
             f"--basetemp={tmp_path / 'bt'}", str(probe)],
            cwd=BACKEND, capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        raise AssertionError(f"the probe run hung: {(e.stdout or b'')[-2000:]!r}") from None
    finally:
        probe.unlink(missing_ok=True)
    tail = proc.stdout[-3000:]
    assert "Timeout" not in proc.stdout, tail
    assert proc.returncode == 0, tail
