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
5. User-Value Probe — "does output contain anything user couldn't get in 2 min?"
6. Doc Sync Check — non-blocking warning (surfaces doc gaps as todos)
7. Push-Ready Gate — binary final verdict
```

### 🚨 CRITICAL: Adversarial Review is NON-NEGOTIABLE

> **This is Gate 2 of the pipeline's 3-gate family** (Gate 0 = diagnose-before-build,
> inside EVALUATE; Gate 1 = Skeptic + SSA, after PLAN; **Gate 2 = Adversarial Review,
> here inside DELIVER**). Gate 0 guards the *framing*, Gate 1 the *plan*, Gate 2 the
> *code*. See INSTRUCTIONS.md "Progress Display — 3 Gates" + TECH.md "diagnose-before-build gate family".

**STOP. Before you proceed past step 2, confirm:** Will you spawn adversarial
sub-agents in step 3? If the answer is anything other than "yes, spawning now"
— you are rationalizing. Read C011, C021, C025 below.

**This gate has been skipped 5 times in 6 weeks.** Each time the agent said
"tests pass, code is simple, I'm confident." Each time the feature was broken.

| What you're thinking | Why it's wrong | Source |
|-----|------|------|
| "Tests pass, adversarial review is unnecessary" | C011: 57 tests green, 10/10 confidence → feature 100% non-functional | C011 |
| "Code is simple, I already reviewed it" | C025: 3 files, 2 functions, "simple" → user caught it | C025 |
| "Validator schema is strict, I'll force past it" | C021: bypassing the gate = bypassing the requirement | C021 |
| "I'll do a quick self-review instead" | Self-review found 0 findings. Adversarial found 5 (2 HIGH). Same session, same code. | run_bd42b58f |
| "Meta-review is redundant — adversarial already checked" | Adversarial reviews CODE. Meta reviews PROCESS (operational blind spots). They catch different classes of bugs. | Pipeline design |
| "Convergence loop passed in 1 iteration — must be clean" | Fast convergence on non-trivial changes may mean gates are too lenient, not code too clean. Extra scrutiny, not less. | GC11 |

**If you skip this step, the pipeline WILL be mechanically blocked** by Check 9
(depth validation) which requires `adversarial_review.profile_tier == "full"` for
full/bugfix profiles. There is no way to close the pipeline without it.

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
- Meta-review HIGH risk unaddressed → escalate. **MID-STAGE exit** (inside the
  convergence loop): always **checkpoint**, NOT in-band — see INSTRUCTIONS.md
  § Escalation Routing Protocol "Mid-stage rule" (blocking a 4h question mid-loop
  would freeze a half-written convergence iteration).

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

### User Path Latency Trace (P6.5)

**After Fresh User Audit, before Completion Audit. Applies to ALL profiles
(full, bugfix, trivial) when the changeset touches a user-facing path.**

Pick 2-3 representative user scenarios. For each, walk the **exact runtime
code path** from user action to visible response, asking at every step:

1. **What does the user SEE at this moment?** (Loading? Nothing? Stale content?)
2. **What does the user WAIT for?** (Network? Sleep? Lock? Background task?)
3. **Is there unnecessary latency?** (Sleep with no data dependency? Sequential
   calls that could be parallel? Lock held across I/O?)
4. **Is there invisible silent failure?** (Method called but return value ignored?
   Parameter passed but implementation ignores it? Fallback that looks like success?)

**Format each trace as a numbered path:**
```
Scenario: User sends "hi" in Slack DM
1. Slack Socket Mode → adapter._handle_event() → gateway.handle_inbound()
2. human_mode=True → HeartbeatManager constructed
3. heartbeat_mgr.post_ack("让我查查") → adapter.send_message() → user sees ack ✓
4. queue.processing = True → SDK call starts
5. SDK returns in 3s → heartbeat cancelled → ack deleted → response posted
Total user-visible latency: ~4s ← ACCEPTABLE for "hi"
```

**Red flags to look for:**
- `asyncio.sleep(N)` where N > 0 on a user-facing path without clear justification
- Method parameter ignored by callee (e.g., `text` passed but callee uses hardcoded string)
- `try/except: pass` on a path that determines what user sees
- Redundant API calls (post → immediately update → post again = flicker)

**If any red flag found:** Fix it NOW in this delivery iteration. Do NOT log
it as "known limitation" or "future work" if the fix is < 30 minutes.

**Why this exists:** PE review (2026-05-20, Slack Human Experience) found 2
bugs that passed TDD (24 tests green), adversarial review (11 findings handled),
and CI (green): (1) `_post_ack` called `send_typing_indicator` which hardcodes
"Thinking..." — ignoring the human-like ack text parameter. Tests used mocks
that accept any call. (2) `asyncio.sleep(2.0)` added 2s latency to EVERY Slack
message including instant answers like "hi". Both are only visible by walking
the actual user path through real production code, not by reading tests or
reviewing isolated files.

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
   NOTE: TEST stage confirmed tests PASS (execution). This step confirms tests
   VERIFY THE RIGHT THING (code review). Passing ≠ correct verification —
   a test can pass while testing the wrong behavior (C011: 57 green tests,
   feature 100% broken because tests validated implementation, not spec).

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

#### Step 0.5: Inherit REVIEW Context

Before dispatching specialists, check REVIEW's litmus verdict:

```bash
python backend/scripts/artifact_cli.py discover --project <PROJECT> --types review --full
```

If REVIEW litmus was BORDERLINE, extract `weak_areas` and inject into ALL
specialist prompts as priority focus: `"REVIEW litmus flagged weak areas:
{weak_areas}. Prioritize these in your analysis."`

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
| **Integration** | Changeset creates new public functions, classes, hooks, or handlers | `stages/specialists/integration.md` |
| **Operational** | Files touch: hooks/, jobs/, schedulers, daemon code, background tasks | `stages/specialists/operational.md` |
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

