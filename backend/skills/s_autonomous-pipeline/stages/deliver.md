# DELIVER Stage

## Base Methodology

> **Reference:** `backend/skills/s_deliver/SKILL.md`
>
> Follow the packaging methodology defined there for structured deliverables, decision logs, and attention flags.

## Pipeline-Specific Behavior

### Execution Order

```
1. Fresh User Audit (P6) — if user-facing infra changed
2. Completion Audit — AC → evidence verification
3. Adversarial Review Gate — spawn specialist sub-agents (multi-domain review)
4. Meta-Review — spawn sub-agent (operational blind spots)
5. Push-Ready Gate — binary final verdict
```

### Push-Ready Gate (Binary — Final Verdict)

**Evaluated LAST, after all other checks complete.**

No numeric score. Binary: PUSH-READY or NOT-PUSH-READY.

Numeric confidence (C011: 10/10 with 100% broken code) measured process compliance,
not code correctness. A number between "push" and "don't push" creates false
gradients — there is no meaningful difference between 7/10 and 8/10.

**PUSH-READY requires ALL of these (any failure = NOT-PUSH-READY):**

```
□ All acceptance criteria have passing tests (no AC without evidence)
□ Zero HIGH/MED findings (confidence >= 7) from adversarial review (or all fixed)
□ Completion audit: all_green = true (deliverables match requirement)
□ Zero regressions on existing tests
□ Meta-review completed with no unaddressed HIGH risks
```

**NOT-PUSH-READY triggers:**
- Any unfixed HIGH finding → block
- Any AC without a passing test → block
- Completion audit gap → block
- Meta-review HIGH risk unaddressed → escalate to user

**Output:** `{"push_ready": true/false, "blockers": [...]}` — no score, no gradient.

---

### Fresh User Audit (P6)

**If the changeset touches user-facing infrastructure**
(deploy, onboarding, config, CLI, API endpoints), answer this question:

> "Could a new user, starting from zero, successfully use this feature
> without modifying source code?"

Check:
1. **No stale values** — hardcoded IPs, usernames, paths, URLs that only
   work for the developer
2. **No hidden prerequisites** — tools, keys, permissions, env vars that
   aren't documented or auto-provisioned
3. **No placeholder that passes silently** — config values that look valid
   but aren't (e.g., a bcrypt hash placeholder that Caddy accepts but
   always rejects auth)
4. **Clear error messages** — if prerequisites are missing, the error says
   what's wrong and how to fix it (not a raw stack trace)
5. **Single documented path** — if multiple ways to do the same thing exist,
   only one is documented; alternatives are deprecated or removed

**Output:** List findings as attention flags. Each flag = a friction point
for a new user.

**Why this exists:** Hive provisioner passed 28 tests and 10/10 confidence
but a "fresh user" audit found 7 P0 gaps in 10 minutes: hardcoded GitHub
repo, S3 bucket collision, stale IPs in update script, placeholder hash that
looked valid, npm blocking EC2 startup, auth_password in list response, no
permission pre-check. All invisible to automated tests because tests run in
the developer's environment. (2026-04-29 + 2026-05-03)

### Completion Audit

**After the push-ready gate, before generating the report,** run the Completion
Audit Protocol. This verifies that deliverables actually match the requirement —
the push-ready gate checks "did we pass all quality gates", the audit checks "did we
build the right thing."

