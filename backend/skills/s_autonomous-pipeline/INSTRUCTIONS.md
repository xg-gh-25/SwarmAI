# Pipeline Orchestrator

Drive the full lifecycle pipeline from requirement to delivery. You ARE the
orchestrator -- execute each stage's behavior inline within this session, don't
invoke separate skills.

## Core Loop

For every pipeline run, follow this loop:

```
1. INIT     -- parse requirement, detect project, load or create pipeline run
2. PROFILE  -- select pipeline profile (full/trivial/research/docs/bugfix)
3. STAGE    -- for each stage in profile (evaluate → ... → deliver → reflect):
               a. Feedback Loop preamble (SIGNAL/CHECK/FAIL for this stage)
               b. Gate check (budget, escalations, retries)
               c. Load stage context (DDD docs + upstream artifacts)
               d. Execute stage behavior (read stage doc, then execute)
               e. Classify decisions (mechanical/taste/judgment)
               f. Verify output (artifact published + schema valid)
               g. Handle result (advance / retry / checkpoint)
4. DELIVER  -- Delivery Gate → Completion Audit → AC Verification → Adversarial Review →
               Quality Convergence Loop (6-layer gate × max 3 iterations) →
               push-ready or escalate. Then: Report, CI.
5. REFLECT  -- Read stages/reflect.md, execute: lessons → IMPROVEMENT.md → DDD loop closed
6. COMPLETE -- summarize, record metrics, final run state
```

---

## Step 1: INIT

### Starting a New Pipeline

Parse the user's message to extract:
- **Requirement:** one sentence to one paragraph describing what to build
- **Project:** detect from context (file paths, explicit mention, chat binding)

If no project detected, confirm with the user. Pipeline needs a project for
artifact storage (L1+).

**Create the pipeline run file:**

```bash
# Check current state
python backend/scripts/artifact_cli.py state --project <PROJECT>

# Check for existing paused pipeline
python backend/scripts/artifact_cli.py discover --project <PROJECT> --types checkpoint --full
```

If a paused pipeline exists for this project, ask: "Resume the existing pipeline
or start a new one?"

**Pipeline run state** is tracked in a JSON file:
```
Projects/<project>/.artifacts/runs/<id>/run.json
```

Create the initial run state:
```json
{
  "id": "run_<8-char-uuid>",
  "project": "<PROJECT>",
  "requirement": "<parsed requirement>",
  "profile": null,
  "status": "running",
  "stages": [],
  "taste_decisions": [],
  "created_at": "<ISO timestamp>",
  "updated_at": "<ISO timestamp>"
}
```

Write this file to `.artifacts/` and announce:
```
Pipeline started: <requirement> (run_<id>)
Project: <PROJECT>
```

### Resuming a Pipeline

When the user says "resume pipeline" or drags a pipeline Radar todo:

1. Read the checkpoint artifact: `discover --types checkpoint --full`
2. Load `runs/<id>/run.json` via `run-get`
3. Check pending escalations -- if any still open, report and wait
4. Skip completed stages, resume from the checkpoint stage
5. Announce:
```
Pipeline RESUMED: <requirement> (run_<id>)
Completed: evaluate, think, plan
Resuming from: build
```

---

## Step 2: PROFILE

After the evaluate stage runs (or from checkpoint), select the pipeline profile
based on the evaluation's scope classification:

| Scope | Profile | Stages |
|-------|---------|--------|
| standard, complex | **full** | evaluate, think, plan, build, review, test, deliver, reflect |
| trivial | **trivial** | evaluate, build, review, test, deliver, reflect |
| research-only | **research** | evaluate, think, reflect |
| docs-only | **docs** | evaluate, think, plan, deliver, reflect |
| bugfix | **bugfix** | evaluate, plan, build, review, test, deliver, reflect |
| goal (open-ended) | **goal** | evaluate, plan, goal_cycle |

If the evaluate stage doesn't classify scope (L0), default to **full**.
The user can override: "skip research, I know the approach" → switch to bugfix.

### Goal Profile Orchestration

When profile is `goal`, the pipeline operates differently after PLAN:

1. **EVALUATE** — standard + goal_mode detection (DoD criteria, max_cycles)
2. **PLAN** — standard (defines approach for achieving the goal, not per-step)
3. **GOAL_CYCLE** — the stage itself loops internally:
   - Each cycle: budget gate → DoD check → pick step → BUILD+TEST → progress → mini-reflect
   - Periodic REVIEW every N cycles on accumulated diff
   - Final ADVERSARIAL on total changeset when DoD met
   - Full REFLECT at goal completion (distills all mini-reflects)
   - See `stages/goal_cycle.md` for complete behavior

**No DELIVER stage** — goal profile doesn't produce a single "delivery candidate."
Instead, each cycle commits incremental progress. Quality is assured by:
- Per-cycle TEST (regression check)
- Periodic REVIEW (convention/pattern compliance)
- Final ADVERSARIAL (fresh-eyes attack on total changeset)

