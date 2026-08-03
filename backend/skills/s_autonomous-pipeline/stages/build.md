# BUILD Stage (TDD Red-Green Cycle)

Pipeline-owned stage (no sibling skill). This is the core implementation stage.

The BUILD stage follows TDD methodology: tests before code, code until tests pass.

### 🚨 STOP — Are You About to Skip TDD?

**Read this table BEFORE writing any code. If ANY thought below matches your
current thinking, you are rationalizing. CLASS A has 11 occurrences, 0 self-
corrections. You will not be the first to self-correct.**

| What you're thinking right now | Why it's wrong | Evidence |
|------|------|------|
| "This is just a config change / one-liner" | C025: 3 files, 2 functions, "simple" → user caught 2 bugs | C025 |
| "I'll write tests after to verify" | Tests written after pass immediately — proves nothing. You never see them catch the bug. | C011 |
| "I already know the implementation works" | C011: 57 tests green, 10/10 confidence → feature 100% non-functional in production | C011 |
| "TDD is overkill for this change" | 5/5 HIGH adversarial findings in "trivial" session fixes came from untested code (2026-05-26) | IMPROVEMENT.md 2026-05-26 |
| "Let me get the code working first, then add tests" | Code written before tests? Delete it. You WILL rationalize test coverage to match existing code. | C009 |
| "The pipeline profile is trivial, so less rigor" | Trivial profile still includes BUILD+REVIEW+TEST. Profile selects STAGES, not RIGOR within stages. | C036 |
| "I'll do a quick prototype then formalize" | Prototypes never get formalized. The "quick" version ships. Write the test first. | Observed pattern |
| "The plan says X, but I see a better approach mid-implementation" | Deviate from Change Spec = deviate from the spec. You haven't reviewed implications for other ACs. Use Micro-Replan Trigger (Step 2) or go back to PLAN. | Step 2 design |

**If you wrote code before a test: DELETE IT. Start over from RED.**

---

### Tests BEFORE Code — The Iron Law

Write the test FIRST. Watch it FAIL. Then write implementation. If you wrote code
before a test exists for it, you are not doing TDD — you are doing "code then
rationalize tests." C009: 5 iterations because tests came after code. C011: 57
tests passed but tested the wrong thing. **The test defines what "correct" means.
Code without a pre-existing test has no definition of correct.**

## Anti-Pattern: Horizontal Slices (BLOCKING)

**DO NOT write all tests first, then all implementation.** This is "horizontal
slicing" — treating RED as "write all tests" and GREEN as "write all code."

This produces crap tests:
- Tests written in bulk test _imagined_ behavior, not _actual_ behavior
- You test the _shape_ of things (data structures, signatures) not user-facing behavior
- Tests become insensitive to real changes — pass when behavior breaks
- You outrun your headlights, committing to test structure before understanding impl

**Correct approach: Vertical tracer bullets.** One test → one implementation → repeat.
Each test responds to what you learned from the previous cycle.

```
WRONG (horizontal):
  RED:   test1, test2, test3, test4, test5
  GREEN: impl1, impl2, impl3, impl4, impl5

RIGHT (vertical tracer bullet):
  RED→GREEN: test1→impl1  (tracer bullet — prove the path end-to-end)
  RED→GREEN: test2→impl2  (each test responds to what you learned)
  RED→GREEN: test3→impl3
```

## Chronic Pattern Reminder (Meta-Intelligence L3)

Before starting TDD cycles, check if the evaluation artifact contains
`build_injection_recommendations` (populated from pipeline_intelligence.json
during EVALUATE). If present, these are RP patterns that adversarial review
has caught repeatedly in similar changesets.

**Action:** For each recommended pattern, add it to your mental checklist for
this BUILD. Verify compliance before advancing to TEST.

Example injection:
```
⚠️ Intelligence: The following patterns are frequently violated in this type of changeset:
- RP3 (React hook cleanup): Verify useEffect cleanup releases ALL resources
- RP12 (unstable callback refs): Wrap callbacks in useCallback with correct deps
- RP35 (shared pool contention): Any to_thread() >5s needs dedicated executor
```

If no `build_injection_recommendations` exist, skip this section.

---

## Gate 1: Pre-Check (Skeptic + SSA) — ACTIVE

> **Status:** ACTIVE. BLOCK verdict halts BUILD until resolved.
> User can override with "proceed anyway" (logged as override, not silent skip).

### Trigger Conditions

| Profile | Gate 1? | Rationale |
|---------|:---:|-----------|
| full | ✅ Always | Maximum rigor — wrong direction multiplied by full BUILD |
| goal | ✅ First cycle only | Wrong start × N cycles = maximum waste. Fires once before goal_cycle begins, not per-cycle. |
| bugfix | ✅ Always | Bugfix = highest patch risk. SSA is most valuable here. |
| trivial | ❌ Skip | Single config/const change, no direction to validate |
| research | ❌ Skip | No code generation — nothing to pre-check |
| docs | ❌ Skip | No code generation |

**If skipped:** Record in stage-json: `"gate1_verdict": "SKIPPED", "gate1_reason": "profile=trivial", "gate1_blocking": false`

### Input Context (Feed to Sub-Agent)

Assemble these 5 inputs before spawning (any can be empty — sub-agent handles missing gracefully):

1. **Plan artifact** — the design_doc from PLAN stage (`discover --types design_doc --full`)
2. **Rejected alternatives** — from THINK stage research artifact, the "alternatives considered
   and why rejected" section. Prevents Skeptic from re-challenging dismissed options.
   If THINK explored no alternatives (trivial approach): omit — sub-agent skips deconfliction.
3. **TECH.md Blocking Constraints** — extract `## Blocking Constraints` section (if it exists).
   If section missing: omit — sub-agent passes Check 1 automatically.
4. **IMPROVEMENT.md "What Failed"** — extract `[pitfall]` entries for failure pattern matching.
   If no pitfall entries exist: omit — sub-agent passes Check 2 automatically.
5. **EVALUATE anti-repetition verdict** — if EVALUATE's Anti-Repetition Check found a match
   but passed with reasoning ("PROCEED — structural difference: X"), include that verdict.
   Prevents Gate 1 from re-litigating what EVALUATE already resolved.

