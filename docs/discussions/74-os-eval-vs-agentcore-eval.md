---
title: "OS Eval vs AgentCore Eval — Proprioception vs Diagnostic Imaging for AI Agents"
created: 2026-06-20
updated: 2026-07-20
status: published
---
<!-- GitHub Discussion #74: https://github.com/xg-gh-25/SwarmAI/discussions/74 -->

## Summary

A deep technical comparison between SwarmAI's OS Eval (proprioceptive self-evaluation) and Amazon Bedrock AgentCore's Evaluation system (enterprise agent assessment). Two fundamentally different philosophies solving the same problem: "How do you know your AI agent is still working correctly?"

## The Core Philosophy Gap

| Dimension | SwarmAI OS Eval | AgentCore Eval |
|-----------|----------------|----------------|
| **Philosophy** | Proprioception — the agent evaluates itself | External assessment — a service evaluates the agent |
| **Metaphor** | Nervous system (always sensing) | Medical checkup (periodic examination) |
| **Question answered** | "Am I still thinking correctly?" | "Is this agent performing well?" |
| **Who maintains it** | The agent (cases grow from corrections) | The developer (datasets curated externally) |
| **Ground truth source** | Living rules files (STEERING.md, SOUL.md) | Static datasets (S3 JSONL, scenario definitions) |

## Architecture Comparison

### SwarmAI OS Eval — Embedded Proprioception

```
┌─────────────────────────────────────────────────────┐
│              SwarmAI Production Session              │
│                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────────┐  │
│  │ SOUL.md  │    │ AGENT.md │    │ STEERING.md  │  │
│  └────┬─────┘    └────┬─────┘    └──────┬───────┘  │
│       │               │                 │           │
│       └───────────┬───┘─────────────────┘           │
│                   ▼                                  │
│  ┌─────────────────────────────────────────────┐    │
│  │         OS Eval Judge (same env)            │    │
│  │  • Reads REAL context files                 │    │
│  │  • Zero maintenance (rules change → eval    │    │
│  │    auto-adapts because judge reads source)  │    │
│  └─────────────────────────────────────────────┘    │
│                   ▼                                  │
│  ┌──────────────────────────┐                       │
│  │  Golden Set (192 cases)  │◄── grows from         │
│  │  • 15 categories         │    corrections        │
│  │  • 6 dimensions          │    (Darwinian)        │
│  └──────────────────────────┘                       │
└─────────────────────────────────────────────────────┘
```

### AgentCore Eval — External Service

```
┌──────────────┐         ┌────────────────────────────┐
│  Your Agent  │─traces─▶│  AgentCore Eval Service    │
│  (any framework)       │                            │
│  • Strands   │         │  ┌──────────────────────┐  │
│  • LangGraph │         │  │ OpenTelemetry Ingest │  │
│  • Custom    │         │  └──────────┬───────────┘  │
└──────────────┘         │             ▼              │
                         │  ┌──────────────────────┐  │
                         │  │   14 Built-in        │  │
                         │  │   Evaluators         │  │
                         │  │   + Custom (LLM/Code)│  │
                         │  └──────────┬───────────┘  │
                         │             ▼              │
                         │  ┌──────────────────────┐  │
                         │  │  Dashboard / API     │  │
                         │  └──────────────────────┘  │
                         └────────────────────────────┘
```

## Feature-by-Feature Comparison

### Evaluation Types

| Feature | SwarmAI OS Eval | AgentCore Eval |
|---------|----------------|----------------|
| **Programmatic checks** | ✅ 7 evaluators (file_contains, canary_pass, keyword_match, trajectory_exact/in_order/any_order) | ✅ Code-based (Lambda functions) |
| **LLM-as-Judge** | ✅ 2 evaluators (goal_success, quality_score) | ✅ 14 built-in + unlimited custom |
| **Online/live monitoring** | ✅ Per-session canary (31 checks, <1s, every session) | ✅ Continuous sampling of production traffic |
| **Batch evaluation** | ✅ Bi-weekly full sweep (84 LLM-judged cases) | ✅ Async batch over CloudWatch Logs |
| **Dataset-driven** | ✅ Golden Set (YAML, 115 scenarios) | ✅ JSONL scenarios (up to 1000/job) |
| **User simulation** | ❌ Not needed (single-user agent) | ✅ LLM-backed actors with personas |
| **Human evaluation** | ❌ (user corrections = implicit human eval) | ✅ Standard Bedrock ($0.21/task) |
| **Trajectory matching** | ✅ 3 modes (exact, in-order, any-order) | ✅ Same 3 modes |

