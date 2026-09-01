---
title: "Autonomous Pipeline: High-Level Design"
created: 2026-08-31
updated: 2026-08-31
tags: [architecture, pipeline, hld, autonomous-delivery, quality-gates]
project: SwarmAI
status: current
---

# High-Level Design: Autonomous Pipeline

**Author:** Swarm | **Date:** 2026-08-31 | **Status:** Draft
**Scope:** SwarmAI subsystem (cross-cutting: session, DDD, skills, jobs)
**Related:** `Autonomous-Pipeline-Design.md` (full technical reference), `s_autonomous-pipeline/INSTRUCTIONS.md` (mechanical orchestration), SwarmAI `TECH.md`

> This HLD answers "are we building the right delivery system, and what did we give
> up?" It is decision-shaped, not a mechanics manual. For the exhaustive stage
> mechanics, tooling surface, and pattern checklists, read the companion technical
> reference `Autonomous-Pipeline-Design.md`. This doc stays at the level a reviewer
> needs to judge the architecture and its trade-offs.

## 1. Overview

### 1.1 Problem Statement

"AI can code" is not "AI can deliver." A model produces syntactically correct code that
passes the tests it was given, the cheapest part of delivery. What ships broken is
everything the construction step does not touch: integration seams, convention
conformance, regressions, unverified cross-boundary contracts, and races on paths no test
exercises. Running a fixed stage sequence once and hoping the output is good enough makes
quality a coin flip: one run is excellent, the next is subtly broken, with no signal telling
the two apart. The recurring, expensive failure is a confident wrong result that reads as
done. The author is the least able to see it, because the same context that wrote the
code shares its blind spots.

### 1.2 Objectives / Success Criteria

| # | Objective | How Measured |
|---|-----------|--------------|
| O1 | Every output is push-ready, mergeable without human code review | Committed change passes all 6 push-ready layers before it lands |
| O2 | A confident-wrong result cannot ship on "looks done" | A structural gate (not prompting) blocks it. The gate cannot be skipped by downgrading the profile |
| O3 | The failure mode is "escalate with a clear gap report," never "ship despite known issues" | Every non-converged run terminates at a checkpoint + gap report, not a silent ship |
| O4 | Each run leaves the system smarter than the last | REFLECT writes lessons back to DDD. The next run reads richer context |
| O5 | The right-sized process for the task, no ceremony tax on a typo, no shortcut on a feature | Profile selected at intake. Rigor rides the profile, not the agent's mood |

### 1.3 Requirements

**Functional**
- Take a one-sentence requirement and produce committed, push-ready code (or a clear DEFER/REJECT/ESCALATE verdict).
- Handle both bounded tasks ("implement feature X") and open-ended goals ("get metric X to Y").
- Run interactively (visible in chat) or as a background job (overnight, notify on completion).
- Feed knowledge back to the domain brain (DDD) on every run.

**Non-functional**
- Completable OFFLINE, no step may depend on the remote (push/CI are outside the boundary).
- Quality is deterministic-or-escalated, never probabilistic-and-shipped.
- Runs inside one agent session (no orchestration service, no message queue) to avoid context-transfer loss.
- Gates are structural (code-enforced where the failure class recurred), not advisory prose a confident model rationalizes past.

### 1.4 Out of Scope

- Push to remote, CI verification, PR creation, user-initiated actions AFTER the pipeline's push-ready guarantee (the box guarantees push-ready. Crossing to the remote is the human's call).
- Per-stage mechanics, exact command surface, the full runtime-pattern roster: those live in the technical reference and the stage docs.
- Multi-agent orchestration, explicitly rejected (§3, D1).

## 2. Solution Architecture

### 2.1 Approach Summary

The pipeline is one agent that switches roles through a fixed stage sequence, with
structural go/no-go gates riding inside it. Its canonical shape is **9 stages · 3 gates ·
2 modes**. The 9 stages are the production line (EVALUATE → THINK → PLAN → BUILD → REVIEW
→ TEST → ADVERSARIAL → DELIVER → REFLECT). The 3 gates are the moments of truth that
decide whether work advances. The 2 modes are how execution runs: **Full** (one pass +
a convergence loop, for a bounded task) or **Goal** (iterate BUILD+TEST toward a
measurable Definition of Done, for an open-ended objective). Both modes run all stages
and pass all gates, the mode changes only how the middle executes, not the rigor.

The load-bearing idea is that individual stages are each sound but collectively
insufficient: each stage only sees its own scope, so a defect that lives in the seam
between stages survives every one of them. Two mechanisms close that gap, a fresh-context
adversarial sub-agent (fresh eyes the author cannot supply) and a convergence loop that
verifies the COMPLETE output against the COMPLETE standard and iterates until no gap
remains or it escalates.

### 2.2 Systems and Interactions

Three layers execute top-to-bottom (diagram + full narrative in the technical reference,
Figure 1):