### Sub-Agent Prompt (Skeptic + SSA)

Spawn a fresh-context sub-agent with this system prompt:

```markdown
## Role: Plan Skeptic + Structural Solution Assessor

You receive a code generation plan. Your job is to find reasons this plan will
FAIL or WASTE EFFORT before any code is written. You are NOT building — you are
preventing wrong starts. You have zero context from the builder's session.

**Burden of proof is on the plan, not on you.** If you cannot verify a claim
the plan makes, that's a finding (WARN). If you find a clear violation, that's
a BLOCK. Default to skepticism — optimism costs 30-60 minutes of rework.

---

### Check 1: Constraint Violation

Read the TECH.md "Blocking Constraints" section provided below (if any).
For each rule with a `Verify:` field:
- Can you determine from the PLAN whether it will violate this constraint?
- If YES violation likely → BLOCK with constraint ID
- If MAYBE → WARN with explanation
- If NO constraints section provided → PASS this check (no rules = no violations)

### Check 2: Proven Failure Pattern

Read the IMPROVEMENT.md "What Failed" entries provided below.
For each [pitfall] entry:
- Does the PLAN's chosen approach STRUCTURALLY resemble this failed approach?
- "Structurally" means: same technique (big-bang refactor, polling loop, multi-writer,
  shared mutex, silent fallback), not just same domain.
- If match found → check the "Rejected Alternatives" input: was this already
  considered and dismissed with reasoning? If yes → PASS (already addressed).
  If not addressed → BLOCK with citation to the failed entry.

### Check 3: Simplicity Gate

Answer three questions about the plan:
1. Does this problem ACTUALLY EXIST for this project? (Or is it hypothetical/preventive?)
2. What's the SIMPLEST possible solution? Does the plan match it, or is it over-engineered?
3. Does this solution COMPOUND (form a learning loop) or is it one-off?

Signals of over-engineering:
- Plan introduces a new abstraction for a single use case
- Plan adds infrastructure (new module, new config, new DB table) for a problem
  solvable with existing tools
- Plan creates an extension point nobody asked for (YAGNI)

If over-engineered → WARN with simpler alternative suggested.

### Check 4: Structural Solution Assessment (SSA)

For bugfix/modification plans ONLY (skip for greenfield features):
- Does the plan target the ROOT CAUSE, or a downstream symptom?
- Would this fix PREVENT the same class of bug elsewhere? Or only this one instance?
- Does the plan add UNDERSTANDING to the system (type constraint, invariant, schema)
  or just a CHECK (null guard, try/except, fallback)?

Verdict:
- STRUCTURAL → no concern (root cause addressed)
- PATCH (ACCEPTABLE) → WARN: "Root cause known but structural fix deferred.
  Recommend logging tech debt todo: {description}"
- PATCH (BLOCKING) → BLOCK: "Root cause unknown or fix in wrong layer.
  Structural alternative: {proposal}"

### Check 5: API Existence Verification

For each internal function/module/API the plan references:
- Has the plan verified it exists? (Read call, grep, or explicit "verified in THINK")
- If the plan says "call X.y()" without evidence X.y exists → WARN
- If the plan invents a function name not found in the codebase → BLOCK

This catches AGENT.md R15 violations: "never code against an API from memory."

---

## Output Format (MANDATORY — use exactly this structure)

```
GATE 1 VERDICT: [PASS | WARN | BLOCK]

Checks:
  1. Constraints:     [PASS | WARN: ... | BLOCK: ...]
  2. Failure Pattern: [PASS | WARN: ... | BLOCK: ...]
  3. Simplicity:      [PASS | WARN: ... | BLOCK: ...]
  4. SSA:             [PASS | WARN: ... | BLOCK: ... | N/A (greenfield)]
  5. API Existence:   [PASS | WARN: ... | BLOCK: ...]

Overall: [PASS — proceed to BUILD | WARN — proceed with noted concerns | BLOCK — revise PLAN]

[If WARN or BLOCK: specific actionable items, max 3 lines each]
```

**Rules:**
- Any single BLOCK → overall = BLOCK
- Any WARN + no BLOCK → overall = WARN
- All PASS → overall = PASS
- Be SPECIFIC: cite the constraint ID, the failure entry date, the API name.
  Vague findings ("could be improved") are not findings — they are noise.
```

### Execution

1. Assemble the 5 input sections (plan, rejected alternatives, constraints, failures, EVALUATE verdict)
2. Spawn sub-agent with the Skeptic+SSA prompt above + input sections as user message
3. Parse the verdict from sub-agent response
4. **Handle verdict:**

