"""Stop a Hive nobody uses, and start it again for the next task (Host side).

A Hive bills by the hour whether or not anything runs on it; most of a Hive's hours were
idle. The Host stops a running Hive once it has been idle for ``hive_idle_stop_minutes``
(app config, default 120; 0 turns it off), and a dispatch to a stopped Hive starts it
first (``wake``, called from core/swarm_out), so no person is needed to bring it back.

**Idle** means, at one look, every one of these, each read (one that cannot be read
counts as busy: stopping wrongly ends work, keeping wrongly costs one more period):

- no open run of this Host on it (``swarm_out.open_runs_by_hive(strict=True)``: a run
  waiting on the person holds its Hive too);
- no session on it streaming, waiting for input, busy with another activity, or with
  background work still going (its streaming-state probe, ``swarm_out._session_states``);
- no run of it still waiting for its lessons (a run whose settle names ``lessons`` in
  ``why_not``, from the runs snapshot): a stopped Hive's lessons are no longer pulled
  (core/swarm_out_settle), and they come back hours after a run ends;
- no dispatch to it in flight (``swarm_out.dispatch_in_flight``): a dispatch has no row
  or open run until its session starts.

The idle time runs from the latest of: the last look that found it busy, its row's
``updated_at`` (a start or any other status change) and this Host's own start, so a Hive
stopped and started between two looks, or a Host that just restarted, never counts hours
it did not see. Then the Hive is claimed (``claim_stop``) and stopped in the background;
when a dispatch began between the last look and the claim, the claim is given back.

- ``idle_after_s(value)``    — the setting as seconds, or None (off).
- ``busy_reasons(...)``      — why a Hive is not idle now ([] = idle).
- ``check_once(now)``        — one look at every running Hive; returns the names stopped.
- ``run_loop()``             — ``check_once`` every CHECK_EVERY_S (main.py, host only).
- ``wake(hive_id)``          — bring a stopped (or stopping) Hive up; one start per Hive.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SETTING = "hive_idle_stop_minutes"
DEFAULT_IDLE_MINUTES = 120
CHECK_EVERY_S = 300
#: a session activity that is not work (core/swarm_out.run_state reads it the same way)
_RESTING = (None, "idle")

#: when this Host started looking (run_loop); idle time is never counted from before it
_HOST_UP_AT: dict[str, float] = {"at": time.time()}
#: hive id -> the last look (wall clock) that found it busy
_LAST_BUSY: dict[str, float] = {}
#: stops started by the loop (a reference keeps each alive until it ends)
_STOPS: set = set()


def idle_after_s(value: Any = ...) -> Optional[float]:
    """The idle time in seconds: the setting's minutes, DEFAULT_IDLE_MINUTES when it is
    missing or not a number, None when it is 0 or false (off). ``...``: read it."""
    if value is ...:
        try:
            from core.app_config_manager import AppConfigManager
            value = AppConfigManager.instance().get(SETTING, None)
        except Exception as e:  # noqa: BLE001 — an unreadable config never turns this off
            logger.warning("hive_idle: reading %s failed (%s)", SETTING, type(e).__name__)
            value = None
    if value is None:
        return DEFAULT_IDLE_MINUTES * 60.0
    if value is False or (isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value) * 60.0
    logger.warning("hive_idle: %s=%r is not a number of minutes; using %d", SETTING, value,
                   DEFAULT_IDLE_MINUTES)
    return DEFAULT_IDLE_MINUTES * 60.0


def busy_reasons(open_runs: Optional[int], sessions: Optional[dict[str, dict[str, Any]]],
                 lessons_waiting: Optional[int], in_flight: bool) -> list[str]:
    """Why a Hive is not idle now; [] when it is. None for a count or the sessions:
    it could not be read (busy, see the module doc)."""
    out: list[str] = []
    if open_runs is None:
        out.append("open runs unknown")
    elif open_runs > 0:
        out.append(f"{open_runs} open run(s)")
    if sessions is None:
        out.append("sessions unknown")
    else:
        for sid, e in sessions.items():
            if e.get("streaming") or e.get("waiting_input") or e.get("background_work") \
                    or e.get("activity") not in _RESTING:
                out.append(f"session {sid} active")
                break
    if lessons_waiting is None:
        out.append("lessons unknown")
    elif lessons_waiting > 0:
        out.append(f"{lessons_waiting} run(s) waiting for lessons")
    if in_flight:
        out.append("dispatch in flight")
    return out


def _db_time(value: Any) -> float:
    """A row's ``updated_at`` (SQLite datetime('now'), UTC) as epoch seconds; 0 when absent."""
    try:
        return float(calendar.timegm(time.strptime(str(value), "%Y-%m-%d %H:%M:%S")))
    except (ValueError, OverflowError):
        return 0.0


