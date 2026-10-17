# INSTRUCTIONS — history and rationale
Not read at runtime. The orchestration card is `INSTRUCTIONS.md`; conditional detail is in
`stages/ref/instructions/`. This file keeps the "why" behind the orchestrator's rules and the
incidents that produced them, for humans and for the evolution pipeline.

## Why the card was split
INSTRUCTIONS.md had grown to ~96K characters and was re-read on every run, so most of each run's
orchestration context went to rules that did not apply to the current stage. The split
(`docs/design/pipeline-speed-systematic-fix.md` §4-C) keeps a ≤10K orchestration card (core loop,
profiles, stage-start / stage-complete, escalation, legal hand-backs, landmarks) and moves
conditional detail to `stages/ref/instructions/`. Every tagged rule (`R-instr-NNN`) stays live in
the card or a ref; `scripts/check_rule_preservation.py` proves it.

What changed in meaning during the split (deliberate, not loss):
- §3c "Read ALL files in the Read column" became "read the execution card; read `stages/ref/` only
  when the card's condition holds" (design C2). The review row no longer requires a full read of
  REVIEW_PATTERNS.md / OPERATIONAL_PATTERNS.md: `spawn_prompt.py --patterns-for` emits the
  triggered rows, and the review refs say when to open OPERATIONAL_PATTERNS.md.
- `token_cost`, `consumed_artifacts`, `stage_doc_consumed`, test layers and finding counts are
  derived by `stage-complete`; the estimation formula survives only for the legacy run-update path.
- Manual `run-observe stage_start/stage_end` calls are only needed on the legacy path
  (`stage-start` / `stage-complete` record stage times).
- The 6-layer gate's L1/L3 verification moved from raw `pytest` commands to `pipeline_test.py`
  modes, matching the `pipeline_test_gate` hook.
- The profile table gained `direct` (plan → build → deliver), the profile for a direct fix.

## Why a per-stage feedback loop (SIGNAL / CHECK / FAIL)
Bugs become mechanical work when the feedback loop exists BEFORE execution. Without pre-defined
signals a stage passes on "vibes": the builder feels done but has no external evidence. Writing
the signal first takes about 30 seconds and prevents the "tests pass but the feature doesn't work"
class of rework. The per-stage loops are shift-left prevention; the Quality Convergence Loop is
the final gate for defects that only appear at system level (a rough 80/20 split between them).

## Why "goal" is chosen by iteration, not verifiability
An earlier heuristic routed any requirement whose result "a command exits 0 when done" could
check into the goal profile. Almost every change can be wrapped in such a check, so a 3-line fix
was mis-routed to goal, the heaviest profile. The test became: must you ITERATE toward a moving
target (a metric, a sweep, convergence)? EVALUATE's decision tree evaluates the size/bug gates
first and goal last for the same reason.

## Why "10 landmarks" and "≤8 stage entries" coexist with "9 stages"
Three counts kept drifting apart in docs: 9 architectural stages, ≤8 orchestration rows per
profile (ADVERSARIAL runs inside DELIVER), and 10 display landmarks (Gate 1 has its own ④). They
are all correct; each is a different projection of the same architecture. The ④/⑧ numbering is
wired into the golden-set trajectory case and tests, so it must not be renumbered.

## Why budget is never a checkpoint trigger
Every checkpoint costs a full session-start overhead (~15K tokens of context reload) and breaks
momentum. Prior runs checkpointed at PLAN→BUILD "because BUILD is big" with only ~10% of the
budget consumed, splitting work for no reason. With 1M context (SESSION_BUDGET=800K) a full run
(historically ~230K tokens) fits in one session. XG's directive: "budget 从来不应该是 block run 的
重要因素" (STEERING #2). `should_checkpoint` is therefore always false and `run-checkpoint` refuses
budget-worded reasons (the confabulation denylist).