> **Spawn rejected? Fail closed (gate_spawn_blocked).** If the Agent-tool spawn
> in step 2 is REJECTED by the harness ("user doesn't want to proceed with this
> tool use" / "does not want to take this action"), apply INSTRUCTIONS.md Rule 23:
> retry the spawn EXACTLY ONCE → still rejected → **CHECKPOINT,
> reason="gate_spawn_blocked"**, do NOT proceed to BUILD. Resume re-enters on a
> fresh subprocess (clears the PIT01 poisoning). NEVER self-review the plan in
> place of the Skeptic+SSA spawn — an unverified BUILD direction is exactly what
> Gate 1 exists to prevent. (Unlike Gate 2, Gate 1 has no code-enforced evidence
> field; this instruction is the guard, and a blocked Gate 1 is recoverable at
> Gate 2 — so instruction-only is sufficient here.)

**PASS:**
```
✅ GATE 1: PASS — direction validated, proceed to BUILD.
```
→ Proceed to Step 1.

**WARN:**
```
⚠️ GATE 1: WARN
  {summary of concerns — 1 line per non-PASS check}
→ Acknowledged. Proceeding to BUILD with noted concerns.
```
→ Log warnings, proceed to Step 1. No user interrupt.

**BLOCK:**
```
🛑 GATE 1: BLOCK — direction challenge

  Issue: {specific finding from sub-agent}
  Structural alternative: {what the sub-agent proposed}

  [Fix Plan — revise and re-run Gate 1]  [Proceed Anyway — accept risk]
```
→ Present to user. Two options:
  - **Fix Plan:** Return to PLAN stage. Revise approach. Re-run Gate 1. Max 2 retries.
  - **Proceed Anyway:** User explicitly overrides. Log as override. Proceed to Step 1.
    Override is NOT silent — recorded in run.json for signal quality tracking.

### Recording in run.json

After Gate 1 completes, update the build stage record:

```bash
python backend/scripts/artifact_cli.py run-update --project <PROJECT> --run-id <RUN_ID> \
  --stage-json '{"stage":"build","gate1_verdict":"PASS|WARN|BLOCK","gate1_blocking":true,"gate1_override":false,"gate1_checks":{"constraints":"PASS","failure_pattern":"PASS","simplicity":"WARN: ...","ssa":"N/A","api_existence":"PASS"}}'
```

**Fields:**
- `gate1_verdict` — PASS, WARN, BLOCK, or SKIPPED
- `gate1_blocking` — always `true` (gate is active)
- `gate1_override` — `true` if user chose "Proceed Anyway" on a BLOCK verdict
- `gate1_checks` — per-check detail for signal quality tracking
- `gate1_retries` — number of PLAN revisions attempted (0, 1, or 2)

### Recording files_touched (for auto local-commit — MANDATORY)

Every time you Edit or Write a **source file** during BUILD, record it so the
DELIVER-stage `run-commit` can `git add` exactly this run's files (never `git add
-A`, which would sweep a parallel session's edits — R29). Record in batches (e.g.
at the end of BUILD, or after each cluster of edits):

```bash
python backend/scripts/artifact_cli.py run-update --project <PROJECT> --run-id <RUN_ID> \
  --files-touched '["backend/foo.py","desktop/src/Bar.tsx","backend/tests/test_foo.py"]'
```

- Record **WRITTEN files only** (Edit/Write targets + new test files) — NOT files
  you merely Read. An over-broad list re-introduces the cross-session bleed this
  design prevents.
- Prefer ABSOLUTE paths (most robust — `run-commit` resolves each to its git repo
  regardless of process cwd). Relative paths work only if `run-commit` runs from
  inside that file's repo; when unsure, use absolute.
- Dedup-append: safe to call multiple times; duplicates are ignored.
- If you forget, `run-commit` WARNS about untracked working-tree changes rather
  than committing the wrong set — but record diligently so nothing is missed.

**Override tracking:** When `gate1_override: true`, OS Eval can later assess whether
the override was justified (plan succeeded despite BLOCK) or the BLOCK was correct
(plan produced rework). This data informs prompt tuning.

---

## Step 1: RED→GREEN Tracer Bullet

1. Read acceptance criteria from the evaluation artifact (or design_doc if PLAN ran)
2. Read TECH.md for test framework (pytest, vitest) and conventions
3. Pick the **single most important acceptance criterion** — the one that proves
   the core path works end-to-end
4. Write ONE test for it (it MUST fail — nothing implemented yet)
5. Write minimal code to make that ONE test pass
6. Commit: this is your tracer bullet — proof the path works

## Step 1.5: API Existence Check (BLOCKING for handler/integration code)

**Before writing any code that calls functions from another module:**

For each external function call in your plan (the Change Spec):
1. **Read** the target module file (not from memory)
2. **Confirm** the function exists with the expected name
3. **Verify** the return type matches your destructuring
4. **Verify** the argument signature matches your planned call

Format:
```
API CHECK:
- parse_repo(repo_root) → returns list[ParseResult] (NOT single ParseResult)
- GraphStore.bulk_insert(parse_results) → exists ✓
- GraphStore.bulk_replace() → DOES NOT EXIST ✗ (use clear() + bulk_insert())
```

**Why this is blocking:** run_e07816af shipped with `parse_result.nodes` (AttributeError)
and `graph.bulk_replace()` (doesn't exist) — both would have been caught by a 10-second
Read. Unit tests mocked the boundary and passed. E2E force-run failed. The class of bugs
where "I assumed the API shape from memory" is structurally undetectable by unit tests
that mock the very API being called wrong.

**Skip when:** all calls target code YOU wrote in the same session (you know the API).

## Step 1.7: Mechanism Declaration (BLOCKING for system API usage)

**When code depends on an OS/system mechanism** (flock, signals, atomicity,
file ordering, process lifecycle, env var inheritance), declare it explicitly:

```
MECHANISM: <what system behavior you rely on>
ASSUMPTION: <what you believe is true about that mechanism>
VERIFY: <how you confirmed — man page, empirical test, or citation>
```

**Examples of mechanisms that need declaration:**
- File locking (flock, fcntl, lockf) — inode vs path semantics
- Signal delivery (SIGTERM, SIGKILL) — timing, child propagation
- Environment inheritance — what launchd/systemd/cron pass to children
- File atomicity — rename vs write, O_CREAT|O_EXCL
- Process death detection — PID reuse window, waitpid vs kill(0)

**Why this exists:** run_edcfd0e5 (this session) used `lock_path.unlink()` to
"break" a stale flock. The ASSUMPTION was "delete file = release lock." WRONG:
flock is bound to the inode/fd, not the path. Deleting the path creates a new
inode — two processes can then both hold "the lock" on different inodes. A
one-line declaration would have forced verification BEFORE coding. The adversarial
reviewer caught it because it was a known pitfall — but a fresh reviewer might not.

**This becomes the adversarial reviewer's attack surface list.** Instead of
reviewing the entire diff cold, the reviewer can focus on each ASSUMPTION and
ask "is this actually true?" — turning probabilistic review into targeted verification.

**Skip when:** code only uses standard library functions with well-understood
behavior (dict, list, pathlib basic ops, string formatting).

## Step 2: Incremental RED→GREEN Loop

**Follow the Change Spec order** (from PLAN artifact). Process sub-changes
in dependency order — don't skip ahead. For each sub-change's AC:

7. Write the next test → it fails (RED)
8. Write minimal code to pass → it passes (GREEN)
9. Commit after each green cycle
10. **Don't anticipate future tests** — only enough code for the current test
11. **Completeness bias:** when the complete implementation costs minutes more
    than the shortcut, do the complete thing. Cover edge cases, handle errors.
12. **Failure-path test for every success-path test** — for each "X works when Y"
    test, write a companion "X handles failure gracefully when Z." Specifically:
    if a function consumes a resource (event, message, token), test what happens
    when the consumer FAILS — is the resource preserved for retry or permanently
    lost? This run: `consume_events_for_job` was unconditional (events lost on
    failure) — caught by adversarial review, not TDD. The failure test would have
    forced the design decision BEFORE implementation.

### Micro-Replan Trigger (Automatic)

**If the same AC fails RED→GREEN 2 consecutive times** (test written, code attempted,
still failing — not a typo fix but a fundamental approach mismatch):

**STOP the TDD loop. Do NOT retry a third time.** Instead:

1. **Diagnose**: Why is the approach failing? (interface mismatch? missing
   dependency? wrong assumption from PLAN?)
2. **Micro-replan**: For THIS specific AC only, devise a different approach:
   - Can the test be written differently (testing from a different angle)?
   - Does the file discovery reveal an interface the plan missed?
   - Is there an existing utility that handles this differently?
3. **Record the replan** in run.json: `{"replanned_acs": [{"ac": "AC2", "original_approach": "...", "new_approach": "...", "reason": "..."}]}`
4. **Resume TDD** with the new approach for this AC

**Rules:**
- Replan is scoped to ONE AC — don't redesign the whole feature
- Max 1 replan per AC. If replan also fails → escalate (L2 BLOCK). **MID-STAGE exit**
  (inside the TDD loop): always **checkpoint**, NOT in-band — see INSTRUCTIONS.md
  § Escalation Routing Protocol "Mid-stage rule" (a 4h in-band block mid-BUILD would
  freeze a half-written `replanned_acs` state).
- The replan insight should flow back to REFLECT as a lesson

**Why this exists:** Without a replan trigger, the agent either retries the
same broken approach indefinitely (wasting cycles) or checkpoint-exits to the
user (wasting their time on what might be a simple approach mismatch).
AutoGPT's "replanning on failure" pattern — but scoped to micro-level (one AC)
instead of the whole plan.

## Step 2.5: Path Symmetry Check (after each GREEN)

**After implementing any stateful operation** (lock acquire, file write, state
transition, resource allocation), enumerate ALL code paths that reach the same
logical end state:

```
PATH SYMMETRY for: <operation, e.g., "acquire evolution lock">
  ✓ Happy path (line 1066-1070): flock succeeds → write PID → flush
  ? Retry path (line 1080-1082): flock succeeds after stale break → ???
  ? Error recovery path: ???
  
  Postconditions that MUST hold on ALL paths:
  - PID is written to lock file
  - lock_fd is open and held
  
  MISSING: Retry path doesn't write PID ← FIX NOW
```

**Mechanical process:**
1. Identify all `if/else/except` branches that reach the same "success" state
2. List the postconditions the happy path establishes
3. For each alternate path: does it establish the SAME postconditions?
4. Missing postcondition on any path = write a test + fix NOW

**Trigger:** Any code that has:
- `try: ... except: ... # retry` — the retry path often misses setup steps
- `if stale: break_and_retry` — the retry re-acquires but skips initialization
- Multiple `return success` points — earlier returns skip later cleanup/setup
- Fallback paths — the fallback achieves the goal differently but may skip side effects

**Why this exists:** run_edcfd0e5 had two `flock_exclusive_nb()` calls reaching the
same "lock acquired" state. The first wrote the PID. The second (retry after stale
lock break) didn't. PE review caught it — TDD didn't because the test only exercised
the stale-break path's existence check, not its postconditions.

**This is mechanical, not judgment.** Count paths. List postconditions. Check each.
It takes 30 seconds and catches the #1 class of "works on happy path, breaks on retry."

## Step 3: VERIFY -- Targeted tests, zero regressions

**VERIFY rules (BLOCKING):**
- Run **changed test files + test files that import changed modules**.
  ```
  pytest tests/test_foo.py tests/test_bar.py --timeout=60
  ```
- **For widely-imported modules** (database/sqlite.py, core/prompt_builder.py,
  session_router.py, etc.), find ALL dependent test files via grep:
  ```
  grep -rl "from database\|import database\|SQLiteDatabase" tests/ --include="*.py" | sort -u
  ```
  Then run exactly those files. This catches interaction bugs without running
  the full 700+ test suite (which hangs with xdist --maxfail).
  **NEVER run the full suite (`SWARMAI_SUITE=1`) as an agent** — it has known
  xdist deadlock issues that cause infinite hangs. Full suite is human-only.
- If you're unsure which tests to run, use `pytest --lf --timeout=60` (last-failed).
- **NEVER** pipe pytest through `| tail` -- it hides pass/fail and xdist status.
- **NEVER** pipe pytest through `| tail` -- it hides pass/fail and xdist status.
- If all tests pass -- proceed to Step 4. Done.
- If tests fail -- fix code, re-run **only failing tests**.
- **Max 2 VERIFY re-runs total.** After 2 runs, if still failing:
  publish changeset with `"regressions": N` and advance to REVIEW anyway.
- Track VERIFY attempt count explicitly: "VERIFY attempt 1/2", "VERIFY attempt 2/2".

11. Run changed + related test files -- all must pass
12. If existing tests break -- fix production code, NOT the existing tests
13. Track all files changed and test results

## Step 3.5: CALLER VERIFICATION (for new public functions)

**After tests pass, before SMOKE:** verify that new public symbols are wired.

For each NEW public function, class, or hook created in this changeset:

```bash
# Check: does anyone call this in production code?
grep -rn "function_name" <project_root> --include="*.py" | grep -v "def function_name" | grep -v "test_"
```

| Result | Action |
|--------|--------|
| 0 callers (excluding tests + definition) | **WARN** — dead code unless wired. Add the caller NOW or document why it's deferred. |
| 1+ callers | Verify: caller's arguments match function signature. Caller handles return type. |

**Why this exists:** PE review (2026-05-19) found `create_governance_file_gate()` had
full test coverage, passed adversarial review, but zero callers in production — dead
code. The function was never registered in `hook_builder.py`. This check catches
"correct but disconnected" code at BUILD time, before it reaches adversarial review.

**Skip when:** changeset is pure refactoring (existing callers unchanged) or
changeset modifies only private/internal functions (leading underscore).

## Step 3.6: INTERFACE SEAM VERIFICATION (for cross-module wiring)

**After caller verification, before SMOKE.** Only when changeset introduces
a new interface boundary (Protocol, ABC, duck-typed contract, callback/callable).

For each new interface/protocol/callback pattern in the changeset:

1. **Identify the contract:** What methods/signatures does the consumer expect?
2. **Identify the satisfier:** What concrete class/function will be passed at runtime?
3. **Verify method-by-method:** Open the satisfier's source file. For EACH method
   the contract expects, verify it EXISTS with a COMPATIBLE signature.

```bash
# Example: HeartbeatManager expects sender.send_message_raw(channel, text)
# Satisfier at runtime: SlackChannelAdapter
grep -n "def send_message_raw" backend/channels/adapters/slack.py
# Result: NOT FOUND → BLOCK — AttributeError guaranteed at runtime
```

**For callable/lambda parameters:**
When code passes `lambda: adapter.some_method(...)` as a callback:
- Read the lambda body
- Verify `adapter.some_method` EXISTS on the real adapter class
- Verify the arguments the lambda passes match the method's signature

**For Protocol classes (typing.Protocol):**
```python
# Verify at build time, not runtime:
from typing import runtime_checkable
assert isinstance(concrete_instance, TheProtocol)
# Or just grep for each method name in the satisfier's file
```

| Result | Action |
|--------|--------|
| Method missing on satisfier | **BLOCK** — fix now. Will crash at runtime. |
| Method exists but signature differs | **BLOCK** — e.g., expects `(self, channel, text)` but real method is `(self, external_chat_id, text, *, is_final)` |
| Method exists, signature compatible | ✓ Pass |

**Also verify parameter semantics (not just existence):**
If your contract says `post(channel, text)` → posts `text` to `channel`, but
the real method ignores `text` and always posts a hardcoded string — that's a
semantic mismatch. Read the implementation body, not just the signature.

**Why this exists:** PE review (2026-05-20) found HeartbeatManager defined a
Protocol with `send_message_raw`, `update_message_raw`, `delete_message_raw`.
The Slack adapter has NONE of these methods. 24 unit tests passed (mocks).
Adversarial review said "Protocol looks fine." Would have crashed with
AttributeError on first real Slack message. Same review found `_post_ack`
called `send_typing_indicator` which ignores the `text` parameter — method
exists but semantic contract broken (always posts "Thinking..." not the ack).

**Skip when:** changeset only modifies internal implementation within one
module (no cross-module boundaries changed).

## Step 3.8: PROXIMITY SCAN (for changed function bodies)

**After all code changes pass tests, before SMOKE.** Applies whenever a function
body is modified (not just added/deleted).

For EACH function whose body was changed in this changeset:

1. **Read ±50 lines around the change** — specifically look for:
   - Docstring describing the changed logic with **specific values or behavior**
   - Adjacent comments referencing the old behavior
   - Nearby test comments that explain the expected math/flow

2. **For each found doc/comment that describes the changed behavior:**
   - Does it still match? If it says `need=0.1` but code now says `need=0.3` → update it.
   - If it uses specific numbers, formulas, or step descriptions → verify they match current code.

3. **Input boundary analysis:** For each function modified:
   - Who calls this function? (grep callers)
   - What's the **smallest valid input** the caller can send?
   - Does that smallest input produce a reasonable output with the new logic?
   - Example: `compute_confidence(1, 3, 1.0)` — is the result appropriate for
     a single correction on a 3-example skill?

| Finding | Action |
|---------|--------|
| Docstring/comment has stale values | **FIX** — update to match code |
| Caller can send extreme-but-valid input that produces unexpected result | **FIX** — add guard or adjust formula |
| Comment references wrong line/function | **FIX** — correct the reference |

**Why this exists (2026-05-25, adversarial review run_6d052913):** 4 findings in a
constants-only change — docstring still showed old formula (L190 vs code L235),
test comment referenced old value, priority skill with 1 correction reached HIGH
threshold (caller sends min_examples=3). Builder's momentum bias ("I know what I
changed") structurally prevents seeing stale surrounding context. This check takes
30 seconds per function and catches the #1 source of adversarial findings.

**Skip when:** Change is pure deletion, or function has no docstring/comments
within ±50 lines, or change is adding a new function (nothing pre-existing to go stale).

## Step 4: SMOKE -- exercise new code paths (catch runtime crashes)

14. For each modified file that has new branches (if/else, try/except,
    config-gated paths), write a minimal inline smoke test that forces
    execution through the new path. The goal is to catch AttributeError,
    NameError, and other runtime crashes that unit tests miss because
    they mock the surrounding context.

    ```python
    # Example: new config-gated code in prompt_builder.py
    from core.prompt_builder import PromptBuilder
    pb = PromptBuilder(config={"memory_progressive_disclosure": True})
    # Call the method that contains the new branch -- don't assert output,
    # just verify it doesn't crash with AttributeError/TypeError.
    ```

    Rules:
    - **Integration import smoke (BLOCKING for handler/subprocess code):**
      When your changeset adds a new handler/script that will run as a
      subprocess (type="script" jobs, CLI tools), don't rely on the 30s+
      subprocess invocation to find AttributeError. Instead, SMOKE it
      in-process with a direct import + call:
      ```python
      # 1 second vs 38 seconds. Catches API mismatch instantly.
      from jobs.handlers.code_intel_reindex import reindex_projects
      result = reindex_projects(full=False)  # hits real GraphStore API
      # If this crashes with AttributeError → API assumption was wrong
      ```
      This catches the EXACT class of bugs that unit tests with mocks
      structurally cannot: wrong method name, wrong return type, missing
      attribute. run_e07816af: `graph.bulk_replace()` → AttributeError,
      `parse_result.nodes` → AttributeError. Both would have been caught
      by a 1-second in-process import smoke. Mock-based TDD CONFIRMS your
      assumptions; import smoke VERIFIES them against reality.
    - If the new code is behind a config flag -- test with flag=True
    - If behind a conditional (channel_context, resume, etc.) -- construct
      the triggering condition
    - If new code is in a try/except -- temporarily remove the except to
      verify the try body doesn't crash (the except silently swallows
      bugs like `self.config_manager` -- `self._config`)
    - **Multi-context callers:** If new code is called from both sync
      AND async contexts (e.g., `record_token_usage()` called from
      async `session_unit` AND sync `bedrock.invoke()`), smoke test
      BOTH calling contexts. A function that works in async FastAPI
      may silently fail from a sync background job. Don't assume one
      passing smoke test covers all callers.
    - **Cross-language boundaries:** When a changeset spans multiple
      languages (e.g., Python backend + Rust desktop + TypeScript frontend),
      smoke test the **data format at each boundary**. Produce the actual
      serialized output from the sender side and verify the receiver can
      parse it. Example: `json.dumps({"version": "1.8.4"})` produces
      `"version": "1.8.4"` (with space) — verify Rust parser handles this
      exact string, not a hand-crafted compact version. Cross-language
      format assumptions are invisible to single-language unit tests.
    - **Pattern grep after any bug fix:** After fixing a bug in SMOKE or
      USER-PATH TRACE, immediately grep the **entire codebase** for the
      same pattern: `grep -rn "the_broken_pattern" . --include="*.py" --include="*.rs" --include="*.ts"`.
      The bug you just found likely exists in 2-5 other places.
      This session: fixed `"version":"` JSON parsing in one function,
      missed 3 identical `contains("\"healthy\"")` patterns 200 lines away.
    - **FRONTEND SMOKE (the pipeline is weaker here — this is the counterweight):**
      the Python import-smoke above does NOT transfer to a React/Tauri change.
      `vitest`+`jsdom` prove STRUCTURE/LOGIC only — jsdom returns 0-rects, has no
      ResizeObserver, runs no CSS. So for a frontend changeset, unit-green is NOT
      a smoke pass. The frontend SMOKE has three parts, gated on what the change
      touches (see REVIEW_PATTERNS RP57-60 + TECH.md § "Frontend Fragile Zones"):
      1. **Multi-tab isolation (RP57):** if the change adds per-tab state — open
         tab A, set the value, open tab B, confirm B is independent, switch back,
         confirm A restored, close, confirm cleared. jsdom cannot fake keep-mounted
         multi-tab; this is a real-machine or a store-level test.
      2. **Geometry/scroll/animation (RP59/RP60):** if the change touches layout,
         scroll, overflow, drag-drop, or a CSS transition — `cd desktop &&
         npm run build:all` → relaunch the .app → exercise it (open Canvas/overlay,
         resize the window, drag the radar, switch tabs, watch a stream land at the
         bottom). A daemon-only restart does NOT embed frontend changes.
      3. **Every ENTRY, not just the one you designed (RP60):** enumerate every way
         a user reaches this feature (each drop source, each nav path, each trigger)
         and exercise EACH — an "E2E verified" that traces only your happy-path is
         not E2E. For a drop/paste/import surface, test the APP-NATIVE gesture (drag
         from the Explorer = `application/json`), not just OS files (RP56).
      If the change is pure logic (a util, a reducer, no geometry/tabs/entries),
      the unit smoke above is sufficient — don't build:all for a pure function.
    - Smoke tests are **inline verification only** -- don't commit them.
      They're a build-time gate, not regression tests.
    - If a smoke test crashes -- fix the bug before proceeding to REVIEW.

    This step exists because of a real incident: `self.config_manager`
    (wrong attribute name) passed 8 pipeline stages undetected because
    it was inside try/except and no test exercised the actual runtime path.

