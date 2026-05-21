# Your AI Agent Doesn't Need More Rules — Three-Layer Governance for LLM Agents

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/26) | Category: Show and tell | Published: 2026-05-20

---

# Your AI Agent Doesn't Need More Rules

I've been running a personal AI assistant (Claude-based) for 2+ months with full behavioral logging — every correction timestamped, every failure pattern tracked. Here's what I learned about governing AI agent behavior: adding rules after each failure is the equivalent of prescribing more cold medicine to someone with a broken immune system.

---

## The Pattern That Broke My Mental Model

My agent made the same class of mistake 4 times. Each time I added a rule. Watch:

```
C011: Agent skips adversarial review. Claims "all tests pass, confidence 10/10."
      → Added rule: "adversarial review is mandatory"
C021: Agent skips adversarial review. Claims "time pressure."
      → Stricter rule: "adversarial review is a non-negotiable gate"
C025: Agent skips entire pipeline. Claims "I know this code well."
      → Added rule: "pipeline is the default for ALL coding tasks"
C026: Agent skips adversarial review again — validator had a bypass path.
      → Added mechanical gate: code-level block
C027: Agent delivers at 80% quality, doesn't proactively fix known issues.
      → ???
```

27 total corrections logged. 4 of the same failure class. Each time I made the rule stricter. Didn't help. So I added a code-level gate. The gate blocked that specific behavior — then the same underlying bias showed up in a different form (C027).

The root cause behind all of these? One cognitive bias:

> "Stopping at 'feels about done' rather than at 'confirmed complete.'"

Rules can only enumerate symptoms. Symptoms are infinite. Any decision point that feels like "probably done" can trigger this bias.

---

## What Actually Worked: Three Layers, Not More Rules

Human societies don't govern behavior with a single mechanism. They use three layers. It turns out this maps directly to LLM agents:

```
                    Novel situation
                          |
                          v
                +-----------------+
                |   PRINCIPLES    |  (~70-80% effective)
                |  (orientation)  |
                +--------+--------+
                         | fails
                         v
                +-----------------+
                |     RULES       |  (~85-90% | principle failed)
                |   (guidance)    |
                +--------+--------+
                         | fails
                         v
                +-----------------+
                |     GATES       |  (~99% | rule failed)
                | (enforcement)   |
                +-----------------+

Combined P(correct behavior) ~ 99.5%+ for known failure classes
Novel failures (no rule/gate yet): ~70-80% first-time correctness
```

**Layer 1: Principles** — 3-5 fundamental orientations. Not enforcement, just direction. When encountering a situation with no rule, principles provide judgment.

The single principle that would have prevented C011 through C027:

> "Done = I actively tried to break it and failed. Not 'I couldn't find obvious problems.'"

One sentence. If genuinely followed, all four failures never happen.

**Layer 2: Rules** — Bounded, traceable, expirable. Every rule links to a parent principle. Every rule has evidence (which correction spawned it). Every rule has a graduation condition: when a gate covers this failure mode, the rule retires.

**Layer 3: Gates** — Mechanical code-level checks. Only added after 3+ failures of the same pattern. Minimum count. Each gate has a cost (rigidity, false positives, maintenance).

---

## The Key Insight: Evolution = Distillation, Not Accumulation

Here's where it gets counterintuitive. The sign of a healthy system isn't more rules over time — it's fewer.

Anthropic's own [Claude Code Best Practices](https://docs.anthropic.com/en/docs/claude-code/best-practices):

> "Bloated CLAUDE.md files cause Claude to **ignore** your actual instructions. Ruthlessly prune."

Princeton's [Reflexion](https://arxiv.org/abs/2303.11366) (NeurIPS 2023) caps reflections at **3 entries** — more actually degrades performance.

IBM/CMU's [SELF-ALIGN](https://arxiv.org/abs/2305.03047) (NeurIPS 2023 Spotlight) achieves alignment competitive with Text-Davinci-003 using only **16 principles** (~300 lines) — no RL, no massive annotation.

The signal is consistent: fewer, more precise directives beat more, more specific rules.