## Why the Escalation Routing Protocol fails safe to checkpoint
The user does not operate pipelines from a next-session briefing ("I 不可能去 briefing 里操作
pipeline"), so escalations land in the current tab. But an in-band ask is safe only when a human
is attending: a channel session auto-answers AskUserQuestion with the first option (silently
faking an L2 decision) and a headless job would hang to its timeout. An earlier draft that said
just "L2 → ask in-band" would have regressed the background-job path; a plan-gate review caught
it. Mid-stage asks are excluded because the per-stage validator was never designed to resume a
half-written stage after a 4h block.

## Why the continuation contract is stated as a rule
The stop side of the protocol is code-enforced (completion gate, checkpoint gate); the continue
side was only prose. Observed failure: the orchestrator ran `advance`, wrote "proceeding to
<STAGE>", and ended its turn (`stop_reason=end_turn`, zero tool calls in that message) with no
gate having fired. `advance` and `stage-complete` now carry `next_action` with a shared phrase
(`_CONTINUATION_PHRASE`), mirrored in every stage card, so CLI and docs cannot drift.

## Why L4 verifies findings against disk
One delivery shipped an artifact marking a hardening fix `resolved: true` after a mid-pipeline git
conflict had silently reverted it. The function had no test coverage, so nothing else caught it;
a principal-engineer review found it by reading the file. The `resolved` flag records intent, not
state, so the validator now greps a structured `disk_check` at publish and completion time. Cost:
seconds per finding.

## Why Rule 22 (stage metrics are the audit trail)
A REPORT.md shipped as an empty stub because the stage records carried no metrics. The chat had
shown "198 tests pass, 2 files +58/-4", but none of it was in run.json, and the report is
generated from run.json. Chat, run.json and REPORT.md must agree.

## Why Rule 23 (spawned adversarial review, two-field evidence, bounded prompts)
- Two runs declared `spawned=true` without any Agent tool invocation. A single boolean is trivially
  bypassable, so the validator requires `evidence` too, backed by a golden-set trajectory case.
- The bounded-spawn rules were stated in docs twice and still did not reach the prompts, because
  prompts were hand-composed and a rule only reaches a prompt the author copies. The three gate
  prompts now live in `data/gate-prompts/` and `spawn_prompt.py` refuses (exit 2, empty stdout) to
  emit one without a numeric cap. `backend/tests/test_deliver_template_drift.py` pins the
  caller-side authority block on the card.
- Failure shape: a skeptic prompt with a long list of verify-this-yourself sub-questions searched
  for a very long time and found little; the same claim as three confirm-or-refute items returned
  in seconds and still caught errors the orchestrator missed. Longer reviews do find more, but the
  marginal return collapses well before an unbounded prompt gives up — bound the search, not the
  coverage.
- Spawn rejection: a prior `interrupt()` poisons the warm subprocess; an in-turn retry loop reuses
  it and loops harmfully (STEERING #1). Only a checkpoint→resume crosses the process boundary.
- Foreground spawns: the Agent tool defaults to background, which ended the visible chat stream
  (no status, verdict arriving later as a notification). `test_pipeline_spawn_foreground.py`
  sweeps every spawning doc for `run_in_background: false`.

## Why Rule 24 (one required-fields reference)
Required fields were split across `STAGE_SCHEMAS` / `STAGE_DEPTH` (artifact) and the
`cmd_run_update` flat gates (stage record), with no single list, so a stage failed and its field
was reverse-engineered from the error. The old Rule 24 was also wrong (it claimed think required
`alternatives`; a plan built to it omitted `acceptance_criteria` and broke BUILD's AC check). The
table was corrected to validator ground truth; `stage-start` examples now supersede it.

## Why COMPLETE runs `--status completed` before the report
`completed_at` is written only by `run-update --status completed`, and REPORT.md's `Duration:`
needs it. Running `run-report` first rendered `Duration: N/A` on 466 of 466 real reports even
though every run.json held a valid span. The completion branch now regenerates an auto-generated
report itself; reports predating the `report_autogenerated` flag (62 of 545 completed runs at the
time) need an explicit `run-report --force`.

## Why the summary spec is fresh-read from `stages/complete.md`
The output contract once lived inline ~750 lines above the point of use; it decayed out of the
attention window and the delivery summary "disappeared" from runs. Reading the card at the
decision point fixed it. The summary is now rendered by code (`_display_block`) and relayed
verbatim, so neither formatting nor numbers depend on the model.

## Why "no text before tool calls"
One-word fragments appeared at the start of visible lines before tool calls. They were not leaked
planning but a tool-call glitch (a corrupted call opener) that lands in pre-call text and is then
copied from history. With no pre-call text there is nowhere for it to land.

## Why the Abandon Protocol
Without capture at abandon time, roughly 15% of pipeline knowledge (partial BUILD work, explored
alternatives) was lost. The `--partial` summary lets a later run with a similar requirement skip
work already done.

## Why REPORT.md omits the uncertain
At commit time REPORT.md is a self-report and CI post-push is its independent auditor. A
precise-looking hand-fabricated value spends the reader's trust on a lie; omission is honest.
