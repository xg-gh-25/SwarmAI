# GOAL_CYCLE Stage

## Purpose

Iterative execution toward an open-ended goal. Repeats BUILD+TEST cycles
until Definition of Done (DoD) criteria are met, max cycles reached, or a
structural blocker is detected.

This is NOT a one-shot stage — it loops internally. The pipeline orchestrator
calls this stage once; the stage itself manages the cycle loop.

---

## Pre-Cycle Setup (runs once, at stage entry)

1. **Load evaluation artifact** — extract `dod_criteria`, `max_cycles`,
   `progress_path`, `cycle_scope`, `review_cadence`
2. **Initialize or load progress file** at `progress_path`:
   - If file exists → resume from last recorded state
   - If file doesn't exist → create with DoD criteria + empty metrics table
3. **Record start commit** — `git rev-parse HEAD` (used for periodic REVIEW
   and final ADVERSARIAL diff base)
4. **Set cycle counter** to last recorded cycle + 1 (or 1 if fresh)
5. **Initialize GoalMetrics** — create ONE instance, reuse throughout stage:
   ```python
   from scripts.goal_metrics import GoalMetrics

   # Extract dod_criteria from the evaluation artifact loaded in step 1:
   dod_criteria = evaluation_artifact["dod_criteria"]

   gm = GoalMetrics(run_dir=Path(run_dir))
   gm.track_goal_start(dod_criteria=dod_criteria)
   # Check cross-run velocity for cycle_scope auto-tuning:
   recommended_scope = gm.get_recommended_cycle_scope()
   # If recommended_scope differs from evaluation's cycle_scope, log it
   ```
   **Important:** Reuse this `gm` instance for all `track_cycle()` calls and
   the final `track_goal_complete()`. Do NOT re-instantiate per cycle.

---

## DoD Quality Rules (BLOCKING — enforced at Pre-Cycle Setup)

The DoD criteria from EVALUATE are the goal profile's primary quality lever.
Weak DoD = weak output. These rules prevent the most common DoD failure modes:

### Rule 1: Mandatory Negative Test

At least ONE DoD criterion must be a **negative test** — it verifies that
a failure case is handled correctly, not just that the happy path works.

```
❌ Weak DoD (all positive):
  - "script exists"
  - "script runs without error"
  - "output contains expected text"

✅ Strong DoD (includes negative):
  - "script exists"
  - "script runs without error"  
  - "output contains expected text"
  - "script exits 1 (not crash) when target unreachable"  ← NEGATIVE
```

