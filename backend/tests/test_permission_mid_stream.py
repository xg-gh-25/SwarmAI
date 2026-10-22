"""Regression tests for the 2026-10-22 permission incident (session 2b67fbbc).

1. A read-only `find ... -exec ls` was gated as a store destroy: any -exec counted.
2. Approving while the turn was still STREAMING raised in continue_with_permission;
   the tab already showed "Approved", the blocked hook never got the decision, the
   session sat in WAITING_INPUT and every later message queued behind it.

Properties: find -exec with a reader is not gated, -delete / -exec rm / unknown
programs on a store still are; a decision made mid-stream reaches the blocked hook,
the unit stays STREAMING, and no second reader is started.
"""

import asyncio

import pytest

from core.permission_manager import permission_manager as pm
from core.security_hooks import _targets_irreplaceable_store
from core.session_unit import SessionState, SessionUnit

STORE = "~/.swarm-ai/SwarmWS/Projects"


def test_find_exec_reader_is_not_a_destroy():
    for cmd in (
        f'find {STORE} -maxdepth 4 -name "*archive*" -size +1M -exec ls -la {{}} \\;',
        f"find {STORE} -name '*.md' -exec wc -l {{}} +",
        f"find {STORE} -name '*.md' -exec /usr/bin/grep -l foo {{}} \\;",
    ):
        assert _targets_irreplaceable_store(cmd) is False, cmd


def test_find_that_deletes_a_store_is_still_gated():
    for cmd in (
        f"find {STORE} -name '*.md' -delete",
        f"find {STORE} -name '*.md' -exec rm {{}} \\;",
        f"find {STORE} -type f -exec sh -c 'rm \"$1\"' _ {{}} \\;",
        f"find {STORE} -name x -execdir mv {{}} /tmp \\;",
        f"find {STORE} -name x -ok somebinary {{}} \\;",
    ):
        assert _targets_irreplaceable_store(cmd) is True, cmd


@pytest.mark.asyncio
async def test_decision_mid_stream_reaches_the_blocked_hook():
    unit = SessionUnit(session_id="perm-mid-stream", agent_id="a")
    unit._client = object()
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.STREAMING)
    rid = "perm_midstream01"
    pm.store_pending_request({"id": rid, "session_id": unit.session_id, "tool_name": "Bash",
                              "tool_input": "{}", "status": "pending",
                              "created_at": "2026-10-22T19:53:47"})
    waiter = asyncio.ensure_future(pm.wait_for_permission_decision(rid))
    await asyncio.sleep(0)
    assert pm.has_live_waiter(rid)

    events = [e async for e in unit.continue_with_permission(rid, True)]

    assert events == []                       # the running stream carries the turn
    assert await asyncio.wait_for(waiter, 2) == "approve"
    assert unit.state == SessionState.STREAMING


@pytest.mark.asyncio
async def test_decision_for_nobody_waiting_still_refused_outside_waiting_input():
    unit = SessionUnit(session_id="perm-no-waiter", agent_id="a")
    unit._client = object()
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.STREAMING)
    with pytest.raises(RuntimeError):
        [e async for e in unit.continue_with_permission("perm_nobody", True)]
