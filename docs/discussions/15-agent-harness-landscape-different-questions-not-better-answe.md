# Agent Harness Landscape — different questions, not better answers

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/15) | Category: General | Published: 2026-05-18

---

Every agent framework optimizes for a different bottleneck. Knowing which bottleneck you have determines which framework is right — not feature comparison tables.

## The five bottlenecks (one per framework)

| Bottleneck | Framework | Their question |
|-----------|-----------|----------------|
| Execution speed | OpenClaw | "How do I go from prompt to result fastest?" |
| Skill optimization cost | Hermes (GEPA) | "How do I make one skill better for $2?" |
| Task decomposition | DeerFlow | "How do I break a complex task into coordinated subtasks?" |
| Production reliability | Claude Agent SDK | "How do I run agents without surprises?" |
| **Compound learning** | **SwarmAI** | "How does the system get measurably smarter over time?" |

These are not competing answers to the same question. They are answers to **different questions**. If your bottleneck is task decomposition, DeerFlow is correct and SwarmAI is overkill. If your bottleneck is compound learning, SwarmAI is correct and DeerFlow is irrelevant.

## What "compound learning" means concretely

**The structural claim:** Session N+1 should be strictly better than Session N — not because the model improved, but because the *harness* learned.

How we verify this is not marketing:

```bash
# Evidence type 1: Corrections that prevent bug classes
$ grep -c "Status: active" backend/context/EVOLUTION.md
25  # 25 structural corrections, each closing an entire class of failure

# Evidence type 2: Quality convergence
$ git log --all --oneline --grep="P0\|Sev-1" | wc -l
43  # Tracked incidents — rate declining as corrections compound

# Evidence type 3: Knowledge that grew from work (not manual writing)
$ find ~/.swarm-ai/SwarmWS/Projects -name "*.md" -path "*/IMPROVEMENT*" -exec wc -l {} +
# DDD docs filled by post-session hooks, not by human typing
```

## The mechanism (why other harnesses don't do this)

Three structural requirements that compound learning demands:

### 1. Post-session reflection hooks

```
Session ends →
  ├→ evolution_trigger (detect what went wrong + pattern match)
  ├→ distillation (promote raw observations → curated memory)
  ├→ ddd_cultivation (grow domain docs from normal work)
  ├→ skill_metrics (score and evict unused skills)
  └→ 9 more hooks, 25s bounded deadline, fail-open
```

Most frameworks stop at "session ended, save history." We start there.

### 2. Knowledge as network (DDD across engines)

```
PRODUCT.md ──→ Pipeline (should we build this?)
TECH.md    ──→ Pipeline (how should we build this?)
IMPROVE.md ──→ Pipeline (what failed before?)
     │         Pollinate (what resonated with audience?)
     │         Community Engine (which topics get engagement?)
     └──→ All engines read the SAME knowledge
           → Coding lessons improve content accuracy
           → Content feedback improves coding priorities
```

Single-engine frameworks cannot get this cross-pollination. Multi-agent frameworks have it in theory but knowledge stays siloed per agent.

### 3. Git-verifiable improvement (not self-reported)

The hardest design choice: **make every claim auditable.**

- Every correction links to a commit hash
- Every P0 links to a release tag + fix commit
- Every hook output is version-controlled
- "Is it getting smarter?" → `git log --oneline -- .context/EVOLUTION.md`

This eliminates the most common failure mode of "self-improving" systems: claiming improvement without evidence.

## Where SwarmAI is NOT the answer

Being honest about limitations:

| If you need... | Don't use SwarmAI | Use instead |
|----------------|-------------------|-------------|
| Quick prototype today | Learning curve too steep | OpenClaw |
| Multi-user team | Single-person design | OpenClaw |
| Budget < $100/mo | Adversarial review doubles token cost | Hermes |
| Complex multi-agent orchestration | We chose multi-skill over multi-agent | DeerFlow |
| Something stable (no breaking changes) | Active experiment, shipping daily | Claude SDK |

These are not weaknesses we are working to fix. They are **design choices** — optimizing for compound learning means NOT optimizing for quick-start, team-scale, or cost-efficiency.

## The thesis we are testing

> One builder + AI + self-evolving harness = team-scale output.
>
> The evidence is the git history: 1,500+ commits in 85 days, 85K LOC production code, 220 test files, 12 autonomous engines — all by one human directing AI.

If this thesis is wrong, the project will show it (P0 rate stops converging, knowledge stops compounding, engines start conflicting). That is also tracked.

---

*Production data: 300+ sessions, 25 corrections, 12 engines, 13 post-session hooks. All verifiable via [AI_CONTEXT.md](https://github.com/xg-gh-25/SwarmAI/blob/main/AI_CONTEXT.md)*