- **Shared decision layer**, EVALUATE → THINK → PLAN. DDD-driven judgment: should we, how, what exactly. Fronts both modes.
- **Execution track**, Full mode runs BUILD → REVIEW → TEST once then a convergence loop. Goal mode runs GOAL_CYCLE (BUILD+TEST looped to DoD, with budget/stuck/revert safeguards, resumable across sessions via the Job System).
- **Shared quality backend**, ADVERSARIAL → DELIVER → REFLECT. Attack, gate, compound. Gates both modes identically.

Cross-cutting, the **DDD knowledge layer** (PRODUCT / TECH / IMPROVEMENT / PROJECT + code
intelligence) is read per-stage on the way in and written back by REFLECT on the way out.
This is the compounding loop that makes each run inform the next.

### 2.3 The Three Gates (the architecture's spine)

A gate is a structural checkpoint a confident model cannot rationalize past, the reason
the system refuses to ship on "looks done." Each gate guards a different kind of truth, and
the earlier it fires the cheaper the failure it catches: a framing error caught at Gate 0
costs one EVALUATE pass. The same error caught at Gate 2 has already paid for PLAN, BUILD,
REVIEW, and TEST.

| Gate | Guards the truth of… | Fires | Catches |
|------|----------------------|-------|---------|
| **Gate 0** | the framing, is the problem itself understood? | inside EVALUATE → THINK | wrong problem, solution-first thinking, a "fix" that is already a no-op, unstated ambiguity |
| **Gate 1** | the plan, is the approach sound, root not symptom? | after PLAN, before BUILD | wrong direction, missed constraints, wrong layer, hallucinated API |
| **Gate 2** | the build, is the code actually correct? | inside DELIVER (blocking) | runtime bugs, security holes, untested paths, self-authored-code blind spots |

Gate 0 and Gate 2 ride within a stage. Gate 1 is the landmark after PLAN. Gate 2 is
mechanically enforced. It cannot be skipped by downgrading the profile to a lighter tier.
Gate 0 was added after framing errors were observed sailing untouched all the way to Gate 2.

### 2.4 Data Flows and Stores

Each stage consumes upstream artifacts and produces its own, persisted and schema-validated
at each boundary via `artifact_cli.py`: evaluation → research → design_doc → changeset →
review → test_report → delivery, with REFLECT writing to DDD rather than producing an
artifact. The EVALUATE artifact additionally carries the `understanding`, `ambiguity_scan`,
and `cross_boundary` blocks that downstream gates consume (TEST Layer 4 reads
`cross_boundary`, and REVIEW reads it too). Run state lives in `run.json`. The durable brain
lives in the DDD markdown docs. There is no database for the pipeline itself. The artifact
files and run.json ARE the store.

## 3. Key Decisions

| # | Decision | Alternatives considered | Rationale | One-way door? |
|---|----------|-------------------------|-----------|:-------------:|
| D1 | **Single agent switching roles**, not multi-agent orchestration | N independent agents communicating by message | Division of labor is a tax paid for limited human bandwidth, not an optimal design. Role-switching keeps full context at zero transfer cost. A sub-agent is spawned ONLY where fresh context is the point (adversarial). Multi-agent adds handoff, state-sync, and information-loss for no gain here. | No, could add orchestration later if the SDK ships shared real-time memory |
| D2 | **Quality convergence over one-shot perfection** | Demand flawless output from one pass | A non-deterministic model cannot guarantee one-pass perfection. The stronger contract is "iterate until verified or escalate," which removes probability from the outcome. | No |
| D3 | **Push-ready is the boundary, push and PR are the user's** | Pipeline auto-creates a PR with auto-merge | The pipeline must be completable offline, and pushing to a public-or-private remote is a deployment decision the human owns. The box guarantees push-ready + a local commit. The human crosses to the remote. | No, reversible policy choice |
| D4 | **Gates are structural, and the heaviest is code-enforced** | Prose rules ("always run adversarial review") | Carefulness does not scale. Gates do. Prose rules failed 11+ times (the CLASS-A self-exemption history). The fix that held was a gate `artifact_cli` refuses to complete without, at the correct profile tier. | No, but removing it would reopen the exact failure history |
| D5 | **Profile immutable after EVALUATE** | Allow re-scoping mid-run | Otherwise a run starts `full` then downgrades to a lighter tier at DELIVER to dodge the adversarial gate, which is the precise bypass D4 exists to prevent. | No |
| D6 | **Push-ready is binary, not scored** | A 1-10 confidence number | A number between "ship" and "don't ship" invents a false gradient (a run once scored 10/10 on 100%-broken code, the score measured process compliance, not correctness). Binary forces a real verdict + a specific gap. | No |
| D7 | **A `refactor` work-type inverts the patch polarity** | Treat every change as patch-tolerant | For a bugfix a deferred structural fix is acceptable tech debt. For a refactor the structural change IS the acceptance criterion, so a patch means the task did not happen. Gate 1 blocks a patch when work_type=refactor. | No |

**Explicitly choosing NOT to do:** no standalone pipeline service (context transfer is
expensive and lossy). No numeric quality score, no auto-push, no profile that skips
adversarial review.

## 4. Quality Attributes

### 4.1 Determinism and Escalation