## Restraint (borrowed from Amazon Spec Studio's 4-detector skeleton — cuts noise)
A padded weak finding is worse than silence — it trains the reviewer to ignore
findings. Do not invent a finding to "look thorough."

**But ZERO findings is valid ONLY after you have Read every changed file and can name
what you checked.** A bare `NO FINDINGS` with no evidence of reading is the C011 /
run_bd42b58f failure signature ("self-review found 0, adversarial found 5, same code"),
NOT a clean result. Zero-is-valid is a noise brake, never a skip license — if you have
not read the files, you have not earned "no findings."

Before you report ANY finding, it must pass ALL 3 questions (not-all-YES → do NOT report):
1. Is it a real BUG (a wrong behavior), not a style/naming/taste preference?
2. Will it actually trigger in production (not a hypothetical edge outside the requirement)?
   **EXCEPTION — shared mutable state:** if the finding touches a variable/flag/field read
   by 2+ sites, REPORT it even if prod-trigger is uncertain. Blast-radius, not your
   confidence about one call site, decides — let Step 3c.1 adjudicate (run_fd4d756b: a
   conf-4 shared-state finding dropped here → real regression shipped). Do NOT pre-empt 3c.1.
3. Do you have a concrete `file:line` + a reproducing input / exploit?

Do NOT report (negative list):
- pure style / naming / formatting preferences
- a path covered by a test you have CONFIRMED is non-vacuous (goes RED when the guarded
  behavior is reverted — RP47). **Bare test existence does NOT count** — an un-mutation-
  verified test is exactly the theater RP47/RP31 say to distrust; if unsure, REPORT.
- hypothetical edge cases outside the stated requirement
- "could be better but isn't wrong" suggestions
- speculative concerns you cannot tie to a specific line

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

