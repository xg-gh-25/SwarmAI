"""Central dedicated-executor registry — class-scoped bounded thread pools.

ROOT-CAUSE FIX for backend-offline (run_b36c7880). Symptom: the right-hand
"backend offline" banner flapped under load and disabled the chat input.

Diagnosis (audit run_688ba817): the backend runs a SINGLE asyncio event loop.
`asyncio.to_thread(...)` ALWAYS offloads to the loop's ONE default
ThreadPoolExecutor (``min(32, cpu+4)`` = 16 workers on a 12-core box). ~50
call sites across the codebase — git subprocess, STS/credential probes, LLM
inference, sqlite reads, filesystem scans, the Welcome-Screen briefing glob —
all pile onto that one pool. A burst of slow work saturates all 16 workers, and
the event loop can then no longer promptly schedule even the ZERO-I/O ``/health``
cache-read handler, nor the readiness-sampler tick — so ``/health`` misses the
Rust watchdog's 3s budget and the frontend declares the (perfectly alive) daemon
offline. One saturated pool, two failure modes (dead health + stale sampler).

The fix is NOT "make /health faster" (it is already instant) and NOT "widen the
watchdog" (that masks a real hang — O030). It is to stop the default pool from
saturating: give each class of slow/blocking work its OWN bounded pool, distinct
from the default pool, so the default pool (and thus /health scheduling +
the sampler) is never starved by a briefing/LLM/git burst.

This mirrors the existing per-purpose pools (``session_unit.subprocess_executor``,
``rss_executor``, ``task_manager._script_executor``) but centralizes them so
callers route by CLASS instead of each rolling their own or falling back to the
default pool. EVOLUTION O006/O020: "blocking >1s → dedicated executor, never the
default pool" — this module is where that rule is finally enforced structurally.

Usage:
    from core import executors
    result = await executors.run_in("subprocess", _run_git, cwd)   # off default pool
    # or, for library APIs that want a raw executor:
    loop.run_in_executor(executors.get_pool("llm"), fn, *args)

Pools (bounded on purpose — a class saturating its OWN pool must not bleed into
another, and MUST never touch the default pool the event loop schedules on):
  io          — filesystem scans / git status / sqlite reads (medium, frequent)
  subprocess  — git subprocess, ada/aws CLI probes (slow WRITERS: commit, clone,
                index rebuild — can block many seconds to ~100s)
  llm         — Bedrock inference (memory extract, summarization) (very slow)
  briefing    — Welcome-Screen briefing_data pre-warm (glob + sqlite) (spiky)
  spawn       — LATENCY-SENSITIVE cold-spawn READERS only (STS preflight, resume
                git) — isolated from the slow 'subprocess' writers so a 100s index
                or an unbounded git-clone never delays a cold-session TTFT
"""

from __future__ import annotations

import asyncio
import atexit
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# Per-class worker caps. Deliberately small: the goal is ISOLATION + BOUNDING,
# not throughput. A class that needs more than its cap should queue within its
# own pool rather than steal workers from the default pool (which schedules
# /health) or from another class. Sized so the SUM stays well under the machine's
# core count so the event loop always has CPU/scheduler headroom.
_POOL_SPECS: dict[str, int] = {
    "io": 6,          # fs/git-status/sqlite — frequent, medium; the widest pool
    "subprocess": 3,  # git/ada/aws CLI — slow, bursty (the slow-WRITER pool)
    "llm": 2,         # Bedrock inference — very slow, low concurrency by design
    "briefing": 1,    # Welcome-Screen pre-warm — serialize; at most one recompute
    "spawn": 2,       # LATENCY-SENSITIVE cold-spawn READERS ONLY (run_e76b3ea5):
                      # credential STS preflight (blocks _ensure_spawned → TTFT) +
                      # resume git status/diff. Isolated from 'subprocess' so a
                      # ~100s uncancellable context_health index rebuild or an
                      # (formerly unbounded) plugin git-clone can NEVER queue ahead
                      # of a cold-session TTFT read. cap=2: readers are low-concurrency.
                      # NEVER route a slow writer here — that defeats the isolation.
}

_pools: dict[str, ThreadPoolExecutor] = {}
_lock = threading.Lock()
_shutdown = False


def get_pool(name: str) -> ThreadPoolExecutor:
    """Return the named dedicated pool, lazily creating it. Raises KeyError for
    an unknown class name (fail-loud — a typo must not silently fall back to the
    default pool, which would reintroduce the starvation this module prevents)."""
    if name not in _POOL_SPECS:
        raise KeyError(
            f"unknown executor pool {name!r}; known: {sorted(_POOL_SPECS)}"
        )
    pool = _pools.get(name)
    if pool is not None:
        return pool
    with _lock:
        pool = _pools.get(name)  # re-check under lock
        if pool is None:
            if _shutdown:
                raise RuntimeError("executor registry already shut down")
            pool = ThreadPoolExecutor(
                max_workers=_POOL_SPECS[name],
                thread_name_prefix=f"swarm-{name}",
            )
            _pools[name] = pool
        return pool


async def run_in(name: str, fn: Callable[..., T], *args: Any) -> T:
    """``await`` ``fn(*args)`` on the named dedicated pool (NOT the default pool).

    Drop-in replacement for ``asyncio.to_thread(fn, *args)`` that routes to a
    class-scoped bounded pool, so slow work never starves the default pool the
    event loop uses to schedule /health. For kwargs, pass a lambda/partial.

    ONE semantic difference from ``to_thread`` (inert today, documented so a future
    caller isn't surprised): ``to_thread`` copies the current ``contextvars.Context``
    into the worker thread; this bare ``run_in_executor`` does NOT. Harmless while the
    backend defines no ``ContextVar`` (verified — zero ContextVar in core/hooks). If a
    ContextVar is ever introduced AND a pooled ``fn`` must read it, wrap with
    ``functools.partial(contextvars.copy_context().run, fn, *args)`` at that callsite."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(get_pool(name), fn, *args)


def shutdown(wait: bool = False) -> None:
    """Shut down all pools. Registered via ``atexit`` (fires at interpreter
    exit). ``wait=False`` so shutdown never blocks daemon stop on an in-flight
    slow job (the OS reaps the threads). NOTE: not yet wired into the FastAPI
    lifespan teardown — atexit is the sole trigger today; in-flight briefing/llm
    threads are not drained on a graceful lifespan stop (acceptable: wait=False
    + daemon process exit reaps them)."""
    global _shutdown
    with _lock:
        _shutdown = True
        for pool in _pools.values():
            pool.shutdown(wait=wait)
        _pools.clear()


atexit.register(lambda: shutdown(wait=False))
