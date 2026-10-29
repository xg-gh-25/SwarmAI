"""Swarm Out relay: a Hive session's live events, republished into this daemon's hub.

A chat tab that shows a Hive session (design docs/design/2026-10-29-swarm-out-hive-
session-tab.md §3) keeps its one subscription to this daemon's ``/api/chat/events``.
The relay subscribes to the Hive's ``/events`` for that one session and publishes what
it receives into the local hub, so the session is one more sid to the tab: its turn
router, connection state and status poll are unchanged.

Rules (design §3, each tested in tests/test_swarm_out_relay.py):

1. Only this session's events of whitelisted types (``swarm_out_remote.clean_event``)
   are published; they get this daemon's ``eventSeq`` (``hub().publish``).
2. Each Hive ``eventSeq`` is published at most once per Hive epoch (``high``); the
   first connect joins (cursor -1), every reconnect sends the relay's own cursor.
3. The Hive's ``events_hello``, ``resync`` and snapshot ``session_state`` are never
   published as received: they describe the Hive's log.
4. On a Hive resync or a new Hive epoch, and when the relay stops, the session's
   buffer and turn in progress are cleared here (``reset_relayed``); for the first two
   a ``resync`` built from local values follows, so the tab reloads from the DB.
5. From ``acquire`` until the last ``release`` the session has a state entry
   (``state_entries`` / ``state_entry``): read from the Hive before ``acquire``
   returns, updated from the events, refreshed by polling the Hive's streaming-state
   (a session the Hive no longer lists is idle there).
6. The hub's own activity writes skip the session (``mark_relayed``).
7. Starting and stopping are serialized; one upstream task per session; a task that is
   no longer current publishes nothing; clients are counted, at most MAX_SESSIONS.
8. Upstream silence past READ_TIMEOUT_S (heartbeats come every 15 s) or an error status
   closes the connection; it reconnects with back-off. A hang guard, not a time limit.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

import httpx

from core import session_events, swarm_out
from core.swarm_out_remote import (
    LineTooLong, RemoteError, clean_event, iter_sse_lines, json_object, parse_data_line, read_capped,
)

logger = logging.getLogger(__name__)

MAX_SESSIONS = 4
READ_TIMEOUT_S = 60          # 4 x the Hive's 15 s events heartbeat
STATE_POLL_S = 15
BACKOFF_S = (1, 2, 5, 10, 30)
JOIN = -1


class _Relay:
    def __init__(self, sid: str, inst: dict[str, Any], entry: dict[str, Any]) -> None:
        self.sid = sid
        self.inst = inst
        self.clients: set[str] = set()
        self.entry = entry          # the Hive's state entry for the session, as last read
        self.connected = False
        self.epoch: Optional[str] = None
        self.high = 0               # highest Hive eventSeq published in ``epoch``
        self.joined = False
        self.version = 0            # bumped whenever an event changes ``entry``
        self.gen = 0
        self.task: Optional[asyncio.Task] = None


def _client(inst: dict[str, Any], read: Optional[float] = None) -> httpx.AsyncClient:
    timeout = httpx.Timeout(swarm_out.HTTP_TIMEOUT_S, read=read) if read else swarm_out.HTTP_TIMEOUT_S
    return swarm_out._client(inst, timeout=timeout)


async def read_state(inst: dict[str, Any], sid: str) -> Optional[dict[str, Any]]:
    """The Hive's streaming-state entry for ``sid``; None when the Hive lists no such
    session (it is idle there). Raises on a failed read."""
    async with _client(inst) as client:
        async with client.stream("GET", "/api/chat/sessions/streaming-state") as r:
            if r.status_code != 200:
                raise RemoteError(502, "the Hive did not give its session state")
            body = json_object(await read_capped(r))
    sessions = body.get("sessions")
    if not isinstance(sessions, dict):
        raise RemoteError(502, "the Hive did not give its session state")
    entry = sessions.get(sid)
    return entry if isinstance(entry, dict) else None


def _idle(prev: Optional[dict[str, Any]]) -> dict[str, Any]:
    """The entry of a session the Hive no longer lists: idle, with an activity seq past
    the last one (the tab keeps the newest seq)."""
    prev = dict(prev or {})
    seq = prev.get("activity_seq") if isinstance(prev.get("activity_seq"), int) else 0
    prev.update({"streaming": False, "state": "idle", "activity": "idle",
                 "activity_seq": seq + 1, "pending_question": None, "pending_count": 0})
    return prev


class RelayManager:
    def __init__(self) -> None:
        self._relays: dict[str, _Relay] = {}
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    # ── state for the poll and the subscription snapshot ───────────────
    def state_entry(self, sid: str) -> Optional[dict[str, Any]]:
        relay = self._relays.get(sid)
        if relay is None:
            return None
        return {**relay.entry, "relay_connected": relay.connected}

    def state_entries(self) -> dict[str, dict[str, Any]]:
        return {sid: e for sid in list(self._relays) if (e := self.state_entry(sid)) is not None}

    def is_relayed(self, sid: str) -> bool:
        return sid in self._relays

    # ── lifecycle ──────────────────────────────────────────────────────
    async def acquire(self, sid: str, client_id: str, inst: dict[str, Any]) -> dict[str, Any]:
        """Start (or join) the relay of ``sid`` for ``client_id``; returns the state entry.

        The Hive's state is read outside the lock (a slow Hive must not hold up other
        sessions' starts and stops), then the start is decided again under it."""
        async with self._get_lock():
            relay = self._relays.get(sid)
            if relay is not None:
                relay.clients.add(client_id)
                return self.state_entry(sid) or {}
            if len(self._relays) >= MAX_SESSIONS:
                raise RemoteError(429, "too many Hive sessions are open")
        entry = await read_state(inst, sid)
        async with self._get_lock():
            relay = self._relays.get(sid)
            if relay is not None:            # started by another call meanwhile: join it
                relay.clients.add(client_id)
                return self.state_entry(sid) or {}
            if len(self._relays) >= MAX_SESSIONS:
                raise RemoteError(429, "too many Hive sessions are open")
            relay = _Relay(sid, inst, entry if entry is not None else _idle(None))
            relay.clients.add(client_id)
            self._relays[sid] = relay
            session_events.hub().mark_relayed(sid)
            relay.gen += 1
            relay.task = asyncio.get_running_loop().create_task(
                self._run(relay, relay.gen), name=f"swarm-out-relay-{sid[:8]}")
            return self.state_entry(sid) or {}

    async def release(self, sid: str, client_id: str) -> None:
        """Drop ``client_id``'s hold; the last one stops the relay and clears the session
        here (rule 4)."""
        async with self._get_lock():
            relay = self._relays.get(sid)
            if relay is None:
                return
            relay.clients.discard(client_id)
            if relay.clients:
                return
            await self._stop(relay)

    async def stop_all(self) -> None:
        async with self._get_lock():
            for relay in list(self._relays.values()):
                await self._stop(relay)

    async def _stop(self, relay: _Relay) -> None:
        relay.gen += 1   # whatever the task does from here publishes nothing
        task = relay.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — stopping
                pass
        hub = session_events.hub()
        hub.reset_relayed(relay.sid)
        hub.unmark_relayed(relay.sid)
        self._relays.pop(relay.sid, None)

    # ── the upstream task ──────────────────────────────────────────────
    def _current(self, relay: _Relay, gen: int) -> bool:
        return relay.gen == gen and self._relays.get(relay.sid) is relay

    def _publish(self, relay: _Relay, gen: int, ev: dict[str, Any]) -> None:
        """Callers check ``_current`` first; a stopped relay's task is cancelled and
        awaited before ``_stop`` returns, so nothing of it publishes afterwards."""
        payload = {k: v for k, v in ev.items()
                   if k not in ("sessionId", "turnId", "eventSeq", "turnClientId", "snapshot")}
        session_events.hub().publish(relay.sid, payload, turn_id=str(ev.get("turnId") or ""),
                                     turn_client_id=ev.get("turnClientId") or None)

    def _restart_log(self, relay: _Relay, gen: int, high: int) -> None:
        """Rule 4: the Hive's log can no longer be followed from our cursor (a resync or
        a new epoch). Clear the session here and tell the tab to reload, with local
        values; then accept the Hive's replay above ``high``."""
        if not self._current(relay, gen):
            return
        hub = session_events.hub()
        hub.reset_relayed(relay.sid)
        relay.high = high
        # published, so it carries this daemon's head as its eventSeq; no turn is active
        env = hub.publish(relay.sid, {"type": "resync", "activeTurnId": None}, turn_id="")
        logger.info("swarm_out_relay: %s restarted from the Hive's log (head %s)", relay.sid[:8], env["eventSeq"])

    def _update_state(self, relay: _Relay, ev: dict[str, Any]) -> None:
        if ev.get("type") == "session_state" and isinstance(ev.get("state"), dict):
            relay.entry = dict(ev["state"])
            relay.version += 1
        elif ev.get("type") == "session_activity":
            relay.entry = {**relay.entry, "activity": ev.get("activity"),
                           "activity_seq": ev.get("activity_seq")}
            relay.version += 1

    def _handle(self, relay: _Relay, gen: int, ev: Any) -> None:
        if not isinstance(ev, dict):
            return
        etype = ev.get("type")
        if etype == "events_hello":
            epoch = ev.get("epoch")
            if relay.epoch is not None and epoch != relay.epoch:
                self._restart_log(relay, gen, 0)
            relay.epoch = epoch if isinstance(epoch, str) else None
            return
        if ev.get("sessionId") != relay.sid:
            return
        if etype == "resync":
            seq = ev.get("eventSeq")
            self._restart_log(relay, gen, seq if isinstance(seq, int) and seq >= 0 else 0)
            return
        if etype == "session_state" and ev.get("snapshot"):
            if isinstance(ev.get("state"), dict) and self._current(relay, gen):
                relay.entry = dict(ev["state"])
                relay.version += 1
                self._publish(relay, gen, {"type": "session_state", "state": relay.entry})
            return
        seq = ev.get("eventSeq")
        if isinstance(seq, int):
            if seq <= relay.high:
                return
            relay.high = seq
        clean = clean_event(ev, relay.sid, post_stream=False)
        if clean is None or not self._current(relay, gen):
            return
        self._update_state(relay, clean)
        self._publish(relay, gen, clean)

    async def _stream_once(self, relay: _Relay, gen: int) -> None:
        cursor = JOIN if not relay.joined else relay.high
        params = {"only": "1", "cursors": json.dumps({relay.sid: cursor})}
        if relay.epoch and relay.joined:
            params["epoch"] = relay.epoch
        async with _client(relay.inst, read=READ_TIMEOUT_S) as client:
            async with client.stream("GET", "/api/chat/events", params=params) as r:
                if r.status_code != 200:
                    raise RemoteError(502, f"the Hive's events answered {r.status_code}")
                relay.joined = True
                relay.connected = True
                async for line in iter_sse_lines(r.aiter_bytes()):
                    ev = parse_data_line(line)
                    if ev is not None:
                        self._handle(relay, gen, ev)

    async def _poll_state(self, relay: _Relay, gen: int) -> None:
        while self._current(relay, gen):
            await asyncio.sleep(STATE_POLL_S)
            await self._refresh_state(relay, gen)

    async def _refresh_state(self, relay: _Relay, gen: int) -> None:
        version = relay.version
        try:
            entry = await read_state(relay.inst, relay.sid)
        except Exception as exc:  # noqa: BLE001 — keep the last known state
            logger.debug("swarm_out_relay: state read failed for %s: %s", relay.sid[:8], type(exc).__name__)
            return
        if not self._current(relay, gen):
            return
        if relay.version != version:
            return   # an event changed the state while the read was out: it is newer
        if entry is None:
            if relay.entry.get("activity") != "idle" or relay.entry.get("streaming"):
                relay.entry = _idle(relay.entry)
                self._publish(relay, gen, {"type": "session_activity", "activity": "idle",
                                           "activity_seq": relay.entry["activity_seq"]})
        else:
            relay.entry = dict(entry)

    async def _run(self, relay: _Relay, gen: int) -> None:
        poller = asyncio.get_running_loop().create_task(self._poll_state(relay, gen))
        attempt = 0
        try:
            while self._current(relay, gen):
                try:
                    await self._stream_once(relay, gen)
                    attempt = 0
                except asyncio.CancelledError:
                    raise
                except (LineTooLong, RemoteError, httpx.HTTPError, ValueError) as exc:
                    logger.info("swarm_out_relay: %s upstream closed (%s)", relay.sid[:8], type(exc).__name__)
                except Exception as exc:  # noqa: BLE001 — Hive data must never end a current relay
                    logger.warning("swarm_out_relay: %s upstream failed (%s); reconnecting",
                                   relay.sid[:8], type(exc).__name__)
                relay.connected = False
                if not self._current(relay, gen):
                    return
                await asyncio.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
                attempt += 1
                await self._refresh_state(relay, gen)
        finally:
            relay.connected = False
            poller.cancel()


_manager: Optional[RelayManager] = None


def manager() -> RelayManager:
    global _manager
    if _manager is None:
        _manager = RelayManager()
    return _manager
