---
expert: performance
display_name_zh: 性能评审官
display_name_en: Performance Auditor
domain: code
scope: "端点、DB 查询、集合循环、渲染路径"
visibility: public
---

> **Pipeline v1: not dispatched.** The review roster is correctness and security (expert simplification E1; §3.5.1 merge); this checklist is merged into correctness. The file stays for the Experts library.

# Performance Specialist Review
<!-- version: 2026-09-13 -->

Scope: When changeset touches backend endpoints, database queries, loops over
collections, or frontend rendering paths.

**Also check, in your domain:**
hook no-op path scaling — O(n) in steady state, shared executor/pool contention — blocking >5s on shared pool starves latency-sensitive consumers, fix-removes-failure-mode regression — newly-successful path consumes shared resources. These are proven perf patterns.

Report each finding in the format the review that called you gives (a pipeline handout's OUTPUT CONTRACT, or the reporting line of a native review); this file says only what to check. If you find nothing, say NO FINDINGS.

---

## Confidence Scoring

How sure you are before you report a finding (when it is not certain, say so in its claim):
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