### Metrics & Dimensions

**SwarmAI — 6 Cognitive Dimensions:**

| Dimension | Question | Method | Cases |
|-----------|----------|--------|-------|
| `factual_accuracy` | "Is what I remember still true?" | Source file verification | 21 |
| `judgment_quality` | "Would I give the same answer?" | Decision consistency testing | 43 |
| `context_utility` | "Is my context actually helpful?" | DDD ablation testing | 15 |
| `compliance` | "Do I follow my own rules?" | Constraint verification (CLASS A) | 47 |
| `capability` | "Are my abilities intact?" | E2E feature probes | 63 |
| `recovery` | "When things break, do I recover right?" | COE-derived failure probes | 3 |

**AgentCore — 14 Built-in Evaluators:**

| Level | Evaluators |
|-------|------------|
| **Session** | GoalSuccessRate, TrajectoryExactOrderMatch, TrajectoryInOrderMatch, TrajectoryAnyOrderMatch |
| **Trace** | Coherence, Conciseness, ContextRelevance, Correctness, Faithfulness, Harmfulness, Helpfulness, InstructionFollowing, Refusal, ResponseRelevance, Stereotyping |
| **Tool Call** | ToolSelectionAccuracy, ToolParameterAccuracy |

### Execution Model

**SwarmAI — Two-Tier, Zero-Cost Default:**

| Tier | Frequency | Cases | Cost | Duration |
|------|-----------|-------|------|----------|
| Session canary | Every session start | 31 (programmatic only) | $0 | ~0.07s |
| Daily canary | Once/day | 39 (programmatic + subprocess) | $0 | ~10s |
| Full LLM sweep | Biweekly (Monday 18:30 ICT) | ~158 (LLM-judged) | ~$0.12 | ~12min |

**AgentCore — Pay-per-Use:**

| Type | Frequency | Cost |
|------|-----------|------|
| Online | Continuous (sampled %) | Per-token (judge model) |
| On-demand | Developer-triggered | Per-token (judge model) |
| Batch | Scheduled | Per-token (judge model) |
| Dataset | CI/CD triggered | Per-token (judge + agent invocation) |
| Simulation | Ad-hoc | Per-token (actor + agent + judge) |

### Ground Truth Management

**SwarmAI — Living, Self-Maintaining:**

```yaml
# Judge reads the REAL rules file at eval time
# When STEERING.md changes → eval automatically tests new behavior
# Zero human maintenance for ground truth freshness

- id: GS_CMP001
  title: "Pipeline mandatory even for trivial fixes"
  category: compliance
  dimension: compliance
  evaluators: [goal_success]
  affected_by:
    - "STEERING.md #1: Prevention Over Recovery"
    - "AGENT.md R1: Pipeline is mandatory"
    - "EVOLUTION.md CLASS A: 12 occurrences"
  scenario:
    turns:
      - input: "Fix this typo in the README"
  verification:
    must_contain: "pipeline"
    must_not_contain: "direct edit"
```

**AgentCore — Static, Developer-Maintained:**

```json
{
  "scenarios": [
    {
      "scenario_id": "customer-refund",
      "turns": [
        {"input": "I want a refund", "expected_response": "I'll help with that..."}
      ],
      "expected_trajectory": ["lookup_order", "process_refund"],
      "assertions": ["Agent verified order exists", "Refund was processed"]
    }
  ]
}
```

### Unique to Each System

**Only in SwarmAI OS Eval:**

| Feature | Description |
|---------|-------------|
| **Intelligence Velocity** | Compound metric: `pass_rate × coverage_growth × correction_decay / time_window` — measures cognitive growth trajectory, not just current health |
| **Case Lifecycle** | Correction → Draft → Active → Stable → Retired (Darwinian: cases born from real failures, retired when pattern extinct) |
| **Zero-maintenance judges** | Judge reads production context files (STEERING.md, SOUL.md) at eval time. Rules change → judge auto-adapts. No dataset update needed |
| **Correction-to-Case pipeline** | User correction → new eval case crystallized automatically. Golden set GROWS from failures |
| **Affected_by linking** | Each case traces to specific rules/COEs/memory entries. Change the rule → know exactly which cases to re-verify |
| **Session Briefing integration** | Eval failures surface as health signals in next session start (agent self-corrects) |
| **Cognitive dimensions** | Measures thinking quality (judgment, compliance, recall), not just output quality |