**Scheduled mode** (for goals spanning multiple sessions):
After EVALUATE+PLAN, if the user requests overnight/unattended execution or
EVALUATE estimates >10 cycles needed, offer scheduled mode:

```yaml
# Job template for scheduled goal execution
jobs:
  - id: goal-<slug>
    name: "<goal description>"
    type: agent_task
    schedule: "0 */4 * * *"
    enabled: true
    config:
      prompt: |
        Resume goal loop for <PROJECT>.
        Progress: <progress_path>
        1. Read progress → 2. Check DoD (if met: disable job, notify)
        3. Execute ONE cycle → 4. Save progress → exit
    safety:
      max_budget_usd: 1.50
      timeout_seconds: 600
```

---

## Step 3: STAGE EXECUTION

For each stage in the selected profile, execute in order.

### Stage Feedback Loop (Per-Stage Preamble)

**Before executing each stage, establish its feedback loop:**

```
1. SIGNAL — What observable output proves this stage succeeded?
   (artifact published, test passing, file exists, command returns expected)
2. CHECK  — How do I verify the signal is real, not assumed?
   (grep for the artifact, run the test, Read the file, execute the command)
3. FAIL   — What does failure look like? (Define explicitly so I recognize
   it immediately instead of rationalizing partial success)
```

**Why this exists:** Bugs become mechanical work when you build the right
feedback loop BEFORE executing. Without pre-defined signals, stages pass on
"vibes" — the builder FEELS done but has no external evidence. With signals,
every stage has a verifiable exit criterion that transforms quality from
probabilistic to deterministic.

**Example (BUILD stage):**
- SIGNAL: All acceptance criteria have a passing test (`pytest -k test_<feature>` exits 0)
- CHECK: Run the test. Read the output. Green = signal confirmed.
- FAIL: Any test red, any criterion without a test, any test that tests the wrong thing.

**Example (REVIEW stage):**
- SIGNAL: 0 high/medium findings in the adversarial review output
- CHECK: Count findings by severity in the sub-agent response
- FAIL: Any finding severity >= medium, or sub-agent returned vague non-findings

**Output the SIGNAL/CHECK/FAIL as inline text in chat** before executing the
stage. This makes the loop inspectable — the user can correct wrong signals
before you spend tokens executing against them.

This preamble takes 30 seconds. It prevents 30 minutes of rework. The loop is
not bureaucracy — it is the cheapest insurance against "tests pass but feature
doesn't work" (C011 pattern).

---

### 3a. Gate Check

Before executing, check:

```
1. Retry exhaustion?  → if stage retry_count >= max_retries → CHECKPOINT
2. Pending L2 BLOCK?  → if any prior escalation unresolved → CHECKPOINT
3. Pipeline cancelled? → EXIT
```

**Max retries per stage:**

| Stage | Max Retries |
|-------|-------------|
| evaluate | 2 |
| think | 2 |
| plan | 2 |
| build | 3 |
| review | 2 |
| test | 3 |
| deliver | 1 |
| reflect | 1 |

### 3b. Load Stage Context

**DDD documents (stage-scoped):**

| Stage | DDD Docs to Read |
|-------|-----------------|
| evaluate | PRODUCT.md, TECH.md, IMPROVEMENT.md, PROJECT.md |
| think | PRODUCT.md, IMPROVEMENT.md |
| plan | PRODUCT.md, PROJECT.md |
| build | TECH.md, PROJECT.md |
| review | TECH.md, IMPROVEMENT.md |
| test | TECH.md, IMPROVEMENT.md |
| deliver | PROJECT.md |
| reflect | IMPROVEMENT.md |

Read the listed DDD docs from `Projects/<PROJECT>/`. Skip any that don't exist
or contain only template placeholders.

**Upstream artifacts:**

```bash
python backend/scripts/artifact_cli.py discover --project <PROJECT> --types <comma-separated> --full
```

| Stage | Upstream Artifacts |
|-------|--------------------|
| evaluate | (none, or prior research) |
| think | evaluation |
| plan | evaluation, research |
| build | design_doc |
| review | changeset |
| test | changeset, design_doc, review |
| deliver | changeset, review, test_report |
| reflect | test_report, delivery |

### 3c. Execute Stage Behavior

**BLOCKING: Before executing ANY stage, you MUST Read ALL files listed
in the "Read" column below.** Skipping a file = skipping the stage's
quality gate = pipeline invariant violation. This is not optional.

All stage docs are in `backend/skills/s_autonomous-pipeline/stages/`.
Read from `backend/skills/` (source of truth), NOT `.claude/skills/`
(projected copy — may be stale within the same session).

