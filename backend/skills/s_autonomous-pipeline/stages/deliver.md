# DELIVER Stage

## Base Methodology

> **Reference:** `backend/skills/s_deliver/SKILL.md`
>
> Follow the packaging methodology defined there for structured deliverables, decision logs, and attention flags.

## Pipeline-Specific Behavior

### Delivery Gate

Run the Delivery Gate first (see confidence scoring below), then proceed.

### Confidence Scoring

Assess confidence via script:

```bash
python backend/skills/s_autonomous-pipeline/scripts/confidence_score.py --run-dir <path>
```

Confidence score formula (1-12):
```
+3 if all acceptance criteria have passing tests
+2 if review found 0 critical issues
+2 if TDD red-green cycle completed cleanly
+2 if completion_audit.all_green (every AC has verified evidence)
+1 if no taste decisions were overridden
+1 if zero regressions on existing tests
+1 if design_doc was available (not just evaluation)
-3 if completion_audit.gaps > 0 and gaps not fixed (deliverable mismatch)
-2 if any acceptance criterion lacks a test
-2 if WTF gate triggered (even if resolved)
-2 if smoke_tests == 0 and files_changed > 1 (runtime crashes likely hidden)
-2 if user_path_traces == 0 and files_changed > 1 (real data flow unverified)
-1 if completion_audit.unfixable_gaps > 0 (known incompleteness)
-1 if integration_trace.checked == 0 (wiring unverified)
-1 if frontend files changed but ux_review.triggered == false (UX unverified)
-1 if runtime_patterns.checked == 0 and applicable patterns exist (known bugs unchecked)
-2 if frontend+backend changed but wire_test.boundaries == 0 (cross-layer contract unverified)
-2 if new endpoint + frontend consumer but probes == 0 (real HTTP path untested)
-1 if lifecycle ops changed but operational_patterns.checked == 0 (OP invariants unchecked)
-1 if state transitions added but inverse_operations.checked == 0 (stuck states possible)
-3 if adversarial_review not run (user-side + PE-side are mandatory)
-3 if adversarial_review has unfixed HIGH PE finding
-1 per unresolved warning from validator
```

If confidence < 7 -- flag for human review even without judgment decisions.

### Fresh User Audit (P6)

**Before confidence scoring, if the changeset touches user-facing infrastructure**
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

**After confidence scoring, before generating the report,** run the Completion
Audit Protocol. This verifies that deliverables actually match the requirement —
confidence scoring checks "did we follow the process", the audit checks "did we
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

**Record the audit results in run.json** (used by confidence scoring):

```json
{
  "completion_audit": {
    "all_green": true,
    "gaps": 0,
    "unfixable_gaps": 0
  }
}
```

**Confidence impact:**
- `all_green: true` → +2 bonus
- `gaps > 0` (not fixed) → -3 penalty
- `unfixable_gaps > 0` → -1 penalty

### Adversarial Review Gate (BLOCKING)

**After Completion Audit, BEFORE generating the report or committing.**

Independent sub-agent review that breaks builder context bias. The builder
agent is too close to the code to catch its own blind spots — a fresh agent
reading the diff cold finds different classes of bugs.

**Why sub-agent, not inline:** The same model in the same context can't
"pretend" to be a different reviewer — cognitive bias doesn't vanish from
a "perspective shift" prompt. A sub-agent starts with zero builder context:
no memory of design decisions, no emotional attachment to the approach. This
is the closest we get to a real second pair of eyes. (Inspired by gstack's
cross-model triangulation pattern.)

**Why mandatory:** 12+ pipeline runs in IMPROVEMENT.md show PE/user reviews
find critical bugs AFTER 10/10 confidence: regex too greedy (this session),
voice input 100% non-functional (C011), 3 CRITICAL subprocess bugs
(run_c2881d2f). Mechanical REVIEW checks verify process; adversarial review
verifies reality.

---

#### Profile-Aware Tiering

| Profile | What runs | Rationale |
|---------|-----------|-----------|
| **full, standard** | Both passes (User-Side + PE) | New capability = highest risk |
| **bugfix** | PE pass only | Bug fixes have narrow scope, user-side trace is low ROI |
| **trivial** | Skip entirely | One-line fix, tests pass, not worth the token cost |
| **research, docs** | Skip entirely | No code changes |

---

#### Execution: Spawn Sub-Agent

**BLOCKING: Use the Agent tool to spawn an independent reviewer.** Do NOT
run this inline — the whole point is context isolation.

```
Agent({
  description: "Adversarial review of pipeline changeset",
  prompt: <see template below>
  // Use default model (opus). Adversarial review needs the strongest
  // code reasoning — subtle bugs (regex anchoring, status code misuse,
  // config injection) require deep understanding of surrounding context.
  // Sonnet saves 30s but risks missing the exact findings this gate exists to catch.
})
```

