---
title: "Goal Loop — Iterative Convergence for Open-Ended Objectives"
created: 2026-05-14
updated: 2026-05-14
tags: [architecture, pipeline, goal-loop, quality-convergence, autonomous-delivery]
project: SwarmAI
status: PE-review
---

# Goal Loop — Iterative Convergence for Open-Ended Objectives

---

## 1. Executive Summary

### Vision: From Single Deliverables to Open-Ended Goals

The Autonomous Pipeline's 9-stage architecture delivers one requirement → one push-ready PR. But many real objectives are open-ended: "get test coverage to 90%", "migrate all callers off deprecated API", "reduce cold start time below 200ms." These cannot be solved in one pass — they require iterative convergence across multiple BUILD+TEST cycles toward a measurable Definition of Done (DoD).

The Goal Loop extends the Pipeline with a cycle-based execution model that iterates until DoD criteria are met, budget is exhausted, or a structural blocker is detected.

### Architecture in One Sentence

EVALUATE defines DoD + max cycles; GOAL_CYCLE iterates BUILD+TEST+DoD-check until convergence or escalation; REFLECT distills lessons from the entire goal execution.

### Relationship to Quality Convergence Loop

The Quality Convergence Loop operates **within** a single pipeline run — iterating on a delivery candidate until it passes the 6-layer push-ready gate. The Goal Loop operates **across** multiple cycles — each cycle produces a small, tested increment toward a larger objective. They are complementary, not competing:

```
Quality Convergence Loop: one candidate → iterate until push-ready (minutes)
Goal Loop: one objective → iterate cycles until DoD met (hours/days)
```

---

## 2. The Problem

### Single-Pass Pipeline Has a Scope Ceiling

The 9-stage pipeline excels at bounded requirements: "add pagination to /users endpoint", "fix the race condition in session cleanup." These have clear completion criteria achievable in one pass.

But open-ended goals resist single-pass delivery:

| Goal Type | Why One Pass Fails |
|-----------|-------------------|
| Coverage targets | Requires identifying gaps, writing tests iteratively, verifying no regressions |
| API migrations | Each caller needs individual analysis; breaking changes cascade |
| Performance optimization | Requires measure → hypothesize → implement → measure loops |
| Tech debt reduction | Multiple independent fixes, each needing verification |

### The Alternative: Manual Decomposition

Without Goal Loop, the user must manually decompose "get coverage to 90%" into 15 individual requirements ("add tests for module X", "add tests for module Y"...) and run the pipeline 15 times. This defeats the purpose of autonomous delivery.

---

## 3. Architecture

### Three-Phase Structure

```
┌─────────────────────────────────────────────────────────────┐
│ EVALUATE                                                     │
│  Define DoD criteria (command or rubric)                     │
│  Set max_cycles, cycle_scope, review_cadence                │
│  Create progress file                                        │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ GOAL_CYCLE (loops internally)                                │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Per Cycle:                                           │    │
│  │  1. Budget Gate (≥150K tokens remaining)             │    │
│  │  2. DoD Check (exit-first if all pass)               │    │
│  │  3. Stuck Detection (3 cycles zero progress → STOP)  │    │
│  │  4. Read Progress → identify largest gap             │    │
│  │  5. Pick Step (1-3 files, one DoD criterion)         │    │
│  │  6. Execute (TDD: RED → GREEN → verify)              │    │
│  │  7. Test (targeted, with regression protocol)        │    │
│  │  8. Update Progress file                             │    │
│  │  9. Mini-Reflect (one-line insight)                  │    │
│  │  10. Periodic REVIEW gate (every N cycles)           │    │
│  │  11. Revert Check (2 consecutive → CONFLICT)         │    │
│  │  12. Loop → back to step 1                           │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  Exit: SUCCESS | CHECKPOINT | STOP | REVERT_LIMIT | BUDGET   │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ REFLECT (Two-Tier)                                           │
│  Mini: per-cycle one-liner (accumulated during cycles)       │
│  Full: distill patterns → IMPROVEMENT.md + PROJECT.md        │
└─────────────────────────────────────────────────────────────┘
```

### DoD Criteria Types

| Type | Definition | Evaluation |
|------|-----------|------------|
| **Command** | Shell command; exit 0 = pass | `bash -c '<cmd>' 2>&1; echo "EXIT:$?"` |
| **Rubric** | Qualitative criteria with explicit pass/fail | LLM evaluates current state against rubric |

Example DoD for "coverage to 90%":
```yaml
dod_criteria:
  - type: command
    name: "Backend coverage ≥ 90%"
    check: "cd backend && python -m pytest --cov=. --cov-fail-under=90 -q 2>&1 | tail -5"
  - type: command
    name: "No test regressions"
    check: "cd backend && python -m pytest --timeout=120 -q"
```

---

## 4. Execution Modes

### Inline Mode (Same Session)

Best for goals completable in ~5-10 cycles. Full context continuity — the agent remembers every prior cycle's decisions.

```
User: "Get backend test coverage to 90%"
  → EVALUATE: defines 2 DoD criteria, max 15 cycles
  → GOAL_CYCLE: runs 8 cycles, each adding tests for one module
  → Final adversarial review on total changeset
  → REFLECT: distills coverage patterns to IMPROVEMENT.md
  → Push-ready PR with all changes
```

### Scheduled Mode (Job System)

Best for goals needing 30+ cycles or overnight/unattended execution. Each job execution runs ONE cycle, using the progress file as the sole state carrier.

