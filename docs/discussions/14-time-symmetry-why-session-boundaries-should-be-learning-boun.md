# Time Symmetry — why session boundaries should be learning boundaries, not idle boundaries

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/14) | Category: General | Published: 2026-05-18

---

Most agent frameworks treat the space between sessions as dead time. The system sits idle until the next user prompt. We think this is a structural waste — and fixing it creates a compounding advantage that widens with every session.

## The pattern (what most frameworks do)

```
Session N ends → [idle] → Session N+1 starts cold
```

The knowledge from Session N lives only in chat history (if you are lucky) or vanishes entirely. Session N+1 reinvents the same context from scratch. Even "memory" systems just store text — they do not *process* it between sessions.

## What we do instead

```
Session N ends →
  ├→ evolution_trigger_hook (detect capability gaps + corrections)
  ├→ distillation_hook (promote patterns from raw logs → curated memory)
  ├→ context_health_hook (validate all 11 context files consistent)
  ├→ ddd_cultivation_hook (grow domain knowledge from normal work)
  ├→ skill_metrics_hook (score skill usage → evict unused)
  ├→ code_intel_hook (update AST graph with new symbols)
  ├→ user_observer_hook (learn user patterns)
  └→ auto_commit_hook (version-control everything)
  [8 hooks fire in parallel, ~30s total, bounded by 25s deadline]
  ↓
Session N+1 starts with a system that learned from Session N
```

## Why this matters (the compound math)

After 300 sessions:
- 25 corrections permanently prevent their class of bug (Evolution Registry)
- 15K tokens of curated memory (distilled from 300 raw DailyActivity files)
- Domain knowledge (DDD docs) grew from 0 to full coverage WITHOUT manual writing
- Skills that got better because low-usage ones were evicted (fewer choices = less confusion)

The key insight: **learning happens at session boundaries, not during sessions.** During a session, the agent is executing. Between sessions, it is reflecting. Most frameworks have no mechanism for reflection because they model a session as an isolated unit.

## The design constraints that make this work

1. **Bounded execution time** — all hooks share a 25s deadline. Post-session work cannot block the next session. Forces hooks to be cheap: incremental, delta-only, fail-open.

2. **Fail-open, not fail-closed** — if a hook crashes, the system is still usable. Hooks add value, they never gate access. This is why we can have 8+ without fragility.

3. **Monotonic improvement** — hooks can only ADD to memory/knowledge/evolution. They cannot delete, overwrite, or degrade. Deletion requires human decision (weekly memory review).

4. **Git as ground truth** — every hook output is version-controlled. You can diff any two sessions and see exactly what the system learned. "Is it actually getting smarter?" is a verifiable question, not a marketing claim.

## What other frameworks would need to do this

1. A structured context system (not just "dump everything into the prompt")
2. Hook infrastructure that fires reliably AFTER the session ends
3. Bounded async execution with deadline enforcement
4. Separation of "user-facing session" from "system-facing reflection"
5. A monotonic improvement guarantee (hooks cannot make things worse)

The hardest part is not the code. It is the design discipline: accepting that "idle time" between sessions is actually the most valuable time, and engineering the system to use it.

---

*Implementation: 13 post-session hooks (`backend/hooks/`), 25s shared deadline, fail-open design. Running in production for 300+ sessions.*