| Stage | Read (BLOCKING) | Scripts to Run |
|-------|----------------|----------------|
| evaluate | `stages/evaluate.md` | — |
| think | `stages/think.md` | — |
| plan | `stages/plan.md` | — |
| build | `stages/build.md` | — |
| review | `stages/review.md` AND `REVIEW_PATTERNS.md` AND `OPERATIONAL_PATTERNS.md` | — |
| test | `stages/test.md` | `scripts/wtf_gate.py` |
| deliver | `stages/deliver.md` | `scripts/confidence_score.py` |
| reflect | `stages/reflect.md` | — |

After reading, execute the stage behavior inline in this session.
DO NOT invoke sibling skills via slash commands — you ARE the pipeline.

### 3d. Classify Decisions

**Every non-trivial decision during stage execution MUST be classified:**

| Classification | Definition | Action | Example |
|---|---|---|---|
| **Mechanical** | One correct answer, deterministic | L0 INFORM, auto-approve | "Use pytest (pyproject.toml)" |
| **Taste** | Reasonable default, human might differ | L1 CONSULT, accumulate for delivery gate | "Monolith over microservice for solo dev" |
| **Judgment** | Genuinely ambiguous, needs human | L2 BLOCK, checkpoint | "This changes the public API" |

Log each decision in the pipeline run state:
```json
{
  "stage": "build",
  "description": "Used sync retry instead of async",
  "classification": "taste",
  "reasoning": "Matches existing codebase style, simpler, but async would be more correct"
}
```

### 3e. Verify Stage Output (Pipeline Validator)

After execution, run the **pipeline validator** to structurally enforce invariants:

```bash
python backend/scripts/pipeline_validator.py check \
  --project <PROJECT> --run-id <RUN_ID> --stage <STAGE>
```

This checks 7 invariants automatically:

| # | Check | Severity | What It Catches |
|---|-------|----------|-----------------|
| 1 | **Stage order** | BLOCK | Skipped stages, out-of-order execution |
| 2 | **Artifact exists** | BLOCK | Missing artifact publish (except reflect) |
| 3 | **Artifact schema** | BLOCK/WARN | Required fields missing (BLOCK), recommended missing (WARN) |
| 4 | **Decision logged** | WARN | No decisions classified (except reflect/deliver) |
| 5 | **Budget recorded** | WARN | token_cost is 0 — needed for calibration |
| 6 | **Profile respected** | BLOCK | Stage not in selected profile |
| 7 | **DDD consistency** | WARN | Non-goals vs TECH.md architecture conflict, failed patterns not recorded, missing DDD docs, staleness since last run. Runs at EVALUATE stage only. |

**Response format:**
```json
{"valid": true, "stage": "evaluate", "errors": [], "warnings": [...],
 "checks_passed": 7, "checks_total": 7}
```

**IMPORTANT: Write checksums to run.json after EVALUATE.**
After the EVALUATE stage completes successfully, run `ddd-check` and store the checksums
in the run state so future staleness detection works:
```bash
# Get current checksums and write to run.json in one step
CHECKSUMS=$(python backend/scripts/pipeline_validator.py ddd-check --project <PROJECT> | python -c "import sys,json; print(json.dumps(json.load(sys.stdin)['checksums']))")
python backend/scripts/artifact_cli.py run-update --project <PROJECT> --run-id <RUN_ID> --ddd-checksums "$CHECKSUMS"
```

**Standalone DDD check** (no pipeline needed):
```bash
python backend/scripts/pipeline_validator.py ddd-check --project <PROJECT>
```
Returns non-goals, failed patterns, doc checksums, and any cross-doc warnings.

**Staleness check** (which completed runs are based on outdated DDD docs?):
```bash
python backend/scripts/pipeline_validator.py ddd-staleness --project <PROJECT>
```
Returns stale_runs (docs changed), fresh_runs (matching), untracked_runs (no checksums stored).
Exit code 1 if any stale runs found — useful for CI gates.

**Handle the result:**
- `valid: true` → advance to next stage. Log any warnings for delivery report.
- `valid: false` → fix the errors before advancing:
  - Missing artifact? Publish it.
  - Schema violation? Update the artifact data.
  - Stage order? You skipped a stage — go back.
  - Profile violation? Wrong stage for this profile — skip it.
- If fix attempts >= max_retries → **checkpoint** with all failure details.

**Full-run validation** (use at pipeline end or for debugging):
```bash
python backend/scripts/pipeline_validator.py summary \
  --project <PROJECT> --run-id <RUN_ID>
```

### 3f. Handle Result

After verification:

- **All mechanical decisions → advance** to next stage
- **Taste decisions found → log them**, advance (review at delivery gate)
- **Judgment decision → CHECKPOINT** immediately

---

## Step 4: DELIVER (includes Quality Convergence Loop)