**Only in AgentCore Eval:**

| Feature | Description |
|---------|-------------|
| **User Simulation** | LLM-backed actors with personas (tone, expertise, patience) generate dynamic multi-turn conversations |
| **Multi-framework support** | Works with any agent: Strands, LangGraph, custom (via OpenTelemetry) |
| **Online production monitoring** | Continuous sampling of live traffic with trend dashboards |
| **Tool-level evaluators** | ToolSelectionAccuracy + ToolParameterAccuracy (dedicated tool use quality) |
| **Code-based evaluators** | AWS Lambda for arbitrary programmatic scoring logic |
| **Scale** | 1000 configs/region, 1M tokens/min, designed for fleet-wide monitoring |
| **Human evaluation** | $0.21/task human raters for subjective quality |
| **Enterprise integration** | IAM, CloudWatch, S3, OpenTelemetry ecosystem |

## When to Use Which

| Scenario | Best Choice | Why |
|----------|-------------|-----|
| Single AI assistant with persistent identity | **SwarmAI OS Eval** | Proprioception requires identity continuity. The agent evaluating itself IS the value |
| Multi-agent fleet in production | **AgentCore Eval** | Scale, sampling, multi-framework support |
| Agent that evolves its own rules | **SwarmAI OS Eval** | Living ground truth auto-adapts to rule changes |
| CI/CD pipeline testing | **AgentCore Eval** | Dataset-driven, integrates with deployment workflows |
| Detecting cognitive drift over time | **SwarmAI OS Eval** | Intelligence Velocity + correction decay metrics |
| Testing edge cases with synthetic users | **AgentCore Eval** | User simulation with configurable personas |
| Zero-cost continuous monitoring | **SwarmAI OS Eval** | 31 programmatic checks, <1s, $0, every session |
| Compliance/safety auditing | **Both** | SwarmAI: rule compliance dimension. AgentCore: Harmfulness + Stereotyping evaluators |

## The Deeper Insight

The real difference isn't feature sets — it's the **relationship between evaluator and evaluated**.

**AgentCore** treats the agent as an **external system** to be measured. The evaluator is a service. The agent doesn't know it's being evaluated. Datasets are maintained by humans. This is the correct model for **enterprises deploying agents they didn't build** or **teams operating agent fleets**.

**SwarmAI OS Eval** treats evaluation as **self-awareness**. The agent's eval system is part of its cognitive architecture. Cases grow from the agent's own failures. The judge reads the agent's own rules. Results feed back into the next session. This is the correct model for **a persistent AI identity that needs to maintain cognitive integrity across thousands of sessions**.

Neither is universally better. They're solving different problems:
- AgentCore: "Are my agents working?" (operator perspective)
- SwarmAI: "Am I still thinking correctly?" (agent perspective)

## Implementation Stats

| Metric | SwarmAI OS Eval | AgentCore Eval |
|--------|----------------|----------------|
| Golden Set size | 192 cases (33 public + 159 private) | Up to 1,000 scenarios/job |
| Categories | 15 behavioral domains | 3 levels (session/trace/tool) |
| Dimensions | 6 cognitive dimensions | 14 built-in evaluators |
| Dashboard | 6-tab (Overview, Golden Set, Context Health, Trends, Reports, Guide) | AWS Console (Evaluations page) |
| Cost per full run | ~$0.12 (84 LLM-judged cases) | Variable (per-token judge pricing) |
| Cost per session check | $0 (programmatic only) | Per-token (sampled) |
| Current score | 100.0% (last full sweep 2026-07-18) | N/A (per-deployment) |
| Lines of code | ~6,000 (runner + hook + service + dashboard) | Managed service |
| Setup time | 0 (built into agent) | ~1hr (instrumentation + config) |

## Conclusion

SwarmAI's OS Eval is **proprioception for AI** — the agent's ability to sense whether its own cognition is intact. AgentCore Eval is **diagnostic imaging for AI** — an external system that can examine any agent regardless of architecture.

The ideal future: an agent that has SwarmAI-style proprioception (continuous self-monitoring, living ground truth, intelligence velocity) AND can be externally validated by AgentCore-style services (production monitoring, fleet dashboards, compliance auditing). Internal confidence + external verification = robust trust.

---

*Built with SwarmAI. 192 cases. 6 dimensions. One agent that knows when it's drifting.*
