"""Regression tests for warm-resume-on-interrupt (reverts the PIT01 over-fix).

WHAT IS TESTED
--------------
``SessionUnit.interrupt()`` and how it leaves the Claude SDK subprocess after a
user Stop, a compaction interrupt, and an autonomous (watchdog) interrupt.

THE REGRESSION THESE TESTS GUARD AGAINST
----------------------------------------
Commit 3b030f41 (2026-06-26, "PIT01 zombie") made EVERY user Stop recycle the
subprocess to COLD via ``_crash_to_cold_async``. The next ``send()`` then had to
cold ``--resume`` the entire transcript. To dodge a RARE, already-handled zombie
(~10s self-heal) it imposed an ALWAYS-PAID cold replay — catastrophic on heavy
sessions (session 7f755afa, ~1.9M tokens, streamed 6.5 MINUTES with no output
after a Stop, then the 90s FE watchdog force-ended it → "resume 根本起不来").

THE FIX (warm by default; recycle only when the turn truly ends)
----------------------------------------------------------------
A user Stop keeps the subprocess WARM in IDLE → the next send continues in the
SAME live process, instant, no ``--resume`` replay. The rare genuine poison is
caught lazily + tightly by the orchestrator's zombie self-heal
(streaming_orchestrator ``zombie_via_error``). Only callers that KNOW the turn
ends and poisons the process (COMPACTION) pass ``recycle_after=True``.

KEY INVARIANTS
--------------
1. USER Stop (default ``interrupt()``)        → WARM: IDLE, _client alive, id kept.
2. COMPACTION (``interrupt(recycle_after=True)``) → COLD, _client None, id kept.
3. WATCHDOG (``interrupt(autonomous=True)``)   → WARM: IDLE, _client alive.
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
async def test_user_stop_keeps_subprocess_warm():
    """User Stop (default interrupt()) keeps the subprocess WARM in IDLE.

    This is the core warm-resume mechanism. COLD-recycle here was the 3b030f41
    regression (forced a multi-minute cold --resume on every Stop). The next
    send() must be able to continue in the SAME live process — instant.
    """
    unit = _streaming_unit("test-user-stop-warm")
    unit._sdk_session_id = "resume-abc123"  # context identity must survive

    result = await unit.interrupt(timeout=5.0, autonomous=False)

    # Interrupt "succeeded" — the turn was stopped.
    assert result is True
    # The subprocess is kept WARM (IDLE), NOT recycled to COLD.
    assert unit.state == SessionState.IDLE, (
        "user Stop must keep the subprocess WARM (IDLE) for instant resume — "
        "recycling to COLD was the 3b030f41 regression (heavy-session resume "
        "took minutes via cold --resume)"
    )
    assert unit._client is not None, (
        "user Stop must NOT clear the client — the warm process is reused on "
        "the next send (zombie self-heal catches the rare real poison lazily)"
    )
    assert unit._sdk_session_id == "resume-abc123", "resume identity preserved"


@pytest.mark.asyncio
async def test_compaction_recycle_after_goes_cold():
    """interrupt(recycle_after=True) (compaction's signature) recycles → COLD.

    Compaction ENDS the turn and returns immediately, and genuinely poisons the
    subprocess, so it opts into the recycle. This is the ONLY non-autonomous
    recycle source — user Stop must NOT do this (see the warm test above).
    """
    unit = _streaming_unit("test-compaction-recycle")
    unit._sdk_session_id = "resume-compaction"

    result = await unit.interrupt(recycle_after=True)

    assert result is True
    assert unit.state == SessionState.COLD, (
        "recycle_after=True (compaction) must recycle the poisoned subprocess "
        "to COLD — the turn ended and the process must not be reused"
    )
    assert unit._client is None, "recycled subprocess client must be cleared"
    assert unit._sdk_session_id == "resume-compaction", (
        "recycle must preserve _sdk_session_id (clear_identity=False) so the "
        "next send resumes via --resume"
    )


@pytest.mark.asyncio
async def test_autonomous_interrupt_keeps_subprocess_warm():
    """Autonomous interrupt (tool-hang watchdog ONLY) keeps the subprocess WARM.

    The watchdog interrupts a wedged tool but does NOT return — it lets the
    model reroute mid-stream, so the process must stay warm. recycle_after is
    irrelevant here (autonomous always stays warm).
    """
    unit = _streaming_unit("test-autonomous-warm")

    result = await unit.interrupt(timeout=5.0, autonomous=True)

    assert result is True
    assert unit.state == SessionState.IDLE, (
        "autonomous interrupt must keep the subprocess warm (IDLE) — the "
        "watchdog ladder relies on warm reroute, not kill"
    )
    assert unit._client is not None, "autonomous interrupt must not clear client"


@pytest.mark.asyncio
async def test_autonomous_overrides_recycle_after():
    """autonomous=True keeps warm even if recycle_after=True is passed.

    Guards the gate order: `recycle_after and not autonomous`. The watchdog's
    warm-reroute invariant wins over a stray recycle request.
    """
    unit = _streaming_unit("test-autonomous-no-recycle")

    result = await unit.interrupt(autonomous=True, recycle_after=True)

    assert result is True
    assert unit.state == SessionState.IDLE
    assert unit._client is not None
