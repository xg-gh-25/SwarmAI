"""Regression tests for subprocess-recycle-on-interrupt (PIT01 zombie fix).

WHAT IS TESTED
--------------
A user Stop calls ``SessionUnit.interrupt(autonomous=False)``. The PRE-FIX
behavior left the Claude SDK subprocess alive + IDLE ("warm") so the next
``send()`` reused it. But a soft interrupt leaves the CLI in a corrupt
turn-state, so the reused subprocess returns an INSTANT empty
``error_during_execution`` → the zombie detector kills + respawns. Every
zombie in the daemon log is preceded by a user Stop on the same session.

THE FIX (lazy recycle, user-Stop only)
---------------------------------------
``interrupt(autonomous=False)`` success now recycles the poisoned subprocess
eagerly via the BLESSED kill path ``_crash_to_cold_async(clear_identity=False)``
(real kill + FD cleanup, preserves ``_sdk_session_id`` for --resume). State
ends at COLD with ``_client is None`` → the next ``send()`` (line 1615/1617)
spawns fresh with --resume. No poisoned process is ever reused.

KEY INVARIANTS
--------------
1. USER Stop (autonomous=False) → state COLD, _client None, resume id preserved.
2. AUTONOMOUS interrupt (autonomous=True: the tool-hang watchdog ONLY) keeps the
   subprocess WARM (IDLE) — the watchdog interrupts but does NOT return, letting
   the model reroute mid-stream. (Compaction calls interrupt() WITHOUT autonomous
   → it recycles, which is correct: compaction returns immediately = turn ends =
   the subprocess is poisoned and must not be reused. Only the still-streaming
   watchdog stays warm.)
3. The recycle uses the existing _crash_to_cold_async path (no new kill path);
   _sdk_session_id survives so context is restored on the next send via --resume.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.session_unit import SessionUnit, SessionState


def _streaming_unit(session_id: str) -> SessionUnit:
    """A SessionUnit driven to STREAMING with a mock SDK client."""
    unit = SessionUnit(session_id=session_id, agent_id="default")
    unit._transition(SessionState.IDLE)      # COLD→IDLE
    unit._transition(SessionState.STREAMING)  # IDLE→STREAMING
    mock_client = AsyncMock()
    mock_client.interrupt = AsyncMock()
    mock_wrapper = MagicMock()
    mock_wrapper.pid = 99999
    unit._client = mock_client
    unit._wrapper = mock_wrapper
    return unit


@pytest.mark.asyncio
async def test_user_stop_recycles_subprocess_to_cold():
    """User Stop (autonomous=False) recycles the poisoned subprocess → COLD.

    PRE-FIX: state stayed IDLE, _client warm (poisoned, reused next send).
    POST-FIX: state COLD, _client None — next send respawns clean via --resume.
    """
    unit = _streaming_unit("test-user-stop-recycle")
    unit._sdk_session_id = "resume-abc123"  # context identity must survive

    result = await unit.interrupt(timeout=5.0, autonomous=False)

    # Interrupt "succeeded" — the turn was stopped.
    assert result is True
    # The poisoned subprocess is recycled, NOT left warm for reuse.
    assert unit.state == SessionState.COLD, (
        "user Stop must recycle the poisoned subprocess to COLD, not leave it "
        "warm (IDLE) for the next send() to reuse into a zombie"
    )
    assert unit._client is None, "recycled subprocess client must be cleared"
    # Resume identity preserved → next send() restores context via --resume.
    assert unit._sdk_session_id == "resume-abc123", (
        "recycle must preserve _sdk_session_id (clear_identity=False) so the "
        "next send resumes the conversation"
    )


@pytest.mark.asyncio
async def test_default_interrupt_recycles_like_compaction():
    """interrupt() with DEFAULT args (how compaction calls it) recycles → COLD.

    Adversarial Gate-2 caught that the compaction escalation
    (streaming_orchestrator.py ~754) calls `interrupt()` with NO autonomous arg.
    This test pins the REAL call signature the compaction path uses (not the
    autonomous=True a watchdog uses), proving compaction's poisoned subprocess
    is recycled, not reused into a zombie.
    """
    unit = _streaming_unit("test-compaction-default")
    unit._sdk_session_id = "resume-compaction"

    # Exactly how compaction calls it: positional/default, no autonomous kwarg.
    result = await unit.interrupt()

    assert result is True
    assert unit.state == SessionState.COLD, (
        "default interrupt() (compaction's call signature) must recycle the "
        "poisoned subprocess — compaction returns after interrupt, ending the "
        "turn, so the process must not be reused"
    )
    assert unit._client is None
    assert unit._sdk_session_id == "resume-compaction"


@pytest.mark.asyncio
async def test_autonomous_interrupt_keeps_subprocess_warm():
    """Autonomous interrupt (tool-hang watchdog ONLY) keeps the subprocess WARM.

    The watchdog interrupts a wedged tool but does NOT return — it lets the
    model reroute mid-stream, so the process must stay warm. Recycling here
    would collapse the warm base rung into the escalated kill rung.

    (Compaction is NOT autonomous — it returns after interrupting, ending the
    turn, so its poisoned subprocess IS recycled. See module docstring.)
    """
    unit = _streaming_unit("test-autonomous-warm")

    result = await unit.interrupt(timeout=5.0, autonomous=True)

    assert result is True
    assert unit.state == SessionState.IDLE, (
        "autonomous interrupt must keep the subprocess warm (IDLE) — the "
        "watchdog/compaction ladder relies on warm reroute, not kill"
    )
    assert unit._client is not None, "autonomous interrupt must not clear client"
