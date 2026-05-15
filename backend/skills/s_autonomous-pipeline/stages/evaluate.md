# EVALUATE Stage

## Base Methodology

> **Reference:** `backend/skills/s_evaluate/SKILL.md`
>
> Follow the full evaluation workflow defined there: parse requirement, score against DDD docs, calculate ROI, classify scope, recommend GO/DEFER/REJECT/ESCALATE, and define acceptance criteria.

## Pipeline-Specific Behavior

### Requirement Clarification Check (P0)

**Before scoring, check the requirement itself for completeness.** A vague
requirement scored as GO produces an under-specified acceptance criteria set —
the pipeline builds something that technically passes but misses the real need.

**Process:**
1. Parse the requirement into: WHO (actor), WHAT (action), WHY (value), WHEN (trigger)
2. For each undefined element:
   - Can it be unambiguously derived from DDD docs (PRODUCT.md scope, TECH.md constraints)?
   - If yes → fill it in, note the derivation source
   - If no → flag as ambiguity
3. List edge cases not addressed (empty state, error path, concurrent access, scale)
4. Cross-reference TECH.md "Constraints" / "Runtime Traps" — does the requirement
   implicitly violate any? If so → flag as conflict

**Exit conditions:**
- 0 ambiguities, 0 conflicts → proceed to scoring
- 1 ambiguity, derivable from context → resolve inline, note assumption in artifact
- ≥2 unresolvable ambiguities → **ESCALATE** (not GO with assumptions)
- Any constraint conflict → **ESCALATE** with explicit conflict description

**Why this exists:** spec-kit's `clarify` pattern — AI proactively finding spec
gaps is higher-yield than human review. Without this step, EVALUATE scores a
vague requirement as GO, acceptance criteria are under-specified, and BUILD
delivers something technically correct but wrong.

### Subsystem Health Audit (P1)

**Before scoring, if the requirement touches an existing subsystem** (not a
greenfield feature), run a 5-minute E2E audit of that subsystem:

1. **Identify the subsystem** — what directory/module does this requirement live in?
2. **List all public operations** — every API endpoint, CLI command, or user action
   the subsystem supports (e.g., deploy, stop, start, update, delete, reset-password)
3. **For each operation, check 8 operational invariants** (from `OPERATIONAL_PATTERNS.md`):
   - OP1: Concurrency guard?
   - OP2: Rollback path?
   - OP3: Data backup?
   - OP4: Access control?
   - OP5: Health unauthenticated?
   - OP6: Fail-loud placeholders?
   - OP7: Single update path?
   - OP8: Config consistency?
4. **For each missing invariant** — add it to the acceptance criteria

This turns a "fix X" requirement into "fix X + harden the neighborhood."
The audit typically finds 3-10× more gaps than the original requirement.

**Why this exists:** Hive run_d326c6ae fixed 5 specific bugs (H1-H5). A 15-minute
post-fix E2E audit found 15 MORE structural gaps (G1-G15) in the same subsystem.
Pipeline never would have found them because it only reviewed the diff. The audit
cost 15 minutes; fixing the gaps individually over time would have cost 15 hours.

**When to skip:** Greenfield features (no existing subsystem to audit), trivial
one-line fixes, or when the user explicitly says "just fix this one thing."

### Codebase Complexity Assessment (if code_intel.db exists)

After DDD doc scoring, if the project has a `code_intel.db`, read the codebase
summary to enrich the feasibility score:

```python
from core.code_intel import load_project_graph
g = load_project_graph("PROJECT_NAME")
if g:
    summary = g.get_codebase_summary()
    # Use: modules affected (keyword → symbol search), dead code in target
    # modules (cleanup overhead), most-connected nodes (fragility indicator)
```

Adjust **Feasibility** score:
- Target module has >5 dead code symbols → -0.5 (cleanup overhead)
- Target module's top node has >50 callers → -0.5 (high fragility)
- Change crosses 3+ modules → -0.5 (coordination cost)

