"""Wake the session that answers for a run when the run needs it (design
docs/design/2026-11-02-hive-run-decision-record.md §1-§2; Swarm Run r7.11 §5.5).

Whoever dispatched a run answers for it, so nothing a run needs before it has landed on
this host goes to the person: not by a timer, not by a Needs You item. The dispatching
agent sees its runs while it follows them (``swarm_out watch``) and in the digest
before its next message (core/session_router ``_dispatch_digest``); this module covers
the rest: a session that ended its turn. After each refresh of the runs
(core/swarm_out.run_refresh_loop) every live session that answers for a run that needs
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
- **again, a bounded number of times**: a woken turn that did nothing leaves the run seen
  and unchanged, so no change wakes it again. A run that still needs its owner, that the
  owner has seen, and whose fingerprint has not changed for ``REWAKE_AFTER_S`` wakes the
  idle owner again (``REWAKE_TEXT``, naming the runs: the digest has nothing new to show),
  at most ``MAX_REWAKES`` times per unchanged state, counted in ``rewake.json``
  (``rewake_due`` / ``count_rewake``; a new fingerprint starts the count over). After that
  the run is one Needs You item (``exhausted``, read by core/attention_authority).

The message is fixed text with a marker line (it is never mistaken for the person's),
sent as ``system`` origin through ``run_conversation`` with a client id made from the
changes it is about, so a resend of the same wake is the same send; it counts as
delivered once the router says ``accepted``. The turn it starts is read to its end in a
task of its own (closing ``run_conversation`` early would end the turn it started); the
pass waits for the receipt only, at most ACCEPT_WAIT_S, and a session whose wake is still
being read is not woken again. It carries no text a Hive wrote: what changed comes as
the digest, Host fields only.

- ``is_wake_text(text)`` — whether a user row is a wake: it starts with a marker, the
  English one or the Chinese one wakes carried before (still in existing transcripts).
- ``wake_sessions()`` — one pass; never raises. core/swarm_out.run_refresh_loop runs it
  in a task of its own, one at a time, so a slow send never holds the refresh.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: the first line of every wake: a turn it starts is recognisable as the system's
MARKER = "[Swarm Out auto-continue]"
WAKE_TEXT = (MARKER + " Runs you dispatched need you (listed above). Take each through the "
             "funnel yourself: review the result, land it on this host or re-run / take it over / "
             "discard it, then judge it (`swarm_out accept`) or, only when you cannot, hand the "
             "landed result to the person (`swarm_out ask-person`). Say in a line or two what you did.")
#: the wake of a lead run whose Swarm Run children changed (design 2026-10-31-swarm-run §5.5)
RUN_MARKER = "[Swarm Run auto-continue]"
RUN_WAKE_TEXT = (RUN_MARKER + " Children of your Swarm Run changed (listed above). Read them with "
                 "`pipeline-v1 child status`: take what meets its acceptance (`child merge`, `child apply`, "
                 "`child accept`), correct or re-dispatch what does not, answer their waits (`child card`, "
                 "`child answer`), then carry the run on. Say in a line or two what you did.")
#: both at once: the run's children and runs the session dispatched itself
MIXED_WAKE_TEXT = (RUN_WAKE_TEXT + " Runs you dispatched yourself need you too: "
                   + WAKE_TEXT[len(MARKER):].strip())
#: a re-wake (module doc): the runs are named here, Host fields only
REWAKE_TEXT = (MARKER + " Runs you dispatched have waited on you for {hours} hours with nothing "
               "changed: {runs}. Take each through the funnel now (`swarm_out runs` shows them): review, "
               "land, re-run, take over, discard, settle without files, or judge it. Say in a line or two "
               "what you did.")
#: how long a run that needs its owner stays unchanged before the owner is woken again
REWAKE_AFTER_S = 3 * 3600
#: re-wakes per unchanged state of one run; after them it is one Needs You item
MAX_REWAKES = 3
#: the markers wakes started with before they became English: transcripts written
#: then still carry them, and they are recognised the same way
LEGACY_MARKERS = ("[Swarm Out 自动继续]", "[Swarm Run 自动继续]")
_ALL_MARKERS = (MARKER, RUN_MARKER) + LEGACY_MARKERS


def is_wake_text(text: object) -> bool:
    """True when *text* (a user row's text) starts with a wake marker, new or legacy."""
    return isinstance(text, str) and text.lstrip().startswith(_ALL_MARKERS)


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
    """Each live session that answers for runs -> the dispatch ids of its runs that need it now."""
    from core import swarm_out_record, swarm_run
    if not isinstance(data, dict):
        return {}
    asks = [a for a in data.get("asks") or [] if isinstance(a, dict)]
    out: dict[str, list[str]] = {}
    for r in data.get("runs") or []:
        owner = swarm_run.owner_of(r)
        if not owner:
            continue
        if swarm_run.is_child(r):
            found = swarm_run.lead(r.get("parent_run"))
            if found is None or found.ended:
                continue           # an ended run's children are closed by the Conductor
        if swarm_out_record.actionable(r, asks) and swarm_out_record.owner_alive(r):
            out.setdefault(owner, []).append(str(r.get("dispatch_id")))
    return out


async def _idle(router: Any, session: str) -> bool:
    from core import session_pending
    if router.is_busy(session):
        return False
    try:
        return await session_pending.count_pending(session) == 0
    except Exception as exc:  # noqa: BLE001 — not knowing is not idle
        logger.warning("swarm_out_wake: queued messages of %s unreadable (%s)", session[:8],
                       type(exc).__name__)
        return False


async def _send(router: Any, session: str, client_id: str, text: str = WAKE_TEXT) -> bool:
    """Send the wake; True once the router owns it (``accepted``) within ACCEPT_WAIT_S.
    The turn is read to its end in a task of its own whatever this returns."""
    accepted = asyncio.Event()

    async def drain() -> None:
        try:
            gen = router.run_conversation(agent_id="default", user_message=text,
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


# ── waking again (module doc) ────────────────────────────────────────────────

_REWAKE_LOCK = threading.Lock()


def _rewake_path():
    from core import swarm_out
    return swarm_out._state_dir() / "rewake.json"


def _load_rewake() -> dict[str, dict]:
    try:
        d = json.loads(_rewake_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def _save_rewake(d: dict[str, dict]) -> None:
    p = _rewake_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / f".{p.name}.{secrets.token_hex(6)}.tmp"
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, p)


def rewake_due(fps: dict[str, str], now: float, keep: Optional[set] = None) -> dict[str, int]:
    """Of the runs in *fps* (dispatch id -> fingerprint now; each needs its owner, who has
    seen it), those due a re-wake: dispatch id -> which re-wake it would be. A run seen
    with a new fingerprint starts over (``since`` now, no wakes). *keep*: every run that
    needs an owner now; the others' entries go. Blocking (one small file)."""
    due: dict[str, int] = {}
    with _REWAKE_LOCK:
        d = _load_rewake()
        changed = False
        for did, fp in fps.items():
            e = d.get(did)
            if not e or e.get("fp") != fp:
                d[did] = {"fp": fp, "since": now, "wakes": 0, "last": now}
                changed = True
                continue
            wakes = int(e.get("wakes") or 0)
            last = float(e.get("last") or e.get("since") or now)
            if wakes < MAX_REWAKES and now - last >= REWAKE_AFTER_S:
                due[did] = wakes + 1
        if keep is not None:
            for did in [k for k in d if k not in keep]:
                del d[did]
                changed = True
        if changed:
            _save_rewake(d)
    return due


def count_rewake(dids: list[str], now: float) -> None:
    """The owner was woken again about *dids*: count it. Blocking (one small file)."""
    with _REWAKE_LOCK:
        d = _load_rewake()
        for did in dids:
            if did in d:
                d[did]["wakes"] = int(d[did].get("wakes") or 0) + 1
                d[did]["last"] = now
        _save_rewake(d)


def exhausted(data: Optional[dict[str, Any]], now: Optional[float] = None) -> list[tuple[dict, dict]]:
    """(row, its re-wake entry) of each run woken MAX_REWAKES times about its current,
    unchanged state, the last of them REWAKE_AFTER_S ago, that still needs its live owner:
    the person is told once (core/attention_authority). Blocking (file reads)."""
    from core import swarm_out_follow, swarm_out_record, swarm_run
    if not isinstance(data, dict):
        return []
    d = _load_rewake()
    if not d:
        return []
    now = time.time() if now is None else now
    asks = [a for a in data.get("asks") or [] if isinstance(a, dict)]
    out = []
    for r in data.get("runs") or []:
        e = d.get(str((r or {}).get("dispatch_id"))) if isinstance(r, dict) else None
        if not e or int(e.get("wakes") or 0) < MAX_REWAKES or swarm_run.is_child(r):
            continue
        if now - float(e.get("last") or 0) < REWAKE_AFTER_S:
            continue                       # the last re-wake's turn may still act on it
        if e.get("fp") != swarm_out_follow._hash(swarm_out_follow.fingerprint(r, data)):
            continue
        if swarm_out_record.actionable(r, asks) and swarm_out_record.owner_alive(r):
            out.append((r, e))
    return out


def _rewake_text(rows: dict[str, dict], dids: list[str]) -> str:
    named = []
    for did in dids[:MAX_DIGEST_NAMED]:
        r = rows.get(did) or {}
        stage = str(r.get("stage") or "?")
        why = f", {r.get('why')}" if r.get("why") else ""
        named.append(f"{did} ({stage}{why})")
    more = len(dids) - len(named)
    return REWAKE_TEXT.format(hours=REWAKE_AFTER_S // 3600,
                              runs=", ".join(named) + (f" and {more} more" if more > 0 else ""))


#: runs one re-wake names; the rest are counted
MAX_DIGEST_NAMED = 10


async def wake_sessions(router: Any = None) -> list[str]:
    """One pass over the runs the last refresh listed; the sessions woken. Never raises."""
    from core import swarm_out, swarm_out_follow, swarm_run
    try:
        if router is None:
            from core import session_registry
            router = session_registry.session_router
        if router is None:
            return []
        data = swarm_out.snapshot()
        children = {str(r.get("dispatch_id")) for r in (data or {}).get("runs") or [] if swarm_run.is_child(r)}
        rows = {str(r.get("dispatch_id")): r for r in (data or {}).get("runs") or [] if isinstance(r, dict)}
        woken: list[str] = []
        # session lookups and file reads: off the event loop
        cands = await asyncio.to_thread(candidates, data)
        needing = {d for ids in cands.values() for d in ids}
        for session, ids in cands.items():
            if len(woken) >= MAX_WAKES_PER_PASS:
                break
            if session in _DRAINING:
                continue                  # its last wake's turn is still being read
            _block, shown = await asyncio.to_thread(swarm_out_follow.digest, session)
            unseen = {k: v for k, v in shown.items() if k in ids}
            if not unseen:
                # it saw these already: woken again only once one stayed unchanged for
                # REWAKE_AFTER_S, at most MAX_REWAKES times (module doc)
                if await _rewake(router, session, ids, rows, data, needing):
                    woken.append(session)
                continue
            if not await _idle(router, session):
                continue
            kids = children & set(unseen)
            text = (WAKE_TEXT if not kids else RUN_WAKE_TEXT if kids == set(unseen) else MIXED_WAKE_TEXT)
            if await _send(router, session, _client_id(session, unseen), text):
                woken.append(session)
        if not cands:
            await asyncio.to_thread(rewake_due, {}, time.time(), set())   # nothing needs anyone
        if woken:
            logger.info("swarm_out_wake: woke %d dispatching session(s)", len(woken))
        return woken
    except Exception as exc:  # noqa: BLE001 — the next pass looks again
        logger.warning("swarm_out_wake: pass failed (%s)", type(exc).__name__)
        return []


async def _rewake(router: Any, session: str, ids: list[str], rows: dict[str, dict],
                  data: Optional[dict[str, Any]], needing: set) -> bool:
    """Wake *session* again about its seen runs that are due (``rewake_due``); True when
    the send was accepted (then counted)."""
    from core import swarm_out_follow

    def fps() -> dict[str, str]:
        return {d: swarm_out_follow._hash(swarm_out_follow.fingerprint(rows[d], data or {}))
                for d in ids if d in rows}
    now = time.time()
    current = await asyncio.to_thread(fps)
    due = await asyncio.to_thread(rewake_due, current, now, needing)
    if not due or not await _idle(router, session):
        return False
    dids = sorted(due)
    shown = {d: f"{current[d]}#rewake{due[d]}" for d in dids}
    if not await _send(router, session, _client_id(session, shown), _rewake_text(rows, dids)):
        return False
    await asyncio.to_thread(count_rewake, dids, now)
    return True