```yaml
jobs:
  - id: goal-coverage-90
    name: "Backend coverage to 90%"
    type: agent_task
    schedule: "0 */4 * * *"  # every 4 hours
    enabled: true
    config:
      prompt: |
        Resume goal loop for SwarmAI.
        Progress: ~/.swarm-ai/SwarmWS/.artifacts/goal-coverage/progress.md
        1. Read progress → 2. Check DoD → 3. One cycle → 4. Save & exit
    safety:
      max_budget_usd: 1.50
      timeout_seconds: 600
```

### Mode Selection Criteria

| Factor | Inline | Scheduled |
|--------|--------|-----------|
| Estimated cycles ≤10 | ✅ | Overkill |
| Estimated cycles >10 | Budget risk | ✅ |
| Context continuity needed | ✅ Full | ❌ Fresh each run |
| Overnight/unattended | ❌ | ✅ |
| Quality gates integrated | Per-cycle REVIEW + final ADVERSARIAL | Only at completion |

Default: **inline**. Switch to scheduled when EVALUATE estimates >10 cycles or user requests unattended execution.

---

## 5. Safety Mechanisms

### Budget Gate

Each cycle checks remaining token budget before executing. Threshold: 150K tokens (enough for 1 cycle + REFLECT + overhead). Below threshold → graceful exit with progress saved.

### Stuck Detection

3 consecutive cycles with zero DoD progress (no criterion flipped) → EXIT with STOP. Prevents infinite loops on structurally impossible goals.

### Regression Protocol

When a cycle's changes break existing tests:
1. Attempt 1: diagnose + scoped fix (≤3 files)
2. Attempt 2: different approach
3. If both fail: REVERT cycle's source changes, mark as blocked

### Revert Limit

2 consecutive cycle reverts → EXIT with REVERT_LIMIT. Indicates conflicting constraints that require human decomposition or architectural change.

### Periodic Review

Every N cycles (configurable `review_cadence`), run a REVIEW-equivalent on the accumulated diff since last review. Catches drift that individual cycles miss.

---

## 6. Progress File as State Machine

The progress file (`progress.md`) is the single source of truth for goal state. It must be self-contained enough for a fresh agent (scheduled mode) to resume without any other context.

Contents:
- DoD criteria with completion status
- Configuration (max cycles, scope, commits)
- Metrics table (cycle-by-cycle progress)
- Current state (next target, lowest-hanging fruit)
- Blockers list
- Cycle log (one-line insights per cycle)

### GoalMetrics Integration

A `GoalMetrics` class tracks velocity across cycles and across runs:
- `track_goal_start(dod_criteria)` — initialize
- `track_cycle(cycle_num, progress_delta, files_changed, tests_added, regression)` — per cycle
- `track_goal_complete(status, total_cycles, dod_met, dod_total)` — at exit
- `get_velocity()` — returns avg delta/cycle for auto-tuning
- `get_recommended_cycle_scope()` — cross-run learning for scope calibration

---

## 7. Exit Conditions

| Exit | Trigger | Action |
|------|---------|--------|
| **SUCCESS** | All DoD criteria pass | Final adversarial → REFLECT → push-ready |
| **CHECKPOINT** | Max cycles reached | Save progress, create todo, recommend next steps |
| **STOP** | 3 cycles zero progress | Diagnose structural blocker, suggest decomposition |
| **REVERT_LIMIT** | 2 consecutive reverts | Identify conflicting constraints, suggest refactor |
| **BUDGET** | <150K tokens remaining | Save progress, suggest resume (inline or scheduled) |

All exits record GoalMetrics for cross-run velocity learning.

---

## 8. REFLECT (Two-Tier)

### Mini-Reflect (Per Cycle)

One-line insight appended to progress file after each cycle. No DDD writes. No LLM distillation. Accumulates raw material cheaply.

### Full REFLECT (At Goal Completion)

Triggered only on SUCCESS exit (after final adversarial passes):

1. Read all mini-reflects from progress file
2. Distill patterns: hardest criteria, highest-leverage actions, recurring blockers, velocity curve
3. Write to IMPROVEMENT.md: "What Worked" + "What Failed" entries
4. Update PROJECT.md: goal completed, date, cycles taken, key insight
5. Feed DDD Cultivation Engine (standard channel)

---

## 9. Integration with Pipeline Architecture

Goal Loop is not a separate system — it is a **pipeline profile**. When EVALUATE determines the requirement is goal-shaped (open-ended, measurable DoD, multi-cycle), it selects the `goal` profile:

```
Pipeline Profiles (6 total):
  full      → 9 stages + Quality Convergence Loop (default)
  trivial   → abbreviated stages for small changes
  research  → EVALUATE + THINK + REFLECT
  docs      → EVALUATE + THINK + PLAN + DELIVER + REFLECT
  bugfix    → abbreviated stages for urgent fixes
  goal      → EVALUATE + GOAL_CYCLE + REFLECT (this document)
```

The same DDD knowledge, the same adversarial review architecture, and the same REFLECT → DDD feedback loop apply. Goal Loop simply changes the execution topology from linear (9 stages) to iterative (N cycles).

---

## 10. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Exit-first DoD check | Prevents unnecessary work when goal is already met |
| One step per cycle (1-3 files) | Keeps changes reviewable, revertable, testable |
| Progress file as sole state | Enables scheduled mode without session persistence |
| 150K budget threshold | Conservative — ensures clean exit with REFLECT |
| Adversarial only at completion | Per-cycle adversarial too expensive; periodic REVIEW catches drift |
| Mini-reflect is text-only | No LLM calls for per-cycle reflection — accumulate cheaply |
| GoalMetrics cross-run | Velocity data improves cycle_scope recommendations over time |

---

*Last updated: May 14, 2026 — v1.0*
