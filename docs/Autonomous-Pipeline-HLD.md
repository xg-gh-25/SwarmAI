---
title: "Autonomous Pipeline: High-Level Design"
created: 2026-08-31
updated: 2026-11-25
tags: [architecture, pipeline, hld, autonomous-delivery, quality-gates]
project: SwarmAI
status: current
---

# High-Level Design: Autonomous Pipeline

**Author:** Swarm | **Date:** 2026-11-25 | **Status:** Current (Pipeline v1)
**Scope:** SwarmAI subsystem (cross-cutting: session, DDD, skills, CDF)
**Related:** `backend/skills/s_autonomous-pipeline/` (INSTRUCTIONS.md + one card per stage), `backend/core/run_protocol/` (the engine), `docs/design/pipeline-v1-remaining-design.md`, `docs/design/pipeline-v1-human-loop.md`. `Autonomous-Pipeline-Design.md` describes the retired pipeline and is kept as history only.

> This HLD answers "are we building the right delivery system, and what did we give
> up?" It is decision-shaped, not a mechanics manual. The exact commands and checks live
> in the skill's stage cards and in `core/run_protocol/`; this doc stays at the level a
> reviewer needs to judge the architecture and its trade-offs.

## 1. Overview

### 1.1 Problem Statement

"AI can code" is not "AI can deliver." A model produces code that passes the tests it was
given, the cheapest part of delivery. What ships broken is everything the construction step
does not touch: a misread requirement, integration seams, regressions, security holes, and
paths no test exercises. The recurring, expensive failure is a confident wrong result that
reads as done. The author is the least able to see it, because the same context that wrote
the code shares its blind spots. And a model that judges its own work will, under pressure,
say "done" when it is not.

### 1.2 Objectives / Success Criteria

| # | Objective | How Measured |
|---|-----------|--------------|
| O1 | What lands is the code that was reviewed and tested | Landing checks: review saw this exact code tree, the TEST report is on this tree, the commit is the reviewed code + REPORT |
| O2 | Model output never decides on its own whether work advances | Every stage exit and the landing are deterministic checks in `core/run_protocol/`; an expert's opinion never blocks landing |
| O3 | A run always ends in a known state | Every loop is capped; a run ends landed, not landed with its reason, or abandoned, never silently half-done |
| O4 | Each run leaves the system smarter than the last | REFLECT sends the run's report to CDF: delivered work, decisions, corrections, practices, gaps |
| O5 | The right-sized process for the task | Profile chosen at create: full, bugfix, trivial, direct or research |

### 1.3 Requirements

**Functional**
- Take one requirement and produce one landed local commit (or, for research, a report), with a REPORT the person reads.
- Ask the person only what only a person can decide: a requirement with two or more readings, or an irreversible or high-risk design trade-off.
- Feed knowledge back to the project's DDD on every run.

**Non-functional**
- Completable offline: no step depends on a remote. Pushing is outside the run.
- Each run's code lives in its own git worktree per repo; the shared main checkout is never edited.
- Runs inside one agent session, with an event-sourced record of every step, so a run can be resumed by another session.
- Checks are code, not prose rules a confident model can rationalize past.

### 1.4 Out of Scope

- Push, pull requests, CI, and production build or deploy: the person reads the REPORT and pushes.
- Eval: stays outside the run.
- Ordinary code changes: those are done natively, without the pipeline. The pipeline is for a risky change or when the person asks for it.

## 2. Solution Architecture

### 2.1 Approach Summary

One orchestrator agent switches roles through a fixed stage sequence. The model writes
content and fixes code; the engine (`core/run_protocol/`) owns state, checks, evidence,
authorization, landing and audit. Every `pipeline-v1` command returns `next`: what to do
now, the checks that still fail, and the card for the stage just entered.

| Profile | Stages |
|---------|--------|
| full, bugfix | EVALUATE → THINK → PLAN → BUILD → REVIEW → TEST → DELIVER → REFLECT |
| trivial | EVALUATE → PLAN → BUILD → REVIEW → TEST → DELIVER → REFLECT |
| direct | BUILD → REVIEW → TEST → DELIVER → REFLECT |
| research | EVALUATE → THINK → DELIVER → REFLECT (lands no code) |

The exact sequences are `PROFILE_STAGES` in `core/run_protocol/lifecycle.py`. There is no
skipped stage.

### 2.2 Systems and Interactions