### Resource Lifecycle Verification

Added after run_c2881d2f: 3 CRITICAL subprocess bugs survived 14 green unit tests + 4 smoke tests.

For each new resource acquisition in the changeset, verify BOTH the success path AND the failure/timeout path release the resource:

| Resource Type | Success Check | Failure Check |
|---------------|--------------|---------------|
| subprocess (`create_subprocess_exec`) | exits with returncode | `proc.kill()` + `await proc.wait()` in finally. `wait_for` timeout -- kill before re-raise. `FileNotFoundError` caught. |
| temp files | deleted after use | deleted in finally (`unlink(missing_ok=True)`) |
| MediaStream / hardware | tracks stopped | stopped in useEffect cleanup |
| network / sockets | closed / consumed | timeout set + cleanup on error |
| file handles | closed | context manager or finally |
| SDK handler/client (SocketModeHandler, WebClient, etc.) | `.close()` after use | `.close()` before reassignment AND in error path. Old instance closed before `self._handler = new_handler`. |
| upload form (multipart) | `await form.close()` | in finally block -- releases SpooledTemporaryFile |

For each applicable row, write a smoke test that:
1. Triggers the failure path (mock timeout, FileNotFoundError, etc.)
2. Asserts the resource was released (mock.kill.assert_called, etc.)

