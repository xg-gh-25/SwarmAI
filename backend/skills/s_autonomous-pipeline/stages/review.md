# REVIEW Stage

### 🚨 STOP — Are You About to Shortcut Review?

**Read this table BEFORE deciding how to review. The thought "I already reviewed
this during BUILD" is the single most common CLASS A rationalization.**

| What you're thinking right now | Why it's wrong | Evidence |
|------|------|------|
| "I already reviewed during BUILD, I know it's correct" | You WROTE it. You're re-reading your own assumptions, not reviewing. C011: builder rated 10/10, feature 100% broken. | C011 |
| "Tests pass, so the code is correct" | Tests verify declared behavior. Review catches undeclared behavior: missing error paths, wrong conventions, integration wiring. | C029 |
| "This is a small change, full review is overkill" | C025: 3 files, "simple" → 2 HIGH findings. Small changes have the HIGHEST skip rate and the HIGHEST bug density per line. | C025 |
| "I'll just do a quick self-review" | Self-review found 0. Adversarial found 5 (2 HIGH). Same code, same session. Your confidence is anti-correlated with bug presence. | run_bd42b58f |
| "The changeset is only 1-2 files, no need for sub-agents" | Integration trace and RP checklist are mechanical — they don't need sub-agents. But they DO need to run. Size doesn't exempt process. | C030 |
| "I'm running low on context/budget, skip to DELIVER" | CHECKPOINT instead. A skipped REVIEW means DELIVER's adversarial is your ONLY safety net — single point of failure. | STEERING R11 |
| "This finding is valid but I'll fix it in a follow-up" | There is no follow-up. This pipeline run ends at DELIVER. A "deferred" finding is a shipped bug. Fix now or escalate. | Pipeline design |

---

### Purpose: REVIEW vs ADVERSARIAL — Different Jobs

| | REVIEW (this stage) | ADVERSARIAL (inside DELIVER/goal_cycle) |
|---|---|---|
| **Who** | Agent self-reviews with mechanical aids | Fresh sub-agent with zero BUILD context |
| **What** | Conformance: does code match spec, patterns, conventions? | Bugs: what breaks under adversarial pressure? |
| **How** | RP1-RP53 checklist, integration trace, DDD conformance | Free-form attack with specialist prompts |
| **Catches** | Missing exports, wrong naming, violated conventions | Logic errors, race conditions, edge cases |

Both are necessary — REVIEW catches "did you follow the rules" (mechanical),
ADVERSARIAL catches "does it actually work" (creative). Neither replaces the other.

### Self-Review ≠ Review

You wrote this code. You cannot objectively review it. "I already looked at it
during BUILD" is not a review — it's re-reading your own assumptions. REVIEW
requires: integration trace (real wiring check), runtime patterns RP1-RP53
(mechanical checklist), and for >3 files: parallel sub-agents with ISOLATED
context. C011: builder rated 10/10, feature was 100% broken.

## Litmus Pre-Gate (< 2 min, no tools, no sub-agent)

**Before** spawning expensive adversarial sub-agents, run a quick structural
sanity check on BUILD output. This is a 30-second read, not a deep review.

### Input

Read the BUILD stage artifacts:
1. Code diff (from git or inline in run context)
2. Acceptance Criteria from PLAN stage (`run.json` → stages → plan → acceptance_criteria)

### Verdict: PASS / BORDERLINE / FAIL

**Hard failures (any one → FAIL, rework to BUILD):**