Quality is either verified (all 6 push-ready layers pass, adversarial clean, completion
audit green → push-ready) or escalated with a precise gap report. There is no "shipped
despite known issues" state. The convergence loop is bounded (max 3 iterations). On
non-convergence it checkpoints with what still fails, what was attempted, and a root-cause
hypothesis, a resumable pause, not a silent failure.

### 4.2 Security and Safety

Security is gated at two layers: a design-level security-boundary check at PLAN (only when
the change crosses a contract boundary), and a fresh-context security specialist at Gate 2
that scans with confidence-gated findings + concrete exploit scenarios. Every code change
is additionally checked against the product secure-coding baseline. The runtime-pattern
checklist (SSOT `REVIEW_PATTERNS.md`) encodes the specific classes, identity-from-request,
fail-open gates, unsafe deserialization, injection sinks, as mechanical checks a reviewer
must verify or mark N/A.

### 4.3 Failure Modes and Graceful Degradation

| Failure | Behavior | Recovery |
|---------|----------|----------|
| Convergence loop does not converge in 3 iterations | Do NOT ship | Checkpoint + gap report. Human fixes or adjusts the requirement |
| Adversarial sub-agent spawn is rejected by the harness | Retry exactly once | Still rejected → checkpoint `gate_spawn_blocked`, then resume re-enters on a fresh subprocess (never fall back to self-review, that is the bypass the gate exists to prevent) |
| A resolved finding was reverted between fix and delivery | L4 disk-check greps the fix on disk (not the honor-system `resolved` flag) → BLOCK | Re-apply the fix, re-verify |
| EVALUATE framing is confidently wrong | Gate 0 skeptic refutes it (WRONG-FRAME / ALREADY-SATISFIED) | Re-observe against source, re-frame, before any code is written |
| A cross-boundary change passes every unit test but breaks the seam | TEST Layer 4 drives the real seam + mutation-verifies it goes RED on revert | Fix the seam. A green-on-revert Layer-4 test is theater and is rejected |
| DDD docs are stale/wrong | Pipeline follows bad guidance | REFLECT proposes a correction through the cultivation gate. Human approves |

### 4.4 Scaling and Cost

Execution runs inside the user's session and competes for its context window. The
convergence loop's iteration cap and the budget-gated checkpoint protocol bound that cost.
Background/goal runs decouple from the session via the Job System (cron-driven cycles,
progress file carries state across sessions). Token budget is tracked per stage for
calibration, never used as a mid-run truncation lever.

## 5. Cross-cutting Impact and Rollout

The pipeline is a **skill invocation** (`s_autonomous-pipeline`), not a service. It rides
the existing agent harness: the 11-file context system supplies DDD + behavioral rules +
user preferences. The Claude Agent SDK + MCP supply tools, MEMORY/EVOLUTION supply past
corrections, the projection layer ships the skill, and the Job System runs it in the
background. Nothing new to deploy, a change to the pipeline is a change to the skill's
markdown + its shared engine (`artifact_cli.py`, `pipeline_validator.py`), and takes effect
for the running system on the next skill re-projection.

Rollout is continuous and versioned in the stage docs themselves, the pipeline is its own
first customer (this HLD's companion doc was calibrated, and this HLD was authored, through
the narrative-writing skill, and the pipeline routinely runs on its own code). No feature flag,
no phased cutover: the profile system is the graceful-degradation dial (a typo gets the
`trivial` profile, a feature gets `full`), so there is no "big-bang" surface to roll out.

## 6. Open Questions

| # | Question | Suggested approach |
|---|----------|--------------------|
| Q1 | The diagrams (`diagrams-pipeline/*.svg`, Figure 1 v4) predate the RP-range and TEST-4-layer calibration, do any hard-code a now-stale count or a 3-layer TEST? | Audit each SVG against the calibrated text, re-render only those that pin a drifted number. Track as a follow-up, not a blocker (the prose is the SSOT, the figures only illustrate). |
| Q2 | The Gate-1 and Understanding-Gate skeptic spawns are `[MUST]` (behavioral), not `[GATE·validator]` code-enforced, so the skeptic sub-agent is agent-discipline. Is that the right strength, or should more of Gate 0/1 be code-enforced like Gate 2? | Watch the recurrence data: if a framing/plan error class recurs 3× despite the prose gate, promote it to a code gate (the same ladder that produced Gate 2). Do not pre-emptively harden what has not yet failed. |
| Q3 | This HLD and the technical reference `Autonomous-Pipeline-Design.md` now overlap in the architecture sections, will they drift apart? | Keep this HLD decision-shaped (why + trade-offs) and the reference mechanics-shaped (how). When they touch the same fact, the reference is the SSOT and this doc links to it rather than restating. Revisit if a future edit has to touch both. |

## Maps to the 5 criteria

Sections 1-2 make the doc self-contained and state the problem + why (criteria 1-2). Section
3 records every major decision with its alternatives and rationale (criterion 3). Section
4.3 names the real failure modes with their mitigations (criterion 4). Section 6 surfaces
the open questions rather than hiding them (criterion 5).