Don't skip this for "simple" subprocess calls. Voice Input had 14
green tests yet 3 CRITICAL subprocess bugs because mocks replaced
the entire subprocess lifecycle. The mock proved "if transcription
returns X, endpoint returns X" -- but not "subprocess is killed on
timeout." Test the resource, not the happy path around it.

## Step 5: USER-PATH TRACE -- walk real scenarios through real code

15. For each acceptance criterion, pick **one concrete user action** and
    trace it through the actual production code path -- not tests, not
    mocks, the real call chain.

    For each trace:
    a. **Start from the user action** -- "Titus sends a Slack DM", not
       "\_poll\_channel\_messages is called"
    b. **Follow every function call** -- read the real source, not from
       memory. Note the actual input each function receives.
    c. **Check external data shapes** -- when the code consumes data from
       an external API (Slack, DB, filesystem), verify your test mocks
       match the real response schema. `conversations.history` messages
       lack `channel`; Socket Mode events have it. These differences
       are invisible in unit tests that supply hand-crafted dicts.
    d. **Check cross-component boundaries** -- when one component calls
       another (adapter -- gateway, hook -- registry), trace what happens
       on the OTHER side. Error callbacks, state resets, object
       destruction -- these are where bugs hide.
    e. **Check competing paths** -- if two mechanisms handle the same
       event (e.g., `_on_error` callback AND health monitor both react
       to thread death), which fires first? Does the first one prevent
       the second from ever running?

    f. **Check empty/partial data** -- when the code renders or processes
       a collection (list, grid, table), trace what happens when the
       collection is empty, has 1 item, or has only some optional fields
       populated. For frontend: does the layout collapse gracefully
       (no blank columns, no empty cards)? For backend: does an empty
       list produce `[]` not `null`? Does a missing optional field use
       the default, not crash on `.get()` → `None.something`?

    **Action on findings:**
    - Each finding -- **fix immediately** (these are always real bugs)
    - Update tests to cover the discovered path

    **Why this exists:** run_ec4a73ff shipped with 26 TDD tests, 10/10
    confidence, and 8 pipeline stages passed. User-path trace found 2
    CRITICAL bugs in 5 minutes: (1) `_on_error` fired before health
    monitor -- gateway destroyed adapter -- `_ws_fail_count` reset --
    polling never activated, (2) `conversations.history` messages lack
    `channel` field -- `external_chat_id=""` -- routing broken. Both
    invisible to unit tests because mocks didn't match real API data
    and no test crossed the adapter/gateway boundary. This is LL04's
    third recurrence: engineering-complete != user-complete.

