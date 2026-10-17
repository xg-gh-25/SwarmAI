# EVALUATE — history and rationale
Not read at runtime. The execution card is `stages/evaluate.md`; conditional detail is in
`stages/ref/evaluate/`. This file keeps the "why" behind EVALUATE's rules, for humans and for
the evolution pipeline.
## Why the Requirement Clarification Check exists
It ports spec-kit's `clarify` pattern: an AI proactively finding spec gaps is higher-yield than
human review. Without it, EVALUATE scores a vague requirement as GO, the ACs come out
under-specified, and BUILD delivers something technically correct but wrong.
**Phase 2 upgrade:** the ≥2-ambiguity exit used to be a terminal dead-end (ESCALATE →
checkpoint → the user re-opens a new run). It became a resumable MCQ loop that keeps the run
alive. The common self-answerable path is untouched; only the genuinely unanswerable residue
reaches the user.
## Why the ambiguity re-scan is self-answering and validator-enforced
The source repos it borrows from (aws-samples/sample-ai-plc, awslabs/aidlc-workflows) question
the *human* Socratically. SwarmAI is autonomous, so it interrogates the requirement and its own
framing, self-answers from code/DDD, and escalates only what is unknowable. It is mechanical and
validator-enforced because the older behavioral grill protocol (see the THINK history) was
almost always skipped.
## Why the Understanding Gate covers every work type
The hard part of most runs is FRAMING, and it happens at EVALUATE before any code. One run
framed a **feature** change as "frontend ignores [DONE]", but `chat.ts` *already* treated
`[DONE]` as authoritative, so the "fix" was a **no-op**. The understanding of the existing
system was wrong, and the earlier bug-only REPRO gate would never have caught it.
Understanding error crosses every work type, so the gate does too. The bug-class REPRO gate came
from runs where the fix that worked came from OBSERVING (ps, log-signal counts, live gauges),
never from reading code and inferring.
## Why the skeptic gets a conclusion, not a research assignment
A skeptic prompt that carried a long list of numbered "verify this yourself" sub-questions ran
for a very long time and found little. The same claim restated as "confirm or refute these 3
specific things" returned in seconds and still caught errors the orchestrator missed. Hence
the bounded item count and the ban on "prove nothing is wrong".
## Why `migration_class` is mandatory on a keyword
A goal run migrated a class of callers across cycles and shipped the `decisions` write path
ungated: no cycle touched that member, so it was in no diff and no per-cycle adversarial saw
it. The class-completeness gate fixes that only when the run declares the class, and opt-in
was the escape hatch (an incomplete mental model simply omits it). A hand-listed member set
inherits the same blind spot, which is why `enumeration_cmd` must grep a physical sink.
## Why the Subsystem Health Audit exists
A Hive run fixed 5 specific bugs. A 15-minute E2E audit after the fix found 15 MORE structural
gaps in the same subsystem. The pipeline would never have found them because it only reviewed
the diff. The audit cost 15 minutes; fixing those gaps one by one over time would have cost
about 15 hours.
## Why Cross-Boundary Classification exists
An overlay re-architecture run migrated 4 surfaces off the legacy `useExclusiveOverlay`
window-event bus onto a new host. **Every unit test passed.** The migration still severed the
agent's proprioception contract on BOTH halves: the `swarm:show-<id>` ACT vocabulary became
close-only (it could not OPEN a migrated surface) and the `active_overlay` SENSE payload read a
now-dead singleton (it could not SEE it). The mandatory adversarial gate caught it; no E2E test
did, because none drove the real registry end to end. A migration's visible 20% is mounting
the new component; the invisible 80% is re-homing the event dispatchers and state readers, and
only a real-system E2E proves both halves reconnected. That is the hole this classification
plus TEST Layer 4 close. The `false` branch needs `ruled_out` because the self-exemption
temptation fires hardest on exactly such a run.
## Why the Anti-Repetition Check exists
It is the ΩmegaWiki "anti-repetition memory" pattern. IMPROVEMENT.md accumulated 40+ failure
entries over 3 months, and without active cross-referencing the same patterns recurred (a
big-bang refactor; a daemon hang three times from the same root cause). The check costs 30
seconds of reading; re-discovering a failure costs hours.
## Why goal is the last profile gate
The old tree asked "can 'done' be verified by a shell command (exit 0)?" FIRST and routed any
YES to goal. Almost every code change has a test that exits 0, so that catch-all sent even a
3-line fix to goal, the heaviest profile (build+test loop per cycle). One fix of 3 stale tests
paid for a full goal run that way. The size/shape gates now come first, and goal requires a
genuinely iterative target.
## Why the Working-Backwards lens self-answers
A greenfield GO can be technically sound yet build something nobody adopts. The lens ports
Amazon Working-Backwards / the aws-samples PR-FAQ pattern, adapted to an autonomous pipeline:
the agent self-answers and the human confirms at REVIEW. A blocking file-based user interview
was rejected because that is the grill-protocol / aidlc-#366 rubber-stamp failure.
`target_customer` was dropped from enforcement because the greenfield Understanding row already
captures "who has it".
## Why structured requirements are `[MUST]`, not `[GUIDE]`
The producer-wiring run found that the three glass-box consumers (REPORT §4.6 audit, the
validator WARN layer, the real-time inline emit) all read EVALUATE's `requirements[]`, and an
EVALUATE that shipped only free-text ACs left all three dark. The publish example also had to
carry concrete SRS fields, because agents copy the command, not the prose.
## Common rationalizations
| Rationalization | Reality |
|---|---|
| "This is obviously a GO, skip the full scoring" | "Obvious" tasks have conflicted with non-goals (3x), duplicated prior failed work (2x), and been mis-scoped as trivial when they were standard. Full scoring takes 30 seconds. |
| "Scope is trivial — I know this pattern" | Scope determines profile (full/trivial/bugfix). Wrong scope = wrong quality gates applied downstream. A 3-file, 2-function change was called "trivial" → skipped adversarial review → shipped broken. |
| "The requirement is clear enough, skip clarification" | Vague requirements scored as GO produce under-specified acceptance criteria. The pipeline builds something that passes but misses the real need. 10 minutes clarifying saves 2 hours building wrong. |
| "DDD docs are stale, skip consistency check" | Stale DDD docs = stale constraints. If you skip the check, you may violate a non-goal or repeat a failed pattern. The check surfaces this; skipping hides it. |
## What the split dropped
- The long-form continuation contract after the publish block: it repeats the orchestrator's
  continuation contract in INSTRUCTIONS; the card keeps the short form with the legal
  hand-backs.
- The "Base Methodology" heading: folded into card step 3 (same pointer to
  `backend/skills/s_evaluate/SKILL.md`).
- A line-number pointer into INSTRUCTIONS for "terminal verdict": replaced by the rule's name.
- Fill colors in the profile decision graph (cosmetic).
