"""Restart silent-history-loss fix (run_af851c26).

Bug: after a graceful daemon restart, a history-bearing tab's persisted
`_sdk_session_id` is re-injected → `is_cold_resume` (requires id is None) is
FALSE → the DB resume history block is never built → spawn rides `--resume`.
If the CLI transcript is gone → session-not-found → the OLD fallback stripped
`--resume` and cold-spawned the HISTORY-LESS options (built on a non-cold-resume
turn), landing the unit IDLE+warm with NO history → the whole conversation was
silently lost for that subprocess's life, with no signal.

Fix: the session-not-found fallback no longer cold-spawns a history-less unit.
It transitions to COLD + clears identity (reusing refresh_context's proven
cold-resume pattern) so the NEXT send() rebuilds with the DB history, bumps a
data-loss counter, and marks the aborted event `history_dropped` so the router
DURABLY SAVES the current message as a pending (sent=0) row. Recovery is
NEXT-INTERACTION, not autonomous (Gate-2): the unit is left COLD and the drain
worker only fires on a transition INTO IDLE, so the saved message + full DB
history are delivered on the user's next send (which spawns COLD→IDLE and
coalesces the pending row) — the pre-fix silent DATA LOSS is fixed; a
one-interaction latency remains.

These tests drive the REAL `_ensure_spawned` fallback (mock only the leaf
`_spawn`, never the branch under test) and are mutation-proven: reverting the
fix flips each assertion RED.
"""
from unittest.mock import AsyncMock, patch

import pytest

from core.session_unit import (
    SessionUnit,
    get_resume_degrade_count,
    reset_resume_degrade_count,
)


def _unit_with_stale_resume(session_id: str = "test-restart-resume") -> SessionUnit:
    """A COLD unit carrying a persisted (stale) sdk_session_id — the exact
    state get_or_create_unit produces after a graceful restart re-injects the
    persisted id."""
    unit = SessionUnit(session_id=session_id, agent_id="default")
    unit._sdk_session_id = "stale-sdk-id-from-persisted-state"
    return unit


async def _collect(unit, options=None, config=None):
    events = []
    async for ev in unit._ensure_spawned(options, config):
        events.append(ev)
    return events


@pytest.fixture(autouse=True)
def _reset_counters():
    reset_resume_degrade_count()
    yield
    reset_resume_degrade_count()


def _session_not_found_exc():
    # Matches is_session_not_found_error patterns (session_utils.py).
    return RuntimeError("ENOENT: no such file or directory, open '/x/.claude/projects/y/session-abc.jsonl'")


@pytest.mark.asyncio
async def test_session_not_found_fallback_does_not_land_historyless_idle():
    """AC1: the fallback must NOT cold-spawn a history-less unit into IDLE.
    It re-arms a true cold-resume (COLD + _sdk_session_id cleared) so the next
    turn rebuilds DB history."""
    unit = _unit_with_stale_resume()
    # _spawn raises session-not-found on the --resume attempt.
    spawn = AsyncMock(side_effect=_session_not_found_exc())

    with patch.object(unit, "_spawn", new=spawn), \
         patch.object(unit, "_crash_to_cold_async", new=AsyncMock()) as crash:
        await _collect(unit, options=_FakeOpts(), config=None)

    # The OLD code called _spawn a SECOND time (options_no_resume) to land a
    # history-less IDLE. The fix must NOT do that — _spawn is called exactly
    # ONCE (the initial --resume attempt that raised session-not-found), then
    # it re-arms cold-resume instead of re-spawning.
    assert spawn.await_count == 1, (
        "fix must NOT re-spawn a history-less subprocess; only the initial "
        f"--resume attempt spawns (got {spawn.await_count} spawns)"
    )
    assert crash.await_count >= 1, "fix must transition to COLD via _crash_to_cold_async"
    # clear_identity=True is the load-bearing arg (drops _sdk_session_id → next
    # turn is_cold_resume=True → DB history injected).
    _, kwargs = crash.call_args
    assert kwargs.get("clear_identity") is True, \
        "fallback must clear identity so next send() cold-resumes with history"


@pytest.mark.asyncio
async def test_session_not_found_fallback_bumps_dataloss_counter():
    """AC3: the data-loss-recovery event is observable (counter bumps in the
    fallback branch, not silently)."""
    unit = _unit_with_stale_resume()
    spawn = AsyncMock(side_effect=_session_not_found_exc())

    before = get_resume_degrade_count()["session_not_found_recovered"]
    with patch.object(unit, "_spawn", new=spawn), \
         patch.object(unit, "_crash_to_cold_async", new=AsyncMock()):
        await _collect(unit, options=_FakeOpts(), config=None)
    after = get_resume_degrade_count()["session_not_found_recovered"]

    assert after == before + 1, "session-not-found history-recovery must bump the counter"


