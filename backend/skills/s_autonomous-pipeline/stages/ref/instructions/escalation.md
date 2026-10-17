# INSTRUCTIONS ref — Escalation Routing Protocol (full) + 3d. Classify Decisions
Read when: a decision might be L2 Judgment, you must tell STAGE-BOUNDARY from MID-STAGE, the
`channel=` header is absent or not `direct`, an EVALUATE clarification is pending, or an in-band
ask timed out. The card carries the summary tree.

**Single source of truth for WHERE a human-escalation goes.** Every human-escalation exit (L2
Judgment at 3d/3f, and the mid-stage exits in build.md / deliver.md) routes through this protocol.
Principle (SwarmAI PRODUCT.md Design Philosophy "HITL & 心流" + AGENT.md Escalation): **the signal
lands in the user's CURRENT chat tab, in-band — never a passive next-session briefing panel the
user has to dig a command out of.**

## 3d. Classify Decisions
Every non-trivial decision during stage execution MUST be classified:

| Class | Definition | Action | Example |
|---|---|---|---|
| **Mechanical** | One correct answer, deterministic | L0 INFORM, auto-approve | "Use pytest (pyproject.toml)" |
| **Taste** | Reasonable default, a human might differ | L1 CONSULT; accumulate for the delivery Taste Decision Gate | "Monolith over microservice for solo dev" |
| **Judgment** | Genuinely ambiguous AND hard to reverse, needs a human | L2 BLOCK → route via this protocol (in-band ask if `channel=direct`, else checkpoint) — never auto-checkpoint | "This changes the public API" |

Log each decision in the stage record (`record.decisions` for stage-complete):
```json
{"stage": "build", "description": "Used sync retry instead of async", "classification": "taste",
 "reasoning": "Matches existing codebase style, simpler, but async would be more correct"}
```

## When this fires
A **human-escalation** is any point where the pipeline genuinely needs the user to decide (L2
Judgment) — NOT a Taste decision (those self-resolve, see the over-ask guard below).

