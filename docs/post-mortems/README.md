# Post-Mortems — How Failures Became Architecture

Each post-mortem follows the same structure:
1. **What happened** — the concrete failure, with evidence
2. **Why it happened** — root cause (not symptoms)
3. **Structural prevention** — how the system now makes this class impossible
4. **The generalizable insight** — what you can apply to your own systems

These aren't historical curiosities. They're the origin stories of mechanisms that are still running in production today.

## Index

| # | Title | Bug Class Eliminated | Mechanism Born |
|---|-------|---------------------|---------------|
| 1 | [Pipeline Confidence Illusion](./01-pipeline-confidence-illusion.md) | Features that pass tests but don't work | Adversarial Review gate |
| 2 | [Understand the State Machine](./02-understand-the-state-machine.md) | Incremental fix-without-understanding | 2-strike rule + state machine audit |
| 3 | [Adversarial Review Origin Story](./03-adversarial-review-origin-story.md) | Self-review assumption blindness | Fresh-context mandatory reviewer |
| 4 | [Bilateral Deadlock](./04-bilateral-deadlock.md) | Frontend + backend simultaneous stuck | State machine recovery symmetry + serialization validation |

## The Pattern

Every correction in SwarmAI follows the same lifecycle:

```
Failure → Correction captured (EVOLUTION.md)
  → Pattern detected (recurring? compound?)
  → Rule promoted (STEERING.md or AGENT.md)
  → Gate hardened (L1 text → L2 code → L3 structural)
  → Class eliminated (zero recurrence)
```

The system currently tracks 25 corrections. Zero have repeated their exact pattern after structural prevention was applied.

## Why Publish These

Most projects publish changelogs (what changed) and sometimes retrospectives (what went wrong). We publish the **mechanism that prevents recurrence** — because that's the interesting part.

If you read these and think "we have the same problem" — the fix isn't specific to SwarmAI. The structural pattern (detect → prevent → verify prevention) applies to any system with a quality gate.
