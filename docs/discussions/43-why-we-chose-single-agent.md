# Why We Chose Single-Agent (And Multi-Agent Frameworks Are Proving Us Right)

> GitHub Discussion: https://github.com/xg-gh-25/SwarmAI/discussions/43

In April 2026, we made a deliberate architectural decision: **single-agent with role-switching over multi-agent orchestration.**

14 months and 170K lines of code later, the two largest multi-agent frameworks in the world are converging toward the same conclusion we started from.

---

## The Thesis

> "Division of labor is a compromise for limited human cognitive bandwidth, not an optimal design. A single agent switching roles (EVALUATOR → BUILDER → REVIEWER) has zero context transfer cost; multi-agent coordination introduces handoff overhead, state sync, and context loss."

Every industry trend — full-stack engineers, DevOps, cross-functional pods — eliminates handoffs. Multi-agent frameworks re-introduce them artificially.

---

## What We Actually Built

SwarmAI runs a 9-stage autonomous pipeline where the **same agent** switches roles:

```
EVALUATE (judge) → THINK (researcher) → PLAN (architect) → BUILD (developer)
→ REVIEW (critic) → TEST (QA) → ADVERSARIAL (attacker) → DELIVER (packager) → REFLECT (learner)
```

One context window. Zero serialization between stages. The agent carries full project knowledge through every role — it doesn't need "handoff documents" because it already knows everything.

For the ADVERSARIAL stage, we spawn a fresh sub-agent specifically for isolation (a reviewer who hasn't built the code can't rationalize its flaws). But this is a **disposable reviewer**, not a persistent peer — it has one job, delivers findings, and terminates.

---

## What The Industry Is Learning

**CrewAI (52K stars, 2B workflow executions)** evolved from pure multi-agent "Crews" to **Flows** — deterministic orchestration with agents embedded only where judgment is needed:

> "Successful teams separated deterministic from probabilistic. Agents only where judgment is needed."
> — CrewAI, "Lessons from 2 Billion Agentic Workflows"

Their most successful customer pattern (DocuSign: 5 "agents" in a deterministic Flow with fixed sequencing) is functionally identical to a single agent switching roles per stage.

They also report:
- **14x code reduction** moving from graph-based multi-agent to simpler Flows
- "Too many abstraction layers stacked on top of each other... debugging nightmares"
- Top bug: agents **hallucinate tool execution** instead of actually calling tools — a failure mode that compounds with more agents

**DeerFlow (69K stars, ByteDance)** added `subagent_enabled: false` as a config option and offers "flash mode" (no sub-agents) for tasks that don't need the overhead. Their SWE-bench scores are actually **lower** via multi-agent than direct model calls — the orchestration overhead eats the turn budget before the actual work happens.

---

## The Overhead Problem (Quantified)

For a typical 3-agent crew:

| Cost | Single-Agent | 3-Agent Crew |
|------|-------------|--------------|
| System prompt tokens | 1x (~500 tok) | 3x + manager (~2000 tok) |
| Context per handoff | 0 (same window) | 1000-3000 tok serialized |
| Failure surface | 1 agent can hallucinate | 3 agents can hallucinate + compound |
| Retry cost | 1 reasoning loop | 3 reasoning loops + coordination |
| Debugging | Single trace | N traces + inter-agent messages |

**Net: 3-10x more tokens for equivalent output.** And the output isn't better — it's often worse because context is lost in serialization.

---

## When Multi-Agent Actually Makes Sense

We're not dogmatic. Multi-agent has genuine value for:

1. **Parallel execution on truly independent tasks** — but you can achieve this with concurrent sessions
2. **Heterogeneous model routing** — cheap model for classification, expensive for composition (but this is model routing, not agent architecture)
3. **Audit/compliance** — named agent = named responsibility (but named pipeline stages achieve the same)
4. **Enterprise sales** — "AI team" maps to how executives think about automation

---

## The Real Lesson

The question isn't "single-agent vs multi-agent." It's:

**"Where does the intelligence boundary live?"**

If your agents share the same model, the same tools, and the same context window — you don't have multiple agents. You have one agent with extra steps.

Multi-agent makes architectural sense when agents have genuinely different:
- Models (GPT-4 for reasoning, Claude for code, Gemini for vision)
- Tool access (one has internet, another has production DB)
- Trust boundaries (user-facing vs internal)
- Lifecycle (ephemeral vs persistent)

For everything else — and that's 90% of use cases — a single capable agent with role-switching is simpler, cheaper, more reliable, and easier to debug.

---

## Our Architecture in Practice

```
Single agent, 9 roles, same context:

┌─────────────────────────────────────────────────────┐
│  Agent Context (1M tokens, persistent across roles)  │
│                                                       │
│  [EVALUATE]→[THINK]→[PLAN]→[BUILD]→[REVIEW]→[TEST]  │
│       ↓                                     ↓        │
│  DDD Knowledge          Multi-Specialist Adversarial │
│  (what we learned)      (disposable fresh reviewers) │
└─────────────────────────────────────────────────────┘

vs. Multi-agent (what we avoided):

┌──────┐   serialize   ┌──────┐   serialize   ┌──────┐
│Agent1│ ───────────→ │Agent2│ ───────────→ │Agent3│
│200K  │   ~3K lost    │200K  │   ~3K lost    │200K  │
└──────┘               └──────┘               └──────┘
         Total: 600K tokens, 6K context lost
```

---

## Discussion

1. **Has anyone migrated FROM multi-agent TO single-agent?** What triggered the switch? What improved?
2. **Where did multi-agent genuinely outperform?** We're curious about cases where the overhead was worth it.
3. **How do you handle the "adversarial review" problem?** (A builder reviewing their own code can't see their own blind spots — this is the one place we use a separate agent.)

---

我们在 2026 年 4 月做了一个刻意的架构决策：单 agent + 角色切换 > 多 agent 编排。14 个月后，两个最大的多 agent 框架（CrewAI 52K⭐ 和 DeerFlow 69K⭐）正在向同一个结论收敛。CrewAI 从纯多 agent "Crews" 演化到确定性 "Flows"，代码减少 14 倍。他们自己的经验：agent 只用在需要判断力的地方，其余用确定性控制流。这正是我们从第一天就在做的事。

---

*Built with [SwarmAI](https://github.com/xg-gh-25/SwarmAI) — one builder + AI operating at team scale.*