The DELIVER step has 4 phases executed in order:
1. **Taste Decision Gate** — batch review of accumulated taste decisions
2. **Deliver stage execution** — read `stages/deliver.md`, run Completion Audit + Adversarial Review
3. **Quality Convergence Loop** — 6-layer push-ready gate, iterate until converged or escalate
4. **Report & CI** — generate REPORT.md, confidence score, final artifacts

---

### 4a. Taste Decision Gate

BEFORE executing deliver stage behavior, collect ALL taste decisions from ALL
prior stages and present them as a batch:

```
DELIVERY GATE -- <N> taste decisions for review:

  1. [THINK, T6]  Chose httpx built-in retry over tenacity (simpler, fewer deps — T6: simplest wins when tooling commoditizes)
  2. [BUILD]      Used sync retry instead of async (matches existing codebase style)
  3. [REVIEW, T5] Skipped type stub generation (low value for internal module — T5: encode judgment not ceremony)

  [Approve All]  [Override #1]  [Override #2]  [Override #3]  [Discuss]
```

**Thesis tagging:** When a taste decision is informed by a thesis from `Knowledge/Learned/THESIS.md`, tag it with `[STAGE, Txx]`. This makes taste decisions auditable — future review can trace why a choice was made. Not every taste decision maps to a thesis; only tag when the connection is real.

**If no taste decisions accumulated:** skip the gate, proceed to delivery.

**If user approves all:** proceed to delivery.

**If user overrides any:** re-run the affected stage with the override as a
constraint. This may cascade (overriding a THINK decision re-runs THINK, which
may change PLAN, which changes BUILD). Re-run the minimum set of affected
downstream stages.

**If user wants to discuss:** enter conversational mode. Once resolved, resume.

---

### 4b. Deliver Stage Execution

Read and execute `stages/deliver.md` inline. This runs:
- Fresh User Audit (P6)
- Completion Audit (verify deliverables match requirement)
- Adversarial Review Gate (spawn sub-agent — see deliver.md for profile-aware tiering)
- Confidence Scoring (`scripts/confidence_score.py`)

The adversarial review in deliver.md is the FIRST pass — it produces the initial
set of findings. The Quality Convergence Loop below re-verifies after fixes.

---

### 4c. Quality Convergence Loop

After deliver stage execution produces a delivery candidate (code written, tests
pass, adversarial review done, confidence scored), the Quality Convergence Loop
evaluates whether the candidate is truly push-ready — and iterates until it is,
or escalates.

**This is NOT a separate pipeline stage.** It does not produce its own artifact,
does not appear in the profile stage list, and is not checked by the validator.
It is an internal sub-loop of the DELIVER step that bridges "delivery candidate"
to "push-ready."

#### The 6-Layer Push-Ready Gate

ALL 6 layers must pass simultaneously. A candidate that passes 5/6 is not push-ready.

| Layer | What It Checks | How to Verify |
|-------|----------------|---------------|
| L1: Tests Pass | All new + existing tests green | `pytest --timeout=60` exits 0 |
| L2: Type-Safe | No type errors, linter clean | Type checker + linter (if configured) |
| L3: No Regressions | Pre-existing tests still pass | Run dependent test files (grep -rl pattern) |
| L4: Adversarial Clean | No critical/medium findings from deliver.md review | All findings fixed or explicitly accepted |
| L5: DDD Conformance | No violation of TECH.md constraints or IMPROVEMENT.md anti-patterns | Mechanical checklist extraction + per-item verification (see below) |
| L6: Decisions Resolved | All taste/judgment decisions surfaced | Decision log complete, no hidden choices |

**L4 clarification:** The adversarial sub-agent was already spawned in 4b (deliver
stage). L4 checks whether all its findings are resolved. Only re-spawn the
sub-agent if a convergence fix changes code that the original review didn't cover.

**L5 mechanism (Constitution Pattern):**
L5 is NOT an honor-system self-assessment. It is a mechanical extraction + verification:

1. **Extract checklist from TECH.md:**
   - "Runtime Traps" section → each trap becomes a MUST-NOT-VIOLATE item
   - "Design Principles" section → each principle becomes a SHOULD-FOLLOW item
   - "Constraints" section → each constraint becomes a MUST-NOT-VIOLATE item
2. **Extract anti-patterns from IMPROVEMENT.md:**
   - "What Failed" section → each failed approach becomes a MUST-NOT-REPEAT item
   - "Anti-Patterns" section (if exists) → same treatment
3. **Verify diff against checklist:**
   - For each MUST-NOT-VIOLATE: does the current diff introduce or rely on the
     prohibited pattern? Binary YES/NO.
   - For each MUST-NOT-REPEAT: does the current approach structurally resemble
     a previously failed approach? If yes → explain why it's different this time
     or flag as violation.
   - For each SHOULD-FOLLOW: does the diff align? Deviation is acceptable only
     with explicit justification logged in the decision record.
4. **Result:** List all violations. Any MUST violation = L5 FAIL. SHOULD deviations
   are noted but non-blocking if justified.