| # | Criterion | How to check |
|---|-----------|-------------|
| HF1 | Majority of new code is scaffold/boilerplate with no domain logic | Skim the diff: are there real conditionals, error handling for THIS system's failure modes, domain-specific decisions? Or just empty class stubs, pass-through functions, TODO comments? |
| HF2 | Acceptance Criteria obviously incomplete | Scan the AC list from PLAN. For each AC, can you name the function/file that implements it within 5 seconds? If you cannot identify coverage for >30% of ACs on a quick pass, FAIL. (Don't rigorously verify — just test recall.) |
| HF3 | Internal contradiction — code behavior contradicts its own docstring/comments | Spot check: do function names match what they do? Do comments describe a different algorithm than what's implemented? |
| HF4 | Missing error handling on external boundaries | Any new HTTP call, file I/O, subprocess, or DB query without try/except or explicit error path = not production-ready |

**Soft signals (3+ present → BORDERLINE):**

| Signal | Ownership (good) | Abdication (bad) |
|--------|-----------------|-----------------|
| Error handling | Handles THIS system's known failure modes | Generic `try: ... except Exception: pass` |
| Test coverage | Tests exercise boundary conditions + sad paths | Only happy-path tests |
| Style consistency | Matches existing codebase naming/structure conventions | Noticeably different patterns imported |
| Constants/limits | Specific values with justification or reference | Magic numbers without comments |
| Complexity match | Code volume proportional to problem complexity | Over-engineered OR suspiciously minimal |

### Outcome routing

| Verdict | Action |
|---------|--------|
| **PASS** | Proceed to Parallel Fan-Out Review (or single-pass). No modifications. |
| **BORDERLINE** | Proceed to review, BUT inject into adversarial prompt: `"Builder flagged weak areas: {litmus_weak_signals}. ALSO examine areas NOT flagged — builder has known self-review bias and may have directed you away from real issues."` |
| **FAIL** | Do NOT spawn sub-agents. Return to BUILD with specific rework instructions: which HF failed, what specifically needs fixing. Max 2 litmus failures per pipeline run (tracked in `run.json`). On 3rd fail → escalate to full REVIEW with ALL sub-agents regardless of changeset size, plus penalty signal: `"Code failed litmus 3 times. Apply maximum scrutiny — builder may be cycling rather than fixing."` |

### Retry accounting

- Litmus failures count against the REVIEW stage retry budget (not BUILD's).
- The "2 failure max" is per pipeline run (persisted in `run.json` under
  `stages.review.litmus_fail_count`). Counter does NOT reset between attempts.
- After escalation (3rd fail → full review), if adversarial BLOCKs, that uses
  the same REVIEW retry pool. Standard retry limit applies.

### Anti-gaming

- Litmus is NOT a substitute for adversarial review. PASS ≠ "code is good."
  PASS = "code is worth spending 30K tokens to deep-review."
- The builder (you) ran litmus on your own code. Bias applies. When uncertain
  about a SPECIFIC criterion, mark it uncertain and PASS overall. But uncertainty
  on 2+ hard-failure criteria simultaneously = BORDERLINE, not PASS.
- Never self-grant PASS to avoid rework. The rework is cheaper than a wasted
  adversarial cycle that finds 10+ issues.
- **Code enforcement:** `pipeline_validator.py` Check 8e validates litmus_gate
  artifact: verdict must be PASS/BORDERLINE/FAIL, hf_checked must be 4 booleans,
  evidence must be >20 chars. Missing or invalid litmus = BLOCK.
- Litmus PASS on HF3/HF4 does NOT substitute for full security scan (Check 2) or
  runtime pattern check (Check 6). Adversarial must re-examine error handling and
  code/comment consistency at full depth regardless of litmus verdict.

---

## Spec Compliance Gate (Serial, BLOCKING — runs BEFORE fan-out)

`NO FAN-OUT REVIEW WITHOUT SPEC SUB-AGENT FIRST`

After Litmus Pre-Gate passes (verdict = PASS or BORDERLINE), dispatch a Spec
Compliance sub-agent FIRST. This verifies the implementation matches acceptance
criteria from PLAN — nothing more, nothing less. It runs SERIAL and BLOCKING because:
- If spec fails, no point checking code quality (saves ~45K tokens)
- Spec reviewer needs zero context from quality reviewers
- Finding "AC #3 not implemented" is cheaper than finding "AC #3 has a race condition"

**Why a FRESH sub-agent, not inline self-assessment:** You wrote this code in
BUILD. Your confidence from BUILD pollutes spec compliance judgment (C011:
builder rated 10/10 on 100% broken code). A fresh sub-agent has ZERO BUILD
context — it only sees the diff + ACs. This isolation is the entire value.

**Mechanical enforcement:** The spec_compliance artifact MUST include
`spawned_as_subagent: true`. Inline self-assessment does NOT count — the
Agent tool call must be verifiable in the conversation. Do NOT include BUILD
confidence ratings, progress commentary, or "I think this is correct" in the
sub-agent prompt — only raw diff + raw ACs.

### Process

1. **Extract Acceptance Criteria** from the PLAN artifact (authoritative source):
   ```bash
   python backend/scripts/artifact_cli.py discover --project <PROJECT> --types design_doc --full
   ```
   Pull `acceptance_criteria` array from the design_doc artifact. This is the
   canonical source — `run.json` references are for quick Litmus lookup only;
   spec gate MUST use artifact_cli to ensure freshness.

2. **Prepare diff context** — same diff used for Litmus (git diff or inline).

3. **Spawn Spec Compliance sub-agent** using the Agent tool:
   ```
   Prompt template: review-agents/spec-compliance.md
   Context: acceptance_criteria + code diff + commit messages
   ```
   The sub-agent returns a JSON report with verdict + coverage matrix.

4. **Route on verdict:**

| Verdict | Action |
|---------|--------|
| **PASS** | Proceed to Parallel Fan-Out Review. No modifications. |
| **BLOCK** | Do NOT spawn quality sub-agents. Return to BUILD with specific MISSING/MISUNDERSTOOD ACs as rework instructions. Increment `litmus_fail_count` (shared counter with Litmus). |
| **WARNING** | Proceed to Parallel Fan-Out, but inject EXTRA findings into the merge phase for visibility in the delivery report. |

### Relationship to Litmus HF2

Litmus HF2 is a 5-second smoke test: "can you name the function/file for >70% of
ACs on a quick pass?" It catches gross incompleteness (>30% ACs unidentifiable).
Spec Compliance is the rigorous per-AC verification for cases that pass HF2 but
have subtle gaps (single missing AC, partially-implemented AC, misunderstood AC).
They are not duplicates — they operate at different precision levels.

### Retry Accounting

- Spec BLOCK uses a SEPARATE counter: `stages.review.spec_fail_count` (not shared
  with Litmus's `litmus_fail_count`).
- Max 2 spec BLOCKs per pipeline run before escalation.
- On escalation: spec findings become MANDATORY fix items in BUILD rework.
- Litmus escalation (3rd litmus_fail_count) triggers "full review with all agents."
  Spec escalation triggers "mandatory BUILD rework." They are independent paths.

### Profile-Aware Behavior

| Profile | Spec Review | Notes |
|---------|-------------|-------|
| full | Always | Standard behavior |
| bugfix | Always | Even bugfixes must match their AC (prevents scope creep) |
| trivial | Always | Trivial still has ACs — verify them |
| goal | Per-cycle | Each goal cycle's mini-BUILD has ACs to verify |

**No profile skips spec review.** This is the lesson of C036 — profile downgrades
must not bypass quality gates.

### Artifact Recording

Include spec compliance results in the review artifact under `"spec_compliance"`:
```json
{
  "spec_compliance": {
    "verdict": "PASS",
    "coverage_matrix": [...],
    "extra_work": [],
    "findings": []
  }
}
```

---

## Parallel Fan-Out Review

**When the changeset touches >3 files OR >100 lines OR touches auth/data/infra
code**, split the review into 3 parallel sub-agents for independent perspectives.
Otherwise, fall back to single-pass review (all checks inline, see sections below).

### Phase A — Fan-Out (parallel, single turn)

Spawn up to 3 sub-agents using the Agent tool. **Issue all Agent calls in a
single assistant turn** so they execute in parallel. Each sub-agent reads the
changeset + relevant DDD docs independently.

```
Sub-agent prompts are in: backend/skills/s_autonomous-pipeline/review-agents/

[Already completed before fan-out — see Spec Compliance Gate above]
  Spec Compliance Agent (review-agents/spec-compliance.md)
  → AC coverage matrix, MISSING/EXTRA/MISUNDERSTOOD detection

[Parallel fan-out agents — spawned in single turn]
1. Code Quality Agent (review-agents/code-quality.md)
   → TECH.md conformance, integration trace, replace/move parity,
     runtime patterns RP1-RP53, depth & seam analysis

2. Security & Safety Agent (review-agents/security-safety.md)
   → Confidence-gated security scan, wire test, blast radius trace

3. UX & Test Agent (review-agents/ux-test.md) — ONLY when frontend files changed
   → UX review (UX1-UX5), test coverage gaps, E2E trace
```

Each sub-agent returns a JSON report. They do NOT communicate with each other.

### Phase B — Merge (main agent)

Once all sub-agent reports are back:

1. **Deduplicate** — same finding from 2 agents = keep the more specific one
2. **Cross-reference** — security finding + code quality finding on same function = same root cause?
3. **Apply anti-rationalization gate** (check 14 below) — reject shortcuts
4. **Produce exit evidence checklist** (check 15 below) — every check has evidence
5. **Single verdict** — GO (advance to TEST) or BLOCK (fix findings first)

> **Two axes, never merged (mattpocock non-merging insight; provenance:
> `Knowledge/Reports/2026-07-12-mattpocock-skills-deep-research.md` S1).** The
> dedup/cross-reference above applies ONLY within the fan-out quality findings
> (code-quality / security / ux). The **spec_compliance verdict is a SEPARATE
> axis** — it answers "does the code do what the spec asked?", not "is the code
> well-built?". A change can pass one axis and fail the other (clean code
> implementing the wrong thing = quality-PASS / spec-FAIL; right feature violating
> conventions = spec-PASS / quality-FAIL). Merging lets one axis mask the other —
> the exact failure the separation prevents.
>
> **This governs REPORTING, not gate topology.** The spec axis is already gated
> UPSTREAM: the Spec Compliance Gate (above) runs before fan-out, and a spec BLOCK
> returns to BUILD *before* the quality sub-agents ever spawn — so it never reaches
> this merge. When Phase B runs, the spec verdict is therefore PASS or WARNING (a
> WARNING injects EXTRA findings for visibility). Record the spec_compliance verdict
> alongside the merged quality verdict as two un-cross-ranked axes — never fold spec
> into the quality dedup. A quality-axis BLOCK here still blocks the stage; the spec
> axis has already had its say upstream.

### When to Skip Fan-Out

Fall back to single-pass review (all checks inline) when ALL are true:
- Changeset touches ≤3 files
- Diff is <100 lines
- Does NOT touch auth, payments, data access, infra, or config

In single-pass mode, execute Tier 1 checks + applicable Tier 2 checks sequentially in the main context.

---

## Base Methodology

> **Reference:** `backend/skills/s_code-review/INSTRUCTIONS.md`
>
> Follow the base code review methodology defined there for structured review findings.

## Durable Findings Format (P3)

**All review findings MUST be written in durable format — no file paths, no line
numbers.** Findings are permanent records in the artifact; they must survive
refactors.

| BAD (stale after refactor) | GOOD (survives refactor) |
|----------------------------|-------------------------|
| "Line 42 in `signal_fetch.py` has a race condition" | "The signal fetch handler has a race condition when two feeds write to the state buffer concurrently" |
| "`github_trending.py:87` missing error handling" | "The GitHub Trending adapter doesn't handle HTTP 429 (rate limit) responses" |
| "Fix `session_unit.py` method `_spawn`" | "The session spawn module doesn't release the slot lock on timeout" |

**Rules:**
- Describe **behaviors and contracts**, not code locations
- Use **module names** (from TECH.md or domain language), not file paths
- Reference **acceptance criteria** by number, not test function names
- A finding should still make sense after a major refactor

## Pipeline-Specific Checks

The REVIEW stage extends the base code review with 17 pipeline-specific checks,
organized into tiers:

**Tier 1 — Always (run for every changeset):**
- 0: Code Intelligence Context (if DB exists)
- 1: Code Review vs TECH.md
- 2: Security Scan
- 3: Integration Trace
- 6: Runtime Pattern Checklist (RP1-RP53) — includes RP35 (pool contention), RP36 (fix-enables-regression), RP37 (process tree lifetime)
- 14: Anti-Rationalization Gate
- 15: Exit Evidence Checklist
- 17: Blocking Constraints (TECH.md per-project rules — passes silently if section absent)

**Tier 2 — Conditional (triggered by changeset characteristics):**
- 4: Replace/Move Parity (only when code moved/replaced)
- 5: UX Review (only when frontend files changed)
- 7: Cross-Boundary Wire Test (only when frontend + backend changed)
- 8: Depth & Seam Analysis (only when new files added)
- 9: Blast Radius (only when infra/release/deploy/CI touched)
- 10: Operational Patterns OP1-OP8 (only when lifecycle ops on infra/cloud subsystems changed)
- 11: Inverse Operation Check (only when new state transitions added)
- 12: Cross-File Consistency (only when modified file has known siblings)
- 13: Neighborhood Review (only when modifying functions in files with >5 functions)
- 16: Platform Matrix Verification (only when path resolution, env vars, or filesystem ops changed)

---

### 0. Code Intelligence Context (if available)

**When `code_intel.db` exists for the project**, run before all other checks.

The agent does NOT run `python -c` commands for this. Instead, read the code_intel
data by using the Read tool on the project's `code_intel.db` existence check, then
reason about blast radius from the changeset:

1. **Check freshness**: Is `Projects/<PROJECT>/code_intel.db` present? If the session
   just started, `context_health_hook` already ran an incremental refresh.

2. **Blast radius** — For each file in the changeset, use `find_callers` / `find_dependents`
   logic: which other files import or call symbols from this file? List them.

3. **Risk assessment** — Count: how many modules does this changeset cross?
   Are there changed functions with high caller counts? Any changed code without
   test coverage (test file callers)?

4. **Inject context** into review preamble:
   ```
   Risk Map: HIGH | 3 functions changed | 14 downstream | 2 untested
   Modules crossed: core → hooks → channels (3-way)
   Top concern: session_unit._send_to_sdk() has 8 callers, 0 tests for error path
   ```

**If risk is HIGH**: expand full blast radius details before running checks.
**If risk is CRITICAL**: WARN — "This changeset has CRITICAL risk. Consider splitting."

This context feeds into existing checks:
- Check 3 (Integration Trace): code_intel provides caller list automatically
- Check 6 (RP Checklist): RP25 blast radius is now computed, not manual
- Check 8 (Depth & Seam): module_map provides seam count

When populating the **review artifact**, include a `"code_intel"` section:
```json
{"code_intel": {"blast_radius_computed": true, "risk_score": 0.45, "risk_mitigated": true,
  "untested_callers": 2, "modules_crossed": 3, "cross_module_justified": true}}
```
This feeds into confidence_score.py rules (+1 blast computed, -2 high risk, -2 untested, -1 cross-module).

**Skip** when no `code_intel.db` exists for the project.

---

### 1. Code Review vs TECH.md

Code review the changeset against TECH.md conventions.

---

### 2. Security Scan (Confidence-Gated)

Run confidence-gated security scan:
- Each finding needs confidence (1-10) + exploit scenario
- >= 8 + Critical/High: auto-fix (mechanical decision)
- 5-7: warning only (taste decision)
- < 5: suppress
- Apply 10 false-positive exclusions

Check IMPROVEMENT.md for known issue patterns.

---

### 3. Integration Trace

Verify every new public symbol is actually wired.

For every new function, parameter, config key, or `.get("key")` call in the changeset, grep the codebase for production callers (exclude test files):

| New symbol type | Verification | Example |
|-----------------|-------------|---------|
| New public function | >= 1 non-test caller exists | `generate_memory_index()` called by `inject_index_into_memory()` |
| New parameter on existing function | >= 1 call site passes it | `memory_progressive=True` passed by `prompt_builder.py` |
| New config key in DEFAULT_CONFIG | Trace: `DEFAULT_CONFIG` -- `config_manager.get()` -- consumer | `memory_progressive_disclosure` read by prompt_builder |
| `agent_config.get("key")` or `config.get("key")` | Verify key has a setter | `_first_user_message` -- no setter |
| New CLI flag / argument | >= 1 caller passes it | `--regenerate-index` -- 0 callers |
| **Calling convention mismatch** | async callee called from sync caller -- explicit bridge exists (`asyncio.run()`, `get_running_loop().create_task()` with loop guard) | sync `bedrock.invoke()` calls `async record_token_usage()` via bare `create_task` -- no running loop in job context -- task silently lost (run_6823b0d4 E2E review) |

**Action on findings:**
- 0 production callers -- **WARN** (not BLOCK). Agent must either:
  - Wire it now (add the caller), or
  - Document as intentional: "deferred -- caller planned for Phase X"
- Undocumented dead symbols are not acceptable -- every WARN needs a resolution.

**Why WARN not BLOCK:** Some interfaces are designed ahead of their callers (e.g., Phase 4 archival functions). Blocking would force premature wiring. But the agent must make an explicit decision, not silently ship dead code.

Include integration trace results in the review artifact under `"integration_trace"`.

### 3b. Input Boundary Analysis (for modified functions)

**When a function's behavior changes (not just refactored):** trace all callers and
check what the **most extreme valid input** is.

For each modified function:

1. `grep -rn "function_name(" --include="*.py" | grep -v "def \|test_"` → list callers
2. For each caller: what input values does it pass? What's the minimum/maximum?
3. Run the function mentally (or literally) with the extreme input. Is the result
   appropriate for the use case?

| Finding | Action |
|---------|--------|
| Extreme-but-valid input produces unexpected tier/category jump | Add a guard or document as intentional |
| Caller hardcodes a specific value that bypasses intended logic | Flag as design tension |

**Example (2026-05-25):** `compute_confidence()` need_signal raised to 0.3. Caller
filters eligible skills with `min_examples=3` for priority skills. Result: 1 correction
in 3 examples + high density (33%) → confidence 0.18 (HIGH tier). This was an
inappropriate promotion for thin evidence — fixed by adding `n_corrections >= 2`
guard in the deploy path. Input boundary analysis would have caught this at REVIEW,
not requiring adversarial review.

---

### 4. Replace/Move Parity Check

When code is **moved or replaced** (not just added):

| Check | What to verify | Example |
|-------|---------------|---------|
| Feature parity | Every capability of old code exists in new code | Old `_recall_knowledge` had TranscriptStore; new `_recall_for_query` must too |
| Dead orphan detection | After removing a call site, grep old function -- if 0 callers remain, flag as dead code | `_recall_knowledge` still defined after its only caller was removed |
| Argument validity | Mock attributes must exist on the real class | `unit.working_directory` doesn't exist on SessionUnit |
| **Control-flow preservation** | **Moved code executes at the same point in the caller's flow** — check early returns, guards, conditional branches ABOVE the new call site. If the caller has `if X: return` before line N, code placed after line N never runs when X is true. | Extracted `_run_data_cleanups()` from `_run_migrations()` but placed it AFTER a fast-path `return` — cleanup never ran for up-to-date DBs (run_91a6fb7e) |
| **Duplicate detection** | After adding a new method, `grep -n "def method_name"` in the same file — parallel sessions may have added a stub | Added `_run_data_cleanups()` at line 2263, parallel session had already added stub at line 1785 — two definitions, Python uses last one silently (run_91a6fb7e) |

This check exists because PE review of the RecallEngine activation found 2 HIGH bugs: (1) replaced function dropped a capability (TranscriptStore), (2) test mock hid a missing attribute. Both would have been caught by feature parity diff. Desktop Update Gaps (run_91a6fb7e) added 2 more: control-flow bypass on code extraction, and duplicate method from parallel session.

---

### 5. UX Review

**Only when changeset includes frontend files** (`.tsx`, `.jsx`, `.css`, `.html`, `.svelte`, `.vue`). Skip entirely for backend-only changesets.

Walk through every new/changed user-facing interaction and check:

| # | Check | What to verify | Example failure |
|---|-------|---------------|-----------------|
| UX1 | **Discoverability** | How does the user discover this feature? Is there a hint, tooltip, or visual affordance? | Diff lines became clickable but no visual cue existed |
| UX2 | **Feedback** | New interactive elements have hover, active, and disabled states? | Clickable rows missing hover highlight |
| UX3 | **Behavioral contracts** | Reused components -- are reactive props actually reactive? (values that must update on scroll/resize/state change) | CommentPopover `topOffset` passed as DOM snapshot instead of React state |
| UX4 | **Escape / click-outside** | Escape and click-outside behave correctly in all contexts: modal, panel, portal? Does Escape propagate unexpectedly? | Escape in portal CommentPopover also closed the parent editor |
| UX5 | **Scroll tracking** | Positioned elements (popover, tooltip, dropdown) -- do they follow when the container scrolls? | Popover stays in place while diff content scrolls away |

**Action on findings:**
- Each finding -- **auto-fix** (these are always bugs, not taste decisions)
- Include UX review results in the review artifact under `"ux_review"`

**Why this exists:** Pipeline run_6455a707 shipped with 10/10 confidence and 44/44
tests, but E2E user walkthrough found 3 bugs in 5 minutes (scroll tracking, no
discoverability hint, Escape propagation). Engineering-complete != user-complete.

---

### 6. Runtime Pattern Checklist

**BLOCKING: Read `backend/skills/s_autonomous-pipeline/REVIEW_PATTERNS.md` and apply RP1-RP53.**

Scan the changeset for known bug patterns. For each pattern that applies, explicitly verify the fix is in place. Do NOT skip patterns -- a "no" answer is fine, but silence means unchecked.

Include checklist results in the review artifact under `"runtime_patterns"`.

---

### 7. Cross-Boundary Wire Test

**Only when changeset includes BOTH frontend API calls AND backend endpoints** (e.g., new `.ts` service function + new `@router.post`). Skip for single-layer changes.

For each frontend-to-backend boundary in the changeset, explicitly answer:

| # | Question | How to verify | Example failure |
|---|----------|--------------|-----------------|
| WR1 | **Content-Type match?** | Frontend sends X -- backend parser expects X | Axios sends `application/json` default, backend expects `multipart/form-data` |
| WR2 | **Field names match?** | Frontend `form.append('audio', ...)` -- backend `form.get("audio")` | Frontend sends `audioFile`, backend reads `audio` -- None |
| WR3 | **Response shape match?** | Backend returns `{"transcript": ...}` -- frontend types `TranscribeResult` has `transcript` | Backend returns `text`, frontend reads `transcript` -- undefined |
| WR4 | **Error shape match?** | Backend raises `HTTPException(400, detail=...)` -- frontend error handler expects `response.data.detail` | Backend returns `{"message": ...}`, frontend reads `detail` |

**Output format:**
```
Wire: POST /api/chat/transcribe
  WR1: pass -- FormData (auto Content-Type) -- request.form() (multipart parser)
  WR2: pass -- "audio" field name matches both sides
  WR3: pass -- {transcript, language, duration_ms} matches TranscribeResult
  WR4: pass -- HTTPException detail -- axios error.response.data.detail
```

This is code-level trace only -- no live requests needed. Read the frontend service function and the backend endpoint side by side.

Include wire test results in the review artifact under `"wire_test"`.

**Why this exists:** Voice Input (run_c2881d2f) had an explicit `Content-Type: multipart/form-data` header that broke the Axios boundary string -- voice input would have been completely non-functional. Integration trace verified "symbols are connected" but not "the data format crossing the wire is correct." This check fills that gap.

---

### 7b. Operational Context Check (Auto-Selected)

**BLOCKING when changeset touches operational code.** Skip for pure business logic.

Infer execution context from file paths in the changeset, then apply the
matching checklist. This catches bugs that are invisible in dev but fatal in
production — the class that pipeline REVIEW + adversarial structurally miss.

**Context detection (automatic, based on file path):**

| File path pattern | Context | Checklist |
|-------------------|---------|-----------|
| `hooks/*.py` | Per-session recurring | HOOK checklist |
| `jobs/*.py`, `scheduler.py` | Cron/background | CRON checklist |
| `routers/*.py`, `channels/*.py` | Request/message handler | ENDPOINT checklist |
| `main.py`, `lifecycle_*.py`, `daemon*` | Startup/shutdown | LIFECYCLE checklist |
| `scripts/*.py`, CLI tools | One-shot invocation | SCRIPT checklist |

**HOOK checklist (per-session hooks):**
```
□ No-op cost: what happens when hook fires and finds nothing? (RP30)
□ Scaling: does no-op cost grow with data accumulation?
□ Time bound: is there a mtime/count filter to cap scan?
□ Error isolation: failure logged + skipped, not blocking session?
□ Idempotent: re-running produces same result (no double-writes)?
```

**CRON checklist (background jobs):**
```
□ Concurrent guard: what if previous run is still running?
□ Stale data: what if external data source returns yesterday's data?
□ Failure notification: does failure surface somewhere (log, health)?
□ Resource cleanup: temp files, connections closed on any exit path?
□ Clock drift: UTC vs local time assumptions correct?
```

**ENDPOINT checklist (request handlers):**
```
□ Auth: is this endpoint behind the correct auth middleware?
□ Concurrent: can 2 requests race on shared state?
□ Timeout: does the handler have a wall-clock upper bound?
□ Error response: does failure return structured JSON, not 500 HTML?
□ Input validation: all path/query/body params validated before use?
```

**LIFECYCLE checklist (startup/shutdown code):**
```
□ Daemon env: no $HOME, $USER, $SHELL assumptions (launchd strips them)
□ Kill semantics: SIGTERM vs SIGKILL vs bootout — which fires here?
□ Partial startup: what if step 3/5 fails? Previous steps cleaned up?
□ Reentrancy: can this be called twice (KeepAlive restart)?
□ Port conflict: what if port 18321 is already occupied?
```

**SCRIPT checklist (one-shot CLI tools):**
```
□ Idempotent: safe to re-run if killed midway?
□ Output: returns structured JSON (not human prose) for agent consumption?
□ Error exit code: non-zero on failure?
□ Path assumptions: uses Path.home() or explicit args, not $HOME?
```

**Output format (added to review artifact under `operational_context`):**
```
Context: HOOK (hooks/context_health_hook.py)
  □ No-op cost: pass — 30-day mtime filter bounds scan
  □ Scaling: pass — O(recent), not O(total)
  □ Time bound: pass — mtime_cutoff = 30 days
  □ Error isolation: pass — try/except per-run, logger.warning
  □ Idempotent: pass — cultivated:true prevents re-processing
```

If no file in the changeset matches any context pattern → skip this check.

---

### 8. Depth & Seam Analysis (T3 + P4)

**For each new file in the changeset**, assess architectural depth and seam
discipline using Ousterhout's framework (*A Philosophy of Software Design*)
and Feathers' seam concept.

**Vocabulary (use exactly, no synonyms):**

| Term | Definition |
|------|-----------|
| **Module** | Anything with an interface and an implementation (function, class, file, package) |
| **Interface** | Everything a caller must know: types, invariants, error modes, ordering, config. Not just the type signature. |
| **Deep** | Small interface hiding significant implementation. High **leverage** (callers get a lot) and **locality** (changes concentrate in one place). |
| **Shallow** | Interface nearly as complex as implementation. Low leverage. |
| **Seam** | Where a module's interface lives — a place behavior can be altered without editing in place. Use this, NOT "boundary." |
| **Adapter** | A concrete thing satisfying an interface at a seam. |
| **Deletion test** | Imagine deleting this module. Complexity vanishes → pass-through. Reappears across N callers → earning its keep. |

**Part A — Depth Analysis:**

For each **new file**:

1. Identify the interface surface: public functions, parameters, config keys, exceptions, invariants, error modes
2. Identify what the implementation hides: internal state, algorithms, I/O, retry logic, caching, format translation
3. Ask: **does the interface hide significant complexity from callers?**
   - **DEEP** (good) — callers get a lot for knowing a little. A caller passes 2 params and gets back a result; the module internally handles retries, parsing, caching, error recovery. Note and move on.
   - **MODERATE** — the interface simplifies, but leaks some implementation concern (callers must know about ordering, config keys, or error modes). Acceptable.
   - **SHALLOW** — the interface is nearly as complex as the implementation. Callers must understand almost everything the module does. Run deletion test:
     - Complexity vanishes → pass-through, suggest inlining or merging
     - Complexity reappears across callers → has value but needs deepening

For **modified files**: only assess if the changeset changed the public interface.

**Part B — Seam Discipline:**

For each **new interface/abstract class/protocol** introduced in the changeset:

1. Count how many adapters (concrete implementations) exist:
   - **0 adapters** → dead interface. WARN: "interface without implementation"
   - **1 adapter** → hypothetical seam. Ask: is the second adapter planned
     (test fake counts)? If not, it's just indirection — suggest removing
     the interface and using the concrete type directly.
   - **2+ adapters** → real seam. ✅ Legitimate abstraction.

2. Check seam exposure:
   - Is the seam **internal** (used by module's own tests) or **external**
     (part of the public interface)?
   - Internal seams exposed externally = leaking implementation detail.

**Output (added to review artifact):**

```json
{
  "depth_analysis": {
    "modules_checked": 3,
    "deep": 2,
    "shallow": 1,
    "findings": [
      {"module": "GitHub Trending adapter", "verdict": "deep", "interface": "1 fn, 2 params", "implementation": "120 lines"}
    ]
  },
  "seam_audit": {
    "new_interfaces": 1,
    "real_seams": 1,
    "hypothetical_seams": 0,
    "findings": []
  }
}
```

**This is informational, not a gate.** Shallow modules and hypothetical seams
are warnings, not blockers. The value is making depth and seam quality visible.

---

### 9. Blast Radius — System Lifecycle Trace (RP25)

**Only when changeset touches infra, release, deploy, CI, or cross-service config.** Skip for pure feature code.

After completing the diff review, step OUTSIDE the diff and trace the full system lifecycle:

1. **List all system-level flows** this changeset participates in (e.g., build→package→deploy→update→run, or config→startup→runtime→shutdown)
2. **For each flow**, trace the complete chain of steps. At each step ask:
   - Does existing code at this step **consume** what the changeset produces? Is it compatible?
   - Does the changeset change a **format** (config file, tar.gz structure, API shape) that downstream steps depend on?
   - If config changed, does the **runtime reload** it without restart? (Caddy, systemd, nginx, etc.)
3. **Check adjacent code** — files in the same directory or module that the changeset DIDN'T touch but that participate in the same flow.

**Output format:**
```
Blast radius trace:
  Flow 1: build → package → S3 → SSM update → EC2
    build: ✅ tar.gz structure unchanged
    S3 sync: ✅ same key pattern
    SSM update: ❌ doesn't reload Caddy when Caddyfile changes
    EC2 runtime: ✅ systemd restart handles backend

  Flow 2: tag → GitHub Actions → publish
    build-hive: ❌ unnecessary dependency on build-macos (blocks 15 min)
    publish: ✅ includes tar.gz
```

**Action:** Fix every ❌ before advancing to TEST. These are always real bugs — they're invisible in the diff but break the system.

**Why this exists:** run_19129544 (unified release pipeline) passed all stages with 9/10 confidence. DevOps E2E audit found 2 HIGH + 3 MED in 5 minutes — all outside the diff, all inside the system lifecycle. Pipeline REVIEW reads the diff; it doesn't trace the system. For infra/release code, the system lifecycle IS the feature. (2026-04-29)

---

### 10. Operational Pattern Checklist (P2)

**Trigger:** Changeset adds/modifies lifecycle operations on **infra, cloud,
or deploy subsystems** (Hive, daemon, CI, release, backup, cron jobs).

Does NOT trigger for: regular API endpoints (GET/POST on chat, settings,
workspace), UI changes, test-only changes, or in-memory state changes.

Read `backend/skills/s_autonomous-pipeline/OPERATIONAL_PATTERNS.md` and
apply OP1-OP8 for the affected subsystem.

Include checklist results in the review artifact under `"operational_patterns"`.

---

### 11. Inverse Operation Check (P4)

For each **new state transition** in the changeset (status change, resource
creation, config modification, enable/disable):

| New Operation | Required Inverse | Example |
|---------------|-----------------|---------|
| create/deploy | cleanup/delete | EC2 launch → terminate + release EIP + delete SG |
| start/enable | stop/disable | EC2 start → stop + disable CloudFront |
| update/modify | rollback/restore | rsync new code → restore .bak on failure |
| write config | validate + rollback | write Caddyfile → caddy validate → mv .bak back |
| acquire resource | release in finally | allocate EIP → release in cleanup |

For each row that applies, verify the inverse exists AND is reachable from
the failure path (not just the happy path).

**Action on findings:** Missing inverse = P0 finding. The system can get
stuck in an irrecoverable state.

Include in review artifact under `"inverse_operations"`.

---

### 12. Cross-File Consistency Check (P3)

For each **modified file**, identify files that serve a similar role:

| If you modified... | Also check... | What to compare |
|-------------------|---------------|-----------------|
| A template/config | Other templates of the same config | Same features, same structure |
| A deploy script | Other deploy/update scripts | Same mechanism, no duplicates |
| An API endpoint | Adjacent endpoints in the same router | Same guards, same patterns |
| A shell script | Other scripts in the same directory | Same conventions, no stale values |

**How to find related files:**
```bash
# Find files with similar names/roles
ls -la $(dirname <modified_file>)/
# Find files that reference the same config/resource
grep -rl "Caddyfile\|basicauth\|reverse_proxy" . --include="*.py" --include="*.sh"
```

**Action on findings:** Drift between related files = WARN. If one has a
feature (logging, timeout, header) that the other lacks, either sync them
or document why they differ.

Include in review artifact under `"cross_file_consistency"`.

---

### 13. Neighborhood Review (P5)

**For every modified function**, read the 2 functions immediately above and
below it in the same file. Check:

1. **Same pattern applied?** — If the modified function now has a guard/lock/
   check, do adjacent functions that do similar things also have it?
2. **Stale references?** — Do adjacent functions reference constants, imports,
   or patterns that the modification invalidated?
3. **Copy-paste drift?** — If the modified function was clearly copied from
   an adjacent one, are both now in sync?

This extends RP25 (blast radius) from "system lifecycle" to "code neighborhood."
The insight: bugs cluster. If one function in a file is wrong, the adjacent
functions that were written at the same time likely have the same issue.

Include in review artifact under `"neighborhood_review"`.

---

### 14. Anti-Rationalization Gate

Before concluding REVIEW, reject these shortcuts:

| Agent Shortcut | Required Response |
|---|---|
| "Changeset is small, skip integration trace" | Small changes with unwired symbols are the #1 silent failure. Trace every new symbol. |
| "Security scan isn't needed for internal code" | Internal code with injection paths gets exploited via MCP tools and API calls. Scan it. |
| "Runtime pattern checklist doesn't apply here" | Check every pattern. Write N/A explicitly. Silence = unchecked. |
| "Operational patterns don't apply — this is just code" | If the code changes ANY lifecycle operation, OP1-OP8 apply. "Just code" that deploys without rollback is an incident waiting to happen. |
| "Wire test is overkill -- the types match" | Types matching != serialization matching. Content-Type bugs are invisible to type checkers. |
| "UX review isn't needed -- the UI change is trivial" | Trivial UI changes cause scroll breaks and accessibility regressions. If UI files changed, check UX. |
| "Review is clean, marking confidence 10/10" | Confidence without evidence is fiction. Score against the checklist, not gut feel. |
| "Blast radius trace not needed -- I only changed scripts" | Infra/release bugs are invisible in the diff and break the system. If it touches build/deploy/CI, trace the lifecycle. |
| "Adjacent functions are fine — I only changed this one" | Bugs cluster. If this function was wrong, the one copied from it 20 lines up is wrong too. Check the neighborhood. |
| "The other config file is fine — I only changed this one" | Config drift is guaranteed if you don't verify. If two files describe the same thing, compare them. |
| "Adversarial review is redundant — REVIEW already checked everything" | REVIEW uses the builder's perspective. Adversarial review uses user + skeptical-reviewer perspectives. They find different classes of bugs. 12+ pipeline runs prove this. |

---

### 15. Exit Evidence Checklist

Confirm each before publishing:
- [ ] Spec compliance gate passed (coverage matrix shows all ACs IMPLEMENTED, or WARNING findings injected into merge)
- [ ] Integration trace output present (`N symbols checked, M connected, K warnings`)
- [ ] Runtime pattern checklist complete (every applicable RP has pass or N/A)
- [ ] Operational pattern checklist complete (every applicable OP has pass or N/A, or "no lifecycle ops, N/A")
- [ ] Security scan ran with confidence scores (or "no security-relevant changes" stated)
- [ ] Wire test results shown (or "single-layer change, N/A" stated)
- [ ] Depth & seam analysis completed for new files (or "no new files, N/A" stated)
- [ ] UX review completed (or "no frontend files, N/A" stated)
- [ ] Blast radius trace completed (or "no infra/release/deploy files, N/A" stated)
- [ ] Inverse operations checked (or "no state transitions, N/A" stated)
- [ ] Cross-file consistency checked (or "no related files found, N/A" stated)
- [ ] Neighborhood review done (or "single-function change, N/A" stated)
- [ ] Blocking constraints checked (or "project has no ## Blocking Constraints section, N/A" stated)

---

### 16. Platform Matrix Verification

**Trigger:** Changeset touches path resolution, env var usage, filesystem
operations, process lifecycle, or any code that could behave differently across
deployment targets.

**Process:** Read TECH.md for the declared platform list. For each platform,
verify the changeset works:

```
TECH.md declares platforms: daemon (macOS), subprocess (Win/Linux), hive (EC2 Linux), dev
This change touches: path resolution

Platform verification:
  - macOS daemon:     ~/Desktop/SwarmAI-Workspace/swarmai → ✓ (candidate list)
  - Hive (Linux EC2): ~/swarmai or /opt/swarmai → ✓ (candidate list)  
  - Windows subprocess: N/A (script jobs don't run on Windows)
  - Dev mode:         __file__ fallback → ✓ (works from source tree)
```

**What to check per platform:**
- Does the path/env var exist on that platform?
- Is the fallback behavior correct when it doesn't exist?
- Does the code silently return a wrong value (vs raising an error)?

**Finding severity:**
- Platform with 0 working resolution paths = **HIGH** (guaranteed failure)
- Platform requires undocumented env var setup = **MEDIUM** (works but fragile)
- Platform covered by fallback that's less optimal = **LOW** (acceptable)

**Why this exists:** run_edcfd0e5 hardcoded `~/Desktop/SwarmAI-Workspace/swarmai`
as the only canonical path. On Hive (EC2 Linux), this path doesn't exist. The code
fell through to the `__file__`-based fallback — which is the EXACT path that's wrong
in daemon context (the bug we were trying to fix). TECH.md explicitly listed 4
platforms — the information was available, but BUILD didn't cross-reference it.

**Skip when:** changeset is purely logic/algorithm with no platform-dependent behavior.

---

### 17. Blocking Constraints (TECH.md per-project rules)

**Check whether the changeset violates any project-declared blocking constraints.**

**Process:**
1. Read the project's TECH.md
2. Find `## Blocking Constraints` section (if it exists)
3. For each rule with a `Verify:` field — run the mechanical check against the changeset
4. Violation = **blocking finding** (identical severity to RP pattern violation)

**Format expected in TECH.md:**
```markdown
## Blocking Constraints
- SEC-01: No hardcoded credentials | Verify: grep -rn 'AKIA[A-Z0-9]{16}' in changed files
- SEC-02: All API routes require @require_auth | Verify: AST check on new route decorators
```

**Behavior:**
- Section exists + rules defined → run each `Verify:` check
- Section exists but empty → pass (no constraints declared)
- Section missing → pass silently (project hasn't declared constraints yet)
- Violation found → blocking finding with constraint ID cited

**Why this exists:** Different projects have different compliance/security/style requirements. Rather than adding an EXTENSIONS.md (5th DDD doc), constraints live as a section in TECH.md — leveraging existing DDD cultivation, staleness detection, and stage routing.

---

## Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> --run-id <RUN_ID> \
  --type review --producer s_autonomous-pipeline \
  --summary "Review: <N findings>, <M auto-fixed>, <K integration warnings>" --stage review \
  --data '{"approved":true,"findings_count":N,"findings":[...],"litmus_gate":{"verdict":"PASS","hf_checked":[true,true,true,true],"soft_signal_count":0,"weak_areas":[],"evidence":"..."},"spec_compliance":{"verdict":"PASS|BLOCK|WARNING","coverage_matrix":[{"ac_number":1,"ac_description":"...","status":"IMPLEMENTED|MISSING|MISUNDERSTOOD","evidence":"..."}],"extra_work":[],"findings":[]},"security_findings":[],"integration_trace":{"checked":N,"clean":true,"details":"..."},"runtime_patterns":{"checked":N,"violations":0,"patterns":[{"pattern":"name","status":"pass|N/A","detail":"what was checked (>10 chars)"}]},"ux_review":{"triggered":true/false,"checks":5,"findings":[...]},"wire_test":{"boundaries":N,"verified":M,"findings":[...]}}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state test --run-id <RUN_ID>
```

---

## Common Rationalizations

| Rationalization | Reality | Source |
|---|---|---|
| "Code is straightforward, self-review is sufficient" | Single-person review has structural blind spots regardless of code simplicity. C011: builder + pipeline rated 10/10, feature was 100% broken. You cannot review your own assumptions — you'll validate them instead of challenging them. | C011 |
| "Integration trace is redundant — unit tests cover wiring" | Unit tests mock boundaries. Integration trace verifies REAL wiring: does caller A actually reach callee B with correct params at runtime? C011: each unit worked perfectly; the data flowing between them was wrong. | C011 |
| "Runtime patterns RP1-RP53 — most don't apply" | Check each and mark N/A explicitly. LL08: `asyncio.get_event_loop()` (deprecated, RP19) and `date('now')` UTC mismatch (RP18) both passed pipeline because "didn't seem to apply." They applied. | LL08 |
| "Small changeset, fan-out review is overkill" | Fan-out threshold (>3 files OR >100 lines) is carefully calibrated. Below threshold, single-pass STILL runs all checks (integration trace, patterns, wire test). "Small" doesn't mean "skip checks" — it means "one reviewer does all checks." | Review design |
| "Findings are low-confidence, I'll suppress them all" | Suppressing is valid for confidence ≤4. But suppressing ALL findings = you didn't try to confirm any. At least verify the top-3 by evidence. One confirmed finding > ten suppressed ones. | Unified Confidence Rubric |