**Spawn-rejection discriminator (fail-closed — BLOCKING):** "Partial results >
no results" applies ONLY when **≥1 specialist actually spawned**. It does NOT
cover spawn REJECTION by the harness ("user doesn't want to proceed with this
tool use" / "does not want to take this action"):

- **≥1 specialist spawned**, others failed/timed out → partial results OK (note
  the uncovered domains in the gate outcome).
- **ZERO specialists spawned (all rejected)** → this is NOT "partial results,"
  it is a BLOCKED gate. Follow INSTRUCTIONS.md Rule 23: retry the spawn batch
  EXACTLY ONCE → still all-rejected → **CHECKPOINT, reason="gate_spawn_blocked"**.
  Resume re-enters on a fresh subprocess (the PIT01 poisoning clears across the
  process boundary).

**NEVER** treat an all-rejected spawn as license to self-review — that is the
CLASS A bypass this gate exists to prevent. There is no self-review branch.

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

**3c. Apply confidence gates (Unified Confidence Rubric).**

> **ORDER IS LOAD-BEARING (§11.3 restraint reframe, run_b3404953):** WHEN a finding
> touches shared mutable state (≥2 readers, grep-derived), the Shared-State Override
> (3c.1) is evaluated first and BLOCKS tier-based suppression — such a finding is NEVER
> suppressed by the confidence tiers below, regardless of how low its confidence. (A
> finding that touches no shared symbol has nothing for 3c.1 to evaluate; the tiers apply
> directly.) This is why the drop is NOT code-enforced as a blind `confidence < 5` filter:
> the shared-state status is grep-derived (not a finding field), so a mechanical drop would
> re-automate the run_fd4d756b regression (a conf-4 shared-state finding wrongly suppressed
> → real kill-healthy-subprocess bug shipped).

Three tiers (a finding cleared by 3c.1, or not shared-state):
- Confidence 7+: show normally in findings output
- Confidence 5-6: show with caveat "⚠️ Medium confidence — verify"
- Confidence 3-4: suppress from main findings (**appendix only — retained, not dropped**)
- Confidence 1-2: suppress entirely (dropped)

**Do NOT collapse this into a binary keep/drop at 5** — the conf 3-4 appendix tier is
retained for visibility, and Step 5's fix-gate (`confidence >= 5`) depends on the
surviving-vs-appendix distinction. Blanket-dropping all sub-5 findings loses the appendix
and violates the "verify top findings by evidence" discipline.

**3c.1 Shared-State Override (BLOCKING — overrides suppression):**

A low-confidence finding may NOT be suppressed if it touches **shared mutable
state** — a variable, flag, field, or attribute read by **2+ code sites**.
Confidence reflects the reviewer's certainty about ONE site; it cannot account
for the blast radius across all readers. The reviewer who flagged it likely
checked the same single consumer you'd check to dismiss it.

**Trigger:** finding references a variable/flag/attribute (e.g. `_content_emitted`,
`self._x`, a status field, a module-level global) that the changeset's diff
**reads or writes**, AND that symbol has ≥2 readers.

**Required action (do NOT suppress until done):**
1. `grep -n "<symbol>" <changed_file>` and across the module → list ALL readers.
2. For EACH reader, answer: does the value change introduced by this changeset
   alter that reader's behavior? Binary yes/no, per site.
3. If ANY reader's behavior changes in a way not covered by a test → the finding
   is REAL regardless of the reviewer's original confidence. Fix + add a test.
4. If all readers verified safe → record the per-site verification in the
   delivery artifact (`shared_state_audit`), THEN suppression is allowed.

**Why this exists (run_fd4d756b, 2026-05-30):** adversarial review flagged a
LOW conf-4 finding on `_content_emitted`. It was suppressed/rejected after
checking only ONE of its 3 consumers (the one that confirmed dismissal). The
2nd consumer (zombie detection: `streaming_dur<2s and not _content_emitted →
kill+retry`) had no guard — a real kill-healthy-subprocess regression shipped
to the delivery artifact. PE review caught it post-hoc. Confidence gating is
calibrated for false-positive noise, NOT for blast-radius blindness. Shared
state is the one place a "low confidence" score is structurally untrustworthy.

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

5. ARCHITECTURAL INTEGRITY (No-Patch Gate)
   - Does this fix ADD net complexity (lines, files, /tmp coordination,
     new guard clauses, new state to manage)? If yes → likely a patch.
   - Is the problem being fixed at the layer where it OCCURS (symptom)
     or the layer where it ORIGINATES (root cause)?
   - Does this solution re-implement capability that already exists
     elsewhere in the system? (e.g., process isolation in shell when
     the daemon already provides it; retry logic in caller when the
     framework already retries)
   - The test: "If I removed this code in 6 months, would the system
     still work correctly because the RIGHT layer handles it?" If no →
     this is the right layer. If yes → this is a patch on the wrong layer.
   - RED FLAG: any solution that requires N callers to "remember" to do
     something (nohup, special flags, coordination files) instead of
     providing a single correct interface.

6. CROSS-FIX INTERACTION
   - The adversarial specialists each reviewed the changeset in their OWN
     domain (correctness, security, perf, …) and each fix was validated in
     isolation. NOBODY checked whether the fixes INTERACT once merged. Answer:
   - Do any two fixes touch the SAME function/state/flow such that applying
     both changes the behavior neither reviewer saw alone? (e.g. fix-A adds a
     guard that fix-B's new caller trips; fix-A and fix-B both mutate the same
     field with opposite assumptions)
   - Does fixing A REINTRODUCE or UN-FIX B — or a bug an EARLIER cycle already
     resolved? (the fixes were applied at different times; the last one wins on
     shared lines)
   - Does the COMBINED changeset create an ordering/lifecycle dependency that no
     single-fix review would flag? (fix-A must run before fix-B; a shared
     resource is now acquired twice / released once)
   - This is DISTINCT from: section 5 No-Patch (one fix at the wrong layer) and
     the Shared-State Override in Step 3c.1 (one symbol's readers). Here the
     unit is MULTIPLE fixes and whether they compose correctly. If the changeset
     has only one fix, answer "N/A — single fix" and move on.

## Output
```json
{
  "risks": [
    {"category": "deployment|scaling|format|first-run|cross-fix",
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

### User-Value Probe (BLOCKING)

**After meta-review, before Doc Sync Check.** Applies to ALL profiles that
produce user-facing output (full, bugfix). Skip for research/docs.

**Purpose:** Catches the "code is correct but output is useless" failure mode.
Adversarial review validates code quality. Meta-review validates operational
correctness. Neither asks: **"Would a real user actually benefit from this?"**

This probe was born from run_bbe3f167 (AI-Ready-Repo Engine M1, 2026-06-01):
pipeline declared PUSH-READY with 9 green tests, adversarial clean, all ACs
pass — but the output was a README paraphrase. No code was read. The TECH.md
had no file citations. code-intel.json edges were fabricated. A user receiving
this output would learn NOTHING they couldn't get from `cat README.md`.

**The probe asks ONE question:**

> "If I'm the user who requested this feature, and I receive this output —
> can I point to at least 3 things in the output that are ONLY knowable by
> the work this pipeline did? Things I couldn't have gotten by spending 2
> minutes myself?"

**How to verify (mechanical, not vibes):**

1. Pick 3 claims from the output (e.g., a convention in TECH.md, an edge in
   code-intel.json, a gotcha in IMPROVEMENT.md, a test result)
2. For each claim, answer: "Could this be produced WITHOUT the work this
   pipeline did?" — i.e., without reading source code, running tests, analyzing
   git history, or asking the user questions.
3. If YES for all 3 → the output is derivable from public surface info →
   **NOT-PUSH-READY: output adds no value over what user could do in 2 minutes**

**Examples:**

| Claim in Output | Derivable without pipeline work? | Verdict |
|---|---|---|
| "Uses Python 3.9+" | YES — visible in pyproject.toml | ❌ Not value-add |
| "palace.py `mine_lock` is threading.Lock, not async-safe — concurrent mine from MCP + CLI = deadlock" | NO — requires reading palace.py:35 + understanding MCP server concurrency model | ✅ Value-add |
| "ALWAYS call through `palace.get_collection()` — never instantiate PersistentClient directly (observed in: searcher.py:20, miner.py:23, format_miner.py:72)" | NO — requires reading 4 source files and identifying the repeated pattern | ✅ Value-add |
| "Architecture: 12 modules" | YES — `ls mempalace/` gives this | ❌ Not value-add |

**Verdict rules:**
- 3+ value-add claims found → PASS (output demonstrates genuine work)
- 1-2 value-add claims → WARNING (output is thin — suggest enrichment)
- 0 value-add claims → **NOT-PUSH-READY** (output is a paraphrase, not analysis)

**Record in delivery artifact:**
```json
{
  "user_value_probe": {
    "verdict": "PASS|WARNING|FAIL",
    "claims_checked": 3,
    "value_add_count": 3,
    "evidence": [
      {"claim": "...", "derivable_without_work": false, "source": "file:line"}
    ]
  }
}
```

**Why this is BLOCKING (not advisory):** The pipeline's entire value proposition
is "I did the work so you don't have to." If the output contains nothing that
required the work, the pipeline consumed tokens and produced noise. A user who
receives a "score 7.8/10" report full of README-derivable facts will never
trust the tool again. First impressions are permanent. One garbage output
destroys credibility for all future outputs.

---

### Doc Sync Check (Non-Blocking Warning)

**After meta-review, before generating report.** Verifies that shipped code
has corresponding documentation updates. Catches "code shipped, docs forgot"
drift that accumulates silently.

**Pre-flight: verify origin/main exists.**
If `git rev-parse origin/main >/dev/null 2>&1` fails, skip doc-sync check
entirely and note "WARN: origin/main not available, doc-sync check skipped"
in the report. This handles fresh clones and shallow checkouts.

**Run these checks against the changeset:**

```bash
# 0. Pre-flight
git rev-parse origin/main >/dev/null 2>&1 || { echo "WARN: origin/main unavailable"; exit 0; }

# 1. New files in backend/core/ → must have TECH.md Key Subsystems entry
NEW_CORE_FILES=$(git diff --name-only --diff-filter=A origin/main...HEAD | grep '^backend/core/' | grep -v '__pycache__\|test')

# 2. Feat commits → docs/ should have been updated in this branch
FEAT_COMMITS=$(git log --oneline origin/main...HEAD | grep -iE "^[a-f0-9]+ feat")
DOCS_IN_BRANCH=$(git diff --name-only origin/main...HEAD -- docs/ | wc -l)

# 3. COE/P0 fix → docs/post-mortems/ should have new file
COE_FIX=$(git log --oneline origin/main...HEAD | grep -iE "COE|P0|bilateral|deadlock|crash.*all")
PM_COUNT=$(git diff --name-only --diff-filter=A origin/main...HEAD | grep '^docs/post-mortems/' | wc -l)
```

**TECH.md location note:** TECH.md is in the SwarmWS workspace, NOT in the
swarmai repo. Use an absolute path Read:
`~/.swarm-ai/SwarmWS/Projects/<PROJECT>/TECH.md`
Do NOT use git grep — use the Read tool to search for the filename stem.

**Evaluation rules:**

| Condition | Check | Gap? |
|-----------|-------|:---:|
| New `.py` in `backend/core/` (not test) | Read `~/.swarm-ai/SwarmWS/Projects/<PROJECT>/TECH.md`, search for filename stem | If not found → GAP |
| Any `feat` commit in changeset | `docs/` has ≥1 file changed in branch (`git diff ... -- docs/`) | If 0 → GAP |
| Commit message mentions COE/P0/crash | `docs/post-mortems/` has new file in changeset | If 0 → GAP |
| New skill created (`backend/skills/s_*`) | Skill appears in `docs/README.md` or relevant design doc | If not → GAP |

**Output:**

```
DOC SYNC CHECK:
  ✅ No new core/ files (or all documented in TECH.md)
  ⚠️ GAP: feat commits present but docs/ not updated in 7 days
  ✅ No COE/P0 fixes (or post-mortem exists)

  Gaps found: 1
  Recommendation: Update docs/README.md or create a design doc for the new feature.
```

**Impact on push-ready:** Doc gaps are **WARNING, not BLOCKING.** The pipeline
can still declare push-ready with doc gaps — but gaps are:
1. Surfaced in the Pipeline Report (§10 Known Gaps)
2. Auto-created as Radar Todo (priority MED, source: pipeline-doc-check)
3. Picked up by the weekly `docs-freshness-audit` job if not addressed

**Why non-blocking:** Blocking on docs would create incentive to write low-quality
docs just to unblock. Better: ship the code, surface the gap, let the weekly
audit enforce. The pipeline's job is quality CODE. Docs are a lagging indicator.

**Why still valuable in pipeline:** Catching the gap AT delivery time means the
author still has context. A week later (when the audit job catches it), context
is gone and the doc will be worse.

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
| Cross-fix interaction | CLEAR / RISK / N/A | <do the fixes compose; single fix = N/A> |

**Overall:** CLEAR / RISKS_IDENTIFIED (N items, M addressed)

## 7.7 Completion Audit
| # | Acceptance Criterion | Evidence | Verified |
|---|---------------------|----------|----------|
| 1 | ... | test_xxx.py::test_yyy | ✅ |
| 2 | ... | [gap: not implemented] | ❌ |

**Gaps found:** N | **Gaps fixed:** M | **Attention flags:** K

## 7.8 Doc Sync Check
| Check | Result | Gap |
|-------|--------|-----|
| New core/ files documented | ✅ / ⚠️ | <detail if gap> |
| Feat commits + docs updated | ✅ / ⚠️ | <detail if gap> |
| COE/P0 + post-mortem exists | ✅ / N/A | <detail if gap> |

**Gaps:** N | **Todos created:** M

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

### Pipeline Boundary: PUSH-READY → auto LOCAL-commit → Pipeline Done

**The pipeline auto-commits LOCALLY at PUSH-READY, then stops.** Push to remote
and CI verification are USER-INITIATED actions, not pipeline steps.

```
Pipeline scope:  code quality達標 → PUSH-READY → auto local-commit → REFLECT → COMPLETE
User scope:      git push → CI green → PR (optional)
```

**Auto local-commit (run_76932250) — MANDATORY, right after the PUSH-READY gate:**

```bash
python backend/scripts/artifact_cli.py run-commit --project <PROJECT> --run-id <RUN_ID>
```

`run-commit` stages EXACTLY this run's `files_touched` (recorded during BUILD, see
below) via `git add -- <path>` — NEVER `git add -A` — so a parallel session's
in-flight edits are never swept in (R29). It commits locally in whichever repo
each file lives in (source repo and/or SwarmWS), and **NEVER pushes** (STEERING
#5: 限制的是 auto-push,不是 local commit). It refuses if the deliver stage isn't
push_ready or if `files_touched` is empty, and WARNS (listing them) if the working
tree has changes this run didn't track — so a forgotten record surfaces loudly
instead of silently committing the wrong set.

**Local PR (run_dcce7023) — surface it EXPLICITLY, it does NOT auto-pop.** When there
are source-repo commits, `run-commit` also writes a `LOCAL_PR.md` (TL;DR + this-run
commits + files-changed + REPORT link + PUSH-READY state) and returns its path as
`local_pr` in the JSON output. Because the pipeline runs source changes as `kind=source`
(deliberately NOT per-file popped, XG: 真实 repo 改动中途不 display,收尾聚成 PR),
this aggregate is the review home for them — but it is written by THIS CLI subprocess,
so no `file_changed` event auto-fires for it (and it lives under `.artifacts/` = a
`process` path). **The agent MUST surface it as an explicit COMPLETE-stage action:**
read the `local_pr` path from the JSON, present the LOCAL_PR.md contents **inline in the
chat** (the visible channel, STEERING #13), and open the Canvas panel via the existing
`ui_action open-canvas`. (There is intentionally no `open-file` action — host-path
infoleak, `ui_actions.py`; the agent presents the content itself, it does not hand the
UI a host path.)

**Why this boundary exists:**
- Local commit is safe + reversible; push is a deployment decision (user controls
  when the remote — possibly PUBLIC — gets updated).
- CI depends on remote infrastructure (auth, network, GitHub availability).
- Pipeline must be completable offline (local-only development is valid).
- STEERING #5 governs push — auto-commit local ≠ auto-push remote.

**What the pipeline DOES guarantee at PUSH-READY:**
- All tests pass locally (L1)
- No regressions (L3)
- Adversarial review clean (L4)
- DDD conformance verified (L5)
- All decisions resolved (L6)

**After pipeline COMPLETE, suggest (don't execute):**
```
Next: push to remote + verify CI
```

The STEERING standing rule (Post-Push CI Ownership) takes over from there —
every `git push` must be followed by `gh run watch` → green or fix. That rule
is always-on regardless of whether a pipeline ran.

### Auto PR Creation (full/bugfix profiles only — USER-INITIATED)

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
python backend/scripts/artifact_cli.py publish --project <PROJECT> --run-id <RUN_ID> \
  --type delivery --producer s_autonomous-pipeline \
  --summary "Delivery: <feature title> (PUSH-READY)" --stage deliver \
  --data '{"title":"...","quality":{"tests_pass":true,"regressions":0,"smoke_pass":true},"adversarial_review":{"spawned":true,"profile_tier":"full","findings_total":N,"findings_fixed":N,"findings_remaining":0,"findings":[{"severity":"HIGH|MEDIUM","resolved":true,"finding":"path/file.py func() line N: issue. Fixed: how."}]},"completion_audit":{"all_green":true,"requirements_met":N,"requirements_total":N,"evidence":"..."},"meta_review":"...","report_path":"runs/<RUN_ID>/REPORT.md"}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state reflect --run-id <RUN_ID>
```

---

## Common Rationalizations

| Rationalization | Reality | Source |
|---|---|---|
| "Tests pass, adversarial review is unnecessary" | C011: 57 tests green, 10/10 confidence → feature 100% non-functional. C021: skipped adversarial when validator was strict. Pipeline confidence measures PROCESS compliance, not CODE correctness. Adversarial review from fresh context catches what self-review structurally cannot. | C011, C021 |
| "Validator schema is strict — I'll force past it" | Strictness IS the quality gate working as designed. Bypassing the validator = bypassing the requirement, not fixing a bug. C021: forced past validator → shipped incomplete. | C021 |
| "Code is simple, 1-2 files, no new API surface" | C025: "simple" task (3 files, 2 new functions) skipped pipeline entirely. Agent said "I know this code well, tests pass." User caught it. Subjective complexity estimates are unreliable — that's why we have mechanical gates. | C025 |
| "I already reviewed this multiple times during BUILD" | Repetition ≠ fresh perspective. You validated your own assumptions N times. Adversarial review spawns a NEW agent with ZERO builder bias. That agent reads the code cold and asks "what's wrong?" — you can't do that to your own work. | LL09 |
| "Convergence loop passed quickly — we're good" | Fast convergence (1 iteration) may mean the gates are too easy OR the agent is rationalizing green across all 6 layers. Quick convergence on a non-trivial change deserves extra scrutiny, not less. | Pipeline design |
| "Meta-review is redundant after adversarial" | Adversarial reviews CODE. Meta-review reviews PROCESS ("what did the pipeline miss?"). They catch different classes: adversarial catches bugs in what was built; meta catches gaps in what WASN'T built. | Pipeline design |
