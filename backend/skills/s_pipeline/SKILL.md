---
name: Pipeline
description: >
  Orchestrate the full Skill Lifecycle Pipeline from a one-sentence requirement
  to a PR-ready delivery. Drives stages sequentially: Evaluate, Think, Plan,
  Build, Review, Test, Deliver, Reflect. Classifies every decision as mechanical
  (auto-approve), taste (batch at delivery gate), or judgment (block for human).
  Checkpoints on L2 BLOCK, retry exhaustion, or context budget limits. Resumes
  from checkpoint in a fresh session.
  TRIGGER: "run pipeline", "pipeline for", "full pipeline", "build end-to-end",
  "execute pipeline", "resume pipeline", "continue pipeline", "pipeline status".
  DO NOT USE: for a single stage (use the specific skill: evaluate, deep-research,
  code-review, qa, deliver). Not for tasks without a clear requirement.
  SIBLINGS: evaluate = the GO/DEFER gate alone | qa = testing alone |
  deliver = packaging alone | pipeline = the full orchestrated sequence.
consumes_artifacts: [evaluation, research, alternatives, design_doc, changeset, review, test_report]
produces_artifact: checkpoint
---

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
               c. Execute stage behavior
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
Projects/<project>/.artifacts/pipeline-run-<id>.json
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
2. Load `pipeline-run-<id>.json`
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

Run the stage's behavior inline. DO NOT invoke a separate skill with slash
commands. Execute the behavior directly in this session.

#### EVALUATE

Follow the s_evaluate workflow:
1. Parse the requirement (what/why/who/constraints)
2. Score against DDD docs (strategic 1-5, feasibility 1-5, historical +/-1, priority 1-5)
3. Calculate ROI = (strategic * 0.35) + (priority * 0.25) + (historical * 0.15) - (inverse_feasibility * 0.25)
4. Classify scope: trivial / standard / complex / research-only / docs-only
5. Recommend: GO (>= 3.5) / DEFER (2.0-3.4) / REJECT (< 2.0) / ESCALATE
6. Define acceptance criteria (3-5 items)

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type evaluation --producer s_pipeline \
  --summary "<GO/DEFER/REJECT>: <one-line>" \
  --data '{"requirement":"...","scores":{...},"recommendation":"GO","scope":"standard","acceptance_criteria":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state think
```

If DEFER or REJECT → pipeline ends. Log reason and exit.
If ESCALATE → L2 BLOCK → checkpoint.

#### THINK

1. Research the requirement: search for existing solutions, patterns, prior art
2. Summarize key findings (3-5 bullet points)
3. Present 3 alternatives:
   - **Approach 1: Minimal** (ships fastest) — effort, risk, tradeoff
   - **Approach 2: Ideal** (best architecture) — effort, risk, tradeoff
   - **Approach 3: Creative** (unexpected angle) — effort, risk, tradeoff
4. Recommend one approach with reasoning
5. If DDD available: align with PRODUCT.md priorities, avoid IMPROVEMENT.md failures

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type research --producer s_pipeline \
  --summary "3 alternatives for <topic>. Recommending: <approach>" \
  --data '{"key_findings":[...],"alternatives":[...],"recommendation":"...","sources":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state plan
```

#### PLAN

1. Take the recommended (or user-chosen) alternative
2. Produce a design document:
   - Architecture/approach description
   - Data model or API contract (if applicable)
   - Acceptance criteria (carry forward from evaluate + refine)
   - Edge cases and error handling
   - Estimated files to change
3. If design requires uncommitted dependencies or API changes → taste/judgment decision

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type design_doc --producer s_pipeline \
  --summary "Design: <approach> for <requirement>" \
  --data '{"approach":"...","acceptance_criteria":[...],"data_model":"...","api_contract":"...","files_to_change":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state build
```

#### BUILD

1. Read TECH.md for conventions, test commands, code style
2. Implement changes guided by the design_doc artifact
3. **Completeness bias:** when the complete implementation costs minutes more
   than the shortcut, do the complete thing. Cover edge cases, add tests for
   new code paths, handle errors. Don't leave TODOs.
4. Use atomic commits: one commit per logical change
5. Track all files changed

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type changeset --producer s_pipeline \
  --summary "<N> files changed, <M> commits" \
  --data '{"branch":"...","commits":[...],"files_changed":[...],"diff_summary":"..."}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state review
```