If the project has no TECH.md or IMPROVEMENT.md → L5 auto-passes (no constitution
to check against). This incentivizes maintaining DDD docs — richer docs = stronger
quality gates.

#### Convergence Behavior

```
LOOP (max 3 iterations):
  1. EVALUATE — Check all 6 gate layers. Collect failures.
  2. If ALL PASS + agent self-assessment positive + goal aligned → EXIT: push-ready
  3. If failures exist:
     a. Identify the SPECIFIC layer that failed
     b. Identify the SPECIFIC issue causing the failure
     c. Apply a MINIMAL, TARGETED fix (not a rewrite)
     d. Re-verify the ENTIRE gate (fix may introduce new failures)
  4. Increment iteration counter
```

**Key properties:**
- Fixes are TARGETED — the loop does not re-run all stages. It fixes the
  specific gap and re-verifies.
- Each iteration NARROWS the gap. If an iteration makes the gap wider
  (introduces more failures than it fixes), STOP and escalate.
- The adversarial sub-agent is only re-spawned when a fix introduces new code
  paths not covered by the original review — scoped to the delta, not the
  entire delivery.

#### Exit Conditions

The loop exits when ALL THREE are true:
1. **All 6 gate layers pass** — no quality gap remains
2. **Agent self-assessment positive** — "I am satisfied with this delivery"
3. **Task goal alignment confirmed** — "This solves what was asked"

If all three: declare push-ready. Proceed to Report & CI (4d).

#### Failure Mode (Max Iterations Exhausted)

If 3 iterations pass without convergence:
- **Do NOT ship** — quality standard not met
- **Escalate with precision:**
  ```
  CONVERGENCE FAILED after 3 iterations:
    Remaining failures: [L3: test_foo_bar regresses, L4: race condition in async path]
    Attempted fixes: [iteration 1: ..., iteration 2: ..., iteration 3: ...]
    Root cause hypothesis: [why this isn't converging]
    Recommendation: [fix manually / adjust requirement / accept known limitation]
  ```
- **CHECKPOINT** — human decides next step

#### Relationship to Stage Feedback Loops

The per-stage feedback loop (§3 preamble) is SHIFT-LEFT prevention — it catches
issues before they flow into the delivery candidate. The Quality Convergence Loop
is the FINAL GATE — it catches issues that escaped individual stages because each
stage only sees its own scope.

Together: per-stage loops prevent 80% of defects. Convergence loop catches the
remaining 20% that only emerge at the system level. Neither alone is sufficient.

---

### 4d. Report & CI

After the convergence loop declares push-ready:

1. Generate REPORT.md at `.artifacts/runs/<RUN_ID>/REPORT.md`
2. Record final confidence score (post-convergence — may be higher than initial)
3. Record convergence metadata in run.json:
   ```json
   {
     "convergence": {
       "iterations": 2,
       "initial_failures": ["L3", "L4"],
       "final_status": "push-ready"
     }
   }
   ```
4. Auto PR (full/bugfix profiles only):
   ```bash
   python backend/skills/s_autonomous-pipeline/scripts/pipeline_pr.py \
     --run-dir <run_dir>
   ```
   Record PR URL in run.json under `pr_result` field if successful.
   If profile is not full/bugfix → skip silently. If gh auth fails → warn, don't block.
5. Advance pipeline state to next stage (reflect)

---

## Step 5: REFLECT

After the Quality Convergence Loop declares push-ready and DELIVER advances
state to `reflect`, execute the REFLECT stage. This is NOT part of COMPLETE —
it's a full pipeline stage with its own execution behavior.

**Execution (BLOCKING):**

1. Read `backend/skills/s_autonomous-pipeline/stages/reflect.md`
2. Execute the 9-step methodology defined there inline
3. Run pipeline validator: `pipeline_validator.py check --stage reflect`
4. Update run.json with the reflect stage entry

**Why explicit:** REFLECT closes the DDD learning loop — it writes lessons to
IMPROVEMENT.md so future pipelines learn from this run. Without REFLECT, the
pipeline generates value but doesn't compound it. A pipeline without REFLECT
is a single-use tool, not a learning system.

**Gate:** The mechanical completion gate (`run-update --status completed`) will
REJECT completion if REFLECT is in the profile but has no stage entry. This is
enforced by the validator, not honor-system.

---

## Step 6: COMPLETE

After reflect stage:

1. Update pipeline run status to "completed"
2. Present the completion summary in chat:

