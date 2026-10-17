# PLAN — history and rationale

Not read at runtime. The execution card is `stages/plan.md`; conditional detail is in
`stages/ref/plan/`. This file keeps the "why" behind PLAN's rules, for humans and for the
evolution pipeline.

## Why exhaustive file discovery

Inspired by Sweep's scored file-localization loop: "search exhaustively, then plan", not
"plan, then hope you didn't miss a file." Multiple pipeline runs reached BUILD only to find
the planned interface didn't exist, there were five more callers than expected, or an existing
utility already handled the need. Discovery in PLAN costs about 3 minutes; finding the gap in
BUILD costs about 20 minutes of backtracking.

## Why a change spec

Inspired by Sweep's "Issue Sub-Request Decomposition": each sub-change maps to one file and
function, ordered by dependency. BUILD's TDD loop works best with a clear "what to do next".
Without a change spec the agent re-derives the sequencing each time and often picks the wrong
order (implementing a consumer before its provider), burning TDD time on logistics.

## Why a test strategy at PLAN

BUILD's RED phase is supposed to be "write test, watch it fail". In practice it was often
"spend 15 minutes on fixture setup, then write the test". Moving the HOW into PLAN lets BUILD
start writing tests immediately.

## Why three-tier boundaries

They prevent the most expensive bug class: building the wrong thing because the agent made an
assumption the user didn't intend.

## Why the security block is verification, not enumeration

Enumerating threats against an unwritten design hallucinates. Naming a trust assumption and
citing the code that enforces it is bounded and checkable, and it is the only security check
that runs before code exists.

## Why the exit contract is written at PLAN

From a direct fix that ran without the pipeline: after the first version was broken by the red
team and rewritten (correct), the agent kept fuzzing to "prove it perfect": about ten rounds
of 60,000 random commands each, 4–5 minutes per call, over 40 minutes. The stop signal (the
rewrite's fuzz finding nothing new) was ignored. One round was wasted because a `sed` edit
that extended the alphabet silently did not apply on macOS, and nobody noticed. Mutation
testing was repeated four times because the script did not isolate the change under test and
was then cut short by `--maxfail`. With the end point fixed first (every counter-example a
regression test, gate tests green, one clean fuzz round) it takes about 15 minutes. Root cause:
the end point was not decided in advance and heavy verification was ad-hoc scripts with no
record and no convergence judgment. Source: `docs/design/pipeline-speed-systematic-fix.md` §G.

## Doc-code drift corrections (AC4)

Earlier wording said the PLAN validator checks all five of `boundaries`, `success_criteria`,
`file_discovery`, `change_spec`, `test_strategy`. It does not: only `acceptance_criteria` is
required at publish; `boundaries`/`success_criteria` are recommended (WARN); the other three
are agent discipline. The sections were relabelled `[MUST]` by discipline rather than "gate".
Since the stage-complete work, `exit_criteria` is also required at plan stage-complete on
strict profiles.

## Common rationalizations

| Rationalization | Reality |
|---|---|
| "I can figure out the files as I build" | Missing one caller = surprise regression in BUILD. One run extracted a function and added a new caller in one commit, and missed 2 bugs visible only in the new calling context. Discovery BEFORE planning shows all affected surfaces. |
| "Boundaries are implicit — everyone knows not to touch X" | Implicit boundaries = silent drift. "Never" boundaries caught 3 unauthorized scope expansions in prior runs. If it isn't written, it isn't enforced. |
| "Acceptance criteria are the same as the requirement" | Requirements say WHAT; ACs say WHEN DONE (testable, binary, per scenario). "Add validation" has 5+ ACs (each invalid input × expected response). Without ACs you build until you "feel done". |
| "Test strategy is obvious — just test the happy path" | Every change X in the design doc needs a matching test. No strategy in PLAN = untested edge cases in BUILD = bugs found in REVIEW = rework. Strategy upfront costs 2 minutes; rework costs 30. |