#### REVIEW

1. Code review the changeset against TECH.md conventions
2. Run confidence-gated security scan:
   - Each finding needs confidence (1-10) + exploit scenario
   - >= 8 + Critical/High: auto-fix (mechanical decision)
   - 5-7: warning only (taste decision)
   - < 5: suppress
   - Apply 10 false-positive exclusions
3. Check IMPROVEMENT.md for known issue patterns

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type review --producer s_pipeline \
  --summary "Review: <N findings>, <M auto-fixed>" \
  --data '{"findings":[...],"approved":true/false,"security_findings":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state test
```

#### TEST

1. Detect test framework (or read from TECH.md)
2. Run tests scoped to changed files
3. For each failure: attempt fix + atomic commit
4. **WTF gate:**
   ```
   wtf_score = 0
   +2 if fix touches > 3 files
   +3 if fix modifies unrelated module
   +2 if fix changes API contract
   +1 if fix_count > 10
   +3 if previous fix broke something
   → halt if wtf_score >= 5 (judgment decision → L2 BLOCK)
   ```
5. Max 20 fixes per session
6. Run full test suite after all fixes

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type test_report --producer s_pipeline \
  --summary "Tests: <passed>/<total> pass, <fixed> bugs fixed" \
  --data '{"passed":N,"failed":M,"fixed":K,"skipped":J,"bugs":[...],"coverage":"..."}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state deliver
```

#### DELIVER

**Run the Delivery Gate first** (see Step 4 below), then:

1. Assemble delivery report: summary, pipeline path, what was built, key
   decisions, quality summary, attention flags
2. Generate PR description (if changeset exists)
3. Update PROJECT.md with delivery entry
4. Check for unresolved issues from upstream stages

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type delivery --producer s_pipeline \
  --summary "Delivery: <feature title>" \
  --data '{"title":"...","summary":"...","decisions":[...],"quality":{...},"attention_flags":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state reflect
```

#### REFLECT

1. Extract lessons from this pipeline run
2. Write to IMPROVEMENT.md: what worked, what failed, patterns discovered
3. Update MEMORY.md if the lesson is cross-project
4. Record outcome for learning:
```bash
python backend/scripts/artifact_cli.py learn --project <PROJECT> \
  --evaluation-id <eval_artifact_id> --outcome success \
  --actual-effort "<T-shirt>" \
  --lessons "lesson 1;lesson 2"
```

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

### 3e. Verify Stage Output

After execution, verify before advancing:

1. **Artifact published?** Check the artifact_cli publish succeeded.
2. **Meets minimum quality?** Stage-specific checks:

| Stage | Verify |
|-------|--------|
| evaluate | Has recommendation + scope + ROI score |
| think | Has key_findings (non-empty) + alternatives with tradeoffs |
| plan | Has acceptance_criteria (>= 1) + chosen approach |
| build | Has commits (non-empty) + files_changed |
| review | Has findings list (even if empty) + approved boolean |
| test | Has pass/fail counts + acceptance criteria coverage |
| deliver | Has pr_description + decision_log + attention_flags |
| reflect | (always passes) |

3. **Log transition summary:** Write 1-2 sentences about what this stage
   decided, for the next stage's context.

If verification fails → **retry** (increment retry count, re-execute stage).
If retry count >= max_retries → **checkpoint** with all failure details.

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
2. Present the completion summary:

```
Pipeline COMPLETE (run_<id>) -- <N> stages, <M> skipped, <K> escalations

  Artifacts:
    evaluation  -> art_xxxx (GO, ROI 4.2)
    research    -> art_xxxx (3 alternatives, chose: <approach>)
    design_doc  -> art_xxxx (<approach>, 5 acceptance criteria)
    changeset   -> art_xxxx (47 lines, 2 files, branch: feat/<feature>)
    review      -> art_xxxx (clean, 0 findings)
    test_report -> art_xxxx (5/5 pass, 94% coverage)
    delivery    -> art_xxxx (PR ready, decision log attached)

  Decisions: <X> mechanical, <Y> taste (all approved), <Z> judgment
  Lessons: <N> written to IMPROVEMENT.md

  PR: ready for merge. No attention flags.