@pytest.mark.asyncio
async def test_session_not_found_fallback_yields_two_frames_history_on_error_frame():
    """AC1/AC8 (two-frame contract, run_2d2e3258): the fallback must yield TWO
    frames — (1) a SESSION_RECOVERING ERROR frame carrying `history_dropped` and
    NO `_abort`, then (2) a bare `{_abort}` sentinel.

    WHY history_dropped MUST ride the error frame, not the _abort frame: send()'s
    `if event.get("_abort"): return` (~2099) swallows the _abort frame BEFORE
    yielding it up, so a combined error+_abort frame never reaches the router →
    the user never sees SESSION_RECOVERING AND mark_pending never fires (silent
    data-loss). The error frame (no _abort) is forwarded; history_dropped on it
    reaches the router's forward-path mark_pending."""
    unit = _unit_with_stale_resume()
    spawn = AsyncMock(side_effect=_session_not_found_exc())

    with patch.object(unit, "_spawn", new=spawn), \
         patch.object(unit, "_crash_to_cold_async", new=AsyncMock()):
        events = await _collect(unit, options=_FakeOpts(), config=None)

    # Frame 1: the forwarded error frame — carries history_dropped, NO _abort.
    err_frames = [
        e for e in events
        if e.get("type") == "error" and e.get("code") == "SESSION_RECOVERING"
    ]
    assert err_frames, "fallback must yield a SESSION_RECOVERING error frame"
    err = err_frames[0]
    assert err.get("history_dropped") is True, \
        "history_dropped MUST ride the ERROR frame (forwarded), not the _abort frame"
    assert not err.get("_abort"), \
        "the error frame must NOT carry _abort (else send() swallows it before forwarding)"

    # Frame 2: a bare _abort sentinel, and it must NOT carry history_dropped
    # (that would strand it — send() swallows _abort frames before the router).
    abort_frames = [e for e in events if e.get("_abort")]
    assert abort_frames, "fallback must yield a terminal _abort sentinel"
    assert not any(e.get("history_dropped") for e in abort_frames), \
        "the _abort sentinel must NOT carry history_dropped (send() swallows it → mark_pending stranded)"

    # Ordering: the error frame must precede the _abort sentinel.
    err_idx = next(i for i, e in enumerate(events)
                   if e.get("code") == "SESSION_RECOVERING")
    abort_idx = next(i for i, e in enumerate(events) if e.get("_abort"))
    assert err_idx < abort_idx, "error frame must be yielded BEFORE the _abort sentinel"


class _FakeOpts:
    """Minimal ClaudeAgentOptions stand-in — vars() must be copyable (the
    fallback did dict(vars(options))); has a resume attr to strip."""
    def __init__(self):
        self.resume = "stale-sdk-id-from-persisted-state"
        self.system_prompt = "baseline no-history prompt"


# ── Layer 4: cross-boundary seam (unit→router history_dropped→mark_pending) ──
# The seam has TWO halves: (a) unit._ensure_spawned emits a SESSION_RECOVERING
# ERROR frame carrying history_dropped (NO _abort) — driven + mutation-proven by
# the tests above; (b) router run_conversation, ON THE FORWARD PATH (before
# `yield event`, NOT in the `_abort` branch — run_2d2e3258), converts the current
# message to pending when history_dropped is set. Half (b) lives deep inside
# run_conversation (behind DB + slot + spawn), so a full E2E harness would mock so
# much it becomes theater. Instead this is a SOURCE-INVARIANT contract test: it
# asserts the router's forward path is wired to history_dropped → mark_pending_by_id,
# AND that the pairing sits BEFORE the `_abort` return (so it runs on the forwarded
# error frame, not the swallowed sentinel). If a future edit drops the pairing or
# moves it back behind the _abort guard (re-stranding the message), this goes RED.
def test_router_forward_path_wires_history_dropped_to_mark_pending():
    from pathlib import Path
    # __file__-relative (NOT cwd-relative): resolves regardless of whether pytest
    # runs from repo root or backend/ (the pyproject rootdir) — a cwd-relative
    # open() FileNotFound's under the standard backend/ invocation (Gate-2).
    router_src = Path(__file__).resolve().parents[1] / "core" / "session_router.py"
    src = router_src.read_text(encoding="utf-8")
    assert 'event.get("history_dropped")' in src, \
        "router forward path must gate on history_dropped (unit→router seam)"
    # The gate + pending conversion must co-occur in the same guarded block.
    idx = src.index('event.get("history_dropped")')
    window = src[idx: idx + 800]
    assert "mark_pending_by_id" in window, \
        "history_dropped branch must call mark_pending_by_id so the current " \
        "message is re-driven by the drain worker (not left sent=1)"
    assert "persisted_msg_id" in window, \
        "pending conversion must target the current turn's persisted_msg_id"
    # CRITICAL (run_2d2e3258): the mark_pending must run on the FORWARD path,
    # BEFORE the `_abort` return — else it sits behind the guard that swallows
    # the sentinel and never fires (the exact silent-data-loss this fixes). The
    # history_dropped gate must appear BEFORE the `if event.get("_abort"):` line.
    abort_idx = src.index('if event.get("_abort"):', idx - 2000 if idx > 2000 else 0)
    assert idx < abort_idx, \
        "history_dropped→mark_pending must run BEFORE the _abort return (forward " \
        "path), not inside/after the _abort branch (would strand the message)"
