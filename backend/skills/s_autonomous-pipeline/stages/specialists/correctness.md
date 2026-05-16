# Correctness Specialist Review
<!-- version: 2026-05-16 | synced with: REVIEW_PATTERNS.md RP1-RP34 -->

Scope: Always dispatched when changeset > 50 lines.

**Cross-reference:** Also check patterns from `REVIEW_PATTERNS.md` that fall
in your domain: RP13 (state machine), RP15 (setTimeout), RP16 (concurrent async),
RP27 (non-deterministic ordering), RP33 (multi-shape returns), RP34 (shell variable
scope across Bash calls). These are proven bug patterns with concrete examples.

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
