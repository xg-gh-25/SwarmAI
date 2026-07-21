# Correctness Specialist Review
<!-- version: 2026-07-21 | synced with: REVIEW_PATTERNS.md RP1-RP52 -->

Scope: Always dispatched when changeset > 50 lines.

**Cross-reference:** Also check patterns from `REVIEW_PATTERNS.md` that fall
in your domain: RP13 (state machine), RP15 (setTimeout), RP16 (concurrent async),
RP27 (non-deterministic ordering), RP33 (multi-shape returns), RP34 (shell variable
scope across Bash calls), RP47 (test-theater — test patches the symbol-under-change
or re-derives a prod formula; mutation-prove RED-on-revert), RP48 (stale/false
comment — changed behavior, unchanged docstring/comment). These are proven bug
patterns with concrete examples.

Output: JSON objects, one finding per line.

```json
{"severity":"HIGH|MED|LOW","confidence":N,"path":"file","line":N,"category":"correctness","summary":"...","fix":"...","fingerprint":"path:line:correctness","specialist":"correctness"}
```

Required: severity, confidence, path, category, summary, specialist.
Optional: line, fix, fingerprint, evidence.
If no findings: output `NO FINDINGS` and nothing else.

---

## Confidence Scoring

Every finding MUST include confidence (1-10):
- 9-10: Read specific code, constructed concrete failure scenario
- 7-8: High confidence pattern match, very likely correct
- 5-6: Moderate — could be false positive
- 3-4: Low — suspicious but may be fine
- 1-2: Speculation

Modifiers: +3 concrete failure demo, +2 reachable from user action, -4 test file.

---

## Categories

### Logic Errors
- Off-by-one in loops, slicing, or range calculations
- Wrong operator (== vs ===, & vs &&, `is` vs `==`)
- Inverted conditions (not/! applied incorrectly)
- Wrong variable used (copy-paste from similar block)
- Short-circuit evaluation hiding side effects

### Edge Cases & Boundary Conditions
- Empty input (None, [], "", 0) not handled
- Maximum/minimum values (integer overflow, empty string vs None)
- Concurrent access to shared mutable state
- First-run vs steady-state behavior difference
- Unicode/encoding edge cases in string operations

### Error Handling
- Exceptions swallowed silently (bare except, empty catch)
- Error paths that leave state inconsistent (partial write, no rollback)
- Missing null/None checks before attribute access
- Errors logged but not propagated (caller never knows)
- Resource cleanup skipped on error path (files, connections, locks)

### State Machine Completeness
- Declared states that are unreachable (no transition leads there)
- Missing transitions (state A can't reach state B despite design intent)
- Transitions without guards (any input triggers transition)
- No terminal state handling (what happens when state machine "finishes"?)
- Concurrent state mutations without synchronization

### Race Conditions & Concurrency
- Shared mutable state accessed from multiple async tasks
- Check-then-act patterns without atomicity (TOCTOU)
- Missing locks on critical sections
- Async operations that assume ordering (fire-and-forget)
- Shared state mutation before async yield without post-yield re-validation

### Type Safety
- Wrong types passed (string where int expected, None where object expected)
- Unsafe casts or type assertions without validation
- Any/unknown types propagating through interfaces
- Union types where only one variant is handled
- Serialization/deserialization type mismatches (JSON parse → wrong shape)

### Return Value Correctness
- Functions that can return wrong type silently (None vs expected object)
- Boolean logic errors in conditional returns
- Missing return statements in conditional branches
- Return value ignored by caller when it shouldn't be

---

## Deep Adversarial Analysis (Mandatory for HIGH confidence findings)

The categories above are WHAT to look for. This section defines HOW to analyze
— the methodology that separates a mechanical checklist from a PE review.

### 1. TOCTOU Analysis (Time-of-Check to Time-of-Use)

For every "check then act" pattern found:
- Identify the CHECK (condition evaluation) and ACT (mutation/decision)
- Determine: is there an `await` between CHECK and ACT?
- If NO await: safe under asyncio cooperative scheduling (single-threaded)
- If YES await: another coroutine can interleave → potential race
- Evidence: trace the exact yield point, identify what could change

### 2. Concurrent Path Exhaustion

For every state/resource mutation:
- List ALL coroutines that can reach this mutation point
- For each pair: what happens if BOTH fire "simultaneously" (interleave at await)?
- Specific checks:
  - Double-transition: is same-state transition a no-op? (should be)
  - Double-cleanup: is cleanup idempotent? (ProcessLookupError, AttributeError on None)
  - Stale reference: can a local variable reference something already cleaned up?

### 3. Self-Cancellation Analysis

For any asyncio task that modifies its own lifecycle:
- Can the task cancel itself? (calling stop/cancel on its own task reference)
- Does it null the reference BEFORE the operation that triggers cancellation?
- What happens if CancelledError is raised at each await point in the task?

### 4. Resource Lifecycle Completeness

For every resource acquired (task, subprocess, file handle, connection):
- Trace ALL exit paths (normal return, exception, cancellation)
- Verify the resource is released on EVERY path
- Special attention to: tasks created but never awaited/cancelled,
  subprocesses that outlive their parent coroutine

### 5. Timeout Semantics

For every timeout mechanism:
- What CAN it cancel? (asyncio.wait_for cannot cancel native I/O)
- What ACTUALLY happens when timeout fires? (CancelledError? TimeoutError? Nothing?)
- Is there a secondary mechanism for what the primary timeout can't handle?
- What's the maximum legitimate wait? (tool execution, high context TTFT)

### 6. Idempotency Verification

For every operation that could be called twice:
- Call it twice: does state remain consistent?
- Call it with stale data: does it crash or gracefully no-op?
- Specific patterns to verify:
  - `_transition(DEAD)` when already DEAD → must be no-op
  - `_force_kill(pid)` when process already gone → ProcessLookupError caught
  - `_stop_watchdog()` when task already None → no-op

### 7. Mock-Production Mismatch Audit (for test reviews)

When reviewing tests alongside production code:
- Does the mock HIDE a real behavior? (mock returns success, production raises)
- Does the test setup match production state? (missing fields, wrong defaults)
- Would the test pass even if the feature is broken? (tautological test)
- Are leaked resources (tasks, files) cleaned up between tests?

**Mechanical form → RP47 (test-theater):** the two grep-able tells that make
"would the test pass even if broken?" assertable — (1) the test `patch(`es the
EXACT symbol the changeset modifies (tests the mock, not the code); (2) the test
re-derives a prod formula/constant in a local helper (asserts the formula against
its own copy). Verdict test: revert the guarded prod line → the test MUST go RED.
Green-on-revert = theater. Apply RP47 to every test the changeset adds/modifies.