```
COMPLETION AUDIT — Verify before declaring done.

1. RESTATE: What were the deliverables?
   - Copy the original requirement verbatim
   - List every acceptance criterion from EVALUATE

2. CHECKLIST: For each deliverable, what's the evidence?
   | # | Acceptance Criterion | Evidence | Status |
   |---|---------------------|----------|--------|
   | 1 | AC text...          | test_xxx passes, file created | ✅ |
   | 2 | AC text...          | [no evidence found]           | ❌ |

2.5. INDEPENDENT AC VERIFICATION (Pre-flight for Adversarial)
   For each AC → test mapping claimed in Step 2:

   a. READ the test file and function body (fresh read, not from memory)
   b. VERIFY the test exercises the AC's behavior:
      - Does the test input match what the AC describes?
      - Does the assertion check the AC's expected output?
      - Could this test pass while the AC is NOT satisfied?
        (If yes → test is necessary but not sufficient → flag as ⚠️)
   c. TRACE one level downstream:
      - Does the implementation connect to the tested interface?
      - Is there a gap between "unit passes" and "feature works E2E"?

   OUTPUT: AC Verification Matrix
   | # | AC | Test | Verifies AC? | E2E Connected? |
   |---|----|----- |--------------|----------------|
   | 1 | ... | test_foo | ✅ Yes | ✅ Yes |
   | 2 | ... | test_bar | ⚠️ Unit only | ❌ No E2E |
   | 3 | ... | test_baz | ❌ Wrong assertion | — |

   VERDICT:
   - All ✅/✅ → set ac_verification.status = "verified", proceed
   - Any ⚠️ or ❌ → fix before proceeding (write integration test, fix assertion)
   - After fix: re-verify only the fixed items, then proceed
   - If fixes attempted but ❌ persists → set ac_verification.status = "failed"

   Record in run.json:
   ```json
   {
     "ac_verification": {
       "status": "verified",  // or "claimed" or "failed"
       "matrix": [
         {"ac": "...", "test": "test_foo", "verifies_ac": true, "e2e_connected": true}
       ]
     }
   }
   ```

   **Why this exists:** C011 (Voice Mode) passed all stages with 10/10 confidence
   and 57 green tests. Feature was 100% non-functional. Builder claimed tests
   verified the spec — they didn't. This step separates "builder claims evidence"
   from "verifier confirms evidence." Same principle as adversarial review
   (different read of the same code catches assumption drift), but cheaper and
   targeted at the AC→test mapping specifically.

   **Push-ready impact:**
   - status="verified" → gate passes (AC evidence confirmed)
   - status="claimed" (or step skipped) → warning (tests exist, unverified)
   - status="failed" → NOT-PUSH-READY (blocker: known gap in spec satisfaction)

3. INSPECT: Verify evidence exists (don't trust memory)
   - For each ✅: cite the specific file, test name, or output
   - For each test cited: confirm it actually tests the criterion
     (not just named similarly)
   - For each file cited: confirm the relevant code/content exists

4. GAPS: What's missing or incomplete?
   - List any ❌ items from step 2
   - List anything the requirement implies but AC didn't capture
   - grep for "TODO", "FIXME", "placeholder" in changed files

5. VERDICT:
   - ALL GREEN → set completion_audit.all_green = true, proceed
   - ANY GAP → fix before proceeding (loop back to BUILD/TEST)
   - UNFIXABLE GAP → surface as attention flag with explanation
```

**Record the audit results in run.json** (used by push-ready gate):

```json
{
  "completion_audit": {
    "all_green": true,
    "gaps": 0,
    "unfixable_gaps": 0
  }
}
```

**Push-ready impact:**
- `all_green: true` → gate passes
- `gaps > 0` (not fixed) → NOT-PUSH-READY (blocker)
- `unfixable_gaps > 0` → WARNING (note in report, user decides)

### Adversarial Review Gate (BLOCKING) — Multi-Specialist Review Army

**After Completion Audit, BEFORE generating the report or committing.**

Independent sub-agent reviews that break builder context bias. Multiple domain
specialists review in parallel — each with isolated context and focused expertise.
A generalist misses domain-specific bugs; specialists find what generalists can't.