- **Decision**, EVALUATE → THINK → PLAN. EVALUATE writes requirements with checkable acceptance criteria and reads the project's PRODUCT and PROJECT recall; THINK and PLAN read TECH and IMPROVEMENT recall and search the DDD further with `cognition find`. PLAN writes the design spec: tasks, exit criteria, alternatives and the chosen one.
- **Execution**, BUILD → REVIEW → TEST. BUILD writes and tests the change in the run's worktree with the repo's own commands and ends with a validation report. It may hand self-contained parts to the person's Hives as child runs and judge what comes back. REVIEW dispatches independent experts. TEST runs the change's tests and the repo's checks on the reviewed code.
- **Delivery**, DELIVER → REFLECT. DELIVER renders the REPORT and lands one local commit (a fast-forward of local main). REFLECT runs inside landing and sends the run's report to CDF (source S3).

Cross-cutting, the **DDD** is read on the way in (recall into `knowledge.md` and
`knowledge_design.md`) and fed on the way out by REFLECT: delivered work → TIMELINE,
decisions → PROJECT, corrections and practices → IMPROVEMENT, gaps → IMPROVEMENT "What to
Watch For". A gap a landed run fixed is closed.

### 2.3 The Checks (the architecture's spine)

A check is code a confident model cannot rationalize past. Each guards a different truth,
and the earlier it fires the cheaper the failure it catches.

| Check | Guards the truth of… | Fires | Catches |
|-------|----------------------|-------|---------|
| **Gate-0** | the framing | inside EVALUATE (bugfix, or a request with 2+ readings) | wrong problem, a fix that is already a no-op, unstated ambiguity, unverifiable acceptance criteria. A skeptic expert, 2 rounds max; can end the run before code |
| **Stage checks** | each stage's document | every stage exit | missing or invalid requirements, design spec or validation report; the whole roster runs and returns every failure together |
| **REVIEW** | the code | after BUILD, and again for any later change | correctness (always) and security (when the change is declared security-relevant); a second round rechecks every fix, a fix to behavior is proven by a test plus a mutation check |
| **Landing checks** | what lands | inside DELIVER | review saw this exact code; fixed findings were rechecked; tests pass on this code; the commit is the reviewed code + REPORT; main has not moved (else sync and test again) |

An expert's opinion never blocks landing: an open HIGH finding is listed as a residual risk
in the REPORT. A failed check is never a question for the person: it is fixed, or the run
ends not landed with its reason.

### 2.4 Data Flows and Stores

Each run has an event-sourced audit directory (`state/pipeline_agent_audit/runs/<run>/`):
a protected `contract.json` and an append-only event log. The run's state is derived only
from that log (`lifecycle.fold`); nobody edits run state by hand, and a security hook denies
writes there. The model writes only JSON documents through the CLI (`requirements.json`,
`design_spec.json`, review fixes, `reflect.json`); markdown, including the REPORT, is
rendered by the engine. The run audit (`run_audit.json`) scores five dimensions: Run
Isolation, Speed, Quality, Autonomy, Token Cost. The durable knowledge lives in the DDD,
written by CDF.

## 3. Key Decisions

| # | Decision | Alternatives considered | Rationale | One-way door? |
|---|----------|-------------------------|-----------|:-------------:|
| D1 | **One orchestrator owns the judgment**, not multi-agent orchestration | N agents communicating by message | Role-switching keeps full context at zero transfer cost. Sub-agents appear only where independence is the point (review experts, Gate-0) or for self-contained parts handed to Hives, whose results the orchestrator judges. | No |
| D2 | **Deterministic checks decide, experts only find** | Let the reviewer's verdict block or pass | A model's verdict is not reproducible, and a blocking opinion makes runs stall on taste. Code checks facts (review saw this tree, tests pass on it); findings the experts raise are fixed or listed as residual risk. | No |
| D3 | **Land one local commit, never push** | Open a PR, auto-merge | The run must be completable offline, and crossing to a remote is the person's decision. The person reads the REPORT and pushes. | No, reversible policy choice |
| D4 | **Every loop is capped** | Iterate until quality converges | An uncapped loop turns a hard problem into an endless run. Gate-0 has 2 rounds, REVIEW one fixing pass + recheck (one more for an open HIGH), TEST sends the run back to REVIEW at most twice, landing fails at most three times. After that the run ends with its reason. | No |
| D5 | **Ask the person only for a real decision** | Ask whenever unsure, or whenever a check fails | A question for a failed check hands the agent's work back. Only a requirement with 2+ readings, or an irreversible or high-risk trade-off, becomes a question: 2–3 options each, at most 2 per run. | No |
| D6 | **Each run in its own worktree** | Edit the shared checkout | Other sessions edit the same repo at the same time. A worktree makes the run's change exactly what it reviewed and tested. | No |

