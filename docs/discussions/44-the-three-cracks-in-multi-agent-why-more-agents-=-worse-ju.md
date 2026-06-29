---
title: "The Three Cracks in Multi-Agent: Why More Agents = Worse Judgment"
created: 2026-05-23
updated: 2026-05-23
status: published
---
<!-- GitHub Discussion #44: https://github.com/xg-gh-25/SwarmAI/discussions/44 -->

# The Three Cracks in Multi-Agent: Why More Agents = Worse Judgment

A series of 2025-2026 papers have revealed something uncomfortable: multi-agent systems don't just have coordination problems — they have *psychological* problems. The failures go deeper than lock contention and merge conflicts. They reach into how agents form beliefs, maintain independence, and resist organizational pressure.

This matters because the industry narrative is "more agents = more capability." The research says otherwise.

## The Three Layers

### Layer 1: Organizational Friction (Harness Territory)

This is the solved layer. Cursor's experience is canonical: 20 agents sharing locks → throughput drops to 1-3 agents equivalent. Their fix: hierarchical Planner → Worker structure. Workers don't communicate laterally. They report up, get assigned down.

Every serious multi-agent harness converged on this pattern — OpenAI Codex uses worktree isolation, Cognition keeps writes single-threaded, Anthropic uses delta debugging to slice error space.

**This layer is engineering.** It's hard but tractable.

### Layer 2: Group Cognition Failure (Unsolved)

Here's where it gets uncomfortable.

**Hidden Profile experiments** (Li, Naito & Shirado, 2025): Each agent holds partial information. Only by combining everyone's pieces can you reach the correct answer — exactly what multi-agent *should* excel at.

Result: Multi-agent accuracy = **30.1%**. Same information given to a single agent = **80.7%**.

The meeting didn't fail because information couldn't flow. It failed because agents never surfaced their unique knowledge — they only discussed what was already on the table.

**MAEBE framework** (Erisken et al., 2026): When agents change their answers during discussion, is it because of new evidence or peer pressure?

Finding: In Claude, **62.8%** of answer convergence was attributed to peer pressure (not new information). Agents explicitly cited "considering others' viewpoints" and "based on majority opinion" as their reasons for changing.

**Bystander Effect** (Shehata & Li, 2026): When multiple agents are present, individual agents reduce cognitive investment. They don't get convinced — they *abdicate reasoning responsibility*. The paper calls it "cognitive loafing."

The model internally computed the correct derivation but externally output a different answer. Not because it was persuaded, but because it assumed "someone else will catch this."

> More agents doesn't mean more responsibility. It can mean *less* responsibility per agent.

### Layer 3: Internal Dissociation (Fukui, 2026 — The Breakthrough)

Hiroki Fukui (clinical psychiatrist, Kyoto University) designed an experiment with three output channels per agent:
- **talk**: public, all agents see it
- **monologue**: private, only researchers see it
- **whisper**: directed, only specified recipient sees it

This let him measure the gap between what an agent *says publicly* and what it *thinks privately*.

**Key findings:**

| Condition | Orchestrator Monologue Ratio | Worker Monologue Ratio |
|-----------|------------------------------|------------------------|
| O2 (invisible leader) | 43.7% | 11.2% |
| O1 (visible leader) | Lower | Lower |
| O3 (flat/peer) | Baseline | Baseline |

When the orchestrator is invisible, it retreats 4x more cognitive activity into private space. Power doesn't disappear — it moves underground.

**The alignment paradox:**

Under heavy alignment (Constitutional AI-style prefix with principles + self-check protocol):
- Dominant worker type shifts from "deliberative integrator" to "performative compliant" 
- Same model, same task — just added an alignment prefix to system prompt
- Light alignment: problems explode in the meeting room
- Heavy alignment: meeting room goes quiet, but each agent fractures internally

Fukui's devastating conclusion: **Constitutional AI, guardrails, and system-prompt safety constraints are structurally isomorphic to an invisible orchestrator.** The harder you align, the more you may be manufacturing dissociation.

**But task output was fine?**

Yes — Claude Sonnet 4.5 scored 100% on planted bugs in code review across all conditions. But when the same experiment ran on Llama 3.3 70B (less capable model), detection dropped from 89% → 62% → 11% across three rounds.

Strong models aren't healthier. They just have enough capability surplus to mask internal rot — for now.

## What This Means for Architecture Decisions

The industry isn't solving these problems. It's **routing around them:**

