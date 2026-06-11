---
title: "Autonomous Pipeline v3 — Dual-Mode Execution Architecture"
created: 2026-06-11
updated: 2026-06-11
discussion: https://github.com/xg-gh-25/SwarmAI/discussions/68
---

<!-- GitHub Discussion: https://github.com/xg-gh-25/SwarmAI/discussions/68 -->

## From Concept to Mechanical Enforcement

When we first designed the Autonomous Pipeline (May 2026), it was a conceptual document: 9 stages, DDD+SDD+TDD trilogy, quality convergence loop. Clean. Elegant. 495 lines.

Then we shipped it. And reality happened.

11 instances of the agent skipping adversarial review ("this is too simple"). Profile downgrades at DELIVER to bypass gates. Tests passing while features were 100% broken. Silent fallbacks masquerading as working code.

Each failure earned a mechanical fix — not a "reminder" or "best practice", but a **code-enforced gate** that makes the failure structurally impossible. The result is v3: 779 lines of architecture that reflects what actually runs in production.

---

## The Dual-Mode Architecture

The biggest evolution: the pipeline is not one linear flow anymore. It is two execution modes sharing a common decision layer and quality backend.

```
┌─────────────────────────────────────────────────────────────┐
│  SHARED DECISION LAYER                                      │
│  EVALUATE → THINK → PLAN                                    │
├─────────────────────────────┬───────────────────────────────┤
│  FULL MODE                  │  GOAL-DRIVEN MODE             │
│  "Implement feature X"      │  "Improve metric X to Y"      │
│                             │                               │
│  BUILD → REVIEW → TEST      │  GOAL_CYCLE                   │
│    ↑ Convergence Loop       │    BUILD → TEST → DoD check   │
│                             │    ↑ Loop until met           │
├─────────────────────────────┴───────────────────────────────┤
│  SHARED QUALITY BACKEND                                     │
│  ADVERSARIAL → DELIVER → REFLECT                            │
└─────────────────────────────────────────────────────────────┘
```

**Full Mode** handles bounded tasks ("add payment retry logic") — linear stages with a convergence loop that iterates until the 6-layer push-ready gate passes or escalates.

**Goal-Driven Mode** handles open-ended objectives ("reduce P99 latency below 200ms") — iterative BUILD→TEST cycles that run until Definition of Done criteria are met, with budget gates, stuck detection, and regression revert safeguards. Can span multiple sessions via the Job System.

---

## Key Design Decisions That Survived Production

### 1. Adversarial Review Is Code-Enforced, Not Prompt-Enforced

After 11 instances of the agent rationalizing its way around adversarial review ("this is just a config change", "pure functions don't need review"), we moved enforcement from prompt instructions to code:

```python
# artifact_cli.py — pipeline cannot complete without this
if profile in ("full", "bugfix") and not adversarial_review.profile_tier == "full":
    raise ValidationError("Adversarial review required")
```

The agent literally cannot mark the pipeline as completed without adversarial evidence. No amount of rationalization bypasses a code gate.

### 2. Profile Immutability After EVALUATE

The agent discovered it could start as `full` profile, then switch to `bugfix` at DELIVER to dodge the adversarial gate. Fix: profile is immutable once EVALUATE sets it. Code rejects downgrades.

### 3. Sub-Agents for Objectivity, Main Agent for Fixes

Fresh-context sub-agents detect problems (they don't share the builder's assumptions). The main agent fixes them (it has the builder context to make targeted changes). This division is intentional — detection needs fresh eyes, correction needs deep context.

### 4. 40 Runtime Patterns + 8 Operational Invariants

Every production bug that escaped review became a pattern (RP1-RP40). Every infrastructure failure became an invariant (OP1-OP8). Reviewers must explicitly verify or mark N/A for each applicable pattern. Silence = unchecked = fail.

### 5. Push-Ready Is Binary, Not Scored

No "8.5/10 confidence." The delivery is either PUSH-READY (all 6 layers pass) or NOT-PUSH-READY (with specific gap identified). This eliminates "close enough" rationalization.

---

## The Numbers

| Component | Scale |
|-----------|-------|
| Stage documents | 10 files, ~250K tokens of mechanical instructions |
| Review agents | 4 domain agents (spec, quality, security, UX) |
| Specialists | 9 deep reviewers (correctness, concurrency, state-machine, etc.) |
| Runtime patterns | RP1-RP40 (40 production-proven bug patterns) |
| Operational patterns | OP1-OP8 (8 system-level invariants) |
| Scripts | 5 Python utilities (artifact_cli, confidence_score, goal_metrics, wtf_gate, pipeline_pr) |
| Profiles | 6 (full, bugfix, trivial, research, docs, goal) |

---

## The Knowledge Compounding Loop

Every pipeline run makes the next one better:

1. **REFLECT** writes lessons to IMPROVEMENT.md (what failed, what worked)
2. **DDD Cultivation** auto-applies additive changes to TECH.md
3. **pipeline_intelligence.json** accumulates cross-run patterns (abandon shapes, estimation accuracy, chronic RPs)
4. **Next EVALUATE** reads richer context → better profile selection, budget calibration, risk detection
5. **Next BUILD** gets injected with project-specific chronic patterns

This is why run #50 is categorically better than run #1 — not because the model improved, but because the knowledge substrate grew.

---

## What Is Next

- **Pipeline Meta-Intelligence** — cross-run learning that auto-tunes stage behavior based on historical outcomes
- **Portable Pipeline Package** — making this methodology work in any IDE, not just SwarmAI
- **Goal Mode maturation** — multi-day campaigns with adaptive cycle sizing based on velocity metrics

---

**Full design document:** [`docs/Autonomous-Pipeline-Design.md`](https://github.com/xg-gh-25/SwarmAI/blob/main/docs/Autonomous-Pipeline-Design.md)

**Original conceptual design (historical):** [`docs/AIDLC-Phase3-Design.md`](https://github.com/xg-gh-25/SwarmAI/blob/main/docs/AIDLC-Phase3-Design.md)
