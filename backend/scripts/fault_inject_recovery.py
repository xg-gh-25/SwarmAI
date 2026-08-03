#!/usr/bin/env python3
"""Fault-injection harness for the runtime_health eval (run_f646b175).

ACTIVELY injects a fault and asserts the recovery path EXECUTES — the STEERING #11
contract that a passive "no zombie happened" snapshot cannot satisfy. This drives
the REAL SessionUnit._retry_with_resume loop (not a re-implementation) with the
narrow spawn seams mocked so a transient error is followed by a successful respawn,
then asserts: (a) the retry loop actually iterated (recovery path executed,
_retry_count advanced), and (b) recovery happened within MAX_RETRY_ATTEMPTS.

Prints `RECOVERY_EXECUTED ok` and exits 0 ONLY when the real recovery path ran and
recovered. Any failure (path didn't run, exceeded retries, exception) exits 1 with
a diagnostic. The runtime_health evaluator greps for the marker.

Isolation: uses SessionUnit.__new__ (no DB, no real subprocess, no lock contention)
— it never touches a live session. Mirrors backend/tests/test_session_unit_recovery_paths.py.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _build_bare_unit():
    """Minimal real SessionUnit for driving the real retry loop (no side effects)."""
    from core.session_unit import SessionState, SessionUnit
    from core.retry_manager import RetryManager

    u = SessionUnit.__new__(SessionUnit)
    u.session_id = "fault-inject-test"
    u.state = SessionState.STREAMING
    u._retry_count = 0
    u._consecutive_oom_kills = 0
    u._OOM_KILL_LIMIT = 3
    u._sdk_session_id = "sdk-fi-1"
    u._app_session_id = "app-fi-1"
    u._model_name = "claude-opus-4-8"
    u._lock = asyncio.Lock()
    u._user_stopped_current_turn = False
    u._interrupted = False
    u._hook_session_context = {}
    u.is_channel_session = False
    u.last_used = time.time()
    u._last_error_type = None
    # Parent attrs read by the retry loop (retry_manager.py). __new__ bypasses
    # __init__, so each must be self-provisioned or the loop raises AttributeError.
    # Kept in sync with `grep -oE 'self\._parent\.[a-zA-Z_]+' retry_manager.py`
    # (23 distinct real attrs; MAX_RETRY_ATTEMPTS/RETRY_BACKOFF_SECONDS are class
    # attrs inherited via __new__, so only instance attrs need provisioning here):
    #   _recycle_kill_pending — read on EVERY retry iteration (the ZOMBIE path this
    #     harness drives); __init__ defaults it False (session_unit.py:652).
    #   _recovery_coordinator — read ONLY on the OOM branch (retry_manager.py:250,
    #     gated by FailureType.OOM at :224). The ZOMBIE injection never reaches it,
    #     so None is safe HERE. ⚠️ If an OOM-injection variant is added to this
    #     harness, replace None with a real/mock RecoveryCoordinator or :250 crashes.
    #   _buffer_overflow_recovery — write-only on the buffer-overflow branch (:88);
    #     set for parity with the sibling recovery-paths test builder.
    u._recycle_kill_pending = False
    u._recovery_coordinator = None
    u._buffer_overflow_recovery = False
    from core.streaming_orchestrator import StreamingOrchestrator
    u._streaming_orchestrator = StreamingOrchestrator(parent=u)
    u._retry_manager = RetryManager(parent=u)
    return u


async def _run() -> tuple[bool, str]:
    """Inject 1 transient error, drive the REAL retry loop, assert recovery ran."""

    u = _build_bare_unit()
    MAX = u.MAX_RETRY_ATTEMPTS  # real constant (3)

    # Track that the recovery path actually EXECUTED (not faked).
    spawn_calls = {"n": 0}

    # Narrow seam mocks — everything the loop calls on the parent, faithfully.
    u._build_retry_options = lambda *a, **k: object()
    u._crash_to_cold_async = AsyncMock(return_value=None)

    async def _fake_spawn(options, config):
        spawn_calls["n"] += 1
        return None  # respawn "succeeds"

    u._spawn = _fake_spawn
    u._transition = lambda *a, **k: None
    u._active_agent_tools = {}
    u._open_tool_uses = {}

    # The retry loop re-streams the resumed turn via
    # _streaming_orchestrator.stream_query(...). Mock THAT seam (the real one the
    # loop calls) to yield a clean recovered turn so the loop converges + returns.
    stream_calls = {"n": 0}

    async def _fake_stream_query(query_content):
        stream_calls["n"] += 1
        yield {"type": "assistant_text", "text": "resumed ok"}
        yield {"_recovered": True}

    u._streaming_orchestrator.stream_query = _fake_stream_query

    # Drive the REAL retry entry point with an injected retriable error.
    recovered = False
    events: list = []
    try:
        from claude_agent_sdk import ClaudeAgentOptions  # type: ignore
        opts = ClaudeAgentOptions()
    except Exception:
        opts = object()

    try:
        async for ev in u._retry_manager._retry_with_resume(
            query_content="resume after fault",
            options=opts,
            config=None,
            initial_error_str="Zombie subprocess detected: error_during_execution 0.0s",
            initial_tb_str="",
        ):
            events.append(ev)
            if isinstance(ev, dict) and ev.get("_recovered"):
                recovered = True
                break
            if isinstance(ev, dict) and ev.get("_abort"):
                return False, f"recovery ABORTED after {u._retry_count} retries"
    except Exception as e:
        return False, f"retry loop raised {type(e).__name__}: {e}"

    # Recovery CONTRACT assertions (STEERING #11 — the path must have executed):
    if spawn_calls["n"] == 0:
        return False, "recovery path did NOT execute (no respawn attempted)"
    if u._retry_count == 0:
        return False, "retry counter never advanced — loop did not iterate"
    if u._retry_count > MAX:
        return False, f"exceeded MAX_RETRY_ATTEMPTS ({u._retry_count} > {MAX})"
    if stream_calls["n"] == 0:
        return False, "resumed turn never re-streamed (orchestrator not reached)"
    if not recovered:
        return False, f"no _recovered event after {u._retry_count} retries"
    return True, (f"recovered after {u._retry_count} retry/retries "
                  f"(respawns={spawn_calls['n']}, restreams={stream_calls['n']}, <= {MAX})")


def main() -> int:
    try:
        ok, msg = asyncio.run(_run())
    except Exception as e:  # never a bare traceback to the evaluator
        print(f"RECOVERY_FAILED harness error: {type(e).__name__}: {e}")
        return 1
    if ok:
        print(f"RECOVERY_EXECUTED ok — {msg}")
        return 0
    print(f"RECOVERY_FAILED — {msg}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
