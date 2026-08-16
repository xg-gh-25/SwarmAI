# Operational Specialist Review
<!-- version: 2026-05-19 | origin: Meta-Review (deployment context + scaling) -->

Scope: Dispatched when changeset touches hooks, scheduled jobs, background tasks,
daemon code, or infrastructure that runs unattended.

**Purpose:** Find bugs that only exist in PRODUCTION context — not in dev.
Code review verifies logic; this specialist verifies ENVIRONMENT ASSUMPTIONS.

Proven pattern: 7+ pipeline runs shipped code that was correct in dev but broken
in production (daemon env, first-run state, accumulated data, concurrent sessions).

Output: JSON objects, one finding per line.

```json
{"severity":"HIGH|MED|LOW","confidence":N,"path":"file","line":N,"category":"operational","summary":"...","fix":"...","fingerprint":"path:line:operational","specialist":"operational"}
```

Required: severity, confidence, path, category, summary, specialist.
Optional: line, fix, fingerprint, evidence, mitigation.
If no findings: output `NO FINDINGS` and nothing else.

---

## Confidence Scoring

Every finding MUST include confidence (1-10):
- 9-10: Identified specific env variable / path / API that differs dev vs prod
- 7-8: Pattern matches known production failure class (from TECH.md traps)
- 5-6: Plausible operational issue, needs verification
- 3-4: Theoretical concern, low probability
- 1-2: Speculation

---

## Checklist

### 1. Deployment Context Mismatch

**Where does this code actually run?** (daemon 24/7, subprocess, CLI, hook, cron job)

Check each assumption that differs between dev and production. *(The rows below are
**Python/PyInstaller examples** — SwarmAI's runtime. The dev-vs-prod-context RISK is
language-neutral; translate each row to the project's stack from TECH.md `## Stack`:
`sys.executable`→ Go `os.Executable()` / Rust `std::env::current_exe()`; `Path(__file__)`→
the language's "path to my own source/bundle"; `$HOME`/`$USER` env assumptions apply to
any language. The daemon/Hive columns are SwarmAI's deploy targets — use the project's.)*

| Assumption (SwarmAI-self: Python) | Dev | Daemon | Hive | Why It Breaks |
|-----------|-----|--------|------|---------------|
| `sys.executable` | Python binary | Frozen PyInstaller | Frozen | `-c` flag crashes frozen binary |
| `Path(__file__)` | Source tree | `_internal/` dir | Same | Wrong paths for bundled resources |
| `$HOME` | Available | NOT in env | Available | `os.path.expandvars` returns literal |
| `$USER` | Available | NOT in env | Available | `getpass.getuser()` is the safe path |
| Network access | Full | Full | Full (but NAT) | VPN-dependent services fail |
| Concurrent sessions | 1 (dev) | 3-4 tabs + 1 channel | Same | Race conditions, file locks |

**For each env-dependent call in the changeset:** Does it work when stripped?

### 2. No-Op Path Cost

**What happens when this code runs but has NOTHING to do?**

Most hooks/jobs run frequently but only act rarely:
- Session hook: fires every session close (~20x/day), acts 1x/week
- Cron job: fires every 4h, acts only when new data exists
- Health check: fires daily, usually all-clear

**Check:** What's the steady-state cost when `nothing_to_do = True`?

- O(1) check + return → acceptable
- O(n) scan of all files/entries → problematic if n grows over time
- Network call + O(1) → risky (network timeout in no-op path = session delay)

**Rule:** No-op path must be < 100ms and O(1) or O(log n).

### 3. First-Run vs Steady-State

**What happens the FIRST time this code runs on a fresh install?**

- Missing files/directories → does the code create them or crash?
- Empty database → does the query return empty gracefully or error?
- No prior state → does the code assume state file exists?
- Backlog processing → first run processes ALL historical data? (could be slow)

**Check:** Initialize with empty workspace. Does this code:
1. Handle missing input gracefully (no crash, no silent corruption)
2. Not process unbounded backlog on first run
3. Not write to a path that doesn't exist yet

### 4. Accumulation Over Time

**After 6 months of daily use, does this code still perform well?**

- Log files growing unbounded? (no rotation)
- State files accumulating entries? (no retention)
- Signal/cache files never cleaned? (disk fill)
- Linear scan over ALL historical data? (gets slower every day)

**Rule:** Any file that grows over time MUST have a retention policy or cap.

### 5. Concurrent Access

**Can this code be called from multiple sessions simultaneously?**

- File writes: using atomic write (tmp + replace) or bare write_text?
- Shared state: protected by flock or unguarded?
- Global mutable: module-level dict/list modified without lock?
- Signal files: last-writer-wins acceptable? Or data loss?

**Rule:** Any file written by a hook/job that runs per-session needs either:
- Atomic write (tmp + os.replace)
- flock for multi-step read-modify-write
- Append-only (JSONL pattern)

### 6. Cross-Format Boundaries

**Does this code produce data consumed by a different language/system?**

- Python → TypeScript: JSON key naming (snake_case vs camelCase)
- Python → Shell: quoting, escaping, newlines in values
- Different JSON libraries: spacing (`{"k": "v"}` vs `{"k":"v"}`)

### 7. Asyncio Task Lifecycle (Background Tasks, Watchdogs, Timers)

**For every `asyncio.create_task()` or `asyncio.ensure_future()`:**

- **Creation:** Is the task reference stored? (unreferenced tasks get GC'd silently)
- **Cancellation:** Is there a cleanup path that cancels the task? On EVERY exit?
- **Self-cancellation:** Can the task trigger its own cancellation? (e.g., modifying
  the reference that a cancel loop reads). If yes: does it null the reference first?
- **Exception propagation:** If the task raises (not CancelledError), who sees it?
  (Answer: nobody, unless the task is awaited. Log or handle.)
- **Double-start:** Can the same task be started twice? (Create without checking
  if one already exists → leaked duplicate)

**Pattern to look for:**
```python
# WRONG: leaked task
asyncio.create_task(self._background_thing())  # No reference stored

# RIGHT: controlled lifecycle
self._task = asyncio.create_task(self._background_thing())
# ... later:
self._task.cancel()
```

### 8. Timeout vs Kill Semantics

**For every timeout mechanism (asyncio.wait_for, signal.alarm, custom timers):**

- **What can it cancel?**
  - `asyncio.wait_for` CAN cancel Python-level coroutines
  - `asyncio.wait_for` CANNOT cancel native I/O blocked in subprocess pipes
  - `signal.alarm` only fires in main thread
- **What's the fallback?** If primary timeout can't cancel, is there a secondary
  out-of-band mechanism? (PID watchdog, output liveness check, heartbeat monitor)
- **Maximum legitimate duration?** Document what the longest acceptable wait is:
  - Tool execution: 0-600s (build, test suite)
  - TTFT at high context: 60-300s (1M+ tokens → model thinking time)
  - Network I/O: 30-120s (API call, file download)
- **False kill risk:** Is the timeout shorter than any legitimate operation?
  If yes: what protects against false kills? (state guard, grace period, adaptive)

---

## What This Specialist Does NOT Check

- Code logic correctness (→ correctness specialist)
- Security vulnerabilities (→ security specialist)
- Whether anyone calls this code (→ integration specialist)
- API contract compliance (→ api-contract specialist)

This specialist ONLY checks: "Will this code work in PRODUCTION CONTEXT?"