| Location | Examples | Routing |
|----------|----------|---------|
| **STAGE-BOUNDARY** (the stage's artifact is published + validated, no stage mid-execution) | a 3d Judgment / 3f decision raised *after* the current stage's artifact is published | **In-band eligible** (decision tree) |
| **MID-STAGE** (the current stage has uncommitted / half-written run state) | build.md replan-fail (mid-TDD), deliver.md meta-review HIGH (mid-convergence), OR a 3d Judgment discovered *during* a stage before its artifact is published | **ALWAYS checkpoint** (Mid-stage rule) |

**Decidable test (apply the test, do not match by name):** MID-STAGE if the current stage has
uncommitted state (partial `replanned_acs`, an in-flight convergence iteration, a
started-but-unpublished artifact). STAGE-BOUNDARY only if the prior stage's artifact is published +
validated and no stage is mid-execution. **When in doubt → mid-stage → checkpoint.** (A 3d
Judgment can surface mid-BUILD — that is MID-STAGE despite 3d's name.)

**DEFER/REJECT/ESCALATE at EVALUATE is NOT routed here:** it is a terminal verdict, not a
continue-the-same-run handoff, governed by `evaluate.md` Exit Routing + Rule 11 (DEFER/REJECT →
pipeline ends; ESCALATE → checkpoint). Do NOT in-band a DEFER.

**CLARIFICATION-ESCALATE at EVALUATE *IS* routed here (do not conflate).** The requirement is worth
doing but has an unanswered ambiguity (≥2 un-self-answerable, or a blocking unknown such as a
`work_class` that can't be derived). EVALUATE publishes its artifact with the ambiguous requirement
at `status: needs-clarification` + a pending `clarifications[]` entry; the ambiguity is a
**STAGE-BOUNDARY L2** (the artifact IS published). Same decision tree: `channel=direct` → in-band
MCQ (`AskUserQuestion`, forced `X. Other`) → on answer write back `clarifications[]`
(`answered_by: user`), upgrade the requirement's `source.kind`→`user-stated` and
`status`→`confirmed`, then **continue the SAME run at EVALUATE** (`AskUserQuestion` gate +
`continue_with_answer`, interactive session only). `channel≠direct` / headless → checkpoint
(free-text reason) + paused-run item, NO timeout (STEERING #2 — a clarification wait is not a
hang); the user re-engages an interactive session to answer, then resumes (a true headless
`awaiting-input` serialize-and-resume carrier is not wired yet). Discriminator: is the requirement
worth doing (clarification-escalate → route) or not (DEFER/REJECT/terminal-ESCALATE → never
in-band)? Governed by `evaluate.md` Exit conditions + Exit Routing.

## Decision tree (STAGE-BOUNDARY escalations only)
```
STAGE-BOUNDARY human-escalation (L2 Judgment)
  ├─ Read the `channel=` field in your own system-prompt runtime header.
  │     (system_prompt.py emits `channel=direct` for a desktop chat tab; a channel session emits
  │      `channel=slack` etc.; a background-JOB pipeline has NO runtime header — field ABSENT.)
  ├─ channel=direct AND header present → IN-BAND ASK
  │     • Call AskUserQuestion IN THE CURRENT TURN, the decision framed as options
  │       (A/B/C + a short "why each").
  │     • ask_question_gate blocks up to 4h (WAITING_INPUT is eviction-protected); the user
  │       answers in THIS tab (front tab directly, background tab via the "Swarm is asking"
  │       toast → click → answer).
  │     • On answer → CONTINUE THE SAME RUN AT THE SAME STAGE. No checkpoint, no new run, no
  │       advance/rewind — the run stays `running`; the answer is a tool result you act on.
  │     • On 4h TIMEOUT (gate returns TIMEOUT_SENTINEL, never a fabricated empty answer)
  │       → FALLBACK CHECKPOINT below.
  └─ channel≠direct OR header ABSENT (background job / headless / unknown)
        → FALLBACK CHECKPOINT (fail-safe — the default when not provably desktop)
        • Do NOT call AskUserQuestion: a channel session AUTO-ANSWERS it with the first option
          (gateway.py), silently faking an L2 decision; a headless `claude --print` job has no
          human and would HANG to the job timeout.
        • Emit a one-line warning IN THE CURRENT TAB naming the decision point, then
          run-checkpoint (status=paused, decision recorded). NEVER auto-archive the run
          (archival requires an owner-death observation — not the pipeline's call).
        • Suggest in-tab: "resume in a desktop session to decide" — do not rely on the
          briefing to carry the action.
```
In-band ask is only safe and meaningful when a human attends the tab; `channel=direct` +
header-present is the ONLY state that proves it. Anything else → checkpoint.

## Mid-stage rule (build.md / deliver.md exits)
A MID-STAGE escalation (replan-fail inside BUILD's TDD loop; meta-review HIGH inside DELIVER's
convergence loop) **always checkpoints — never in-band.** Blocking a 4h question mid-stage would
freeze a half-written stage (partial `replanned_acs`, in-flight convergence iteration count) that
the per-stage validator was never designed to resume. Reach a stage boundary first; only
boundary-level L2 is in-band eligible. The decision tree does NOT apply mid-stage.

## Taste vs L2 — do NOT over-raise (the over-ask guard)
Escalation is scarce; every raise breaks the user's flow. Default to deciding.

| Class | Handling | Raises? |
|-------|----------|:-------:|
| **Mechanical** (one correct answer) | just do it | no |
| **Taste** (a sensible default exists, a human might differ) | decide the default + disclose in ONE line in the current tab ("chose X because Y; say so to override"); accumulate for the Delivery Taste Gate | no |
| **Judgment / L2** (genuinely ambiguous AND hard to reverse — a public API change, a one-way data migration) | route through the decision tree | yes |

**Worked counter-example:** "I lean toward retry-with-backoff but want to confirm" is **Taste, not
L2** — there is a sensible default (your leaning) and it is reversible. Decide, disclose one line,
do NOT call AskUserQuestion. Raising it transfers your judgment cost onto the user (判断 ≠ 甩给人).
Disclosure is ONE line, never a paragraph — a wall of decision notes is its own flow-pollution.