```
Pipeline COMPLETE (run_<id>) -- <N> stages, <M> skipped, <K> escalations
Confidence: <score>/10

  TL;DR: <2-3 sentences: what was built, what problem it solves, what value
         it delivers. Written for someone who won't read the rest.>

  Artifacts:
    evaluation  -> art_xxxx (GO, ROI 4.2)
    research    -> art_xxxx (3 alternatives, chose: <approach>)
    design_doc  -> art_xxxx (<approach>, 5 acceptance criteria)
    changeset   -> art_xxxx (47 lines, 2 files, TDD: 5 red → all green)
    review      -> art_xxxx (clean, 0 findings)
    test_report -> art_xxxx (5/5 pass, 0 regressions)
    delivery    -> art_xxxx (PR ready, confidence 9/10)

  TDD: <N> criteria → <M> tests generated → <K> bugs caught → all green
  Decisions: <X> mechanical, <Y> taste (all approved), <Z> judgment
  Lessons: <N> written to IMPROVEMENT.md

  Report: .artifacts/runs/<run_id>/REPORT.md
```

3. Save the final pipeline-run JSON to `.artifacts/`
4. The REPORT.md (generated in DELIVER) is the permanent record — always
   saved to `.artifacts/runs/<RUN_ID>/REPORT.md` alongside the run.json

---

## Budget Tracking

### Before Each Stage

Check whether the next stage fits in the remaining budget:

```bash
python backend/scripts/artifact_cli.py run-budget --project <PROJECT> --run-id <RUN_ID>
```

This returns:
- `consumed`: total tokens used so far (from stage `token_cost` fields)
- `remaining`: session budget minus consumed
- `next_stage`: the next stage in the profile
- `next_stage_estimate`: calibrated token estimate for that stage
- `should_checkpoint`: true if budget is insufficient or >70% consumed
- `calibration_source`: "historical" (from past runs) or "defaults"

**If `should_checkpoint` is true → run the checkpoint protocol below.**

### After Each Stage

Update the stage's `token_cost` field in the pipeline run. Estimate from work done:

**Token estimation formula:**
```
token_cost = base_stage_cost
           + (ddd_docs_read * 2000)
           + (artifacts_consumed * 3500)
           + (lines_of_code_changed * 50)
           + (test_count * 200)
           + (tool_calls * 1500)
```

**Base stage costs (when no historical data):**

| Stage | Base | Typical Range | Notes |
|-------|------|---------------|-------|
| evaluate | 6K | 4-10K | DDD reads + scoring |
| think | 10K | 5-20K | Research + alternatives |
| plan | 8K | 5-15K | Design doc generation |
| build | 40K | 15-80K | TDD cycle: tests + code + verify |
| review | 15K | 8-25K | Code review + security scan |
| test | 25K | 10-50K | Run suite + fix failures |
| deliver | 20K | 8-50K | Audit + adversarial + convergence loop (max 3 iter) + report |
| reflect | 3K | 2-5K | Lesson extraction |

After 5+ completed runs, `run-history` provides calibrated averages per stage
(with 20% buffer). Historical data always overrides base estimates.

### Historical Calibration

Check past run costs to calibrate estimates:

```bash
python backend/scripts/artifact_cli.py run-history --project <PROJECT>
```

Returns per-stage averages from completed runs. The `run-create` command
automatically uses historical data (with 20% buffer) when available.

---

## Checkpoint Protocol

### ⚠️ BLOCKING: Budget Check Required Before ANY Checkpoint

**NEVER checkpoint based on "feeling" or "intuition" about context usage.**
Before every checkpoint, you MUST run:
```bash
python backend/scripts/artifact_cli.py run-budget --project <PROJECT> --run-id <RUN_ID>
```

**Only checkpoint if `should_checkpoint: true` in the response** OR one of the
non-budget triggers below fires. With 1M context (SESSION_BUDGET=800K), a full
pipeline (evaluate+think+plan+build+review+test+deliver+reflect) fits comfortably
in ONE session. Historical average is ~230K tokens for a full run.

**Why this rule exists:** Every checkpoint costs a full session-start overhead
(~15K tokens for context reload) and breaks agent momentum. Prior runs checkpointed
at PLAN→BUILD "because BUILD is big" but budget was only 10% consumed. This wasted
user time and split work unnecessarily.

### When to Checkpoint

Checkpoint (pause the pipeline) when ANY of:
- L2 BLOCK escalation (judgment decision)
- Stage retry exhaustion (>= max_retries failures)
- Budget insufficient for next stage (`run-budget` returns `should_checkpoint: true`)
- Pipeline error (unexpected failure)

