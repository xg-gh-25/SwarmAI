---
expert: concurrency
display_name_zh: 并发安全评审官
display_name_en: Concurrency Auditor
domain: code
scope: "竞态、锁、共享可变状态、async 阻塞"
visibility: public
---

> **Pipeline v1: not dispatched.** The review roster is correctness and security (expert simplification E1; §3.5.1 merge); this checklist is merged into correctness. The file stays for the Experts library.

# Concurrency Specialist Review
<!-- version: 2026-06-08 | origin: Pipeline Meta-Intelligence (GAP 8) -->

Scope: Dispatched when changeset touches ThreadPoolExecutor, asyncio.to_thread,
locks/semaphores, shared mutable state, process spawning, or any `await` inside
a loop that could run concurrently.

**Purpose:** Find bugs that only manifest under CONCURRENT execution — code review
verifies sequential correctness; this specialist verifies concurrent safety.

Proven pattern: Three-pool isolation fix (2026-05-20), OOM cascade (4 tabs,
2026-04-12), health endpoint hang (pool exhaustion), cross-turn bleed (pipe flush
race), job scheduler blocking default pool.

Report each finding in the format the review that called you gives (a pipeline handout's OUTPUT CONTRACT, or the reporting line of a native review); this file says only what to check. If you find nothing, say NO FINDINGS.

---

## Confidence Scoring

How sure you are before you report a finding (when it is not certain, say so in its claim):
- 9-10: Identified specific shared resource with measured contention path
- 7-8: Pattern matches known concurrent failure class
- 5-6: Plausible race condition, needs load testing to confirm
- 3-4: Theoretical contention, unlikely in production load
- 1-2: Speculation

---

## Checklist

### 1. Shared Resource Enumeration (MUST DO FIRST)

**Before analyzing the changeset, enumerate ALL shared resources it touches:**
- Thread pools (default asyncio pool, named executors)
- Database connections (sqlite, WAL mode implications)
- File handles (shared log files, state files, JSONL)
- Process slots (a pool size limit, subprocess limits)
- Network ports (a fixed port)
- Mutable module-level state (singletons, registries, counters)

**For each shared resource, answer:**
1. Who are ALL the consumers? (not just the new code — grep the codebase)
2. What's the longest any consumer can hold it?
3. What happens when the resource is exhausted?

### 2. Pool Sizing vs Consumer Count

**If the changeset adds a new `asyncio.to_thread()` or `run_in_executor(None, ...)`:**
- What pool does it use? (None = default asyncio pool, SHARED)
- How long can this operation block? (>1s = concerning, >5s = dedicated pool required)
- Who else uses the same pool? (health endpoints, DB queries, SSE keepalives)
- If this new consumer blocks for max duration, do the OTHER consumers starve?

**Rule:** Any blocking operation >5s on a shared pool with latency-sensitive
consumers (<1s requirement) MUST use a dedicated ThreadPoolExecutor.

**Red flags:**
- `asyncio.to_thread(subprocess.run, ...)` with timeout >30s on default pool
- `run_in_executor(None, blocking_io)` where blocking_io can hang indefinitely
- New consumer added to pool that already has N-1 max_workers consumers

### 3. Fix-Removes-Failure-Mode Regression

**If the changeset FIXES a bug that previously caused early failure:**
- What code path is now REACHABLE that was previously unreachable?
- What shared resources does the newly-successful path consume?
- Could the previously-impossible success case cause resource exhaustion?

**Classic pattern:** Circuit breaker reset → operations now succeed → pool fills
with long-running operations → health endpoint starves.

**Ask:** "Now that this fix lets the operation SUCCEED, what does success cost?"

### 4. Process Tree Lifetime Mismatch

**If the changeset spawns a subprocess:**
- What's the subprocess's expected runtime?
- What's the spawner's minimum lifetime? (session timeout, eviction policy)
- If spawner dies, does subprocess die too? (SIGKILL propagation)
- If subprocess outlives spawner: is that intentional? (detached mode)

**Red flags:**
- Build process (120-300s) spawned from session (60s idle timeout)
- Job subprocess with no process group isolation
- Child process without explicit cleanup on parent death

### 5. Ordering Guarantees

**If the changeset fires N async operations that produce ordered output:**
- Are responses consumed in ORDER or just as-they-arrive?
- If order matters: is there an explicit ordering mechanism? (sequence numbers, await serial)
- Can responses arrive out of order? (parallel HTTP calls, concurrent DB writes)

**Red flags:**
- `Promise.all()` / `asyncio.gather()` where result ORDER matters but items have different latencies
- Event stream where messages from multiple sources interleave without sequencing
- Database writes without transaction isolation where read-after-write assumes ordering

### 6. Lock Ordering / Deadlock Prevention

**If the changeset acquires multiple locks or uses nested `async with`:**
- Is there a consistent lock ordering? (always A before B, never B before A)
- Can a thread hold lock A and wait for lock B while another holds B and waits for A?
- Are all locks acquired with timeouts? (no indefinite waits)

**Red flags:**
- `_spawn_lock` held while calling code that acquires `_env_lock` (or vice versa)
- `async with semaphore:` inside `async with other_semaphore:`
- File locks (fcntl.flock) without timeout → process hangs if lock holder crashes

### 7. Atomic State Transitions Under Concurrency

**If the changeset reads state → decides → writes state (not atomic):**
- Can another task modify the state between read and write?
- Is the read-decide-write protected? (lock, CAS, atomic UPDATE WHERE)
- What happens if two tasks both read the same state and both decide to write?

**Red flags:**
- `if status == "idle": status = "running"` without a lock (TOCTOU)
- `count = get_count(); count += 1; set_count(count)` (lost update)
- `SQLite UPDATE ... SET status='active'` without `WHERE status='idle'` (no CAS)

---

## Examples of HIGH findings

- HIGH · `backend/jobs/executor.py:156`: subprocess.run(timeout=480s) on default asyncio thread pool. Pool has 8 workers shared with health endpoint aiosqlite. 4 concurrent jobs = pool exhaustion = health endpoint hangs = Tauri 'not responding'. Fix: Use dedicated _job_executor = ThreadPoolExecutor(4) instead of default pool

- HIGH · `core/session_unit.py:892`: _retry_with_resume() bypasses _acquire_slot() — spawns subprocess without checking alive_count >= max_tabs. Under OOM pressure, retry creates unbounded processes. Fix: Check alive_count >= max_tabs in retry path, same as normal spawn

- MED · `core/session_router.py:234`: compute_max_tabs() reads RSS from /proc without lock. If called from eviction loop AND spawn path simultaneously, the RSS measurement is stale by the time the spawn decision executes. Fix: Serialize compute_max_tabs calls via _spawn_lock, or accept staleness with safety margin
