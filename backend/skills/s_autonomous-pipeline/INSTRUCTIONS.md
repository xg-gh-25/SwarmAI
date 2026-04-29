# Pipeline Orchestrator

Drive the full lifecycle pipeline from requirement to delivery. You ARE the
orchestrator -- execute each stage's behavior inline within this session, don't
invoke separate skills.

## Core Loop

For every pipeline run, follow this loop:

```
1. INIT     -- parse requirement, detect project, load or create pipeline run
2. PROFILE  -- select pipeline profile (full/trivial/research/docs/bugfix)
3. STAGE    -- for each stage in profile:
               a. Gate check (budget, escalations, retries)
               b. Load stage context (DDD docs + upstream artifacts)
               c. Execute stage behavior (read stage doc, then execute)
               d. Classify decisions (mechanical/taste/judgment)
               e. Verify output (artifact published + schema valid)
               f. Handle result (advance / retry / checkpoint)
4. DELIVER  -- at delivery stage, run the Delivery Gate
5. COMPLETE -- summarize, reflect, record metrics
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

If the evaluate stage doesn't classify scope (L0), default to **full**.
The user can override: "skip research, I know the approach" → switch to bugfix.

---

## Step 3: STAGE EXECUTION

For each stage in the selected profile, execute in order:

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
| review | `stages/review.md` AND `REVIEW_PATTERNS.md` | — |
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

## Step 4: DELIVERY GATE

At the deliver stage, BEFORE generating the delivery report, collect ALL taste
decisions from ALL prior stages and present them as a batch:

```
DELIVERY GATE -- <N> taste decisions for review:

  1. [THINK]   Chose httpx built-in retry over tenacity (simpler, fewer deps)
  2. [BUILD]   Used sync retry instead of async (matches existing codebase style)
  3. [REVIEW]  Skipped type stub generation (low value for internal module)

  [Approve All]  [Override #1]  [Override #2]  [Override #3]  [Discuss]
```

**If no taste decisions accumulated:** skip the gate, proceed to delivery.

**If user approves all:** proceed to delivery.

**If user overrides any:** re-run the affected stage with the override as a
constraint. This may cascade (overriding a THINK decision re-runs THINK, which
may change PLAN, which changes BUILD). Re-run the minimum set of affected
downstream stages.

**If user wants to discuss:** enter conversational mode. Once resolved, resume.

---

## Step 5: COMPLETE

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
| deliver | 8K | 5-15K | Report generation + gate |
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