**NOT valid reasons to checkpoint:**
- "BUILD is a big stage" (it's ~60K tokens, you have 800K)
- "I've read a lot of files" (file reads are cheap, ~2K per file)
- "Context might be getting full" (run `run-budget` to check, don't guess)

### How to Checkpoint

Use the atomic checkpoint command — it pauses the run, publishes a checkpoint
artifact, AND creates a Radar todo in one call:

```bash
python backend/scripts/artifact_cli.py run-checkpoint \
  --project <PROJECT> --run-id <RUN_ID> \
  --stage <next_stage> --reason "<why paused>"
```

This does 3 things atomically:
1. Sets pipeline run status to "paused" with checkpoint metadata
2. Publishes a checkpoint artifact to `.artifacts/`
3. Creates a high-priority Radar todo for visibility and resume

Then present to user:
```
Pipeline PAUSED at <STAGE> (run_<id>)
Reason: <why>

  Completed: evaluate, think, plan
  Next: build
  Pending: <escalation summary>
  Budget: <consumed>/<total> tokens (<pct>% used)

  Resume: resolve the issue, then "resume pipeline for <PROJECT>"
  (A Radar todo has been created for tracking.)
```

---

## Progress Display

Show progress after each stage completes:

```
Pipeline: <requirement> (run_<id>)
Project: <PROJECT> | Profile: <profile>

  [done] EVALUATE  <one-line summary>
  [done] THINK     <one-line summary>
  [>>>>] PLAN      <what's happening now>
  [    ] BUILD
  [    ] REVIEW
  [    ] TEST
  [    ] DELIVER
  [    ] REFLECT
```

Status: `[done]` `[>>>>]` `[skip]` `[FAIL]` `[STOP]` `[    ]`

**During DELIVER convergence loop**, show iteration status inline:
```
  [>>>>] DELIVER   converge: iteration 2/3 (L3 regression fix applied, re-verifying)
```

---

## Rules

1. **Execute inline, never invoke skills.** You ARE the pipeline. Run each
   stage's behavior directly. Do not use `/evaluate` or `/qa` as slash commands.
2. **Read stage docs before executing.** The dispatch table in §3c is BLOCKING.
3. **TDD is mandatory in BUILD.** RED → GREEN → VERIFY → SMOKE → TRACE → PROBE.
   Fix code, not tests. Changing tests = changing the spec.
4. **Classify every decision.** No unclassified decisions. If unsure, default
   to "taste" (surface at delivery gate rather than block or ignore).
5. **Verify before advancing.** Run pipeline_validator.py after every stage.
6. **Completeness bias.** When the complete implementation costs minutes more
   than the shortcut, do the complete thing.
7. **Atomic commits.** One commit per logical change in BUILD and TEST stages.
8. **Never loop forever.** Respect max_retries. Checkpoint on exhaustion.
9. **Taste decisions batch at delivery.** Don't interrupt mid-pipeline.
10. **Judgment decisions block immediately.** CHECKPOINT at once.
11. **DEFER/REJECT at evaluate ends the pipeline.**
12. **Always generate REPORT.md.** at `.artifacts/runs/<RUN_ID>/REPORT.md`.
13. **Confidence score at delivery.** Use `scripts/confidence_score.py`.
    Below 7 → flag for human review.
14. **Source-path reads.** Always read from `backend/skills/` (source of truth),
    not `.claude/skills/` (projected copy).
15. **Value over completion.** The pipeline's purpose is to deliver qualified
    value, not to finish stages. A pipeline that reaches DELIVER with a
    working feature is worth more than one that reaches REFLECT with a
    broken feature. If BUILD produces something that technically passes
    tests but doesn't solve the user's actual problem — that's a failure,
    not a success. When in doubt: does this change make the user's life
    better? If the answer isn't clearly yes — stop and reassess, don't
    push through to mark done.
16. **Evidence over assertion.** Do not rely on intent, partial progress,
    elapsed effort, or memory of earlier work as proof of completion
    (adapted from Codex /goal). The ONLY valid proof is external evidence:
    a test that passes, a file that exists, a command that returns the
    expected output. "I implemented X" is not evidence — `test_x passes`
    is evidence. "I reviewed the code" is not evidence — `3 findings,
    all fixed` is evidence. Every claim in Completion Audit must cite
    a verifiable artifact.
17. **No premature completion.** Do not advance to REFLECT or mark
    status=completed unless the Quality Convergence Loop (Step 4c) exits
    push-ready — all 6 gate layers pass, self-assessment positive, goal
    aligned. Budget pressure is not a valid reason to skip convergence —
    if budget is low, CHECKPOINT with clear remaining work, don't
    compress the loop. A half-delivered feature with a checkpoint resume
    plan is better than a "completed" pipeline with hidden gaps.
18. **Adversarial findings must be specific.** Each adversarial review
    finding must include: (a) file path and line number or function name,
    (b) what's wrong, (c) concrete fix. Findings like "looks good",
    "could be improved", or "consider adding" are not findings — they
    are noise. If the sub-agent returns vague findings, reject them
    and re-prompt with "be specific: file, line, what's wrong, how to
    fix."
19. **Every stage is mandatory — the pipeline is a DDD loop, not a checklist.**
    The completion gate (`run-update --status completed`) mechanically enforces
    that ALL profile stages are either `completed` or `skipped` (with explicit
    `skip_reason` field). This is not an honor system — it's a hard gate.

    **Why:** Each stage serves the DDD learning loop:
    - EVALUATE reads IMPROVEMENT.md (learns from past) → decides GO/DEFER
    - THINK/PLAN reads PRODUCT.md + TECH.md → informed design
    - BUILD/REVIEW/TEST writes code → verified implementation
    - DELIVER packages + audits → qualified delivery
    - REFLECT writes IMPROVEMENT.md (teaches future) → closes the loop

    A pipeline that skips REFLECT breaks the learning loop. A pipeline that
    skips EVALUATE might build something already tried and failed. Every stage
    has a purpose — skipping one creates a gap that compounds over time.

    **To skip a stage:** Set `status: "skipped"` with a `skip_reason` field
    explaining WHY it's safe to skip. "Budget pressure" is NOT a valid reason —
    CHECKPOINT instead. Valid reasons: "user override: approach already known",
    "profile does not include this stage", "prerequisite output empty".

    **If context is exhausted:** CHECKPOINT with the next stage as resume point.
    Do not compress or skip stages to fit in the current session.

---

## Artifact Operations Reference

```bash
# ── Artifact Registry ──

# Discover upstream artifacts
python backend/scripts/artifact_cli.py discover --project <PROJECT> --types <types> --full

# Publish an artifact
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type <type> --producer s_autonomous-pipeline --summary "<summary>" --data '<json>'

# Get pipeline state
python backend/scripts/artifact_cli.py state --project <PROJECT>

# Advance pipeline state
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state <stage>

# Record outcome (reflect stage)
python backend/scripts/artifact_cli.py learn --project <PROJECT> \
  --evaluation-id <id> --outcome <success/partial/failure> \
  --actual-effort "<effort>" --lessons "<semicolon-separated>"

# List all projects
python backend/scripts/artifact_cli.py projects

# ── Pipeline Run Management ──

# Create a new pipeline run
python backend/scripts/artifact_cli.py run-create --project <PROJECT> \
  --requirement "<requirement text>" [--profile full|trivial|research|docs|bugfix]

# Update pipeline run (add stage, taste decision, change status/profile)
python backend/scripts/artifact_cli.py run-update --project <PROJECT> --run-id <RUN_ID> \
  [--stage-json '<json>'] [--taste-decision '<json>'] [--status <status>] [--profile <profile>]

# Get pipeline run state (or list all runs if --run-id omitted)
python backend/scripts/artifact_cli.py run-get --project <PROJECT> [--run-id <RUN_ID>]

# ── v2: Budget & Checkpoint ──

# Check budget before next stage
python backend/scripts/artifact_cli.py run-budget --project <PROJECT> --run-id <RUN_ID>

# Atomic checkpoint: pause + artifact + Radar todo
python backend/scripts/artifact_cli.py run-checkpoint --project <PROJECT> --run-id <RUN_ID> \
  --stage <next_stage> --reason "<why paused>"

# Historical token costs for calibration
python backend/scripts/artifact_cli.py run-history --project <PROJECT> [--limit 10]

# ── v3: Dashboard, Resume, Background Jobs ──

# Cross-project pipeline dashboard (all projects)
python backend/scripts/artifact_cli.py run-status [--active-only]

# Resume a paused pipeline (after escalation resolved)
python backend/scripts/artifact_cli.py run-resume --project <PROJECT> --run-id <RUN_ID>

# Create a background pipeline job (runs via scheduler)
python -m jobs.job_manager pipeline \
  --project <PROJECT> --requirement "<what to build>" \
  [--schedule "0 9 * * 1-5"] [--profile full] [--budget 2.00] [--one-shot]
```

## Background Execution (v3)

Pipelines can run as background jobs via the Swarm Job System. This decouples
pipeline execution from interactive chat sessions.

### Creating a Background Pipeline

```bash
# Recurring: run every weekday at 9am
python -m jobs.job_manager pipeline \
  --project SwarmAI --requirement "Run QA on recent changes" \
  --profile bugfix --schedule "0 1 * * 1-5"

# One-shot: run once (for a specific feature)
python -m jobs.job_manager pipeline \
  --project ClientApp --requirement "Add payment retry logic" \
  --profile full --budget 3.00 --one-shot
```

### Monitoring

```bash
# All active pipelines across all projects
python backend/scripts/artifact_cli.py run-status --active-only

# Full dashboard (active + recent completed)
python backend/scripts/artifact_cli.py run-status
```

### Resuming After Escalation

When a background pipeline checkpoints (L2 BLOCK or budget), a Radar todo appears.
After the user resolves the issue:

```bash
# Mark the pipeline as resumable
python backend/scripts/artifact_cli.py run-resume --project <PROJECT> --run-id <RUN_ID>

# Then either:
# 1. Drag the Radar todo into chat → agent resumes the pipeline
# 2. Say "resume pipeline for <PROJECT>" → agent reads checkpoint and continues
# 3. Wait for next scheduler run → background job picks up the resumed pipeline
```
