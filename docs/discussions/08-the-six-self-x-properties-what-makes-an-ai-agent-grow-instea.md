# The Six Self-X Properties — what makes an AI agent grow instead of reset

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/8) | Category: General | Published: 2026-05-18

---

> "AI 之间的差距不是模型。是模型上面那一层。"

## The Six Self-X Design Properties

Most AI agents are stateless functions with a chat interface. Same model + same prompt + different day = same capability. Nothing compounds.

A **harness** is the engineering layer ABOVE the model that gives it continuity. Not a framework. Not a wrapper. A set of design decisions about persistence.

We've identified 6 properties that, when designed correctly, turn a stateless model into something that grows:

### 1. Self-Context — "Who maintains what?"

The design problem isn't "how much context to inject" — it's **ownership**.

```
System-owned files → Overwritten every startup. Correctness by code.
User-owned files   → Copy-only-if-missing. Respect preferences.
Agent-owned files  → AI-exclusive. Only AI knows what's useful cross-session.
```

One `CLAUDE.md` with everything = simple, but 3 months later nobody knows what's stale. Separation of ownership = each party maintains what they're best at maintaining.

### 2. Self-Memory — "What earns the right to persist?"

Memory is not storage. It's **progressive distillation**.

```
Raw (30-day TTL) → Curated (agent judges value) → Structured (research-grade) → Authoritative (judgment basis)
```

The interesting design question: under what conditions does an observation earn the right to become a rule?

**Memory sovereignty** is the strategic extension — never delegate memory to a platform. Whoever controls schema, lifecycle, and pruning controls the moat.

### 3. Self-Evolution — "Errors that structurally cannot recur"

Evolution is engineering, not training.

- Training: "AI learned not to do X" (behavioral, fragile, loses with context)
- Engineering: "The code path for X was structurally eliminated" (architectural, permanent)

```
Correction → Root Cause → Pattern → Rule → Class eliminated
```

After 25 corrections: not "remembers to avoid 25 things" but "25 categories of bugs are structurally impossible."

### 4. Self-Feedback — "Never cold-start, never leave empty-handed"

Every session has a symmetric lifecycle:

```
Pre-session:  Load context → Health check → Briefing → Project detection
Post-session: Extract insights → Propose knowledge → Update evolution → Refresh index
```

The agent never starts from zero. Never finishes without capturing value. Hooks create temporal symmetry — like the autonomic nervous system (runs continuously, user-invisible).

### 5. Self-Healing — "Health score changes behavior, not generates reports"

Most "health checks" are dashboards for humans. Different design:

```
Score LOW  → AI automatically reduces autonomy, asks more questions, flags uncertainty
Score HIGH → AI acts autonomously, doesn't interrupt
```

Not "human tells AI what to trust" — the system earns trust through demonstrated reliability.

### 6. Self-Monitoring — "Be your own first reviewer before anyone sees"

```
Pre-implementation:  Problem → Scenarios → Simplest approach → What breaks?
Post-implementation: Switch perspective → Re-read as reviewer → Capture lessons
Adversarial review:  Spawn independent sub-agent (fresh context, no builder bias)
```

Same person as builder AND reviewer has structural blind spots. Self-monitoring uses **different contexts** to examine the same output.

## The Meta-Principle

> **Prevention > Recovery**

If you need a watchdog, the design already failed.
If you need "remember not to do X", X is still possible.
If you need a human to fix it, the system isn't self-sufficient.

Make correct behavior the ONLY possible behavior.

## Questions

- Which of the 6 properties is hardest to implement? (Our answer: Self-Healing, because it requires quantifying trust)
- Is there a 7th property missing? (Candidates: Self-Explanation, Self-Limitation?)
- Can these properties be retrofitted onto existing agents, or do they require ground-up design?

---

*Full poster with visual design: [Agent Harness d5](https://xg-gh-25.github.io/swarm-content/content/posters/2026-05-16-agent-harness-d5.png)*

![Agent Harness](https://raw.githubusercontent.com/xg-gh-25/swarm-content/main/content/posters/2026-05-16-agent-harness-d5.png)
