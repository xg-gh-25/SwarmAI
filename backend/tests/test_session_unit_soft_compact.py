"""Tests for the SOFT (never-kill) context-compaction path (run_37822fae).

The bug: _check_context_soft_compact wrapped compact() in a 30s timeout copied
from the proactive-restart KILL path. On the soft path there is no kill, so a
slow-but-progressing LLM /compact (~600K tokens at SOFT_COMPACT_PCT=60%) was
guillotined mid-flight — leaving the in-flight /compact in a half-state — AND the
success cooldown was stamped on timeout, so it would not retry for 180s while
context kept growing. The very purpose of compact (carry task-needed context
across) was destroyed.

Fix (approach A, XG-approved): bound only a genuine HANG (SOFT_COMPACT_HANG_S,
300s), and on timeout/failure leave context intact + retry after a SHORT backoff
(SOFT_COMPACT_FAIL_BACKOFF) — never the 180s success cooldown.

Methodology: forced-execution (this path + compact() were previously UNTESTED on
a CRITICAL 360-caller file). We mock compact() to control success/slow/hang and
assert the cooldown stamp drives the next-eligibility correctly. We do NOT mock
_check_context_soft_compact itself — it is the unit under test.
"""

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from core import session_unit as su
from core.session_unit import (
    SessionUnit,
    SessionState,
    SOFT_COMPACT_COOLDOWN,
    SOFT_COMPACT_HANG_S,
    SOFT_COMPACT_FAIL_BACKOFF,
)


def _unit():
    """A minimal IDLE SessionUnit with context% above the soft-compact trigger,
    without running __init__ (avoids the full subprocess/registry wiring)."""
    u = SessionUnit.__new__(SessionUnit)
    u.session_id = "test-soft-compact"
    u.state = SessionState.IDLE
    u._model_name = "claude-opus-4-8"
    u._last_soft_compact = float("-inf")
    # Force pct >= SOFT_COMPACT_PCT: tokens above 60% of a large window.
    u._last_known_context_tokens = 900_000
    # Subprocess-IO lock (run_4b74b764): prod __init__ always creates it;
    # _check_context_soft_compact probes _client_io.locked() to yield when a
    # turn holds the client. The __new__ fixture must provide it (unlocked here
    # so the compact path proceeds as before this lock existed).
    import asyncio
    u._client_io = asyncio.Lock()
    return u


def _eligible_again_in(u):
    """Seconds until the cooldown gate would next allow a compact, given the
    stamp left in _last_soft_compact. Mirrors the gate at line ~3160:
    eligible when (now - _last_soft_compact) >= SOFT_COMPACT_COOLDOWN."""
    elapsed = time.monotonic() - u._last_soft_compact
    return SOFT_COMPACT_COOLDOWN - elapsed


@pytest.mark.asyncio
async def test_constants_present_and_sane():
    # AC1: the disaster ceiling is far above "slow", distinct from the old 30s.
    assert SOFT_COMPACT_HANG_S >= 120.0
    assert SOFT_COMPACT_FAIL_BACKOFF < SOFT_COMPACT_COOLDOWN


@pytest.mark.asyncio
async def test_slow_compact_under_hang_ceiling_completes():
    """AC1: a compact that takes longer than the OLD 30s but under the hang
    ceiling must COMPLETE (not be abandoned). We simulate ~0s here but assert the
    wait_for ceiling passed to it is the hang ceiling, not 30s."""
    u = _unit()
    captured = {}

    async def fake_wait_for(coro, timeout):
        captured["timeout"] = timeout
        # consume the coro to avoid "never awaited" warnings
        return await coro

    with patch("core.prompt_builder.PromptBuilder") as PB, \
         patch.object(su.asyncio, "wait_for", side_effect=fake_wait_for):
        PB.get_model_context_window.return_value = 1_000_000
        u.compact = AsyncMock(return_value={"success": True})
        await u._check_context_soft_compact()

    assert captured["timeout"] == SOFT_COMPACT_HANG_S  # not 30.0
    u.compact.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_stamps_full_cooldown():
    """AC3: a successful compact keeps the 180s success cooldown (happy path
    unchanged) — next eligibility ~SOFT_COMPACT_COOLDOWN away."""
    u = _unit()
    with patch("core.prompt_builder.PromptBuilder") as PB:
        PB.get_model_context_window.return_value = 1_000_000
        u.compact = AsyncMock(return_value={"success": True})
        await u._check_context_soft_compact()

    # Stamped ~now → must wait ~full cooldown before next compact.
    assert _eligible_again_in(u) > SOFT_COMPACT_FAIL_BACKOFF + 10


