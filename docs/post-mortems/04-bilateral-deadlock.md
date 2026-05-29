# COE10: AskUserQuestion Bilateral Deadlock

> One malformed content block. Frontend crashes. Backend stuck forever.
> Both sides fail simultaneously. Neither can recover the other.

## The Incident (2026-05-29)

A single `ask_user_question` content block with `questions: undefined` (instead of an array) triggered a bilateral deadlock:

1. **Frontend crash:** `questions.every()` threw TypeError → React tree crashed → ALL chat tabs dead (no per-message error boundary)
2. **Backend stuck:** session already transitioned to `WAITING_INPUT` → frontend can't send answer → backend refuses new messages with RuntimeError → session permanently unrecoverable

This is a **bilateral deadlock** — both sides fail simultaneously with no self-healing path. Single-sided failures recover naturally (frontend reload, backend timeout). Bilateral = permanent.

## Root Causes

### 1. No runtime validation on serialization boundary

TypeScript type `questions: AskUserQuestion[]` doesn't hold after SSE deserialization or DB restoration:

```typescript
// Type says this is always an array
interface AskUserQuestionBlock {
  questions: AskUserQuestion[];
}

// Reality after SSE deserialization or DB restore:
// questions could be undefined, null, or malformed
// TypeScript types evaporate at runtime boundaries
```

### 2. State machine asymmetry

`STREAMING` had `force_unstick_streaming()` (stall timeout recovery), but `WAITING_INPUT` had no equivalent. Design assumed "frontend will always respond."

```python
# STREAMING: has recovery
async def force_unstick_streaming(self):
    """Timeout → kill subprocess → COLD"""
    ...

# WAITING_INPUT: no recovery
# If frontend dies, session stays here forever
```

### 3. No React error boundary at message/block level

One render crash kills entire tree. No blast radius containment.

```tsx
// One bad block = all tabs dead
// No boundary between individual content blocks
<ContentBlock data={block} />  // throws → entire React tree unmounts
```

## The Fix (4 files, +48/-6 lines)

| File | Strategy |
|------|----------|
| `AskUserQuestion.tsx` | `Array.isArray()` guard + `(q.options \|\| []).map()` + early return for empty |
| `ContentBlockRenderer.tsx` | `return null` if questions missing/invalid |
| `SkillsPage.tsx` | Same guard (has its own AskUserQuestion renderer) |
| `session_unit.py` | `send()` auto-recovers from WAITING_INPUT: kill subprocess → COLD → resume |

Frontend fix:

```tsx
// Before: trusts TypeScript type at runtime
{questions.every(q => q.answered) && <SubmitButton />}

// After: validates at serialization boundary
if (!Array.isArray(questions) || questions.length === 0) {
  return null;  // Graceful degradation, not crash
}
{questions.every(q => q.answered) && <SubmitButton />}
```

Backend fix:

```python
async def send(self, message: str):
    if self.state == SessionState.WAITING_INPUT:
        # Auto-recover: kill stuck subprocess, reset to COLD, resume
        await self._kill_subprocess()
        self.state = SessionState.COLD
        # Fall through to normal send path
    ...
```

## Adversarial Review Findings (6 total)

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| F1 | MEDIUM | Empty questions rendered empty shell with submit button → user can submit `{}` | Fixed: early return |
| F2 | LOW | `q.options` could also be undefined | Fixed: fallback `[]` |
| F3 | HIGH | `--resume` after killing WAITING_INPUT may not replay the AskUserQuestion. SDK behavior uncertain. | Accepted risk — better than permanent stuck |
| F4-F6 | LOW | Lower severity edge cases | Covered by existing guards |

## Key Lessons

### 1. Serialization boundary = validation boundary

TypeScript types evaporate at SSE/DB/network edges. Always runtime-validate deserialized data. The type system protects you at compile time; it does nothing at the boundary where data enters your process.

### 2. State machine recovery symmetry

Every non-terminal state depending on external input MUST have a force-recovery path. Never trust the client to respond. If `STREAMING` gets a timeout, `WAITING_INPUT` needs one too.

### 3. Bilateral failures are 10x worse than unilateral

The failure window is small (frontend crash exactly when backend enters `WAITING_INPUT`) but once hit, it's permanent. Design for simultaneous failure, not just individual failure.

### 4. Single crash kills all tabs

Without per-block error boundaries, React's "crash one = crash all" applies. Blast radius of a render error = entire app. Error boundaries should wrap at the content-block level, not just the app level.

## Pattern Recognition

This is the same class as COE06 (stale subprocess) — both are "backend depends on client liveness, client dies, backend stuck forever." GC10 governance candidate now at 2/3 evidence (one more instance → auto-promote to steering rule).

The generalized rule: **Any state that blocks on external input without a timeout is a deadlock waiting to happen.** The timeout exists for `STREAMING`. It must exist for every blocking state.

---

**Status:** Resolved. Commit `0683fbef`.