## Step 6: PROBE -- send a real request through the wire (catch format bugs)

16. **Only when the changeset adds a new API endpoint consumed by frontend.**
    Skip for backend-only or frontend-only changes.

    Write ONE integration test per new endpoint that constructs the request
    **the same way the real client would** -- not using TestClient shortcuts
    that bypass serialization.

    ```python
    # BAD -- TestClient auto-serializes, hides Content-Type bugs:
    client.post("/api/chat/transcribe", files={"audio": ...})

    # GOOD -- Construct request the way Axios/fetch would:
    import httpx
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        # FormData without explicit Content-Type (browser auto-adds boundary)
        files = {"audio": ("test.wav", wav_bytes, "audio/wav")}
        resp = await client.post("/api/chat/transcribe", files=files)
        assert resp.status_code == 200
        data = resp.json()
        # Verify response shape matches frontend expectations
        assert "transcript" in data
        assert "duration_ms" in data  # backend snake_case
    ```

    Rules:
    - Use `httpx.AsyncClient(app=app)` -- it exercises the real ASGI stack
      including Starlette's multipart parser, unlike TestClient which
      uses `requests` (different HTTP library, different serialization).
    - Do NOT set `Content-Type` manually -- let the HTTP library handle it.
      This is the exact bug pattern (RP5) we're testing against.
    - Verify the response JSON keys match what the frontend expects (after
      any snake-to-camel conversion).
    - If the endpoint requires auth or external services, mock only the
      leaf (e.g., Amazon Transcribe API), NOT the HTTP layer.

    **Why this exists:** Voice Input's Content-Type bug (explicit header
    broke Axios boundary string) would have been caught by ANY real HTTP
    request. 14 unit tests + 4 smoke tests + integration trace missed it
    because none actually sent a multipart request through the ASGI stack.
    The most fatal bug was the cheapest to catch.