**Why multi-specialist:** A single reviewer simultaneously checking Security +
Performance + Correctness + API Contract suffers attention dilution. Each domain
requires a different mindset (attacker vs scaling vs logic vs contract). Parallel
specialists with isolated context produce deeper, more confident findings.
(Adopted from gstack's Review Army pattern — verified in production.)

**Why mandatory:** 12+ pipeline runs in IMPROVEMENT.md show reviews find critical
bugs AFTER confidence was high: voice input 100% non-functional (C011), 3 CRITICAL
subprocess bugs (run_c2881d2f), 4 hallucinated false-positive criticals (IMPROVEMENT.md).
Confidence gating solves false positives; specialists solve false negatives.

**⚠️ MECHANICALLY ENFORCED:** `run-update --status completed` validates the deliver
artifact's `adversarial_review.profile_tier` field. If tier is `skipped`/`lite` for
full/bugfix profiles → pipeline completion is **BLOCKED by code**. You cannot close
the pipeline without proof that adversarial review ran at the correct tier. This is
not a prompt guideline — it is a programmatic gate in `artifact_cli.py` that refuses
to write `status: completed` until the validator passes.

---

#### Profile-Aware Tiering

| Profile | What runs | Rationale |
|---------|-----------|-----------|
| **full, standard** | All specialists (scope-gated) + Red Team (conditional) | New capability = highest risk |
| **bugfix** | Correctness + Security only | Narrow scope, skip performance/API |
| **trivial** | Skip entirely | One-line fix, tests pass, not worth the token cost |
| **research, docs** | Skip entirely | No code changes |

---

#### Step 1: Scope Detection

Analyze the changeset to determine which specialists to dispatch:

```bash
# Get changed files
git diff --name-only origin/main...HEAD 2>/dev/null || git diff --name-only HEAD~1
```

**Dispatch rules:**

| Specialist | Dispatch When | Checklist |
|-----------|---------------|-----------|
| **Correctness** | Always (if changeset > 50 lines) | `stages/specialists/correctness.md` |
| **Security** | Files touch: routers/, handlers, auth, database queries, user input, file paths | `stages/specialists/security.md` |
| **Performance** | Files touch: endpoints, database, loops over collections, hooks, background tasks | `stages/specialists/performance.md` |
| **API Contract** | Files touch: routers/, schemas/, models, response types, frontend services | `stages/specialists/api-contract.md` |
| **Red Team** | CONDITIONAL: changeset > 200 lines OR any specialist found HIGH severity | `stages/specialists/red-team.md` |

**For bugfix profile:** Only dispatch Correctness + Security.

**🚨 MECHANICAL OVERRIDE: diff > 100 lines = full tier, regardless of profile.**

```bash
DIFF_LINES=$(git diff --stat origin/main...HEAD 2>/dev/null | tail -1 | grep -oE '[0-9]+ insertion' | grep -oE '[0-9]+')
DIFF_LINES=${DIFF_LINES:-0}
if [ "$DIFF_LINES" -gt 100 ]; then
  echo "TIER OVERRIDE: $DIFF_LINES insertions > 100 → forcing FULL adversarial (all specialists)"
fi
```

If override triggers: dispatch ALL applicable specialists per the dispatch rules
above, not just the profile's subset. A 382-line refactor in "bugfix" profile
is not a "bugfix" in adversarial review terms — it's a cross-module migration
with concurrency, import order, and dead-code risks that only full specialist
coverage catches. This gate exists because run_12f19e0e (2026-05-16) used lite
tier on a 382-line migration; PE review caught a HIGH (shell variable scope)
that full adversarial would have found.

Count the total changed lines. If < 50 lines, skip all specialists.
Print dispatch summary:
```
Dispatching N specialists: [names]. Skipped: [names] (scope not detected).
```

---

#### Step 2: Parallel Specialist Dispatch

**BLOCKING: Use the Agent tool to spawn ALL selected specialists in a SINGLE
message** (multiple Agent tool calls) so they run in parallel. Each sub-agent
has fresh context — no prior review bias.

**Sub-agent prompt template (per specialist):**

```
You are a specialist code reviewer focused exclusively on <DOMAIN>.
Read the checklist below, then use the Read tool to read EVERY changed
file listed. Do not skip any file — review all of them.
Apply the checklist against the code.

## Context
Project: <PROJECT>
Requirement: <requirement from run.json>
Files changed: <list of all changed files>

## Project-Specific Traps (from TECH.md)
<paste "Runtime Environment Traps" or "Architecture Invariants" section from
the project's TECH.md — these are proven footguns in THIS codebase that
generic checklists don't cover. If TECH.md has no such section, omit this block.>

## Checklist
<paste contents of the specialist's .md file>

## Output
For each finding, output a JSON object on its own line:
{"severity":"HIGH|MED|LOW","confidence":N,"path":"file","line":N,"category":"<domain>","summary":"description","fix":"recommended fix","fingerprint":"path:line:category","specialist":"<name>"}

Required fields: severity, confidence, path, category, summary, specialist.
Optional: line, fix, fingerprint, evidence, exploit (required for security specialist).

If no findings: output `NO FINDINGS` and nothing else.
Do not output anything else — no preamble, no summary, no commentary.

Be specific. Every finding needs: file, line (or function), what's wrong,
and how to fix it. Vague findings ("could be improved") are rejected.
```

**Sub-agent configuration:**
- Use default model (opus) — adversarial review needs strongest reasoning
- Do NOT use `run_in_background` — all specialists must complete before merge
- If any specialist fails or times out, log the failure and continue with
  results from successful ones. Partial results > no results.

---

#### Step 3: Collect, Merge, and Deduplicate

After all specialist sub-agents complete:

**3a. Parse findings:**
- "NO FINDINGS" → skip (specialist found nothing)
- Otherwise: parse each JSON line, collect into unified list

**3b. Fingerprint and deduplicate:**
- Fingerprint: `{path}:{line}:{category}` (or `{path}:{category}` if no line)
- Same fingerprint from multiple specialists → keep highest confidence, tag:
  "MULTI-SPECIALIST CONFIRMED (specialist1 + specialist2)", boost confidence +1 (cap 10)

**3c. Apply confidence gates (Unified Confidence Rubric):**
- Confidence 7+: show normally in findings output
- Confidence 5-6: show with caveat "⚠️ Medium confidence — verify"
- Confidence 3-4: suppress from main findings (appendix only)
- Confidence 1-2: suppress entirely

---

#### Step 4: Red Team (Conditional, Sequential)

**Runs AFTER specialists complete** (needs their findings as input — intentionally
not parallel). Adds ~30s wall-clock time when triggered.

**Dispatch Red Team ONLY IF:**
- Total changeset > 200 lines, OR
- Any specialist produced a HIGH severity finding

If neither condition met, skip Red Team.

**Red Team sub-agent receives:**
1. The red-team checklist from `stages/specialists/red-team.md`
2. The merged specialist findings (so it knows what was already caught)
3. The list of changed files

Red Team findings merge into the unified list with same dedup/gating rules.

---

#### Step 5: Fix Findings

**For all findings that survived confidence gating (Step 3c — confidence >= 5):**
- HIGH severity: fix immediately (auto-fix)
- MED severity: fix if confidence >= 7, otherwise note with recommendation
- LOW severity: note in pipeline report only

Re-run affected tests after any code fix.

---

#### Gate Outcome

```
Adversarial Review Gate (Multi-Specialist):
  Specialists dispatched: N (correctness, security, performance, api-contract)
  Red Team: dispatched / skipped (reason)
  Total findings: N (X HIGH, Y MED, Z LOW)
  After confidence gating: M shown (K suppressed)
  Fixed: F | Noted: N
  Multi-specialist confirmed: C findings

  PASS → enter Quality Convergence Loop (INSTRUCTIONS.md Step 4c)
  FAIL → loop back: fix → re-test (max 1 loop)
```

**After adversarial review passes:** Enter the Quality Convergence Loop
(INSTRUCTIONS.md Step 4c). The convergence loop re-verifies all 6 push-ready
layers (including that adversarial findings stay fixed) and iterates up to
3 times if gaps remain. Only after convergence declares push-ready → proceed
to Pipeline Report.

**Record in run.json:**
```json
{
  "adversarial_review": {
    "profile_tier": "full|bugfix|skipped",
    "specialists_dispatched": ["correctness", "security", "performance"],
    "red_team_dispatched": true,
    "total_findings": 8,
    "after_confidence_gate": 5,
    "suppressed": 3,
    "high_fixed": 2,
    "med_fixed": 1,
    "low_noted": 2,
    "multi_confirmed": 1
  }
}
```

---

### Meta-Review: "What Did the Pipeline Miss?" (BLOCKING)

**After adversarial review passes, BEFORE declaring push-ready.**

A different sub-agent that doesn't review the CODE — it reviews the PIPELINE'S
BLIND SPOTS for this specific changeset. This is the PE review layer that was
previously manual (user had to ask "从PE角度看下").

**Why this exists:** Pipeline REVIEW + adversarial consistently catch code
correctness bugs but miss operational/scaling/deployment-context bugs:
- run_d73239fe: O(n) no-op scan in a per-session hook (RP30)
- run_bded2f47: sys.executable in daemon context (environment assumption)
- run_91a6fb7e: cross-language JSON space after colon (format assumption)

These are NOT code bugs — the code is correct in dev. They're
**deployment context mismatches** that only surface in production.

**Spawn sub-agent:**

```
Agent({
  description: "Meta-review — pipeline blind spot analysis",
  prompt: <template below>
})
```

**Sub-agent prompt template:**

```
You are NOT reviewing the code for bugs. The adversarial reviewer already did that.

You are reviewing what the PIPELINE LIKELY MISSED — operational, scaling, and
deployment-context issues that code review structurally cannot catch.

## Context
Project: <PROJECT>
Requirement: <requirement>
Files changed: <list>
Where this code runs: <hook/endpoint/cron/startup/CLI — infer from file path>

## Your Analysis (answer each explicitly)

1. DEPLOYMENT CONTEXT
   - Where does this code run? (daemon 24/7, sidecar, CLI, hook, cron)
   - Does it have different behavior in dev vs production?
   - Are there assumptions that hold in dev but not in production?
     (sys.executable, $HOME, network access, file permissions, concurrency)

2. OPERATIONAL SCALING
   - What's the no-op cost? (this runs every <interval> — what happens when
     there's nothing to do?)
   - Does cost scale with data history or just recent data?
   - What's the steady-state after 6 months of accumulation?

3. CROSS-BOUNDARY FORMAT
   - Does this produce/consume data across language boundaries?
   - Are there format assumptions (JSON spacing, encoding, line endings)?
   - Does a serializer/parser pair from different libraries agree on format?

4. FIRST-RUN vs STEADY-STATE
   - Is there a backlog that gets processed on first deployment?
   - Could first-run side effects be different from steady-state?
   - Is the first-run behavior safe (won't corrupt, won't flood)?

## Output
```json
{
  "risks": [
    {"category": "deployment|scaling|format|first-run",
     "description": "...",
     "severity": "HIGH|MED|LOW",
     "mitigation": "..."}
  ],
  "verdict": "CLEAR" | "RISKS_IDENTIFIED"
}
```

If verdict is CLEAR: "I found no operational blind spots. Pipeline coverage
was adequate for this changeset."

If verdict is RISKS_IDENTIFIED: list each risk with concrete mitigation.
```

**After meta-review returns:**

- `CLEAR` → proceed to push-ready gate
- `RISKS_IDENTIFIED` with HIGH → fix before push-ready (same as adversarial HIGH)
- `RISKS_IDENTIFIED` with only MED/LOW → note in report, proceed (tech debt awareness)

**Profile gating:**
- full, bugfix → run meta-review
- trivial, research, docs → skip (no operational context to analyze)

---

### Pipeline Report

Generate pipeline report as markdown in the project's artifacts directory.
Save to: `Projects/<PROJECT>/.artifacts/runs/<RUN_ID>/REPORT.md`

The report follows this template (every run produces one):

```markdown
# Autonomous Pipeline Report: <title>

**Run ID:** run_<id> | **Project:** <PROJECT> | **Profile:** <profile>
**Date:** <ISO date> | **Status:** PUSH-READY / NOT-PUSH-READY

## TL;DR
<2-3 sentences: what was built, what problem it solves, what value it delivers.
Written for someone who won't read the rest of the report. Skip jargon.>

## 1. Requirement
<original requirement text>

## 2. Evaluation
| Dimension | Score | Rationale |
|---|---|---|
| Strategic | X/5 | ... |
| Feasibility | X/5 | ... |
| ROI | X.X | GO/DEFER/REJECT |

**Scope:** <classification> | **Acceptance Criteria:** <list>

## 3. Methodology: DDD + SDD + TDD

### DDD Knowledge Applied
| Stage | Doc | Insight | Decision Impact |
|-------|-----|---------|-----------------|
| <stage> | <PRODUCT/TECH/IMPROVEMENT/PROJECT.md> | <specific quote or fact> | <what it changed> |

(Only list instances where DDD CHANGED an outcome. Omit stages where docs were read but didn't influence decisions.)

### Approach
<chosen approach from THINK/PLAN or direct — one sentence>

### TDD Cycle
RED (<N> tests generated, all failed) → GREEN (code until pass) → VERIFY (full suite)
Bugs caught in RED phase: <N> (<brief description of most significant>)

## 4. Pipeline Execution
| Stage | Status | Artifact | Key Output |
|---|---|---|---|
| EVALUATE | done | art_xxx | GO, ROI X.X |
| THINK | done/skip | art_xxx | ... |
| ... | ... | ... | ... |

## 5. TDD Results
| Metric | Value |
|---|---|
| Acceptance criteria | N |
| Tests generated | M |
| Tests per criterion | X.X |
| Bugs caught (RED phase) | K |
| Smoke tests (new paths) | S |
| Runtime crashes caught by smoke | C |
| User-path traces | T |
| Bugs found by user-path trace | B |
| Regressions | 0 |
| Total test suite | N tests, all passing |

## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|---|---|---|---|
| BUILD | ... | mechanical | ... |

## 7. Quality Gates
| Gate | Result |
|---|---|
| REVIEW (code quality) | N findings, M auto-fixed |
| REVIEW (security) | clean / N findings |
| REVIEW (integration) | N symbols checked, M connected, K warnings |
| BUILD (user-path) | T traces, B bugs found and fixed |
| TEST (TDD) | pass |
| VALIDATOR | 6/6 checks |
| Push-Ready | ✅ PUSH-READY / ❌ NOT-PUSH-READY (blockers: ...) |

## 7.5 Adversarial Review (Multi-Specialist)
| Specialist | Dispatched | Findings | Fixed | Noted |
|-----------|-----------|----------|-------|-------|
| Correctness | ✓/✗ | N | M | K |
| Security | ✓/✗ | N | M | K |
| Performance | ✓/✗ | N | M | K |
| API Contract | ✓/✗ | N | M | K |
| Red Team | ✓/✗ (conditional) | N | M | K |

**Confidence gating:** N total → M shown (K suppressed at confidence ≤4)
**Multi-specialist confirmed:** N findings confirmed by 2+ specialists

**Key findings (HIGH/MED, confidence >= 7 only):**
| ID | Specialist | Finding | Why Invisible to Other Gates | Fix Applied |
|----|-----------|---------|------------------------------|-------------|
| <S-1> | <specialist> | <specific issue> | <why BUILD/REVIEW/TEST couldn't catch this> | <fix> |

**Gate value:** <HIGH/MED/LOW — one sentence explaining what specialist review uniquely provided>

## 7.6 Meta-Review (Pipeline Blind Spot Analysis)
| Category | Verdict | Detail |
|----------|---------|--------|
| Deployment context | CLEAR / RISK | <what was found> |
| Operational scaling | CLEAR / RISK | <no-op cost, steady-state> |
| Cross-boundary format | CLEAR / RISK | <format assumptions> |
| First-run vs steady-state | CLEAR / RISK | <backlog behavior> |

**Overall:** CLEAR / RISKS_IDENTIFIED (N items, M addressed)

## 7.7 Completion Audit
| # | Acceptance Criterion | Evidence | Verified |
|---|---------------------|----------|----------|
| 1 | ... | test_xxx.py::test_yyy | ✅ |
| 2 | ... | [gap: not implemented] | ❌ |

**Gaps found:** N | **Gaps fixed:** M | **Attention flags:** K

## 8. Files Changed
- `path/to/file.py` (created, N lines)
- `path/to/other.py` (modified)

## 9. Lessons (from REFLECT)
- Lesson 1
- Lesson 2

## 10. Known Gaps & Attention Flags
<any warnings, meta-review risks accepted, or deferred issues>

## 11. Methodology Impact
| Concept | Decision Point | Impact | Counterfactual (without this) |
|---------|---------------|--------|-------------------------------|
| DDD Knowledge | <stage: specific moment> | <what it prevented or enabled> | <what would have happened> |
| TDD (RED→GREEN) | <stage: specific moment> | <bug caught or design improved> | <what would have shipped> |
| Quality Convergence | <iteration count + what it caught> | <cross-module or system-level issue> | <what would have shipped> |
| Adversarial Review | <finding summary> | <what only fresh eyes could find> | <what would have shipped> |
| Goal Loop | <cycle count + velocity insight> OR N/A | <what iterative execution enabled> | <manual scoping + no velocity learning> OR N/A |

**Rules for this table:**
- Every row must cite a SPECIFIC moment from THIS run — no generic claims
- If a concept had zero impact, write "N/A" with reason (honest > impressive)
- Counterfactual answers: "What would have shipped to production without this?"
- An honest "N/A — bounded feature" is more credible than a manufactured insight

---
Generated by SwarmAI Autonomous Pipeline | <date>
```

### CI Health Check (Blocking)

After committing, verify the latest CI run on `main` is green. Pipeline output
is only "delivered" when CI confirms it doesn't break backend, frontend, or
version consistency.

```bash
# Check the CI run for the exact HEAD commit (full SHA, no prefix collision)
HEAD_SHA=$(git rev-parse HEAD)
gh run list --branch main --limit 5 --json name,conclusion,headSha \
  | python3 -c "
import json, sys
runs = json.load(sys.stdin)
head = '$HEAD_SHA'
ci = [r for r in runs if r['name'] == 'CI' and r['headSha'] == head]
if not ci:
    print('⏳ CI not started yet for HEAD — push first, then wait')
    sys.exit(0)
c = ci[0]['conclusion']
if c == 'success':
    print('✅ CI green — delivery confirmed')
elif c is None:
    print('⏳ CI still running — check back in ~2min')
else:
    print(f'❌ CI {c} — fix before declaring done')
    sys.exit(1)
"
```

If CI is red, the pipeline must fix the issue and re-commit before DELIVER is complete.
Do NOT proceed to REFLECT with red CI — a delivered feature that breaks the build is not delivered.

### Auto PR Creation (full/bugfix profiles only)

After CI confirms green, create a PR automatically to close the "Coding as
Black Box" delivery loop. The user stated a requirement; now a PR appears.

```bash
python backend/skills/s_autonomous-pipeline/scripts/pipeline_pr.py \
  --run-dir <run_dir>
```

**Guards (handled by the script internally):**
- Profile must be `full` or `bugfix` (research/docs/goal/trivial = skip silently)
- `gh auth status` must succeed (else: warn, don't block — push-ready is still valid)
- If on `main` branch: creates `pipeline/<run_id>` branch, pushes, then PRs against main
- If on feature branch: pushes to remote with `-u`, then PRs against main

**PR contents:**
- Title: `feat(<scope>): <requirement condensed>` (always <=70 chars)
- Body: TL;DR + Pipeline Delivery stats + Files Changed + link to full REPORT.md
- Flag: `--auto` (auto-merge when CI required checks pass)

**Failure handling (AC6):**
- PR creation failure is a WARNING, not an error
- Pipeline status is still "push-ready" regardless of PR outcome
- Record result in run.json under `"pr_result"` field

**When to use `--dry-run`:**
- User explicitly said "don't create PR" or "I'll handle the PR"
- Pass `--dry-run` to get the formatted command without executing

Record PR URL in run.json if successful:
```json
{"pr_result": {"success": true, "pr_url": "https://github.com/..."}}
```

### PROJECT.md Update

Update PROJECT.md with delivery entry.

### Unresolved Issues

Check for unresolved issues from upstream stages.

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type delivery --producer s_autonomous-pipeline \
  --summary "Delivery: <feature title> (PUSH-READY)" --stage deliver \
  --data '{"title":"...","quality":{"tests_pass":true,"regressions":0,"smoke_pass":true},"adversarial_review":{"spawned":true,"profile_tier":"full","findings_total":N,"findings_fixed":N,"findings_remaining":0,"findings":[{"severity":"HIGH|MEDIUM","resolved":true,"finding":"path/file.py func() line N: issue. Fixed: how."}]},"completion_audit":{"all_green":true,"requirements_met":N,"requirements_total":N,"evidence":"..."},"meta_review":"...","report_path":"runs/<RUN_ID>/REPORT.md"}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state reflect
```