def _lessons_by_hive() -> Optional[dict[str, int]]:
    """Hive id -> the runs still waiting for their lessons, from the runs snapshot; None
    when there is a dispatch log but no snapshot yet (unknown)."""
    from core import swarm_out
    snap = swarm_out.snapshot()
    if snap is None:
        return {} if not swarm_out._log_path().exists() else None
    out: dict[str, int] = {}
    for r in snap.get("runs") or []:
        if not isinstance(r, dict):
            continue
        settle = r.get("settle") if isinstance(r.get("settle"), dict) else {}
        if "lessons" in (settle.get("why_not") or []):
            out[str(r.get("hive_id"))] = out.get(str(r.get("hive_id")), 0) + 1
    return out


async def _running_hives() -> list[dict[str, Any]]:
    import aiosqlite
    from core import swarm_out
    async with aiosqlite.connect(swarm_out._db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute(
            "SELECT id, name, updated_at FROM hive_instances WHERE status = 'running'")
        return [{"id": r[0], "name": r[1], "updated_at": r[2]} for r in await cur.fetchall()]


async def _sessions(hive_id: str) -> Optional[dict[str, dict[str, Any]]]:
    from core.carriers import registry
    from core.carriers.base import EC2
    try:
        return await registry.carrier(EC2).location(hive_id).sessions()
    except Exception as e:  # noqa: BLE001 — unreachable, not answering: unknown (busy)
        logger.info("hive_idle: session probe of %s failed (%s)", hive_id, type(e).__name__)
        return None


def _provisioner():
    from database import db
    from hive.provisioner import HiveProvisioner
    return HiveProvisioner(Path(db.db_path))


async def _claim(prov, hive: dict[str, Any]) -> bool:
    """Claim the Hive for its idle stop; False (nothing claimed) when a dispatch to it is
    in flight, or began while the claim was being made, or the Hive is no longer running."""
    from core import swarm_out
    if swarm_out.dispatch_in_flight(hive["id"], hive["name"]):
        return False
    try:
        await prov.claim_stop(hive["id"])
    except RuntimeError:
        return False                      # stopped, updating or restarting meanwhile
    if swarm_out.dispatch_in_flight(hive["id"], hive["name"]):
        await prov.release_stop_claim(hive["id"])
        return False
    return True


async def _stop(prov, hive_id: str, name: str) -> None:
    try:
        await prov.stop(hive_id, claimed=True)
    except Exception as e:  # noqa: BLE001 — stop recorded it on the Hive (error + why)
        logger.warning("hive_idle: stopping %s failed (%s)", name, type(e).__name__)


async def check_once(now: Optional[float] = None) -> list[str]:
    """One look at every running Hive; stop each one idle for the idle time. Returns
    the names of the Hives whose stop it started. The idle stop is a duty of the EC2
    carrier (its Hives are EC2 instances this Host stops); without that carrier nothing
    is looked at (design 2026-11-20-swarm-out-multi-carrier §4.5)."""
    from core import swarm_out
    from core.carriers import registry
    from core.carriers.base import EC2, IDLE_STOP
    idle_s = idle_after_s()
    if idle_s is None or EC2 not in {c.kind for c in registry.with_duty(IDLE_STOP)}:
        return []
    now = time.time() if now is None else now
    hives = await _running_hives()
    running = {h["id"] for h in hives}
    for hid in [h for h in _LAST_BUSY if h not in running]:
        _LAST_BUSY.pop(hid, None)          # a stopped Hive's next run starts its own count
    if not hives:
        return []
    open_runs = await asyncio.to_thread(swarm_out.open_runs_by_hive, True)
    lessons = await asyncio.to_thread(_lessons_by_hive)
    probes = await asyncio.gather(*(_sessions(h["id"]) for h in hives))
    prov = None
    stopped: list[str] = []
    for h, sessions in zip(hives, probes):
        reasons = busy_reasons(None if open_runs is None else open_runs.get(str(h["name"]), 0),
                               sessions,
                               None if lessons is None else lessons.get(str(h["id"]), 0),
                               swarm_out.dispatch_in_flight(h["id"], h["name"]))
        if reasons:
            _LAST_BUSY[h["id"]] = now
            continue
        since = max(_LAST_BUSY.get(h["id"], 0.0), _db_time(h.get("updated_at")), _HOST_UP_AT["at"])
        if now - since < idle_s:
            continue
        prov = prov or _provisioner()
        if not await _claim(prov, h):
            _LAST_BUSY[h["id"]] = now
            continue
        logger.info("hive_idle: Hive %s idle for %.0f min: stopping it", h["name"], (now - since) / 60)
        _LAST_BUSY.pop(h["id"], None)
        task = asyncio.create_task(_stop(prov, h["id"], h["name"]), name=f"hive-idle-stop-{h['id'][:8]}")
        _STOPS.add(task)
        task.add_done_callback(_STOPS.discard)
        stopped.append(str(h["name"]))
    return stopped


async def run_loop() -> None:
    """``check_once`` every CHECK_EVERY_S. Never raises."""
    _HOST_UP_AT["at"] = time.time()
    while True:
        await asyncio.sleep(CHECK_EVERY_S)
        try:
            await check_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — tried again next round
            logger.warning("hive_idle: the idle check failed (%s)", type(e).__name__)


# ── start for a dispatch ─────────────────────────────────────────────────────

class WakeError(RuntimeError):
    """The Hive could not be brought up; the message says why and is safe to show."""


#: how often a start in progress (or a stop to wait for) is looked at
WAKE_POLL_S = 5.0
#: a start that is not up by then has failed (EC2 start + the health check: minutes)
WAKE_LIMIT_S = 900.0
#: hive id -> the start in progress: every dispatch to the Hive waits for the same one,
#: and one whose caller stopped waiting still runs to its end (its status is never left
#: at 'starting')
_STARTS: dict[str, asyncio.Task] = {}

#: the statuses a dispatch brings up (core/swarm_out._ensure_running)
WAKES_FROM = ("stopped", "stopping")


async def hive_row(ref: str) -> dict[str, Any]:
    """id, name, status and error_message of the Hive with this id or name."""
    import aiosqlite
    from core import swarm_out
    async with aiosqlite.connect(swarm_out._db_path()) as conn:
        await conn.execute("PRAGMA busy_timeout = 3000")
        cur = await conn.execute(
            "SELECT id, name, status, error_message FROM hive_instances WHERE id = ? OR name = ?",
            (ref, ref))
        rows = await cur.fetchall()
    if len(rows) != 1:
        raise WakeError(f"no single Hive named {ref!r}")
    r = rows[0]
    return {"id": r[0], "name": r[1], "status": r[2], "error_message": r[3]}


async def _bring_up(hive_id: str) -> None:
    prov = _provisioner()
    end = time.monotonic() + WAKE_LIMIT_S
    while time.monotonic() < end:
        row = await hive_row(hive_id)
        status = row["status"]
        if status == "running":
            return
        if status == "stopped":
            try:
                await prov.start(hive_id)
            except Exception as e:  # noqa: BLE001 — start recorded it, or another start holds it
                logger.info("hive_idle: start of %s: %s", row["name"], type(e).__name__)
                await asyncio.sleep(WAKE_POLL_S)
            continue
        if status in ("stopping", "starting", "installing"):
            await asyncio.sleep(WAKE_POLL_S)
            continue
        why = f": {row['error_message']}" if row.get("error_message") else ""
        raise WakeError(f"Hive {row['name']} is {status}{why}")
    raise WakeError(f"Hive {hive_id} did not come up within {WAKE_LIMIT_S:.0f} s")


async def wake(hive_id: str) -> None:
    """Bring the Hive up: wait for a stop in progress to end, start it, and wait until it
    is running. Raises WakeError when it ends anywhere else. Every caller waits for the
    same start; a caller that stops waiting leaves it running to its end."""
    task = _STARTS.get(hive_id)
    if task is None or task.done():
        task = asyncio.ensure_future(_bring_up(hive_id))
        _STARTS[hive_id] = task
        task.add_done_callback(lambda t, h=hive_id: _STARTS.pop(h, None) if _STARTS.get(h) is t else None)
    await asyncio.shield(task)