@pytest.mark.asyncio
async def test_timeout_is_failsafe_short_backoff_not_full_cooldown():
    """AC2 (the core fix): on hang-timeout, do NOT stamp the 180s success
    cooldown — leave context intact and retry after the SHORT backoff."""
    u = _unit()
    async def _raise_timeout(coro, timeout):
        coro.close()  # tidy: close the unawaited compact() coroutine
        raise asyncio.TimeoutError

    with patch("core.prompt_builder.PromptBuilder") as PB, \
         patch.object(su.asyncio, "wait_for", side_effect=_raise_timeout):
        PB.get_model_context_window.return_value = 1_000_000
        u.compact = AsyncMock()
        await u._check_context_soft_compact()  # must NOT raise

    # Next eligibility must be ~FAIL_BACKOFF away, NOT ~COOLDOWN away.
    remaining = _eligible_again_in(u)
    assert remaining <= SOFT_COMPACT_FAIL_BACKOFF + 5
    assert remaining < SOFT_COMPACT_COOLDOWN - 60  # decisively shorter than 180s


@pytest.mark.asyncio
async def test_exception_is_failsafe_short_backoff():
    """AC2: a compact() that raises is also fail-safe (short backoff, not 180s)."""
    u = _unit()
    with patch("core.prompt_builder.PromptBuilder") as PB:
        PB.get_model_context_window.return_value = 1_000_000
        u.compact = AsyncMock(side_effect=RuntimeError("compact boom"))
        await u._check_context_soft_compact()  # must NOT raise

    remaining = _eligible_again_in(u)
    assert remaining <= SOFT_COMPACT_FAIL_BACKOFF + 5


@pytest.mark.asyncio
async def test_swallowed_failure_is_failsafe_not_full_cooldown():
    """Gate-2 F1 (HIGH): compact() SWALLOWS failures and returns {"success": False}
    WITHOUT raising (no subprocess / not IDLE / internal SDK error). "Didn't raise"
    must NOT count as success — else the full 180s cooldown is stamped and the
    original bug re-opens for the most common failure path. The fix must inspect
    the RETURN VALUE, not just absence-of-raise."""
    u = _unit()
    with patch("core.prompt_builder.PromptBuilder") as PB:
        PB.get_model_context_window.return_value = 1_000_000
        u.compact = AsyncMock(return_value={"success": False, "message": "no client"})
        await u._check_context_soft_compact()

    remaining = _eligible_again_in(u)
    assert remaining <= SOFT_COMPACT_FAIL_BACKOFF + 5
    assert remaining < SOFT_COMPACT_COOLDOWN - 60  # decisively NOT the 180s cooldown


@pytest.mark.asyncio
async def test_reentrancy_stamp_before_await_blocks_concurrent_compact():
    """Gate-2 F5 (MEDIUM): compact() stays IDLE for its whole duration (no
    _transition), so the state gate gives no mutual exclusion. The cooldown is
    stamped BEFORE awaiting, so a second entry that runs while compact() is still
    in-flight is cooldown-blocked and does NOT fire a second /compact."""
    u = _unit()
    compact_calls = {"n": 0}

    async def slow_compact():
        compact_calls["n"] += 1
        # While "in flight", a concurrent post-turn hook fires:
        await u._check_context_soft_compact()
        return {"success": True}

    with patch("core.prompt_builder.PromptBuilder") as PB:
        PB.get_model_context_window.return_value = 1_000_000
        u.compact = slow_compact
        await u._check_context_soft_compact()

    # The re-entrant call must have been cooldown-blocked → only ONE /compact.
    assert compact_calls["n"] == 1
