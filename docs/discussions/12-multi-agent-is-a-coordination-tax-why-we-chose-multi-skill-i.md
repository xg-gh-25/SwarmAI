# Multi-Agent is a coordination tax — why we chose Multi-Skill instead

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/12) | Category: General | Published: 2026-05-18

---

> "Coordination is a tax on limited cognition. Don't pay taxes you don't owe." — KD29

## The Industry Consensus (and why we disagree)

The AI agent industry has converged on **multi-agent** as the architecture for complex tasks:

- **CrewAI:** Define agents with roles (researcher, writer, reviewer), orchestrate them
- **LangGraph:** Build DAGs of agent nodes passing messages
- **AutoGen:** Multiple agents conversing in group chat
- **gstack (Garry Tan):** 23 "specialists" — CEO, Designer, Eng Manager, QA...
- **OpenClaw (Claude Code):** Spawns sub-agents via Task tool

The pitch: "Division of labor works for humans, so it works for AI."

**We chose the opposite.** One agent, multiple skills, role-switching within a single context. Here's why.

## The Coordination Tax

Every time you split work across agents, you pay:

| Tax | Cost | Example |
|-----|------|---------|
| **Context transfer** | Lost nuance | Agent A knows WHY the user wants X. Agent B only sees "do X." |
| **State sync** | Race conditions | Agent A changes a file. Agent B reads the old version. |
| **Handoff overhead** | Time + tokens | Summarizing context for the next agent = re-processing work already done. |
| **Error propagation** | Cascading failures | Agent A makes a wrong assumption. Agents B, C, D build on it. |
| **Coordination protocol** | Complexity | Who goes first? Who resolves conflicts? Who owns the final output? |

These aren't implementation details you can engineer away. They're **structural properties of distributed systems.** The CAP theorem applies to agents too.

## What Multi-Agent Actually Solves

Multi-agent genuinely helps when:
1. **Parallel execution** — tasks with zero dependency can run simultaneously
2. **Isolation** — one agent's failure shouldn't corrupt another's state
3. **Resource limits** — one context window isn't enough for the full problem

For everything else, it's overhead disguised as architecture.

## Our Alternative: Multi-Skill Orchestration

```
ONE agent + MANY skills + ROLE-SWITCHING within pipeline stages

EVALUATE (judgment role) → THINK (researcher role) → PLAN (architect role) →
BUILD (engineer role) → REVIEW (adversarial reviewer role) → TEST (QA role) →
DELIVER (release engineer role) → REFLECT (retrospective role)
```

Same "virtual team" as gstack's 23 specialists. But:
- **Zero context transfer cost** — the agent switching from "builder" to "reviewer" already knows everything
- **Zero state sync** — one process, one filesystem view, one git state
- **Zero handoff** — no summarization needed, full conversation history preserved
- **Adversarial review still works** — we spawn sub-agents ONLY for review (fresh context = genuine second opinion)

## When We DO Use Multiple Agents

The exception proves the rule. We use sub-agents for exactly ONE thing: **adversarial review.**

Why? Because the builder's context IS the blind spot. A reviewer who read the same conversation has the same blind spots. A fresh-context reviewer catches what self-review structurally cannot.

But this is "multiple contexts for review" — not "multiple agents coordinating on one task."

## The gstack Observation

Garry Tan's gstack is interesting because it looks like multi-agent but isn't:

```
/plan-ceo-review    → Claude in "CEO" role, same context
/review             → Claude in "reviewer" role, same context
/qa                 → Claude in "QA" role, same context
/ship               → Claude in "release eng" role, same context
```

It's **role-based prompting within one agent.** The "23 specialists" are 23 system prompts, not 23 processes. This is exactly our architecture — skills = roles, pipeline stages = role transitions, one agent = one context.

The branding says "virtual team." The architecture says "single agent, multiple hats."

## The Deeper Principle

> Division of labor is a compromise for limited human cognitive bandwidth, not an optimal design.

Humans need teams because one person can't hold all context. AI agents with 1M context windows DON'T have this limitation. Splitting them up re-introduces the exact problem (limited context) that the technology solved.

Every industry trend eliminates handoffs:
- Full-stack > frontend + backend
- DevOps > dev + ops
- Cross-functional pods > siloed departments

Multi-agent frameworks go the opposite direction. They re-introduce handoffs artificially.

## When Multi-Agent WILL Win (future)

Our position changes if:
- Models get shared real-time memory (one agent writes, another reads instantly)
- Context windows shrink (forcing distribution)
- Tool-use becomes blocking (parallel execution matters more)

Until then: one agent, many skills, role-switching. Pay for coordination only where it's structurally unavoidable (adversarial review).

## Questions

- Is the "one agent, many roles" approach actually scalable? What breaks at 100 skills?
- Does gstack's success (98K stars) validate single-agent-multi-role, or is it just good marketing?
- When CrewAI/LangGraph users say "multi-agent works for us" — are they solving a real coordination problem, or cargo-culting human org design?
- Is there a task complexity threshold where multi-agent genuinely outperforms? (Our hypothesis: only when tasks require >1M tokens of context total.)

---

*Our architecture: 61 skills, 1 agent, 8 pipeline stages, zero coordination overhead. [SwarmAI](https://github.com/xg-gh-25/SwarmAI)*
