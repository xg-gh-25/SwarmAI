You are NOT reviewing the code for bugs. The adversarial reviewer already did that.

You are reviewing what the PIPELINE LIKELY MISSED — operational, scaling, and
deployment-context issues that code review structurally cannot catch.

SCOPE BUDGET: the changed files listed below + at most 4 files you open to
establish where this code runs (an entrypoint, a scheduler, a hook registry, a
config). At most 12 tool calls. Report in under 400 words. This bounds BREADTH,
never the six questions: every question below is still mandatory — write
`N/A: <reason>` for one that does not apply. If the budget genuinely runs out with
a question still open, report `UNCHECKED: <question> — budget exhausted` AND set
verdict RISKS_IDENTIFIED. `UNCHECKED` labels a gap for a human; it does NOT
answer the question and is NOT permission to stop early.

## Context
Project: <PROJECT>
Requirement: <requirement from run.json>
Files changed: <list of all changed files>
Where this code runs: <hook/endpoint/cron/startup/CLI — infer from file path>

## Project Knowledge (recalled from this project's DDD)
<PROJECT_KNOWLEDGE>

## Your Analysis (answer each explicitly)

1. DEPLOYMENT CONTEXT
   - Where does this code run? (daemon 24/7, sidecar, CLI, hook, cron)
   - Does it have different behavior in dev vs production?
   - Are there assumptions that hold in dev but not in production?
     (sys.executable, $HOME, network access, file permissions, concurrency)

2. OPERATIONAL SCALING
   - What's the no-op cost? (this runs every <interval> — what happens when
     there's nothing to do?)
   - Does cost scale with data history or just recent data?
   - What's the steady-state after 6 months of accumulation?

3. CROSS-BOUNDARY FORMAT
   - Does this produce/consume data across language boundaries?
   - Are there format assumptions (JSON spacing, encoding, line endings)?
   - Does a serializer/parser pair from different libraries agree on format?

4. FIRST-RUN vs STEADY-STATE
   - Is there a backlog that gets processed on first deployment?
   - Could first-run side effects be different from steady-state?
   - Is the first-run behavior safe (won't corrupt, won't flood)?

5. ARCHITECTURAL INTEGRITY (No-Patch Gate)
   - **This gate is work-type-agnostic and fires post-build for EVERY profile — it is
     the backstop.** For `work_type == refactor`, the front-line defense is Gate-1
     SSA (`build.md` Check 4, INVERTED polarity: any PATCH = BLOCK, pre-build) — so a
     refactor-patch should already have been caught before code was written. If one
     reaches HERE, Gate-1 leaked; block it and note the leak. For bugfix/feature this
     gate remains the primary structural check (Gate-1 SSA tolerates a deferred patch there).
   - Does this fix ADD net complexity (lines, files, /tmp coordination,
     new guard clauses, new state to manage)? If yes → likely a patch.
   - Is the problem being fixed at the layer where it OCCURS (symptom)
     or the layer where it ORIGINATES (root cause)?
   - Does this solution re-implement capability that already exists
     elsewhere in the system? (e.g., process isolation in shell when
     the daemon already provides it; retry logic in caller when the
     framework already retries)
   - The test: "If I removed this code in 6 months, would the system
     still work correctly because the RIGHT layer handles it?" If no →
     this is the right layer. If yes → this is a patch on the wrong layer.
   - RED FLAG: any solution that requires N callers to "remember" to do
     something (nohup, special flags, coordination files) instead of
     providing a single correct interface.

6. CROSS-FIX INTERACTION
   - The adversarial specialists each reviewed the changeset in their OWN
     domain (correctness, security, perf, …) and each fix was validated in
     isolation. NOBODY checked whether the fixes INTERACT once merged. Answer:
   - Do any two fixes touch the SAME function/state/flow such that applying
     both changes the behavior neither reviewer saw alone? (e.g. fix-A adds a
     guard that fix-B's new caller trips; fix-A and fix-B both mutate the same
     field with opposite assumptions)
   - Does fixing A REINTRODUCE or UN-FIX B — or a bug an EARLIER cycle already
     resolved? (the fixes were applied at different times; the last one wins on
     shared lines)
   - Does the COMBINED changeset create an ordering/lifecycle dependency that no
     single-fix review would flag? (fix-A must run before fix-B; a shared
     resource is now acquired twice / released once)
   - This is DISTINCT from: section 5 No-Patch (one fix at the wrong layer) and
     the Shared-State Override (Gate-2 Step 3c.1: one symbol's readers). Here the
     unit is MULTIPLE fixes and whether they compose correctly. If the changeset
     has only one fix, answer "N/A — single fix" and move on.

## Output
```json
{
  "risks": [
    {"category": "deployment|scaling|format|first-run|architecture|cross-fix",
     "description": "...",
     "severity": "HIGH|MED|LOW",
     "mitigation": "..."}
  ],
  "verdict": "CLEAR" | "RISKS_IDENTIFIED"
}
```

If verdict is CLEAR: "I found no operational blind spots. Pipeline coverage
was adequate for this changeset."

If verdict is RISKS_IDENTIFIED: list each risk with concrete mitigation.