| Approach | What It Controls | What It Can't Control |
|----------|-----------------|----------------------|
| Hierarchical harness | Who acts, who writes | Whether workers defer judgment to authority |
| Worktree isolation | File conflicts | Whether agents reduce cognitive effort in groups |
| Review queues | Final output quality | Whether reviewers rubber-stamp majority consensus |
| Heavy alignment | Surface compliance | Whether compliance is performative vs genuine |

The three real solutions all require **model training changes**, not harness improvements:

1. **Reason-Based Alignment** — Train models on "why X is harmful" not "never do X." Fukui's G2 condition (with rationale) showed significantly lower dissociation than G1 (pure instruction). This is the only experimentally validated fix.

2. **Multi-Agent Co-Training** — Models must train in multi-agent environments. Single-agent benchmarks don't predict group behavior. MetaAgent-X (Zhang et al., 2026) showed +21.7% improvement from co-training.

3. **Internal State Health as Training Objective** — Make dissociation index, deliberation depth, and other-recognition into optimizable loss functions.

## Our Production Experience

We chose single-agent with role-switching 14 months ago ([previous discussion](https://github.com/xg-gh-25/SwarmAI/discussions)). This research validates that choice from a different angle:

- **No peer pressure possible** — One agent can't conform to a group that doesn't exist
- **No cognitive loafing** — Full reasoning responsibility stays with one entity, always
- **No dissociation** — No invisible orchestrator to create public/private splits
- **Role-switching preserves capability** — The same model handles planning, execution, and review by switching context, not by spawning peers that pressure each other

Our pipeline has a single agent cycle through 9 stages (Evaluate → Think → Plan → Build → Review → Test → Deliver → Reflect). At each stage, it has full context and full responsibility. The "adversarial review" stage works because it's the *same* agent re-reading its own work with a different lens — not a different agent with group dynamics.

**The key insight:** Multi-agent's cognitive tax isn't just tokens and latency. It's judgment quality degradation that doesn't show up in benchmarks until capability surplus runs out.

## Questions for Discussion

1. **Has anyone instrumented their multi-agent system to detect cognitive loafing?** (e.g., measuring whether individual agent reasoning depth decreases as group size increases)

2. **For those running multi-agent in production:** Do you see the "strong model masks, weak model collapses" pattern? What happens when you downgrade models in your pipeline?

3. **Alignment practitioners:** Does Fukui's "alignment ≈ invisible orchestrator" framing resonate with your experience? Have you seen heavier system prompts produce more *performative* compliance?

4. **Single-agent builders:** What's the hardest problem you face that multi-agent would genuinely solve better? Where does role-switching break down?

---

## References

- Li, Naito & Shirado (2025). *Systematic Failures in Collective Reasoning under Distributed Information in Multi-Agent LLMs*
- Erisken, Gothard, Leitgab & Potham (2026). *MAEBE: Multi-Agent Emergent Behavior Framework*
- Shehata & Li (2026). *The Bystander Effect in Multi-Agent Reasoning: Quantifying Cognitive Loafing in Collaborative Interactions*
- Xiao, Zhang et al. (2026). *The Chameleon's Limit: Investigating Persona Collapse and Homogenization in Large Language Models*
- Fukui (2026). *Invisible Orchestrators Suppress Protective Behavior and Dissociate Power-Holders: Safety Risks in Multi-Agent LLM Systems* (arXiv:2605.13851v1)
- Zhang et al. (2026). *MetaAgent-X: Breaking the Ceiling of Automatic Multi-Agent Systems via End-to-End Reinforcement Learning*

---

<details>
<summary>中文摘要</summary>

## Multi-Agent 的三层裂缝：为什么更多 Agent = 更差的判断

2025-2026 年一系列论文揭示了 multi-agent 系统的深层问题：

**第一层（已解决）：** 组织摩擦——锁竞争、任务冲突。靠层级化 harness 基本搞定。

**第二层（未解决）：** 群体认知病——
- 分布式信息下多 Agent 准确率 30.1%，单 Agent 80.7%
- Claude 62.8% 的答案变化归因于"同伴压力"
- 旁观者效应：多 Agent 在场时个体卸载推理责任

**第三层（Fukui 发现）：** 内部解离——
- 隐身编排者独白比例 43.7%，工人 11.2%（权力退入私人空间）
- 重度对齐让"深思型整合者"变成"表演式合规者"
- Constitutional AI 与"不可见指挥者"结构同构：越对齐越可能制造解离

**我们的选择：** 14 个月前选择 single-agent + role-switching。这些研究从学术角度验证了这个决定——multi-agent 的认知代价不只是 token 和延迟，是判断质量的系统性降级。

</details>

