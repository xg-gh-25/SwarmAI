"""Independent-thread liveness heartbeat — decouples "process alive" from the
asyncio event loop / GIL so a *busy* backend can never be misread as a *dead* one.

## Why this exists (the false-offline root cause)

The backend is a single asyncio event loop. When it does heavy synchronous CPU
work on the loop thread (context assembly, token estimation, SSE serialization),
it cannot service a `GET /health` probe for several seconds. The Tauri watchdog
(`lib.rs::watchdog_down_decision`) escalates two missed 3s probes (~6s) to
"Terminated" → the UI disables the chat input. But the process is perfectly
alive — just *busy*. That is the "backend频繁offline" bug.

Pool isolation (dedicated ThreadPoolExecutors, run_b36c7880) fixes the
*readiness sampler starvation* half, but NOT this half: the readiness sampler
itself runs on the same blockable loop (`main.py` `asyncio.create_task`), so a
loop-thread stall stalls the sampler too. The only structural fix is a liveness
signal that does NOT live on the loop.

## The mechanism — two signals in one file

An independent ``threading.Thread`` (NOT an asyncio task) ticks every
``TICK_INTERVAL_S`` and atomically writes ``~/.swarm-ai/heartbeat`` with:

- ``pid`` + ``ts``  — **process alive**. The thread only reads a monotonic clock,
  formats a tiny JSON, and does an atomic file replace, then sleeps. Both
  ``time.sleep`` and the write syscall RELEASE the GIL, so this thread keeps
  ticking even when the loop thread is 100% CPU-bound. A fresh ``ts`` (mtime) =
  the process is running.
- ``loop_age`` — **loop alive vs wedged**. A 1-line asyncio task
  (:func:`loop_tick_loop`) bumps a shared monotonic timestamp every
  ``TICK_INTERVAL_S``. The heartbeat thread computes ``loop_age = now - last_tick``.
  Small (< a few s) = the loop is scheduling normally. Large (≥ tens of s) = the
  loop is genuinely wedged (deadlock / permanent block), NOT merely busy.

The Tauri watchdog reads both to distinguish three cases:
  * heartbeat MISSING/STALE (> ~10s)        → process dead        → Terminated
  * heartbeat FRESH + loop_age small         → transient busy      → Degraded (never death)
  * heartbeat FRESH + loop_age ≥ wedge-limit → genuine deadlock    → Terminated

This makes false-offline (busy misread as dead) structurally impossible. An old
binary without this file → the watchdog sees MISSING → its pre-existing
behavior, i.e. zero regression.

**Scope honesty (run_5b0d6ec3 MED-4):** a genuinely WEDGED loop (loop_age ≥ 15s)
is *surfaced* — the watchdog stops protecting it and, once the miss streak
escalates, emits a terminated event that disables the UI input. But nothing here
*actuates a recovery* of a wedged-but-alive process: launchd's KeepAlive sees a
live pid and will NOT respawn it, and this module issues no SIGKILL. So a true
permanent deadlock ends in "UI disabled, process still wedged" until the user (or
launchd, if the process eventually dies) intervenes. Forcing a restart of a
wedged process (`launchctl kickstart -k`) is a DESTRUCTIVE action deliberately
NOT taken automatically here — it belongs to an explicit operator decision, not
to this observability primitive. The busy case (the actual false-offline bug) is
fully handled; the wedge case is detected and surfaced, not auto-killed.

## Not a cost/budget/timeout control (STEERING #2 / O030)

Nothing here truncates or kills work. The heartbeat is a pure *observability*
signal written to a file; the death *decision* lives in the Tauri layer and, in
daemon mode, only emits a UI event (launchd owns process lifecycle). The
``loop_age`` wedge-limit is a hang *discriminator* (busy-vs-dead), not a deadline
on any operation.

Key public symbols:

- ``start_heartbeat``    — spawn the independent writer thread (idempotent).
- ``stop_heartbeat``     — signal the thread to stop + best-effort join.
- ``loop_tick_loop``     — the asyncio task that proves the loop is scheduling.
- ``bump_loop_tick``     — bump the shared loop timestamp (called by the task).
- ``HEARTBEAT_PATH``     — the file the Tauri watchdog reads.
- ``TICK_INTERVAL_S``    — writer + loop-tick cadence (1s).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path

from config import get_app_data_dir

logger = logging.getLogger(__name__)

# Writer + loop-tick cadence. 1s gives the Tauri watchdog (3s probe timeout,
# escalates at ~6s) sufficient granularity to see a fresh heartbeat between
# probes and to measure loop_age against a tens-of-seconds wedge limit.
TICK_INTERVAL_S: float = 1.0

# The file the Tauri watchdog reads. Lives in the app data dir (~/.swarm-ai),
# the same stable root the daemon already owns.
HEARTBEAT_PATH: Path = get_app_data_dir() / "heartbeat"

# ---------------------------------------------------------------------------
# Shared loop-liveness timestamp.
#
# ``_last_loop_tick`` is written by the asyncio loop-tick task (on the event
# loop) and read by the independent writer thread. A plain float assignment is
# atomic under CPython's GIL, so no lock is needed for this single scalar. Its
# initial value is 0.0 = "the loop has not ticked yet" → the writer reports a
# large loop_age until the first bump, which is correct (pre-yield startup).
# ---------------------------------------------------------------------------
_last_loop_tick: float = 0.0

# Writer-thread handle + stop signal. Module-level so start/stop are idempotent
# and the thread survives for the process lifetime (daemon/hive).
_writer_thread: threading.Thread | None = None
_stop_event: threading.Event = threading.Event()
_start_lock = threading.Lock()


def bump_loop_tick() -> None:
    """Record that the asyncio event loop just scheduled us (loop is alive).

    Called every ``TICK_INTERVAL_S`` by :func:`loop_tick_loop`. If the loop
    thread is wedged, this stops being called → ``loop_age`` grows in the
    heartbeat file → the watchdog can tell a genuine deadlock from mere busyness.
    """
    global _last_loop_tick
    _last_loop_tick = time.monotonic()


def _write_heartbeat_once() -> None:
    """Atomically write the heartbeat file with pid + ts + loop_age.

    Atomic via write-to-temp + ``os.replace`` so the watchdog never reads a
    half-written file. All exceptions are swallowed by the caller's loop — a
    failed write must never crash the writer thread (the watchdog will simply
    see a stale mtime and treat it as MISSING, the safe direction).
    """
    now = time.monotonic()
    last = _last_loop_tick
    # loop_age: seconds since the loop last ticked. If it never ticked (0.0),
    # report a large sentinel so a pre-yield/never-started loop reads as wedged
    # rather than falsely fresh.
    loop_age = (now - last) if last > 0 else 999.0
    payload = {
        "pid": os.getpid(),
        # Wall-clock epoch seconds — the watchdog compares against file mtime /
        # its own clock, so an absolute timestamp is what it needs (monotonic is
        # process-local and meaningless cross-process).
        "ts": time.time(),
        "loop_age": round(loop_age, 3),
    }
    tmp = HEARTBEAT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, HEARTBEAT_PATH)


def _writer_run() -> None:
    """Independent-thread body: write the heartbeat every TICK_INTERVAL_S.

    Runs off the asyncio loop so it keeps ticking even when the loop thread is
    CPU/GIL-bound. ``Event.wait`` (not ``time.sleep``) so ``stop_heartbeat`` can
    wake it immediately for a clean shutdown.
    """
    logger.info("heartbeat writer started (path=%s, interval=%.1fs)", HEARTBEAT_PATH, TICK_INTERVAL_S)
    # Write once immediately so the file exists before the first probe window.
    try:
        _write_heartbeat_once()
    except Exception as exc:  # pragma: no cover - best-effort, never crash
        logger.debug("initial heartbeat write failed (non-fatal): %s", exc)
    while not _stop_event.wait(TICK_INTERVAL_S):
        try:
            _write_heartbeat_once()
        except Exception as exc:  # pragma: no cover - loop must never die
            logger.debug("heartbeat write failed (non-fatal): %s", exc)
    # On stop: best-effort remove the file so a restarted binary doesn't read a
    # stale heartbeat from the previous process before its own writer starts.
    try:
        HEARTBEAT_PATH.unlink(missing_ok=True)
    except Exception:  # pragma: no cover
        pass
    logger.info("heartbeat writer stopped")


def start_heartbeat() -> None:
    """Start the independent heartbeat writer thread. Idempotent.

    Call once at daemon/hive startup. Safe to call again — a second call is a
    no-op while the thread is alive.
    """
    global _writer_thread
    with _start_lock:
        if _writer_thread is not None and _writer_thread.is_alive():
            return
        # Defensive (run_5b0d6ec3 HIGH-2): remove any heartbeat file left by a
        # PREVIOUS process that died without a clean shutdown (SIGKILL/OOM → the
        # stop-path unlink never ran). Otherwise this new process's first-write
        # window could let the Tauri watchdog trust a dead predecessor's still-
        # <10s-fresh file. The writer's write-once below also overwrites it, but
        # unlinking first guarantees a clean slate even if that first write is
        # briefly delayed by startup contention.
        try:
            HEARTBEAT_PATH.unlink(missing_ok=True)
        except Exception:  # pragma: no cover - best-effort
            pass
        _stop_event.clear()
        _writer_thread = threading.Thread(
            target=_writer_run,
            name="swarm-heartbeat",
            daemon=True,
        )
        _writer_thread.start()


def stop_heartbeat() -> None:
    """Signal the writer thread to stop and best-effort join it.

    Holds ``_start_lock`` so a concurrent :func:`start_heartbeat` cannot
    interleave (run_5b0d6ec3 MED-3). Only clears ``_writer_thread`` if the join
    actually succeeded — nulling a still-alive (join-timed-out) handle would let
    a subsequent start spawn a SECOND writer thread racing on the same file.
    """
    global _writer_thread
    with _start_lock:
        _stop_event.set()
        thread = _writer_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=TICK_INTERVAL_S * 2)
            if thread.is_alive():
                # Join timed out — leave the handle in place so the is_alive()
                # guard in start_heartbeat still sees a live thread and refuses
                # to spawn a duplicate. The stop_event stays set; the thread will
                # exit on its next wait() wakeup.
                return
        _writer_thread = None


async def loop_tick_loop() -> None:
    """Lifespan task: bump the loop-liveness timestamp every TICK_INTERVAL_S.

    This proves the event loop is scheduling. It is deliberately trivial (bump a
    scalar, sleep) so it can NEVER be the thing that's slow — if it stops
    bumping, the loop itself is wedged, which is exactly the signal the heartbeat
    thread reports as ``loop_age``. A broad except keeps the task immortal.
    """
    logger.info("loop-tick task started (interval=%.1fs)", TICK_INTERVAL_S)
    while True:
        try:
            bump_loop_tick()
        except Exception:  # pragma: no cover - must never die
            pass
        await asyncio.sleep(TICK_INTERVAL_S)