**Skip** when no `code_intel.db` exists or requirement is research-only.

### Goal Mode Detection

If the requirement describes an open-ended improvement (not a bounded feature),
classify as `goal_mode: true` and switch to `goal` profile.

**Indicators of goal mode:**
- "Get X to Y%" / "Improve X until Y" / "Reduce X below Y"
- "Migrate all callers" / "Fix all warnings" / "Remove all instances of"
- Measurable end state but unbounded scope (don't know how many changes needed)
- No single "done" deliverable — done means metric reached

**When detected:**
1. Set `scope: "goal"` in evaluation (triggers `goal` profile selection)
2. Generate `dod_criteria` array — each criterion has type + check:

```json
{
  "goal_mode": true,
  "dod_criteria": [
    {"type": "command", "check": "pytest --cov-fail-under=90 src/", "desc": "Coverage >90%"},
    {"type": "rubric", "check": "Read each error msg. PASS if: states problem, suggests fix, no stack traces.", "desc": "User-friendly errors"}
  ],
  "max_cycles": 10,
  "progress_path": "Projects/<PROJECT>/.artifacts/goals/<slug>.md",
  "cycle_scope": "one test file or one module fix per cycle",
  "review_cadence": 3
}
```

**DoD criteria rules:**
- `command` type: shell command, exit 0 = pass. ALWAYS prefer this.
- `rubric` type: explicit pass/fail rubric (not just goal statement). Use only
  when criterion is inherently subjective.
- If >50% criteria are `rubric` with vague rubrics → ESCALATE (goal too subjective)
- A goal with ALL `rubric` criteria and no measurable progress metric → ESCALATE

**If NOT goal mode:** proceed with standard evaluation (scope = standard/complex/trivial/bugfix).

**Ordering note:** Goal Mode Detection runs AFTER scoring. If scoring recommends
DEFER/REJECT, that takes precedence — the requirement isn't worth pursuing
regardless of whether it's a goal or a feature. If scoring recommends GO and
the requirement matches goal indicators → override scope to "goal".

### Acceptance Criteria Quality Gate

**Every AC must describe an observable outcome, not a mechanism.**

Test: "If I implement a no-op that produces the named artifact (file, output, endpoint) but delivers zero user value — does this AC still pass?" If yes → AC is too weak.

| ❌ Mechanism AC | ✅ Outcome AC |
|----------------|--------------|
| "Save .full_data.json to disk" | "Re-render with insights completes in <2s without network calls" |
| "Add fallback query to forecast table" | "≥90% of top-20 accounts have non-empty owner field" |
| "Filter incomplete month" | "No MTD partial data appears in insights_data.json monthly_trend" |

**Rules:**
- Each AC must be verifiable by a command, assertion, or observation — not by reading code
- "Does X exist?" is never sufficient — "Does X achieve Y?" is required
- If the AC is about a cache/optimization: the AC measures the speedup, not the cache existence
- If the AC is about data quality: the AC measures the output quality, not the query change

### Pre-mortem Gate

After scoring, if the initial recommendation is GO, the base methodology's
Step 3.5 (Pre-mortem) is **mandatory** in the pipeline. The pre-mortem output
(`pre_mortem` array) MUST be included in the evaluation artifact JSON.

If the pre-mortem triggers a score adjustment or escalation, update the
artifact accordingly before publishing.

### Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type evaluation --producer s_autonomous-pipeline \
  --summary "<GO/DEFER/REJECT>: <one-line>" --stage evaluate \
  --data '{"requirement":"...","scores":{...},"recommendation":"GO","scope":"standard","acceptance_criteria":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state think
```

### Exit Routing

- **DEFER or REJECT** -- pipeline ends. Log reason and exit.
- **ESCALATE** -- L2 BLOCK -- checkpoint. Human review required before pipeline can continue.
