# C020: Extract ≠ Extend

> Extracting a function for reuse AND adding a new caller
> in one commit hides bugs that only exist in the new context.

## What Happened

Task: add a watchdog that auto-recovers the backend if it crashes. The implementation extracted `spawn_subprocess()` from `start_backend` (the original caller) and simultaneously added the watchdog as a second caller — all in one commit.

Self-review missed two bugs that **only exist in the watchdog context**:

1. **Stale PID on Err path:** When spawn fails, the old PID stays in state. In `start_backend` this is harmless (function returns Err to user, nobody reads stale state). In the watchdog, execution continues with stale PID → subsequent health checks poll a dead process.

2. **Concurrent shutdown race:** `start_backend` runs at app startup (no concurrent actors). The watchdog runs mid-lifecycle where `graceful_shutdown_and_kill` can fire during the 20-second async probe. Original caller: safe. New caller: race condition.

Both bugs are invisible in the original calling context. They only manifest because the new caller has different invariants (concurrent actors, continued execution after failure).

## Why It Happened

**Combining extraction with extension makes review focus on the wrong question.**

When extraction and new-caller are in one commit, the reviewer asks: "Is the code correct?" — and it is. The function works. Tests pass.

The question that catches bugs is: "What's different about the new calling context?" — but this question is invisible when mixed with a mechanical refactor. The extraction (safe, behavior-preserving) provides cover for the extension (dangerous, new invariants).

## Structural Prevention

**Two separate commits** (added to STEERING.md):

- **Commit 1:** Pure extraction. Same caller, same behavior. Refactor only.
- **Commit 2:** Add the new caller. Review focuses entirely on: "What's different about this context?"

This forces the review of commit 2 to confront the dangerous question directly, without the safe refactoring as noise.

**Calling Context Audit** (Pre-Implementation Checkpoint #6):

For each new caller of an extracted function, explicitly list:
- What's different about the calling context vs the original?
- Concurrency? Lifecycle stage? Error recovery model? Other actors mutating shared state?

Different context = different invariants = different bugs.

**Code Quality Scan** (added 🟡 pattern):

"Shared state mutation before async yield without post-yield re-validation" — detects the exact pattern where `.await` yields for >1s and assumptions about shared state may be stale when execution resumes.

## The Generalizable Insight

**Refactoring is safe. Adding callers is dangerous. Mixing them is invisible danger.**

The safety of extraction creates false confidence that extends to the new caller. "I just moved code around" is true for commit 1 but false for the combined change. The new caller introduces invariants that the original context never had — and those invariants are where bugs live.

This generalizes beyond functions: any time you make something reusable AND use it in a new context simultaneously, you're hiding the dangerous part (new context) behind the safe part (extraction).

## Code References

- Bug commit: `132c747` (combined extraction + new caller)
- STEERING.md rule: search "Extract ≠ Extend (Separate Commits)"
- Pre-Implementation Checkpoint #6: `backend/context/AGENT.md` (search "Calling context audit")
- Code Quality Scan pattern: `backend/context/AGENT.md` (search "shared state mutation before async yield")