**Sub-agent prompt template:**

```
You are a paranoid staff engineer reviewing code you didn't write. Your job
is to find what the builder missed. You have no context about why decisions
were made — judge the code on its own merits.

## Context
Project: <PROJECT>
Requirement: <requirement from run.json>
Acceptance criteria: <list from evaluation artifact>
Profile: <full|bugfix>
Files changed: <list>

## Your Task

Read every changed file listed above. For each file, run two passes:

### Pass 1: User-Side Functional Review (SKIP if profile=bugfix)
For every changed function:
1. What user action triggers this? Trace from user → code, not code → user.
2. Walk the happy path through REAL code (not tests). Does data arrive in
   the right format at each boundary?
3. Walk 3 failure paths: concurrent execution, partial failure, missing
   prerequisite. What breaks?
4. Read 5 lines above and below. Stale code? Duplicate logic? Same bug?

### Pass 2: PE (Production Engineering) Review
For every changed line:
1. Correctness — does this do what the comment says? Edge cases handled?
   Regex/query/format correct for ALL inputs (not just test inputs)?
2. Robustness — empty/None/wrong-type input? Unexpected external response?
   File locked? DB missing?
3. Security — user input sanitized before shell/SQL/config/HTML/JSON?
4. Consistency — same error handling, naming, HTTP status codes as adjacent code?
5. Dead code — unused imports? Stale comments? Duplicate headers? Debug leftovers?

## Output Format
```json
{
  "user_side": [
    {"id": "U1", "severity": "HIGH|MED|LOW", "finding": "...", "fix": "..."}
  ],
  "pe_side": [
    {"id": "PE-1", "severity": "HIGH|MED|LOW", "finding": "...", "fix": "..."}
  ],
  "summary": "N user findings, M PE findings, K HIGH"
}
```

Be specific. "Regex might be wrong" is useless. "Regex `\\s+admin\\s+\\S+`
matches any word after 'admin' including comments — anchoring to bcrypt
format `\\$2[ab]\\$` prevents false match" is actionable.
```

**After sub-agent returns:**

1. Fix all HIGH and MED findings immediately
2. Note LOW findings in the pipeline report
3. Re-run affected tests if any code changed
4. Record results in run.json

---

#### Gate Outcome

```
Adversarial Review Gate:
  User-side: N findings, M fixed (or "skipped — bugfix profile")
  PE-side:   N findings, M fixed, K noted

  PASS → enter Quality Convergence Loop (INSTRUCTIONS.md Step 4c)
  FAIL → loop back: fix → re-test → re-review (max 1 loop)
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
    "profile_tier": "full|pe_only|skipped",
    "user_findings": 3,
    "user_fixed": 3,
    "pe_findings": 6,
    "pe_fixed": 5,
    "pe_noted": 1
  }
}
```

**Confidence impact:**
- Not run (when required by profile) → **-3**
- All HIGH/MED fixed → no penalty (gate working as designed)
- Unfixed HIGH PE finding → **-3** (blocking bug shipped)

---

### Pipeline Report

Generate pipeline report as markdown in the project's artifacts directory.
Save to: `Projects/<PROJECT>/.artifacts/runs/<RUN_ID>/REPORT.md`

The report follows this template (every run produces one):

```markdown
# Autonomous Pipeline Report: <title>

**Run ID:** run_<id> | **Project:** <PROJECT> | **Profile:** <profile>
**Date:** <ISO date> | **Confidence:** <score>/10

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
- **DDD docs loaded:** <which docs, what was learned>
- **Approach:** <chosen approach from THINK/PLAN or direct>
- **TDD cycle:** RED (<N> tests generated, all failed) -> GREEN (code until pass) -> VERIFY (full suite)

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
| Confidence | X/12 |

## 7.5 Adversarial Review
| Pass | Findings | Fixed | Noted |
|------|----------|-------|-------|
| User-side | N | M | — |
| PE-side | N | M | K |

## 7.6 Completion Audit
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
<any warnings, low-confidence items, or deferred issues>

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

### PROJECT.md Update

Update PROJECT.md with delivery entry.

### Unresolved Issues

Check for unresolved issues from upstream stages.

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type delivery --producer s_autonomous-pipeline \
  --summary "Delivery: <feature title> (confidence: <N>/10)" \
  --data '{"title":"...","summary":"...","decisions":[...],"quality":{...},"attention_flags":[],"confidence_score":N,"confidence_breakdown":{...},"report_path":"runs/<RUN_ID>/REPORT.md"}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state reflect
```
