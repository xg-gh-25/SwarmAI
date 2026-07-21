# Performance Specialist Review
<!-- version: 2026-07-21 | synced with: REVIEW_PATTERNS.md RP1-RP52 -->

Scope: When changeset touches backend endpoints, database queries, loops over
collections, or frontend rendering paths.

**Cross-reference:** Also check patterns from `REVIEW_PATTERNS.md` in your domain:
RP30 (hook no-op path scaling — O(n) in steady state), RP35 (shared executor/pool contention — blocking >5s on shared pool starves latency-sensitive consumers), RP36 (fix-removes-failure-mode regression — newly-successful path consumes shared resources). These are proven perf patterns.

Output: JSON objects, one finding per line.

```json
{"severity":"HIGH|MED|LOW","confidence":N,"path":"file","line":N,"category":"performance","summary":"...","fix":"...","fingerprint":"path:line:performance","specialist":"performance"}
```

Required: severity, confidence, path, category, summary, specialist.
Optional: line, fix, fingerprint, evidence.
If no findings: output `NO FINDINGS` and nothing else.

---

## Confidence Scoring

Every finding MUST include confidence (1-10):
- 9-10: Measured or obvious O(n^2) with known data scale
- 7-8: Clear pattern match (N+1, blocking async, unbounded query)
- 5-6: Potential issue — depends on data volume
- 3-4: Might scale poorly in theory, insufficient context
- 1-2: Premature optimization concern

Modifiers: +3 if data volume known (from DDD docs) and proves scaling issue,
+2 if on a hot path (per-request, per-session, hook that runs every turn),
-2 if cold path (startup, admin-only, rare operation).

---

## Categories

### N+1 Queries & Database
- ORM associations traversed in loops without eager loading
- Database queries inside iteration blocks (for/map/each)
- SELECT * when only specific columns needed in hot path
- Missing indexes on columns used in WHERE/ORDER BY (check schema)
- Queries without LIMIT that grow with data volume (unbounded)

### Algorithmic Complexity
- O(n^2) patterns: nested loops over same/growing collection
- Repeated linear searches that could use a hash/set lookup
- String concatenation in loops (should use join/builder)
- Sorting or filtering large collections multiple times
- Recomputing values that could be cached/memoized

### Blocking in Async Contexts
- Synchronous I/O (file read, subprocess, HTTP) inside async functions
- time.sleep() / blocking waits inside event-loop handlers
- CPU-intensive computation blocking the main thread
- subprocess.run() without timeout in async context
- Database operations without connection pooling/timeout

### Hook & Recurring Path Scaling
- No-op cost analysis: what happens when this code runs and does NOTHING?
- Scanning ALL historical data when only recent data needed
- File system walks without depth limits or caching
- Per-session/per-turn cost that grows with session history
- Background loops without early-exit for no-work-needed case

### Memory & Resource
- Large allocations in hot paths (per-request object creation)
- Growing data structures without bounds (append without trim)
- Missing connection/file/resource cleanup (leaks over time)
- Loading entire file/dataset into memory when streaming would work
- Cache without eviction policy (grows unbounded)

### Frontend Rendering (if applicable)
- Unnecessary re-renders (new object refs in render path)
- Fetch waterfalls (sequential API calls that could be parallel)
- Missing lazy loading on below-fold content
- Large bundle imports when tree-shakeable alternative exists
- Layout thrashing (read-write-read DOM cycles)

### Steady-State Analysis
- What happens after 6 months of accumulated data?
- What's the cost per invocation × invocations per day?
- Does the no-op path have acceptable cost at scale?
- Are there O(history) operations that should be O(recent)?
