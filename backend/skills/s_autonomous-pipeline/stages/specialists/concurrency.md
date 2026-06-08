# Concurrency Specialist Review
<!-- version: 2026-06-08 | origin: Pipeline Meta-Intelligence (GAP 8) -->

Scope: Dispatched when changeset touches ThreadPoolExecutor, asyncio.to_thread,
locks/semaphores, shared mutable state, process spawning, or any `await` inside
a loop that could run concurrently.

**Purpose:** Find bugs that only manifest under CONCURRENT execution — code review
verifies sequential correctness; this specialist verifies concurrent safety.

Proven pattern: Three-pool isolation fix (RP35, 2026-05-20), OOM cascade (4 tabs,
2026-04-12), health endpoint hang (pool exhaustion), cross-turn bleed (pipe flush
race), job scheduler blocking default pool.

Output: JSON objects, one finding per line.

```json
{"severity":"HIGH|MED|LOW","confidence":N,"path":"file","line":N,"category":"concurrency","summary":"...","fix":"...","fingerprint":"path:line:concurrency","specialist":"concurrency"}
```

Required: severity, confidence, path, category, summary, specialist.
Optional: line, fix, fingerprint, evidence, mitigation.
If no findings: output `NO FINDINGS` and nothing else.

---

## Confidence Scoring

Every finding MUST include confidence (1-10):
- 9-10: Identified specific shared resource with measured contention path
- 7-8: Pattern matches known concurrent failure class (RP35, RP36, RP37)
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
- Process slots (max_tabs, subprocess limits)
- Network ports (fixed port 18321)
- Mutable module-level state (singletons, registries, counters)

**For each shared resource, answer:**
1. Who are ALL the consumers? (not just the new code — grep the codebase)
2. What's the longest any consumer can hold it?
3. What happens when the resource is exhausted?

### 2. Pool Sizing vs Consumer Count (RP35)

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

### 3. Fix-Removes-Failure-Mode Regression (RP36)

**If the changeset FIXES a bug that previously caused early failure:**
- What code path is now REACHABLE that was previously unreachable?
- What shared resources does the newly-successful path consume?
- Could the previously-impossible success case cause resource exhaustion?

**Classic pattern:** Circuit breaker reset → operations now succeed → pool fills
with long-running operations → health endpoint starves.

**Ask:** "Now that this fix lets the operation SUCCEED, what does success cost?"

### 4. Process Tree Lifetime Mismatch (RP37)

**If the changeset spawns a subprocess:**
- What's the subprocess's expected runtime?
- What's the spawner's minimum lifetime? (session timeout, eviction policy)
- If spawner dies, does subprocess die too? (SIGKILL propagation)
- If subprocess outlives spawner: is that intentional? (detached mode)

**Red flags:**
- Build process (120-300s) spawned from session (60s idle timeout)
- Job subprocess with no process group isolation
- Child process without explicit cleanup on parent death

### 5. Ordering Guarantees (RP16)

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

## Examples of HIGH findings from SwarmAI history

```json
{"severity":"HIGH","confidence":9,"path":"backend/jobs/executor.py","line":156,"category":"concurrency","summary":"subprocess.run(timeout=480s) on default asyncio thread pool. Pool has 8 workers shared with health endpoint aiosqlite. 4 concurrent jobs = pool exhaustion = health endpoint hangs = Tauri 'not responding'.","fix":"Use dedicated _job_executor = ThreadPoolExecutor(4) instead of default pool","specialist":"concurrency"}
```

```json
{"severity":"HIGH","confidence":9,"path":"core/session_unit.py","line":892,"category":"concurrency","summary":"_retry_with_resume() bypasses _acquire_slot() — spawns subprocess without checking alive_count >= max_tabs. Under OOM pressure, retry creates unbounded processes.","fix":"Check alive_count >= max_tabs in retry path, same as normal spawn","specialist":"concurrency"}
```

```json
{"severity":"MED","confidence":7,"path":"core/session_router.py","line":234,"category":"concurrency","summary":"compute_max_tabs() reads RSS from /proc without lock. If called from eviction loop AND spawn path simultaneously, the RSS measurement is stale by the time the spawn decision executes.","fix":"Serialize compute_max_tabs calls via _spawn_lock, or accept staleness with safety margin","specialist":"concurrency"}
```
