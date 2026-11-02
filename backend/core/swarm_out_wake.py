"""Wake the session that dispatched a run when the run needs it (design
docs/design/2026-11-02-hive-run-decision-record.md §1-§2; Swarm Run r7.11 §5.5).

Whoever dispatched a run answers for it, so nothing a run needs before it has landed on
this host goes to the person: not by a timer, not by a Needs You item. The dispatching
agent sees its runs while it follows them (``swarm_out watch``) and in the digest
before its next message (core/session_router ``_dispatch_digest``); this module covers
the rest: a session that ended its turn. After each refresh of the runs
(core/swarm_out.run_refresh_loop) every live dispatching session with a run that needs
it and that it has not seen gets one system message:

- **needs it** (core/swarm_out_record.actionable): a result to review, land or judge, a
  stopped or expiring run, a wait of the agent's own class, a revert the person asked
  for. An open ask (the person's), a running or a settled run do not wake anyone.
- **not seen**: the run's follow fingerprint differs from the session's seen record
  (core/swarm_out_follow ``digest``); the woken turn's own digest marks it seen, so one
  change wakes the session once.
- **only when idle**: no turn open or starting (``SessionRouter.is_busy``) and no queued
  message (core/session_pending). A message sent while the session is busy would be
  merged with the person's own words into one turn. A busy session is not counted down:
  it is looked at again after the next refresh.

The message is fixed text with a marker line (it is never mistaken for the person's),
sent as ``system`` origin through ``run_conversation`` with a client id made from the
changes it is about, so a resend of the same wake is the same send; it counts as
delivered once the router says ``accepted``. The turn it starts is read to its end in a
task of its own (closing ``run_conversation`` early would end the turn it started); the
pass waits for the receipt only, at most ACCEPT_WAIT_S, and a session whose wake is still
being read is not woken again. It carries no text a Hive wrote: what changed comes as
the digest, Host fields only.

- ``wake_sessions()`` — one pass; never raises. core/swarm_out.run_refresh_loop runs it
  in a task of its own, one at a time, so a slow send never holds the refresh.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: the first line of every wake: a turn it starts is recognisable as the system's
MARKER = "[Swarm Out 自动继续]"
WAKE_TEXT = (MARKER + " Runs you dispatched need you (listed above). Take each through the "
             "funnel yourself: review the result, land it on this host or re-run / take it over / "
             "discard it, then judge it (`swarm_out accept`) or, only when you cannot, hand the "
             "landed result to the person (`swarm_out ask-person`). Say in a line or two what you did.")
#: sessions woken in one pass at most (each starts a turn); the rest wait for the next
MAX_WAKES_PER_PASS = 5
#: how long a pass waits for a send's ``accepted`` receipt (the turn goes on either way;
#: without it, the same client id is sent again on a later pass)
ACCEPT_WAIT_S = 60.0

#: the tasks reading woken turns to their end, and the sessions they are for
_DRAINS: set[asyncio.Task] = set()
_DRAINING: set[str] = set()


def _client_id(session: str, shown: dict[str, str]) -> str:
    key = session + json.dumps(sorted(shown.items()))
    return "swarm-out-wake-" + hashlib.sha256(key.encode()).hexdigest()[:24]


def candidates(data: Optional[dict[str, Any]]) -> dict[str, list[str]]:
    """Each live dispatching session -> the dispatch ids of its runs that need it now."""
    from core import swarm_out_record
    if not isinstance(data, dict):
        return {}
    asks = [a for a in data.get("asks") or [] if isinstance(a, dict)]
    out: dict[str, list[str]] = {}
    for r in data.get("runs") or []:
        if not isinstance(r, dict) or not r.get("dispatched_from"):
            continue
        if swarm_out_record.actionable(r, asks) and swarm_out_record.owner_alive(r):
            out.setdefault(r["dispatched_from"], []).append(str(r.get("dispatch_id")))
    return out


async def _idle(router: Any, session: str) -> bool:
    from core import session_pending
    if router.is_busy(session):
        return False
    try:
        return await session_pending.count_pending(session) == 0
    except Exception:  # noqa: BLE001 — not knowing is not idle
        return False


async def _send(router: Any, session: str, client_id: str) -> bool:
    """Send the wake; True once the router owns it (``accepted``) within ACCEPT_WAIT_S.
    The turn is read to its end in a task of its own whatever this returns."""
    accepted = asyncio.Event()

    async def drain() -> None:
        try:
            gen = router.run_conversation(agent_id="default", user_message=WAKE_TEXT,
                                          session_id=session, client_id=client_id,
                                          input_origin="system")
            async with contextlib.aclosing(gen) as events:
                async for ev in events:
                    if isinstance(ev, dict) and ev.get("type") == "accepted":
                        accepted.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the same client id is tried next pass
            logger.warning("swarm_out_wake: waking %s failed (%s)", session[:8], type(exc).__name__)
        finally:
            _DRAINING.discard(session)

    _DRAINING.add(session)
    task = asyncio.create_task(drain(), name=f"swarm-out-wake-{session[:8]}")
    _DRAINS.add(task)
    task.add_done_callback(_DRAINS.discard)
    got = asyncio.ensure_future(accepted.wait())
    try:
        await asyncio.wait({got, task}, timeout=ACCEPT_WAIT_S, return_when=asyncio.FIRST_COMPLETED)
    finally:
        got.cancel()
    return accepted.is_set()


async def wake_sessions(router: Any = None) -> list[str]:
    """One pass over the runs the last refresh listed; the sessions woken. Never raises."""
    from core import swarm_out, swarm_out_follow
    try:
        if router is None:
            from core import session_registry
            router = session_registry.session_router
        if router is None:
            return []
        data = swarm_out.snapshot()
        woken: list[str] = []
        # session lookups and file reads: off the event loop
        for session, ids in (await asyncio.to_thread(candidates, data)).items():
            if len(woken) >= MAX_WAKES_PER_PASS:
                break
            if session in _DRAINING:
                continue                  # its last wake's turn is still being read
            _block, shown = await asyncio.to_thread(swarm_out_follow.digest, session)
            unseen = {k: v for k, v in shown.items() if k in ids}
            if not unseen:
                continue                  # it saw these already: nothing new to wake it for
            if not await _idle(router, session):
                continue
            if await _send(router, session, _client_id(session, unseen)):
                woken.append(session)
        if woken:
            logger.info("swarm_out_wake: woke %d dispatching session(s)", len(woken))
        return woken
    except Exception as exc:  # noqa: BLE001 — the next pass looks again
        logger.warning("swarm_out_wake: pass failed (%s)", type(exc).__name__)
        return []
