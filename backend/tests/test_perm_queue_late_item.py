"""The losing perm_task must not swallow a permission item (incident 2026-10-22).

Reproduces the race in StreamingOrchestrator's read loop with a real
asyncio.Queue: asyncio.wait returns on the SDK task while perm_task is pending,
the loop yields once (``await tick_task``), the hook enqueues in that window, and
perm_task finishes with the item. ``_cancel_losers`` must hand the item back
instead of discarding it; a perm_task that is still waiting is cancelled and
leaves the queue intact.
"""

import asyncio

import pytest

from core.streaming_orchestrator import StreamingOrchestrator


@pytest.mark.asyncio
async def test_item_taken_after_the_race_is_returned():
    q: asyncio.Queue = asyncio.Queue()
    sdk_task = asyncio.ensure_future(asyncio.sleep(0, result="tool_use message"))
    perm_task = asyncio.ensure_future(q.get())
    done, pending = await asyncio.wait([sdk_task, perm_task],
                                       return_when=asyncio.FIRST_COMPLETED)
    assert sdk_task in done and perm_task in pending
    q.put_nowait({"requestId": "perm_late"})   # the hook, right after the tool_use
    await asyncio.sleep(0)                     # the loop's `await tick_task` yield
    await asyncio.sleep(0)
    assert perm_task.done() and q.qsize() == 0  # the item has left the queue

    late = await StreamingOrchestrator._cancel_losers(pending, perm_task)

    assert late == {"requestId": "perm_late"}


@pytest.mark.asyncio
async def test_waiting_perm_task_is_cancelled_and_queue_untouched():
    q: asyncio.Queue = asyncio.Queue()
    perm_task = asyncio.ensure_future(q.get())
    await asyncio.sleep(0)

    late = await StreamingOrchestrator._cancel_losers({perm_task}, perm_task)

    assert late is None and perm_task.cancelled()
    q.put_nowait({"requestId": "perm_next"})
    assert q.get_nowait() == {"requestId": "perm_next"}  # no orphaned getter


@pytest.mark.asyncio
async def test_own_cancellation_still_propagates():
    q: asyncio.Queue = asyncio.Queue()

    async def runner():
        perm_task = asyncio.ensure_future(q.get())
        await asyncio.sleep(0)
        asyncio.current_task().cancel()
        await StreamingOrchestrator._cancel_losers({perm_task}, perm_task)

    with pytest.raises(asyncio.CancelledError):
        await asyncio.ensure_future(runner())