**Explicitly choosing NOT to do:** no standalone pipeline service, no numeric quality score,
no push verdict, no PR, no CI run inside the pipeline.

## 4. Quality Attributes

### 4.1 Determinism and Escalation

A run lands only when the landing checks pass on the exact tree it commits. If they cannot
pass within the caps, the run ends not landed with a deterministic reason, and its work is
kept on its `run/<id>` branch with a bundle and diff. The person can restore, resume or land
it by hand (`run-land`), which rechecks every tree.

### 4.2 Security and Safety

The security expert is dispatched when the change is declared security-relevant: by the
repo's `.swarmai/review.md`, by `"risk": ["security"]` in the BUILD report, or by a design
that names security boundaries. The correctness expert reviews every change against the
rows of the repo's review pattern table it triggers, read at the run's base so a run cannot
change its own review. Security hooks deny editing a run's audit directory, amending a
commit inside an active run, and hand-committing the files a run owns. Dangerous commands,
production actions and external sends keep their approval prompts.

### 4.3 Failure Modes and Graceful Degradation

| Failure | Behavior | Recovery |
|---------|----------|----------|
| EVALUATE framing is confidently wrong | Gate-0 skeptic refutes it | Re-frame before any code; or the run ends before code |
| PLAN refused three times | PLAN marked degraded; run goes on to BUILD without a design spec | REVIEW and TEST still decide; the audit says PLAN was refused |
| An expert's answer cannot be read | Asked once more with the parser's reasons | A second unreadable answer is not asked again; the checks decide |
| A HIGH finding is still open after the recheck | Listed as residual risk | The person sees it in the REPORT before pushing |
| Main moved during DELIVER | Sync; if the run's files were touched, test again | A merge conflict is resolved in the worktree; it does not use up a landing failure |
| The session that ran it is gone | Run is paused | Another session takes over once the first is not streaming, then resumes |
| DDD guidance is stale or wrong | The run follows bad guidance | REFLECT records a correction or a replacement for that entry through CDF |

### 4.4 Scaling and Cost

Execution runs inside the user's session and competes for its context window. Token use is
measured from the run's transcripts and reported in the audit's Token Cost row, never used
as a mid-run truncation lever. Large independent parts can run on the person's Hives as
child runs at the same time.

## 5. Cross-cutting Impact and Rollout

The pipeline is a **skill** (`s_autonomous-pipeline`) plus an engine (`core/run_protocol/`,
driven by the `pipeline-v1` CLI), not a service. It rides the existing harness: the
context files supply behavioral rules and user preferences, recall supplies the DDD, CDF
takes the REFLECT report, and the desktop shows runs and decision cards. A change to the
pipeline is a change to the stage cards or the engine, tested like any other code.

Rollout is continuous: the pipeline is its own first customer. The profile system is the
graceful-degradation dial (a small, unambiguous change gets `direct`, a feature gets
`full`), so there is no big-bang surface to roll out.

## 6. Open Questions

| # | Question | Suggested approach |
|---|----------|--------------------|
| Q1 | The diagrams (`docs/diagrams-pipeline/*.svg`) show the retired pipeline. Redraw or delete? | Redraw one figure from `PROFILE_STAGES` and the landing checks; delete the rest. The prose and the code are the source. |
| Q2 | Security review depends on the change being declared security-relevant. Is that strong enough? | Watch the record: if a security finding reaches main on a change that was not declared, add the missing rows to the repo's `.swarmai/review.md` rather than a built-in path rule. |
| Q3 | Gate-0 runs only for bugfixes and ambiguous requests. Do framing errors slip through on full runs with one reading? | Count runs whose REVIEW or TEST found a framing error; widen Gate-0 only if that recurs. |

## Maps to the 5 criteria

Sections 1-2 make the doc self-contained and state the problem + why (criteria 1-2). Section
3 records every major decision with its alternatives and rationale (criterion 3). Section
4.3 names the real failure modes with their mitigations (criterion 4). Section 6 surfaces
the open questions rather than hiding them (criterion 5).