**Why:** run_2f2e078a delivered code that passed all 4 positive DoD criteria
but had 4 undetected gaps (untested error path, failure propagation, scope
mismatch, orphan accumulation). A single negative test ("what happens when
the server is down?") would have caught 3 of 4.

**Enforcement:** At Pre-Cycle Setup, scan `dod_criteria`. If zero criteria
test a failure/error/edge case → the agent MUST add one before starting
cycles. Heuristic: criterion text contains "fail", "error", "invalid",
"not", "without", "missing", "crash", "timeout", or tests exit code != 0
on a deliberately broken input.

If the evaluation artifact has no negative test:
```
⚠️ DoD has no negative test. Adding: [auto-generated criterion].
   Override with "skip negative" if intentional.
```

### Rule 2: Cross-Path Coverage

When the deliverable has **multiple input paths** (e.g., `--scope full` vs
`--scope frontend-only`, or `--backend` vs `--frontend` vs `--all`), at
least one DoD criterion must exercise a **non-default path**.

**Why:** run_2f2e078a had 4 DoD criteria, all exercising the default path.
The `frontend-only` scope path was completely untested — it existed in code
but could have been syntactically broken and DoD would still pass.

**Heuristic for detection:** If the PLAN's `files_planned` introduces code
with branching on a user-supplied flag/arg/scope — require DoD for ≥2 branches.

### Rule 3: Behavioral Over Existential

Prefer DoD criteria that verify **behavior** over **existence**.

```
❌ Existential: grep -q "function_name" file.py
✅ Behavioral: python3 file.py --action 2>&1 | grep -q "expected output"
```

Existential criteria pass on dead code. Behavioral criteria require the
code to actually execute.

---

## Per-Cycle Execution

Each cycle follows this exact sequence:

### 1. Budget Gate

```
remaining_tokens = session_budget - tokens_consumed
if remaining_tokens < 150_000:
    → EXIT with BUDGET (save progress, checkpoint)
```

150K = enough for 1 more cycle (~50K) + REFLECT (~30K) + overhead.

### 2. DoD Check (exit-first)

Run ALL DoD criteria. If all pass → proceed to **Final Quality Gate** section below (adversarial on total changeset), then EXIT with SUCCESS.

For each criterion:
- **command type:** Run the shell command. Exit code 0 = pass, non-zero = fail.
  ```bash
  bash -c '<check_command>' 2>&1; echo "EXIT:$?"
  ```
  Parse the last line for `EXIT:0` (pass) or `EXIT:N` (fail).

- **rubric type:** Read the state described in the rubric, evaluate against
  the explicit pass/fail criteria. Output `PASS` or `FAIL: <reason>`.

Record per-criterion results in progress file.

### 3. Stuck Detection

```
if last 3 cycles all have zero progress (same DoD results, no criterion flipped):
    → EXIT with STOP (structural blocker)
```

### 4. Read Progress

- Read progress file → identify: completed criteria, remaining gaps, last
  cycle's action, blockers from prior cycles
- Determine which DoD criterion has the largest gap (most leverage)

### 5. Pick Step

Based on DoD gap analysis, select ONE bounded step:
- Touch 1-3 files
- Address one specific gap toward one DoD criterion
- Respect `cycle_scope` from evaluation (default: "one test file or one module fix")

Announce: `Cycle N: targeting [DoD criterion] via [specific action]`

### 6. Execute Step (BUILD-equivalent)

Standard TDD discipline within the step:
- Write test for the expected improvement (RED)
- Implement the fix/addition (GREEN)
- Verify no regressions on touched files

### 7. Test

Run targeted tests for files changed in this cycle:
```bash
pytest tests/test_<module>.py -v --timeout=60
```

If tests fail → Regression Protocol (see below).

### 8. Update Progress

Append to progress file:
```markdown
| N | YYYY-MM-DD | <metric_before> → <metric_after> | +<delta> | <action taken> |
```

Update "Current State" section with new targets.

### 9. Mini-Reflect

Append ONE line to progress file Cycle Log:
```markdown
**Cycle N:** <what worked or didn't> → <insight for next cycle>
```

No DDD write. No LLM call for distillation. Just text.

### 9.5. Track Cycle Metrics (GoalMetrics)

After mini-reflect, record this cycle's metrics using the `gm` instance
created at Pre-Cycle Setup (step 5):

```python
gm.track_cycle(
    cycle_num=current_cycle,
    progress_delta=<fraction of DoD newly met this cycle>,  # 0.0-1.0
    files_changed=<count of source files modified>,
    tests_added=<count of new tests written>,
    regression=<True if regression protocol fired>,
)
```

This feeds the velocity auto-tuning system. Higher-quality data here →
better `cycle_scope` recommendations for future goals.

**progress_delta calculation (strict definition):**
```
progress_delta = (criteria_met_after - criteria_met_before) / total_criteria
```
- If 1 of 3 criteria flipped this cycle → `1/3 ≈ 0.33`
- If no criterion flipped → `0.0` (even if partial progress occurred)
- Partial progress within a criterion (e.g., coverage 73%→76% toward 90%)
  is logged in the progress file text but NOT encoded in progress_delta.
  This keeps velocity a clean integer-step metric: each delta represents
  a criterion completion event, not a continuous estimate.

### 10. Periodic REVIEW Gate

```
if cycle_number % review_cadence == 0:
    git_diff = git diff <last_review_commit>..HEAD
    → Run REVIEW stage behavior on this diff
    → If findings: append EACH to the Findings Ledger (step 10.5 shape) with
      a new ID, Cycle Found, severity, confidence, status=APPLICABLE, file:line.
      (Do NOT dump into free-text Blockers — a plain-text note cannot be
      re-judged across cycles. The ledger is the tracked home.)
    → Update last_review_commit to HEAD
```

### 10.5 Cross-Cycle Finding Re-Judgment (AutoSDE #2, run_7583af5f)

`A FINDING IS NOT DONE UNTIL RE-JUDGED AGAINST THE LATEST DIFF`

**Runs every cycle that the Periodic REVIEW gate fires (same cadence), and once
more in the Final Quality Gate.** This is the mechanism that catches
"fixed-in-cycle-3, regressed-in-cycle-7" — the machine version of OT01's
"looked fixed, recurred." A fresh adversarial on the total diff does NOT catch
this: it has no memory that a finding was ever raised and marked resolved, so a
silent regression reads as clean. Re-judgment DOES, because it re-checks each
KNOWN finding.

**Process (for each ledger finding NOT already OBSOLETE):**
```
current_diff = git diff <start_commit>..HEAD
for finding in Findings Ledger where status != OBSOLETE:
    re-judge finding against current_diff + current file state:
      - the code it flags still exists AND still exhibits the problem
            → status = APPLICABLE   (even if it was RESOLVED in an earlier cycle —
                                     this is the regression catch)
      - the problem is verifiably fixed in the current code
            → status = RESOLVED (record cycle)
      - the code it referenced no longer exists at all
            → status = OBSOLETE
    write the updated status + re-judged cycle back to the ledger row
```

**Rule — this must have TEETH (Gate-1 finding, run_7583af5f):** re-judgment that
only updates a markdown table changes nothing. Every finding that ends this step
as **APPLICABLE and unresolved** MUST be carried forward into the DELIVER stage's
`adversarial_review.findings[]` — verbatim, with its `severity` + `confidence` —
so the finding-level confidence gate (`_blocked_findings`, pipeline_validator.py)
blocks completion on it exactly as if a fresh adversarial had just raised it.
Writing it only to the progress file is a no-op: the progress file never reaches
`_blocked_findings`. The ledger is the working memory; the DELIVER artifact is
the enforcement surface.

### 11. Revert Check

```
if 2 consecutive cycles ended in revert:
    → EXIT with REVERT_LIMIT (conflicting constraints)
```

### 12. Loop

Return to Step 1 (Budget Gate) for next cycle.

---

## Regression Protocol

When TEST fails on current cycle's changes:

```
Attempt 1:
  - Diagnose: which test fails and why?
  - Fix: scoped to the failing test (≤3 file changes)
  - Re-run TEST
    → Pass: continue to Step 8
    → Fail: go to Attempt 2

Attempt 2:
  - Different fix approach
  - Re-run TEST
    → Pass: continue to Step 8
    → Fail: REVERT cycle N source code changes only
      - git checkout -- <source files changed this cycle>
      - Do NOT revert progress file (it lives in .artifacts/, tracks state)
      - Mark step as "blocked by [test/reason]" in progress file
      - Increment revert counter
      - Continue to Step 11 (Revert Check)
```

---

## Exit Conditions

### EXIT with SUCCESS

All DoD criteria pass (verified by running commands/rubrics).

**Record Goal Completion (GoalMetrics):**
```python
gm.track_goal_complete(
    status="success",
    total_cycles=current_cycle,
    dod_met=<criteria met>,
    dod_total=<criteria total>,
)
# Write velocity summary to progress file
velocity = gm.get_velocity()
# Append to progress file: "## Velocity Summary\n- Avg delta/cycle: {velocity['avg_delta_per_cycle']}\n..."
```

**Final Quality Gate (before exiting goal_cycle stage):**

0. **Cross-Cycle Re-Judgment (run step 10.5 one final time on the total diff).**
   Re-judge every ledger finding against `git diff <start_commit>..HEAD`. Carry
   every APPLICABLE-and-unresolved finding into the DELIVER
   `adversarial_review.findings[]` (with severity + confidence) so the
   `_blocked_findings` confidence gate sees it. A finding that was RESOLVED in an
   early cycle but is APPLICABLE again here = a caught cross-cycle regression;
   it blocks exactly like a fresh one.

1. Full ADVERSARIAL review on total changeset:
   ```bash
   git diff <start_commit>..HEAD
   ```
   Spawn sub-agent with this diff + DoD criteria + requirement. MERGE its new
   findings into the ledger (they too become APPLICABLE rows) so the ledger is
   the complete set the DELIVER gate enforces.

2. **Cross-path adversarial prompt (MANDATORY):**
   The adversarial sub-agent MUST be given this additional instruction:

   > "Beyond reviewing the diff for correctness, answer these cross-path
   > questions:
   > 1. List ALL distinct code paths (branches, flag values, scope options)
   >    in the delivered code. For EACH path: was it exercised by the DoD
   >    criteria? If no → it's an untested path. Flag it.
   > 2. For each function that calls another function: what happens if the
   >    callee FAILS (returns error, throws, exits non-zero)? Is the failure
   >    propagated, handled, or silently swallowed?
   > 3. If this code runs repeatedly (N times), does anything accumulate
   >    without cleanup? (sessions, files, locks, temp data)
   > 4. Are there any code paths that exist in the diff but cannot be
   >    reached by any DoD criterion? Those are dead-on-arrival paths."

   **Why this exists:** run_2f2e078a adversarial found 5 single-file bugs
   but missed 4 cross-path gaps (untested frontend path, failure propagation,
   scope mismatch, orphan accumulation). Standard adversarial asks "is this
   code correct?" — cross-path adversarial asks "is this code correct FOR
   ALL ITS USES?"

2.5. **Class-Completeness Gate (MANDATORY when `migration_class` is declared — run_1d3df9e6):**

   Runs AFTER the cross-path adversarial (step 2), BEFORE marking the stage complete.
   The cross-path adversarial is DIFF-SCOPED — it only sees code the cycles TOUCHED, so a
   class sibling that NO cycle touched is invisible to it (this is how the `decisions`
   write path shipped ungated in run_0d60e04e: 7 per-cycle adversarials all passed). This
   gate is CLASS-SCOPED: it enumerates the FULL class from live source and blocks any
   member neither migrated nor carved-out.

   ```python
   from scripts.check_migration_class import check_migration_class, to_delivery_finding
   mc = evaluation_artifact.get("migration_class")   # None on a non-migration goal
   res = check_migration_class(mc, cwd=Path.cwd())
   print(res.coverage_table)                          # AC7: always emit the audit table
   if not res.passed:
       finding = to_delivery_finding(res)             # HIGH, confidence 9
       # AC6 TEETH: carry into the DELIVER adversarial_review.findings[] so the existing
       # _blocked_findings gate blocks COMPLETE exactly like a fresh adversarial finding.
       adversarial_review["findings"].append(finding)
   ```

   - `migration_class` absent → `check_migration_class` returns a no-op PASS (AC2): a
     feature/bugfix goal is unaffected. But if the goal's REQUIREMENT contained a migration
     keyword, EVALUATE Gate-0 already BLOCKED the run until `migration_class` was declared
     (evaluate.md AC11) — so a real migration cannot reach here undeclared.
   - A MISSED / BAD_ENUMERATION / UNJUSTIFIED_CARVEOUT member → the HIGH finding blocks
     COMPLETE. Fix = migrate the member (or declare an honest carve-out) and re-run.

3. If adversarial finds issues → execute up to 3 more fix cycles
4. If issues persist after 3 fix cycles → CHECKPOINT with findings

**After final adversarial passes:** Mark goal_cycle stage as completed with an
`adversarial_review` **object** (NOT a bare `true`) carrying its `findings[]` — the
same dict shape the DELIVER adversarial records (see steps above, and
`adversarial_review["findings"].append(finding)`). The validator's STAGE_SCHEMAS +
STAGE_DEPTH for `goal_cycle` (D4, run_57929039) require `dod_met` plus an
`adversarial_review` **dict** with a `findings` key — a bare `adversarial_review: true`
FAILS depth validation (a scalar cannot carry `findings`). This governs the goal_cycle
artifact's SHAPE. **Where the findings actually BLOCK:** the unresolved-finding gate
(`_blocked_findings`) runs at the DELIVER stage — so the final cross-path findings must
also be carried into the DELIVER `adversarial_review.findings[]` (steps above), where an
unresolved HIGH/blocking finding blocks completion exactly like a fresh deliver
adversarial. (goal_cycle's own schema enforces shape + presence; DELIVER enforces the
block — do not expect goal_cycle-stage validation alone to reject an unresolved finding.)
The pipeline then proceeds to DELIVER → REFLECT as normal stages.

### EXIT with CHECKPOINT

Max cycles reached without DoD met.

**Record Goal Metrics:**
```python
gm.track_goal_complete(status="checkpoint",
                       total_cycles=current_cycle, dod_met=<met>, dod_total=<total>)
```

Output:
```
Goal Loop CHECKPOINT after N cycles:
- DoD criteria: X/Y met
- Progress trend: [improving/stalled/regressing]
- What's left: [remaining criteria with current values]
- Recommendation: [extend cycles / adjust DoD / manual fix needed]
```

Creates Radar todo for user.

### EXIT with STOP

3 consecutive cycles with zero DoD progress.

**Record Goal Metrics:**
```python
gm.track_goal_complete(status="stop",
                       total_cycles=current_cycle, dod_met=<met>, dod_total=<total>)
```

Output:
```
Goal Loop STOPPED — structural blocker detected:
- Last 3 cycles: [actions taken]
- No criterion improved
- Likely cause: [diagnosis]
- Suggestion: [decompose goal / different approach / human intervention]
```

### EXIT with REVERT_LIMIT

2 consecutive cycle reverts (changes break existing tests, can't make progress).

**Record Goal Metrics:**
```python
gm.track_goal_complete(status="revert_limit",
                       total_cycles=current_cycle, dod_met=<met>, dod_total=<total>)
```

Output:
```
Goal Loop CONFLICT — cannot progress without regression:
- Cycle N: [action] → broke [test]
- Cycle N+1: [different action] → broke [test]
- Conflicting constraints: [what the goal requires vs what existing tests enforce]
- Suggestion: [refactor needed / relax test / redefine goal scope]
```

### EXIT with BUDGET

Remaining tokens < 150K mid-session.

**Record Goal Metrics:**
```python
gm.track_goal_complete(status="budget",
                       total_cycles=current_cycle, dod_met=<met>, dod_total=<total>)
```

Output:
```
Goal Loop paused — budget conservation:
- Cycles completed: N
- DoD: X/Y met
- Progress saved to: [progress_path]
- Resume: "resume pipeline for SwarmAI" or scheduled mode
```

---

## REFLECT (Two-Tier)

### Mini-Reflect (per cycle)

Already handled in Step 9 — one-line insight appended to progress file.
No DDD writes. No LLM distillation. Accumulates raw material.

### Full REFLECT (at goal completion)

**This now happens in the formal REFLECT stage (after DELIVER).** When
goal_cycle exits with SUCCESS, the pipeline proceeds to:
1. **DELIVER** — standard deliver stage behavior (packaging, report, CI)
2. **REFLECT** — standard reflect stage behavior (DDD loop closure)

The REFLECT stage reads all mini-reflects from the progress file as
input context, combining them with the standard reflect workflow
(lessons → IMPROVEMENT.md → DDD update → PROJECT.md).

This ensures goal runs get the same DDD loop closure as full runs —
no separate inline implementation needed.

---

## Progress File Format

```markdown
# Goal: <goal description>

## Definition of Done
- [x] <criterion 1> (met cycle N)
- [ ] <criterion 2> (current: <value>, target: <value>)
- [ ] <criterion 3> (current: <value>, target: <value>)

## Configuration
- Max cycles: <N>
- Review cadence: every <N> cycles
- Cycle scope: <description>
- Start commit: <hash>
- Last review commit: <hash>

## Metrics
| Cycle | Date | Metric | Delta | Action |
|-------|------|--------|-------|--------|
| 1 | 2026-05-14 | 73% → 76% | +3% | Added tests for memory.py |
| 2 | 2026-05-14 | 76% → 79% | +3% | Added tests for session.py |

## Current State
- Next target: <what to work on>
- Lowest-hanging fruit: <specific file/module>

## Blockers
(none, or list of blocked items with reasons)

## Findings Ledger
<!-- Cross-cycle finding tracking (Cross-Cycle Finding Re-Judgment, step 10.5).
     Every REVIEW/adversarial finding from ANY cycle lives here with a status
     that is RE-JUDGED against the current diff each cadence. This is what
     catches "fixed in cycle 3, regressed in cycle 7" — a plain-text Blockers
     dump cannot. Severity + confidence are carried so the finding can be
     handed to the DELIVER confidence gate verbatim. -->
| ID | Cycle Found | Severity | Confidence | Status | File:Line | Finding |
|----|-------------|----------|------------|--------|-----------|---------|
| F1 | 2 | MEDIUM | 8 | RESOLVED (cycle 4) | foo.py:12 | missing null guard |
| F2 | 3 | HIGH | - | APPLICABLE | bar.py:88 | race on shared dict |

<!-- Status values: APPLICABLE (still reproduces against current diff) |
     RESOLVED (verified fixed against current diff) | OBSOLETE (the code it
     referenced no longer exists). Only APPLICABLE + unresolved findings are
     carried into the DELIVER adversarial_review.findings. -->

## Cycle Log
**Cycle 1:** <insight>
**Cycle 2:** <insight>
```

---

## Scheduled Mode (Job System)

For goals spanning multiple sessions, create a job after EVALUATE+PLAN:

```yaml
# Append to ~/.swarm-ai/SwarmWS/Services/swarm-jobs/user-jobs.yaml
jobs:
  - id: goal-<slug>
    name: "<goal description>"
    type: agent_task
    schedule: "0 */4 * * *"  # every 4 hours (adjust as needed)
    enabled: true
    category: user
    config:
      prompt: |
        Resume goal loop for SwarmAI project.
        Progress file: <progress_path>
        
        Steps:
        1. Read progress file
        2. Check DoD criteria — if ALL met, disable this job and notify owner
        3. If not met: execute ONE cycle (pick step, implement, test, update progress)
        4. Save progress and exit
        
        Constraints:
        - One cycle only per execution
        - Do not run REVIEW or ADVERSARIAL (those run at cycle boundaries inline)
        - If stuck (same state 3 runs): disable job and create Radar todo
      create_todos: false
    safety:
      max_budget_usd: 1.50
      timeout_seconds: 600
```

**Note:** Scheduled mode loses context between executions. The progress file
is the ONLY state carrier. Write progress clearly enough that a fresh agent
can pick up where the last one left off.

---

## Inline vs Scheduled Decision

| Factor | Inline | Scheduled |
|--------|--------|-----------|
| Goal completable in ~10 cycles | ✅ Use this | Overkill |
| Goal needs 30+ cycles | Too expensive (budget) | ✅ Use this |
| Context continuity matters | ✅ Full context | ❌ Fresh each time |
| Overnight/unattended | ❌ Needs session open | ✅ Runs via cron |
| Quality gates (REVIEW, ADVERSARIAL) | Integrated per cycle | Only at completion |

Default: **inline**. Switch to scheduled when EVALUATE estimates >10 cycles
or the user explicitly requests overnight execution.