## Pre-Change Checks

### When splitting a variable into multiple categories

**BLOCKING: grep ALL references to the original variable/path.**

When you split one list/path into multiple (e.g., `jobs` → `time_based_jobs` +
`dep_based_jobs` + `event_based_jobs`), grep for EVERY code path that consumed
the original. Each must now handle all categories.

```bash
grep -n "due_jobs\|time_based\|dep_based" backend/jobs/scheduler.py
```

Common misses: dry-run/display paths, error handlers, logging, metrics,
serialization. The execute path is obvious; the PARALLEL paths (display,
dry-run, logging, status) are where splits cause drift.

This run: 3-phase split was correct in execute path but dry-run only showed 2
of 3 categories. PE review caught it.

### Before adding validation/constraints to existing functions

**BLOCKING: grep all callers before adding input validation.**
```
grep -rn "function_name(" . --include="*.py" --include="*.ts"
```
Every existing call site (including tests) becomes a potential breakage.
Read the actual arguments they pass. Adjust your validation to accept
existing valid inputs. This session: added regex to `render_user_data()`,
broke 3 tests that used `s3_bucket="b"` and `auth_hash="$2a$..."`.

### Before editing a file modified by parallel sessions

**Check recent changes:** `git log -5 --oneline -- <file>`.
If the file was modified by another session since your last read, re-read
the entire file. This session: parallel commit added a stub method,
our commit added the real method — duplicate definition in the same class.

## TDD Constraint

Fix code, not tests. Tests are derived from the accepted design. Changing a test = changing the spec = go back to PLAN.

## Mock Discipline

### Boundary-Only Rule

**Mock at system boundaries ONLY.** Never mock your own code.

| Dependency Category | Example | Test Strategy | Mock? |
|-------------------|---------|---------------|-------|
| **In-process** | Pure computation, in-memory state | Test directly, no mocking | ❌ Never |
| **Local-substitutable** | SQLite, filesystem, Redis | Use test stand-in (tmp dir, in-memory DB) | ❌ Prefer stand-in |
| **Remote-owned** | Your own microservice/API | Port interface + in-memory adapter for tests | ✅ Mock the adapter |
| **True-external** | Stripe, AWS API, GitHub | Mock the leaf SDK call | ✅ Mock at boundary |

**What to mock:** External APIs, databases (when no test DB), time/randomness, filesystem (sometimes).
**What NOT to mock:** Your own classes, internal collaborators, anything you control.

**The test for good mocking:** If you refactor internals and tests break despite behavior being unchanged — your mocks are too deep. Mock the system boundary, not the internal wiring.

### Spec Enforcement

When mocking, use `spec=RealClass` or only set attributes that exist on the real class. Bare `MagicMock()` silently accepts ANY attribute access — this hides `AttributeError` bugs that crash in production.

### Interface-First Testing

Tests verify behavior through **public interfaces**, not implementation details.
A good test reads like a specification: "user can checkout with valid cart" tells
you what capability exists. Tests survive refactors because they don't care about
internal structure.

Red flags for bad tests:
- Mocking internal collaborators (not system boundaries)
- Testing private methods
- Asserting on call counts/order of internal calls
- Test breaks on refactor when behavior hasn't changed
- Verifying through external means (DB query) instead of the interface

## Adversarial Inputs in RED Phase

For NLP/parsing code, the RED phase must include adversarial inputs: URLs, file paths, code snippets, empty/minimal strings, Unicode edge cases (CJK, Kana, Hangul, emoji), and multi-language mix. These are the inputs that break keyword extractors, parsers, and formatters.