```

3. Save the final pipeline-run JSON to `.artifacts/`

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

Update the stage's `token_cost` field in the pipeline run. This is an estimate
based on the work done (DDD docs read + artifacts consumed + code generated).
Rough heuristics:

- Reading a DDD doc: ~2K tokens
- Consuming an artifact: ~3-5K tokens
- Generating code: ~500 tokens per 10 lines changed
- Running tests + analyzing output: ~10-15K tokens
- Writing an artifact: ~2-3K tokens

These don't need to be exact — they calibrate over time via `run-history`.

### Historical Calibration

Check past run costs to calibrate estimates:

```bash
python backend/scripts/artifact_cli.py run-history --project <PROJECT>
```

Returns per-stage averages from completed runs. The `run-create` command
automatically uses historical data (with 20% buffer) when available.

---

## Checkpoint Protocol

### When to Checkpoint

Checkpoint (pause the pipeline) when ANY of:
- L2 BLOCK escalation (judgment decision)
- Stage retry exhaustion (>= max_retries failures)
- Budget insufficient for next stage (`run-budget` says `should_checkpoint: true`)
- Pipeline error (unexpected failure)

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

Show progress after each stage completes. Use this format:

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

Stage status indicators:
- `[done]` = completed successfully
- `[>>>>]` = currently executing
- `[skip]` = skipped (not in profile)
- `[FAIL]` = failed, will retry or checkpoint
- `[STOP]` = checkpointed (pipeline paused)
- `[    ]` = pending

---

## Rules

1. **Execute inline, never invoke skills.** You ARE the pipeline. Run each
   stage's behavior directly. Do not use `/evaluate` or `/qa` as slash commands.
2. **Classify every decision.** No unclassified decisions. If unsure, default
   to "taste" (surface at delivery gate rather than block or ignore).
3. **Verify before advancing.** Never skip verification. Garbage in one stage
   becomes garbage in all downstream stages.
4. **Completeness bias.** When the complete implementation costs minutes more
   than the shortcut, do the complete thing. (gstack "Boil the Lake" principle.)
5. **Atomic commits.** One commit per logical change in BUILD and TEST stages.
   This enables rollback if a fix breaks something.
6. **Never loop forever.** Respect max_retries. Checkpoint on exhaustion.
   Three attempts at the same stage is enough.
7. **Taste decisions batch at delivery.** Don't interrupt the user mid-pipeline
   for taste decisions. Accumulate them, present once at the delivery gate.
8. **Judgment decisions block immediately.** Don't continue past a judgment
   decision. The whole point is that the agent genuinely doesn't know.
9. **Pipeline state is the artifact registry.** Use artifact_cli for ALL state
   operations. No separate state store.
10. **DEFER/REJECT at evaluate ends the pipeline.** Don't continue stages after
    the evaluate stage says stop.

## Artifact Operations Reference

```bash
# ── Artifact Registry ──

# Discover upstream artifacts
python backend/scripts/artifact_cli.py discover --project <PROJECT> --types <types> --full

# Publish an artifact
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type <type> --producer s_pipeline --summary "<summary>" --data '<json>'

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
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py pipeline \
  --project <PROJECT> --requirement "<what to build>" \
  [--schedule "0 9 * * 1-5"] [--profile full] [--budget 2.00] [--one-shot]
```

## Background Execution (v3)

Pipelines can run as background jobs via the Swarm Job System. This decouples
pipeline execution from interactive chat sessions.

### Creating a Background Pipeline

```bash
# Recurring: run every weekday at 9am
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py pipeline \
  --project SwarmAI --requirement "Run QA on recent changes" \
  --profile bugfix --schedule "0 1 * * 1-5"

# One-shot: run once (for a specific feature)
python3 ~/.swarm-ai/SwarmWS/Services/swarm-jobs/job_manager.py pipeline \
  --project ClientApp --requirement "Add payment retry logic" \
  --profile full --budget 3.00 --one-shot
```

The job system spawns a headless Claude CLI session that runs the pipeline
orchestrator. Checkpoints create Radar todos visible in the sidebar.

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
