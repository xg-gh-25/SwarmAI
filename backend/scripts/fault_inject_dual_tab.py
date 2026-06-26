#!/usr/bin/env python3
"""Fault-injection harness: dual-tab cross-routing isolation (run_4596411e, GS_RTH002).

ACTIVELY injects the dual-tab eviction race — Tab B needs a slot while Tab A owns
an IDLE session — and drives the REAL ``session_router._evict_idle`` to assert the
owned session is NEVER reclaimed (R6 cross-tab isolation: eviction reclaims ONLY
orphan sessions). STEERING #11: forces the real guard path to execute. Mirrors the
shipped test ``test_no_cross_tab_evict.py`` (RP45: reuse the real path).

Modes:
  (default)    Tab A's IDLE session is window-OWNED + force=True → eviction MUST
               refuse (returns False, session not killed) → ISOLATION_OK, exit 0.
  --negative   The IDLE session is an ORPHAN (no window owns it) → eviction MUST
               proceed (returns True, session killed) → NON_VACUOUS ok, exit 0.
               Proves the guard DISCRIMINATES owned vs orphan; a vacuous
               always-refuse would fail this. Wrong outcome → exit 1.

Isolation: a real SessionRouter with mock-backed units; owned_session_ids patched;
never a live session.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _make_router():
    from core.session_router import SessionRouter
    r = SessionRouter(prompt_builder=MagicMock(), config=MagicMock())
    r._lifecycle_manager = None  # skip hook firing
    return r


def _add_idle(router, session_id: str):
    from core.session_unit import SessionState, SessionUnit
    unit = SessionUnit(session_id=session_id, agent_id="default")
    unit._transition(SessionState.IDLE)
    unit.is_channel_session = False
    unit.last_used = time.time() - 9999  # well past grace
    unit._hooks_enqueued = True
    unit.kill = AsyncMock()
    router._units[session_id] = unit
    return unit


def _requester(router):
    from core.session_unit import SessionUnit
    return SessionUnit(session_id="tab-B-requester", agent_id="default")


async def _evict(router, exclude, *, owned, force):
    with patch("routers.settings.owned_session_ids", return_value=owned):
        return await router._evict_idle(exclude=exclude, force=force)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    negative = "--negative" in argv

    router = _make_router()
    tab_a = _add_idle(router, "tab-A-session")

    if negative:
        # Orphan (no window owns it) → eviction CAN proceed (anti-starvation).
        result = asyncio.run(_evict(router, _requester(router), owned=set(), force=False))
        if result is True and tab_a.kill.await_count >= 1:
            print("NON_VACUOUS ok — orphan IDLE session WAS evictable "
                  "(guard discriminates owned vs orphan)")
            return 0
        print("VACUOUS FAIL — orphan was not evicted; guard cannot discriminate")
        return 1

    # Positive: Tab A is window-OWNED; Tab B forces a slot. Isolation MUST hold.
    result = asyncio.run(_evict(router, _requester(router),
                                owned={"tab-A-session"}, force=True))
    if result is False and tab_a.kill.await_count == 0:
        print("ISOLATION_OK — window-owned IDLE tab refused eviction even with "
              "force=True (no cross-tab route/steal)")
        return 0
    print("ISOLATION_FAILED — an owned tab was cross-evicted (R6 violation)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
