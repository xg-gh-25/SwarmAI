# Your AI Agent Doesn't Need More Rules — Three-Layer Governance and Cognitive Evolution for LLM Agents

> 📎 [View on GitHub](https://github.com/xg-gh-25/SwarmAI/discussions/22) | Category: General | Published: 2026-05-19

---

## TL;DR

Rule accumulation is a dead end for AI Agent self-evolution. In SwarmAI, we observed: 27 behavioral corrections, the same cognitive bias repeating 4 times before being controlled, and an ever-growing instruction file that didn't proportionally improve output quality. The problem isn't "not enough rules" — it's "no number of rules can fix a fundamental judgment deficiency."

We propose: LLM Agent behavioral governance should mirror human society's three-layer structure — Principles (morality), Rules (law), Gates (enforcement) — and evolution should move toward **distillation** (fewer rules, sharper principles) rather than accumulation (more rules).

---

## The Problem: Why Rules Fail

### Observed Pattern

We've operated SwarmAI (a desktop AI assistant built on Claude Agent SDK) for 2+ months with full correction history. A representative failure pattern:

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

**4 occurrences of the same failure class.** Each time we added a rule. Rules didn't work, so we added stricter rules. Still didn't work, so we added a code-level gate. The gate only blocks that specific behavior — then the same underlying bias manifests in a different form (C027).

This isn't an edge case. This is the **structural dilemma** of LLM agent behavioral governance.

### Root Cause

Behind these superficially different corrections lies **one cognitive bias**:

> "Stopping at 'feels about done' rather than at 'confirmed complete.'"

Rules can only enumerate symptoms ("don't skip review," "don't skip pipeline," "don't accept 80%"). They can't treat the cause. And symptoms are infinite — any decision point that feels like "probably done" can trigger this bias.

**Accumulating rules = prescribing more cold medicine brands to someone with a broken immune system. The effective approach is fixing the immune system.**

---

## Inspiration: How Human Societies Govern Behavior

Human societies don't rely on a single mechanism. Three layers coexist, each with a distinct role:

| Layer | Human Society | Effective For | Failure Mode |
|-------|--------------|---------------|--------------|
| **Morality/Ethics** | Internal constraint | Highly internalized individuals | Most people selectively comply |
| **Law/Rules** | External codification | Anyone who can understand rules | Bounded; novel situations uncovered |
| **Enforcement/Punishment** | Physical coercion | Everyone | High cost; can only backstop |

The key insight: **All three are necessary.**

- Morality alone → utopian fantasy (unrealistic expectations of human nature)
- Laws alone → rigid, unbounded growth (novel situations always outnumber laws)
- Enforcement alone → police state (high cost, low flexibility)

The most stable society = morality handles 90% of daily decisions, laws handle 9% of edge cases, enforcement backstops 1% of proven bad actors.

---

## Design: Three-Layer Governance for LLM Agents

### Layer 1: Principles — Orientation, Not Enforcement

**Count:** 3-5 fundamental orientations. No more.

**Purpose:** When encountering a NEW situation (no rule, no gate), principles provide judgment direction.

**Properties:**
- Each principle covers an entire CLASS of failures, not a specific instance
- NOT expected to have 100% compliance — probabilistic effectiveness ~70-80%
- Positioned at highest attention priority in the system prompt
- Written for maximum clarity and memorability

**Example (covering C011-C027 entirely):**

> "Done = I actively tried to break it and failed. Not 'I couldn't find obvious problems.'"

One principle. If genuinely followed, every correction from C011 through C027 would never have occurred.

### Layer 2: Rules — Bounded, Traceable, Expirable

**Properties:**
- Every rule MUST link to a parent principle (orphan rules = deletion candidates)
- Every rule has evidence (which corrections spawned it)
- Every rule has a **graduation condition**: when a gate covers this failure mode, the rule can retire
- Total count is bounded — adding a rule requires justifying why the principle alone is insufficient
- Rules are CONCRETE: "when X, do Y" format

**Lifecycle:**

```
New failure → Can the principle cover this?
  YES → Refine principle precision (no new rule)
  NO  → Add rule (linked to principle, with evidence)
        → Rule fails 3x → Promote to gate
        → Gate deployed → Rule retires
```

### Layer 3: Gates — Minimum, Mechanical, Proven

**Properties:**
- Only added after 3+ failures of the same pattern at rule level
- Must be mechanically detectable (code can check, not just text can advise)
- Minimum count — each gate has a cost (rigidity, false positives, maintenance)
- Gates are insurance, not primary guidance

### Layer Interaction

```
                    Novel situation
                          │
                          ▼
                ┌─────────────────┐
                │   PRINCIPLES    │  (~70-80% effective)
                │  (orientation)  │
                └────────┬────────┘
                         │ fails
                         ▼
                ┌─────────────────┐
                │     RULES       │  (~85-90% | principle failed)
                │   (guidance)    │
                └────────┬────────┘
                         │ fails
                         ▼
                ┌─────────────────┐
                │     GATES       │  (~99% | rule failed)
                │ (enforcement)   │
                └─────────────────┘

Combined P(correct behavior) ≈ 99.5%+ for known failure classes
Novel failures (no rule/gate yet): ~70-80% first-time correctness
```

---

## Evolution = Distillation, Not Accumulation

### Why Accumulation Fails

Anthropic's official [Claude Code Best Practices](https://docs.anthropic.com/en/docs/claude-code/best-practices) (2025):

> "Bloated CLAUDE.md files cause Claude to **ignore** your actual instructions!"
> "Ruthlessly prune. If Claude already does something correctly without the instruction, **delete it**."

Princeton's [Reflexion](https://arxiv.org/abs/2303.11366) (NeurIPS 2023) caps reflections at **3 entries** — more actually degrades performance.

IBM/CMU's [SELF-ALIGN](https://arxiv.org/abs/2305.03047) (NeurIPS 2023 Spotlight) achieves alignment competitive with Text-Davinci-003 using only **16 principles** (~300 lines) — no RL, no massive annotation.

The signal is consistent: **fewer, more precise directives > more, more specific rules.**

### The True Direction of Evolution

| Signal | Meaning |
|--------|---------|
| Instruction file getting shorter + output quality improving | Principles genuinely generalizing |
| Gate fire count → 0 | Upstream layers sufficient; gates become insurance |
| New failure type handled correctly on first occurrence | OS genuinely upgraded (generalization) |
| Same failure class stops recurring after principle change | Internalization working |

Anti-signals:

| Anti-signal | Meaning |
|-------------|---------|
| Instruction file keeps growing | Still patching, not upgrading |
| Every new failure needs a new gate | Principles not generalizing |
| Same bias manifests in different forms | Treating symptoms, not root cause |

### Evolution Operations

| Operation | When | Effect |
|-----------|------|--------|
| **Principle refinement** | New failure within existing principle's scope, but principle wasn't precise enough | Sharpen wording |
| **Rule retirement** | Gate mechanically covers the failure mode | Reduce noise |
| **Rule → Principle absorption** | 3+ rules stem from same root | Merge into one clearer principle, delete rules |
| **Gate graduation** | Gate hasn't fired in 30+ days | Consider removing |
| **Principle compression** | Two principles overlap | Merge |

---

## Why Gates Are Non-Negotiable: A Critical Negative Result

Google DeepMind's research ([Huang et al., ICLR 2024](https://arxiv.org/abs/2310.01798)) demonstrates:

> **LLMs cannot reliably self-correct reasoning without external feedback.** Intrinsic self-correction can DEGRADE performance.

This means: pure "self-reflection" based agent evolution is wishful thinking. **External verification (mechanical gates) is a structural requirement, not a nice-to-have.**

Principles set direction. Rules refine guidance. But only Gates provide the external feedback signal that tells the system it ACTUALLY failed — rather than "feeling good about itself."

The three-layer model is NOT "ideally we'd remove gates." All three layers are permanently necessary. Evolution means each layer becomes more precise within its own scope — not eliminating any layer.

---

## The LLM Reality: Probabilistic Compliance

An LLM's "moral compliance" isn't constant. Same model, varying conditions:

- **Context position**: Top 20% of prompt = strongest attention, highest principle effectiveness
- **Task complexity**: Complex tasks produce stronger "just finish" reward signals competing with "check carefully" signals
- **Session length**: More accumulated context = more competition for attention = principle effectiveness drops

**Analogy:** Same person, sleep-deprived = lower self-control. Not a different person — just fewer cognitive resources available.

**Design implication:** The system must account for this variance. Never assume principles always work. Rules are probability boosters. Gates are absolute floors.

### The Stateless Paradox

LLMs start each session fresh. Weights don't change. The only "evolution substrate" = modifications to system prompt files.

This means "OS upgrade" physically takes the form of:
1. **Distillation** — Compress 50 rules into 3-5 principles + essential rules
2. **Positioning** — Ensure principles occupy maximum-attention positions
3. **Gates as immune memory** — Code-level gates = the immune system's memory of past pathogens; no re-thinking needed for known threats

---

## Related Work

| Work | Key Contribution | Relation to Our Model | Source |
|------|-----------------|----------------------|--------|
| **Constitutional AI** (Anthropic, 2022) | Compact principle set governs behavioral refinement via self-critique | Validates "fewer principles > more rules" at training level | [arXiv:2212.08073](https://arxiv.org/abs/2212.08073) |
| **SELF-ALIGN / Dromedary** (IBM/CMU, NeurIPS 2023 Spotlight) | 16 principles achieve competitive alignment with zero RL | Strongest validation — distillation works | [arXiv:2305.03047](https://arxiv.org/abs/2305.03047), [GitHub](https://github.com/IBM/Dromedary) |
| **Claude's Character** (Anthropic, 2024) | "Broad traits, not narrow rules" | Production deployment of principle-over-rule philosophy | [anthropic.com/research/claude-character](https://www.anthropic.com/research/claude-character) |
| **OpenAI Model Spec** (2024) | Objectives > Rules > Defaults hierarchy | Near-identical architecture to our proposal | [cdn.openai.com/spec/model-spec-2024-05-08.html](https://cdn.openai.com/spec/model-spec-2024-05-08.html) |
| **Collective Constitutional AI** (Anthropic, 2023) | Public participatory principle generation; consensus-filtering as distillation | Governance of principles themselves | [anthropic.com/research/collective-constitutional-ai](https://www.anthropic.com/research/collective-constitutional-ai-aligning-a-language-model-with-public-input) |
| **Promptbreeder** (DeepMind, 2023) | Self-referential improvement — mutation operators evolve | Meta-evolution: evolve the evolutionary mechanism itself | [arXiv:2309.16797](https://arxiv.org/abs/2309.16797) |
| **"Large Language Models Cannot Self-Correct"** (DeepMind, ICLR 2024) | Self-correction without external feedback degrades performance | Why Gates are mandatory | [arXiv:2310.01798](https://arxiv.org/abs/2310.01798) |
| **Reflexion** (Princeton/Northeastern, NeurIPS 2023) | Cap reflections at 3 — more is worse | Evidence for bounded rules | [arXiv:2303.11366](https://arxiv.org/abs/2303.11366) |
| **Self-Discover** (DeepMind, 2024) | Compositional principles > accumulated rules | 10-40x cheaper, +32% over CoT | [arXiv:2402.03620](https://arxiv.org/abs/2402.03620) |
| **DSPy** (Stanford NLP, 2023) | Compilation replaces manual prompt engineering | Engineering realization of anti-accumulation | [arXiv:2310.03714](https://arxiv.org/abs/2310.03714), [GitHub](https://github.com/stanfordnlp/dspy) |
| **ExpeL** (Tsinghua, AAAI 2024) | Experience → insights → recall at inference | Distillation mechanism for runtime agents | [arXiv:2308.10144](https://arxiv.org/abs/2308.10144) |
| **ADAS** (UBC/Vector Institute, 2024) | Meta-agent designs better agents; curated archive | Selection pressure prevents pure accumulation | [arXiv:2408.08435](https://arxiv.org/abs/2408.08435) |
| **Agent-R** (2025) | Self-correction capability improves iteratively | Meta-capability evolution | [arXiv:2501.11425](https://arxiv.org/abs/2501.11425) |
| **Symbolic Learning for Self-Evolving Agents** (AIWaves, 2024) | Agent config as "learnable parameters" | Formal framework for intuitive practice | [arXiv:2406.18532](https://arxiv.org/abs/2406.18532), [GitHub](https://github.com/aiwaves-cn/agents) |
| **SPIN: Self-Play Fine-Tuning** (UCLA, ICML 2024) | Self-play converges when model matches target distribution | Proves distillation has a mathematical endpoint | [arXiv:2401.01335](https://arxiv.org/abs/2401.01335) |
| **OPRO: LLMs as Optimizers** (DeepMind, ICLR 2024) | LLMs optimize prompts via scored history | Counterexample — accumulative, hits context limits | [arXiv:2309.03409](https://arxiv.org/abs/2309.03409) |
| **Claude Code Best Practices** (Anthropic, 2025) | "Bloated CLAUDE.md causes Claude to ignore instructions — ruthlessly prune" | Practitioner validation of anti-accumulation | [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code/best-practices) |
| **Voyager** (NVIDIA/Caltech, 2023) | Composable skill library + self-verification in open-ended learning | Compositional rules + gate pattern | [arXiv:2305.16291](https://arxiv.org/abs/2305.16291) |

---

## Our Contribution (Gaps in Existing Literature)

1. **Runtime bidirectional loop** — Existing work operates at training time (CAI, SELF-ALIGN) or one-shot compilation (DSPy). We implement continuous principle ↔ rule ↔ gate lifecycle management at runtime.

2. **Rule expiry mechanism** — All existing systems either only accumulate (ExpeL, Reflexion's capped buffer) or replace wholesale (DSPy compilation). Rule traceability + graduation conditions are novel.

3. **Reverse distillation** — SELF-ALIGN does forward distillation (principles → behavior). We propose bidirectional: observed behavioral rules compressed BACK into principles.

4. **Failure mode migration** — The "same bias, different surface form" problem (C011→C027) is acknowledged but unsolved in the literature.

---

## Discussion Questions

1. **Principle count:** What's the minimum principle set that covers all observed failure classes? Is there a universal set?
2. **Verification:** How do we verify a principle is "truly internalized" vs. "just exists in the prompt"? (Our test: novel error type handled correctly on first encounter.)
3. **Fourth layer:** Is there a layer we're not seeing? (e.g., model selection, temperature, structural prompt architecture)
4. **Cross-agent transfer:** Can principles distilled from one agent transfer to another?
5. **Convergence:** [SPIN](https://arxiv.org/abs/2401.01335) proves self-play has a mathematical convergence point. Does principle distillation have an "evolution endpoint"?

---

## Try It Yourself

If you're building a long-running AI agent with behavioral instructions:

1. **Count your rules.** If >30, you likely have redundancy and signal dilution.
2. **Trace each rule to a failure.** If it doesn't link to an actual observed error, it's prophylactic noise.
3. **Look for rule clusters.** 3+ rules that stem from the same root cause = candidate for principle extraction.
4. **Test removal.** Delete 30% of rules. If output quality doesn't change, those rules were already ignored.
5. **Add one gate.** For your most stubborn failure (3+ repeats), add a code-level check. Measure whether the associated rule can then retire.

---

*This article is based on 2+ months of production operation data from SwarmAI and 27 behavioral corrections. SwarmAI is a desktop AI command center built on Claude Agent SDK, using AIDLC (AI-Driven Development Lifecycle) for autonomous development.*

*We're actively experimenting with this three-layer governance model. If you're working on similar problems — let's discuss.*