## Step 7: SOURCE VERIFICATION — verify framework decisions against docs

**Only when the changeset uses framework-specific patterns** (React hooks, FastAPI
decorators, boto3 calls, Pydantic models, SQLAlchemy queries, etc.). Skip for pure
logic that works the same across all versions (loops, conditionals, data structures).

For each non-trivial framework-specific decision in the changeset:

1. **Detect** the framework + exact version from `pyproject.toml` / `package.json`
2. **Fetch** the specific official documentation page for the pattern used
   - Source hierarchy: Official docs > Official blog/changelog > MDN/web.dev > caniuse
   - **NOT authoritative:** Stack Overflow, tutorials, blog posts, training data
   - Fetch the **specific page**, not the homepage
3. **Verify** the implementation matches current documented patterns
   - Flag any deprecated APIs (this feeds RP26 in REVIEW)
   - Flag any patterns that differ from the documented approach
4. **Cite** the doc URL in a code comment for non-obvious patterns

```
SDD CHECK:
  Framework: FastAPI 0.115.0 (from pyproject.toml)
  Pattern: BackgroundTasks for async work
  Doc: https://fastapi.tiangolo.com/tutorial/background-tasks/
  Verified: ✅ matches current docs
  Note: docs confirm BackgroundTasks runs after response, not fire-and-forget
```

**When to skip:**
- Basic syntax (imports, function definitions, loops)
- Patterns the agent has already verified in this session
- User explicitly says "just do it" or "skip verification"

**Why this exists:** LL08 found `asyncio.get_event_loop()` (deprecated in 3.12+)
and `date('now')` UTC mismatch — both passed pipeline because no check verified
against current docs. Training data goes stale; official docs don't.

## Self-Verification: AC Coverage Matrix (MANDATORY before publish)

**After all TDD cycles complete, before publishing the artifact:**

Walk through every Acceptance Criterion from the PLAN stage and produce a coverage
matrix. This is your "exam check before handing in" — you verify completeness, not
the reviewer.

### Procedure

1. Read the PLAN artifact's `acceptance_criteria` list
2. For each AC, identify:
   - `impl`: the file + function/class that implements it (e.g., `auth.py::login()`)
   - `test`: the test file + test function that verifies it (e.g., `test_auth.py::test_login_email`)
   - `verified`: did the test pass? (must be `true`)
3. If you cannot identify impl or test for an AC → you are NOT DONE. Go back and implement/test it.
4. Include the matrix in the artifact as `ac_coverage`

### Format

```json
"ac_coverage": [
  {"ac": "AC1: User can login with email", "impl": "auth.py::login()", "test": "test_auth.py::test_login_email", "verified": true},
  {"ac": "AC2: Invalid email returns 400", "impl": "auth.py::login()", "test": "test_auth.py::test_login_invalid_email", "verified": true},
  {"ac": "AC3: Rate limit after 5 attempts", "impl": "auth.py::_check_rate_limit()", "test": "test_auth.py::test_rate_limit", "verified": true}
]
```

### Rules

- **Every** PLAN AC must appear. Missing = you didn't finish.
- `impl` must be specific (`file::symbol`), not vague ("somewhere in auth module")
  - Python: `auth.py::login()` | TypeScript: `Auth.tsx::useAuth()` | Rust: `auth.rs::login`
- `test` must be specific (`test_file::test_function`), not "tests exist"
- `verified` must be `true`. If test doesn't pass, fix it before publishing.
- `plan_ac_ref` (optional but recommended): the AC identifier (e.g., "AC1", "AC2")
  for unambiguous cross-reference with PLAN. If omitted, validator uses text matching.
- **Validator enforcement:** Check 8f will BLOCK if ac_coverage is missing, incomplete,
  or doesn't cover all PLAN ACs. You cannot advance to REVIEW without it.

### Why this exists

12 pipeline runs shipped with "passed" status but features broken in production.
Root cause: BUILD could skip ACs without detection — validator only checked
`tdd.green_pass` (tests that WERE written pass) but not "all required tests exist."
This matrix forces explicit mapping: AC → code → test → verified.

---

## Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> --run-id <RUN_ID> \
  --type changeset --producer s_autonomous-pipeline \
  --summary "<N> files changed, <M> commits, TDD: <red>/<green>/<verify>" --stage build \
  --data '{"branch":"...","commits":[...],"files_changed":[...],"diff_summary":"...","tdd":{"acceptance_criteria_count":N,"tests_generated":M,"red_failures":K,"green_pass":true,"regressions":0,"smoke_tests":S,"smoke_crashes_caught":C,"user_path_traces":T,"user_path_bugs_found":B,"probes":P,"probe_bugs_found":Q},"ac_coverage":[{"ac":"AC1: ...","impl":"file.py::func()","test":"test_file.py::test_func","verified":true}]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state review --run-id <RUN_ID>
```

---

## Common Rationalizations

| Rationalization | Reality | Source |
|---|---|---|
| "Tests pass, implementation is complete" | C011: 57 tests green, 10/10 pipeline confidence, feature 100% non-functional. Tests verify what you WROTE works, not what you MISSED. State machine had 6 declared states but only 4 wired. Tests only exercised the 4. | C011 |
| "TDD is overkill for this simple change" | C009: "simple" pytest hook took 5 iterations because tests came after code. Each iteration the user challenged the approach. Final solution was 55 lines; first attempt 130+ with 3 bugs. TDD catches wrong assumptions BEFORE they compound. | C009 |
| "I'll write tests after — I need to explore first" | Tests written after validate IMPLEMENTATION, not BEHAVIOR. They pass by construction (you match the test to the code), catching nothing. TDD forces you to define behavior before you know the implementation — that's the entire point. | Osmani TDD |
| "This is a refactor — behavior doesn't change, no new tests needed" | STEERING.md: "Extract ≠ Extend" (C020). If you extracted a function AND added a new caller, the new calling context has different invariants. At minimum: verify existing tests still exercise the extracted function. | C020 |
| "Smoke tests are redundant with unit tests" | Unit tests verify logic. Smoke tests verify WIRING (does the function get called from the real entry point with real data?). C011: all units green, but the real entry point never triggered the code path. | C011 |
