# GOAL_CYCLE — history and rationale (never read at runtime)
Why the goal_cycle stage looks the way it does. The runtime rules live in
`stages/goal_cycle.md` (card) and `stages/ref/goal_cycle/*.md`. Nothing here is a rule;
if something here reads like one, it is already in the card or a ref.
## The 2026 split (design `docs/design/pipeline-speed-systematic-fix.md` §4-B, §4-C, §4-G)
The old `stages/goal_cycle.md` (~28.5K chars) was read in full on every entry. It became
a ~5K execution card plus five refs:
| Old section | Now |
|---|---|
| Pre-Cycle Setup, DoD Quality Rules 1-3, step 9.5 GoalMetrics | `ref/goal_cycle/setup.md` |
| Step 10 Periodic REVIEW, 10.5 re-judgment, Progress File Format | `ref/goal_cycle/findings-ledger.md` |
| Final Quality Gate 0-4, 2.5 class-completeness, 2.6 Layer-4 E2E, artifact shape | `ref/goal_cycle/final-gate.md` |
| Regression Protocol, Exit Conditions (CHECKPOINT/STOP/REVERT_LIMIT/BUDGET), budget note | `ref/goal_cycle/exits.md` |
| Scheduled Mode, Inline vs Scheduled Decision | `ref/goal_cycle/scheduled.md` |
| Per-cycle steps 1-12 (renumbered 1-11 on the card; budget note folded into stop rules) | card |
| "REFLECT (Two-Tier)" | dropped: duplicate of `stages/ref/reflect/goal-mode.md` |
The Scheduled Mode job template is also summarized in INSTRUCTIONS.md (Goal Profile
Orchestration); the fuller SwarmAI-self version with the other-project caveat stays in
`ref/goal_cycle/scheduled.md`, so nothing was dropped from it.
Command translations made in the split:
| Old command | Now |
|---|---|
| `pytest tests/test_<module>.py -v --timeout=60` (step 7) | `pipeline_test.py --mode verify` (changed tests + importers) |
| RED/GREEN by hand inside step 6 | `pipeline_test.py --mode tdd --files <tests>` (max 5) |
| re-run TEST in the Regression Protocol | `pipeline_test.py --mode recheck` |
| revert the contract line by hand for 2.6 | `pipeline_test.py --mode mutation --mutations <spec.json>` |
| ad-hoc fuzz / replay / e2e scripts | `pipeline_test.py --mode probe --name <n> -- <argv>` (converges on a 0-new-findings round) |
| DoD criterion that is a test command | the runner (raw pytest/vitest/tsc is allowed but does not count as evidence) |
| `publish` + `run-update --stage-json` + `advance` | `stage-complete --stage goal_cycle --data @/tmp/<R>/goal_cycle.json` |
The record-level `adversarial_review: true` survives because the goal-profile
completion gate in `artifact_cli.py run-update --status completed` reads the goal_cycle
stage RECORD, while the validator's STAGE_SCHEMAS/STAGE_DEPTH read the artifact.
## Why the DoD Quality Rules exist
A goal run delivered code that passed all 4 positive DoD criteria but had 4 undetected
gaps: an untested error path, failure propagation, a scope mismatch and orphan
accumulation. A single negative test ("what happens when the server is down?") would
have caught 3 of the 4 (Rule 1). The same run had 4 DoD criteria, all exercising the
default path; the `frontend-only` scope path was completely untested and could have been
syntactically broken with the DoD still passing (Rule 2). Existential checks
(`grep -q function_name`) pass on dead code (Rule 3).
## Why the cross-path adversarial prompt exists
In that run the adversarial pass found 5 single-file bugs but missed the 4 cross-path
gaps above. A standard adversarial asks "is this code correct?"; the cross-path prompt
asks "is this code correct for all its uses?".
## Why the Class-Completeness Gate exists
The cross-path adversarial is diff-scoped, so a class member no cycle touched is
invisible to it. A migration goal shipped the `decisions` write path ungated while all 7
per-cycle adversarials passed. The gate enumerates the whole class from live source.
## Why Layer 4 lives inside goal_cycle
The goal profile has no standalone `test` stage, so TEST's Layer 4 cross-boundary E2E
was unreachable under goal and a cross-boundary goal run skipped the real-seam E2E
entirely. Step 2.6 restores it with the same contract, and the completion gate scans
every stage artifact for `cross_boundary_e2e.run`, so the profile cannot route around it.
## Why the Findings Ledger is re-judged every cadence
A fresh adversarial on the total diff has no memory that a finding was raised and marked
resolved, so "fixed in cycle 3, regressed in cycle 7" reads as clean. Re-judging every
known finding against the latest diff catches it. Gate-1 then found the first version
only updated a markdown table, which changed nothing: the progress file never reaches
`_blocked_findings`. Hence the carry-forward rule into DELIVER `adversarial_review.findings[]`.
## Why budget is not a loop exit
The original step 1 exited the loop with BUDGET when `remaining < 150K` tokens. It was
removed (STEERING #2; XG: budget should never be a major factor in blocking a run). A goal
loop ends on DoD met, Stuck Detection, max cycles or a true-trigger checkpoint; physical
context overflow is SDK autocompact's job. The BUDGET exit survives only for the rare
case autocompact cannot reclaim context, and its `status="budget"` label is kept for
metrics continuity.
## Why the final spawn says `run_in_background: false`
The Agent tool defaults to background, and a background spawn ends the parent's visible
stream: the user sees nothing running. `test_pipeline_spawn_foreground.py` requires the
directive in every doc that spawns a sub-agent.
