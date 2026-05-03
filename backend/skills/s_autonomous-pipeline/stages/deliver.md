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
-1 per unresolved warning from validator
```

If confidence < 7 -- flag for human review even without judgment decisions.

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

## 7.5 Completion Audit
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