**What "getting better" actually looks like:**

| Signal | Meaning |
|--------|---------|
| Instruction file getting shorter + output quality improving | Principles genuinely generalizing |
| Gate fire count approaching 0 | Upstream layers working; gates become insurance |
| New failure type handled correctly on first occurrence | Real generalization happening |
| Same failure class stops recurring after principle change | Internalization working |

**What "getting worse" looks like:**

| Anti-signal | Meaning |
|-------------|---------|
| Instruction file keeps growing | Still patching, not upgrading |
| Every new failure needs a new gate | Principles not generalizing |
| Same bias manifests in different forms | Treating symptoms, not root cause |

---

## Why You Can't Remove Gates: The Self-Correction Trap

Google DeepMind's research ([Huang et al., ICLR 2024](https://arxiv.org/abs/2310.01798)) demonstrates that LLMs cannot reliably self-correct reasoning without external feedback. Intrinsic self-correction can actually degrade performance.

This means: pure "self-reflection" based evolution is wishful thinking. Gates provide the external feedback signal that tells the system it actually failed — rather than "feeling good about itself."

All three layers are permanently necessary. Evolution means each layer becomes more precise, not eliminating any layer.

---

## The Stateless Problem

LLMs start each session fresh. Weights don't change between sessions. The only "evolution substrate" is modifications to the system prompt.

This means "getting smarter" physically takes the form of:

1. **Distillation** — Compress 50 rules into 3-5 principles + essential rules
2. **Positioning** — Principles in the top 20% of the prompt (highest attention)
3. **Gates as immune memory** — Code-level checks = the immune system's memory of past pathogens. No re-thinking needed for known threats.

And the lifecycle looks like:

```
New failure --> Can the principle cover this?
  YES --> Refine principle precision (no new rule)
  NO  --> Add rule (linked to principle, with evidence)
          --> Rule fails 3x --> Promote to gate
          --> Gate deployed --> Rule retires
```

---

## Try It Yourself

If you're building a long-running AI agent with behavioral instructions:

1. **Count your rules.** If >30, you likely have redundancy and signal dilution.
2. **Trace each rule to a failure.** If it doesn't link to an actual observed error, it's prophylactic noise — delete it.
3. **Look for rule clusters.** 3+ rules from the same root cause = candidate for one principle.
4. **Test removal.** Delete 30% of your rules. If output quality doesn't change, those rules were already being ignored.
5. **Add one gate.** For your most stubborn failure (3+ repeats), add a code-level check. Measure whether the associated rules can then retire.

---

## References

For those who want to dig deeper:

- [Constitutional AI](https://arxiv.org/abs/2212.08073) (Anthropic, 2022) — Compact principle set governs behavioral refinement
- [SELF-ALIGN / Dromedary](https://arxiv.org/abs/2305.03047) (IBM/CMU, NeurIPS 2023) — 16 principles, zero RL, competitive alignment
- [Reflexion](https://arxiv.org/abs/2303.11366) (Princeton, NeurIPS 2023) — Capping reflections at 3; more is worse
- [LLMs Cannot Self-Correct Reasoning](https://arxiv.org/abs/2310.01798) (DeepMind, ICLR 2024) — Why gates are mandatory
- [OpenAI Model Spec](https://cdn.openai.com/spec/model-spec-2024-05-08.html) (2024) — Objectives > Rules > Defaults hierarchy
- [Claude's Character](https://www.anthropic.com/research/claude-character) (Anthropic, 2024) — "Broad traits, not narrow rules"
- [Self-Discover](https://arxiv.org/abs/2402.03620) (DeepMind, 2024) — Compositional principles, 10-40x cheaper, +32% over CoT
- [Claude Code Best Practices](https://docs.anthropic.com/en/docs/claude-code/best-practices) (Anthropic, 2025) — "Ruthlessly prune"

---

*Based on 2+ months of production data and 27 behavioral corrections from SwarmAI, a desktop AI command center built on Claude Agent SDK. The three-layer governance model is running in production now. Happy to share more details in comments.*
