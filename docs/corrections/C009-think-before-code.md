# C009: Think Before Code

> A 55-line solution took 5 iterations because the first line of code
> was written before the first scenario was listed.

## What Happened

Task: build a pytest safety hook that prevents accidental full test suite execution (700+ tests, known deadlock risk with xdist).

**Iteration 1:** Built a mutex-based solution (130+ lines). User: "Tabs are independent — why mutex?"
**Iteration 2:** Removed mutex, added auto-rewrite of test commands. User: "Auto-rewrite is wrong abstraction."
**Iteration 3:** Removed auto-rewrite, added pattern detection. User: "Think systematically — what are the actual scenarios?"
**Iteration 4:** Listed scenarios. Realized most complexity was unnecessary.
**Iteration 5:** Block-only hook. 55 lines. 15/15 edge cases pass. Shipped.

The user had to push back 4 times. A PE review (which the user had to request) found 4 more bugs in earlier iterations.

## Why It Happened

**Implementation-first thinking.** The agent's reward signal for "I produced code" is stronger than "I understood the problem." Each iteration felt productive (new code!) but was actually regressive (new bugs, wrong abstraction).

The pattern:
1. Hear problem → immediately think about implementation
2. Write code → discover edge case
3. Patch edge case → discover the abstraction is wrong
4. Repeat until user intervenes

The correct sequence (which took a user forcing it):
1. Hear problem → list ALL scenarios
2. For each scenario → what's the expected behavior?
3. What's the simplest code that covers all scenarios?
4. Write it once.

## Structural Prevention

**Pre-Implementation Checkpoint** (added to `backend/context/AGENT.md`):

Before writing code for any task touching >1 file or introducing a new mechanism, explicitly output:

1. **Problem** — one sentence
2. **Scenarios** — every input × expected behavior, including edge cases
3. **Simplest approach** — least code that covers all scenarios
4. **What could break** — for each scenario, what's the failure mode

This makes thinking visible and correctable before code is written. The checkpoint is a blocking requirement — the agent cannot begin implementation without it.

## The Generalizable Insight

**Iterations are not progress.** Each round of "write → discover bug → rewrite" feels like forward motion but is actually lateral drift. The final solution (55 lines) was simpler than the first attempt (130 lines) — every iteration *removed* complexity rather than adding capability.

The structural fix isn't "think harder" (unenforceable). It's "make thinking visible" (checkable). When scenarios and approach are written before code, a wrong direction is caught in 30 seconds of reading instead of 30 minutes of debugging.

## Code References

- Pre-Implementation Checkpoint: `backend/context/AGENT.md` (search "Pre-Fix Check")
- Final hook implementation: `backend/hooks/pretool_use_hook.py` (65 lines)
- Iteration history: commits `1e68325` → `c9e5ec8` (6 commits, squash not possible)
