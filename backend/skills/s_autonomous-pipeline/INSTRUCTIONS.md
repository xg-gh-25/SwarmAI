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

Run the stage's behavior inline. DO NOT invoke a separate skill with slash
commands. Execute the behavior directly in this session.

#### EVALUATE

Follow the s_evaluate workflow:
1. Parse the requirement (what/why/who/constraints)
2. Score against DDD docs (strategic 1-5, feasibility 1-5, historical 1-5, priority 1-5)
3. Calculate ROI = (strategic * 0.35) + (priority * 0.25) + (historical * 0.15) + (feasibility * 0.25)
4. Classify scope: trivial / standard / complex / research-only / docs-only
5. Recommend: GO (>= 3.2) / DEFER (2.0-3.1) / REJECT (< 2.0) / ESCALATE
6. Define acceptance criteria (3-5 items)

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type evaluation --producer s_autonomous-pipeline \
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
  --type research --producer s_autonomous-pipeline \
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
  --type design_doc --producer s_autonomous-pipeline \
  --summary "Design: <approach> for <requirement>" \
  --data '{"approach":"...","acceptance_criteria":[...],"data_model":"...","api_contract":"...","files_to_change":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state build
```

#### BUILD (TDD Red-Green Cycle)

The BUILD stage follows TDD methodology: tests before code, code until tests pass.

**Step 1: RED — Generate tests from acceptance criteria**

1. Read acceptance criteria from the evaluation artifact (or design_doc if PLAN ran)
2. Read TECH.md for test framework (pytest, vitest) and conventions
3. Generate test stubs — one test per acceptance criterion minimum:
   ```
   # For each criterion in acceptance_criteria:
   #   -> Write a test that WILL FAIL (nothing implemented yet)
   #   -> Name: test_<criterion_slug>
   #   -> Assert the expected behavior from the criterion
   ```
4. Run the tests — **all must FAIL** (this proves the tests are meaningful)
5. If any test passes before implementation → the test is trivial or wrong, rewrite it

**Step 2: GREEN — Implement until all tests pass**

6. Read TECH.md for code conventions, patterns, and style
7. Implement changes guided by the design_doc artifact (if available)
8. Run tests after each logical change — watch failures decrease
9. **Completeness bias:** when the complete implementation costs minutes more
   than the shortcut, do the complete thing. Cover edge cases, handle errors.
10. Use atomic commits: one commit per logical change

**Step 3: VERIFY — Targeted tests, zero regressions**

⚠️ **VERIFY rules (BLOCKING):**
- Run **changed test files + test files that import changed modules**. NOT the full suite.
  ```
  pytest tests/test_foo.py tests/test_bar.py --timeout=60
  ```
- If you're unsure which tests to run, use `pytest --lf --timeout=60` (last-failed).
- **NEVER** run bare `pytest` without specifying files — conftest blocks >300 tests.
- **NEVER** use `--run-all` — that's for humans running `make test-all`, not pipeline.
- **NEVER** pipe pytest through `| tail` — it hides pass/fail and xdist status.
- If all tests pass → proceed to Step 4. Done.
- If tests fail → fix code, re-run **only failing tests**.
- **Max 2 VERIFY re-runs total.** After 2 runs, if still failing:
  publish changeset with `"regressions": N` and advance to REVIEW anyway.
- Track VERIFY attempt count explicitly: "VERIFY attempt 1/2", "VERIFY attempt 2/2".

11. Run changed + related test files — all must pass
12. If existing tests break → fix production code, NOT the existing tests
13. Track all files changed and test results

**Step 4: SMOKE — exercise new code paths (catch runtime crashes)**

14. For each modified file that has new branches (if/else, try/except,
    config-gated paths), write a minimal inline smoke test that forces
    execution through the new path. The goal is to catch AttributeError,
    NameError, and other runtime crashes that unit tests miss because
    they mock the surrounding context.

    ```python
    # Example: new config-gated code in prompt_builder.py
    from core.prompt_builder import PromptBuilder
    pb = PromptBuilder(config={"memory_progressive_disclosure": True})
    # Call the method that contains the new branch — don't assert output,
    # just verify it doesn't crash with AttributeError/TypeError.
    ```

    Rules:
    - If the new code is behind a config flag → test with flag=True
    - If behind a conditional (channel_context, resume, etc.) → construct
      the triggering condition
    - If new code is in a try/except → temporarily remove the except to
      verify the try body doesn't crash (the except silently swallows
      bugs like `self.config_manager` → `self._config`)
    - Smoke tests are **inline verification only** — don't commit them.
      They're a build-time gate, not regression tests.
    - If a smoke test crashes → fix the bug before proceeding to REVIEW.

    This step exists because of a real incident: `self.config_manager`
    (wrong attribute name) passed 8 pipeline stages undetected because
    it was inside try/except and no test exercised the actual runtime path.

    **Resource lifecycle verification** (added after run_c2881d2f: 3
    CRITICAL subprocess bugs survived 14 green unit tests + 4 smoke tests):

    For each new resource acquisition in the changeset, verify BOTH the
    success path AND the failure/timeout path release the resource:

    | Resource Type | Success Check | Failure Check |
    |---------------|--------------|---------------|
    | subprocess (`create_subprocess_exec`) | exits with returncode | `proc.kill()` + `await proc.wait()` in finally. `wait_for` timeout → kill before re-raise. `FileNotFoundError` caught. |
    | temp files | deleted after use | deleted in finally (`unlink(missing_ok=True)`) |
    | MediaStream / hardware | tracks stopped | stopped in useEffect cleanup |
    | network / sockets | closed / consumed | timeout set + cleanup on error |
    | file handles | closed | context manager or finally |

    For each applicable row, write a smoke test that:
    1. Triggers the failure path (mock timeout, FileNotFoundError, etc.)
    2. Asserts the resource was released (mock.kill.assert_called, etc.)

    Don't skip this for "simple" subprocess calls. Voice Input had 14
    green tests yet 3 CRITICAL subprocess bugs because mocks replaced
    the entire subprocess lifecycle. The mock proved "if transcription
    returns X, endpoint returns X" — but not "subprocess is killed on
    timeout." Test the resource, not the happy path around it.

**Step 5: USER-PATH TRACE — walk real scenarios through real code**

15. For each acceptance criterion, pick **one concrete user action** and
    trace it through the actual production code path — not tests, not
    mocks, the real call chain.

    For each trace:
    a. **Start from the user action** — "Titus sends a Slack DM", not
       "\_poll\_channel\_messages is called"
    b. **Follow every function call** — read the real source, not from
       memory. Note the actual input each function receives.
    c. **Check external data shapes** — when the code consumes data from
       an external API (Slack, DB, filesystem), verify your test mocks
       match the real response schema. `conversations.history` messages
       lack `channel`; Socket Mode events have it. These differences
       are invisible in unit tests that supply hand-crafted dicts.
    d. **Check cross-component boundaries** — when one component calls
       another (adapter → gateway, hook → registry), trace what happens
       on the OTHER side. Error callbacks, state resets, object
       destruction — these are where bugs hide.
    e. **Check competing paths** — if two mechanisms handle the same
       event (e.g., `_on_error` callback AND health monitor both react
       to thread death), which fires first? Does the first one prevent
       the second from ever running?

    **Action on findings:**
    - Each finding → **fix immediately** (these are always real bugs)
    - Update tests to cover the discovered path

    **Why this exists:** run\_ec4a73ff shipped with 26 TDD tests, 10/10
    confidence, and 8 pipeline stages passed. User-path trace found 2
    CRITICAL bugs in 5 minutes: (1) `_on_error` fired before health
    monitor → gateway destroyed adapter → `_ws_fail_count` reset →
    polling never activated, (2) `conversations.history` messages lack
    `channel` field → `external_chat_id=""` → routing broken. Both
    invisible to unit tests because mocks didn't match real API data
    and no test crossed the adapter↔gateway boundary. This is LL04's
    third recurrence: engineering-complete ≠ user-complete.

**The TDD constraint:** Fix code, not tests. Tests are derived from the accepted
design. Changing a test = changing the spec = go back to PLAN.

**Mock discipline:** When mocking objects in tests, use `spec=RealClass` or only
set attributes that exist on the real class. Bare `MagicMock()` silently accepts
ANY attribute access — this hides `AttributeError` bugs that crash in production.
For integration-facing tests (anything that touches cross-module boundaries),
prefer real objects over mocks. If you must mock, mock the leaf dependency
(DB, network), not the intermediate object.

**Adversarial inputs in RED phase:** For NLP/parsing code, the RED phase must
include adversarial inputs: URLs, file paths, code snippets, empty/minimal
strings, Unicode edge cases (CJK, Kana, Hangul, emoji), and multi-language mix.
These are the inputs that break keyword extractors, parsers, and formatters.

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type changeset --producer s_autonomous-pipeline \
  --summary "<N> files changed, <M> commits, TDD: <red>/<green>/<verify>" \
  --data '{"branch":"...","commits":[...],"files_changed":[...],"diff_summary":"...","tdd":{"acceptance_criteria_count":N,"tests_generated":M,"red_failures":K,"green_pass":true,"regressions":0,"smoke_tests":S,"smoke_crashes_caught":C,"user_path_traces":T,"user_path_bugs_found":B}}'
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
4. **Integration Trace** — verify every new public symbol is actually wired:

   For every new function, parameter, config key, or `.get("key")` call in the
   changeset, grep the codebase for production callers (exclude test files):

   | New symbol type | Verification | Example |
   |-----------------|-------------|---------|
   | New public function | >= 1 non-test caller exists | `generate_memory_index()` called by `inject_index_into_memory()` ✅ |
   | New parameter on existing function | >= 1 call site passes it | `memory_progressive=True` passed by `prompt_builder.py` ✅ |
   | New config key in DEFAULT_CONFIG | Trace: `DEFAULT_CONFIG` → `config_manager.get()` → consumer | `memory_progressive_disclosure` read by prompt_builder ✅ |
   | `agent_config.get("key")` or `config.get("key")` | Verify key has a setter | `_first_user_message` — no setter ❌ |
   | New CLI flag / argument | >= 1 caller passes it | `--regenerate-index` — 0 callers ❌ |

   **Action on findings:**
   - 0 production callers → **WARN** (not BLOCK). Agent must either:
     - Wire it now (add the caller), or
     - Document as intentional: "deferred — caller planned for Phase X"
   - Undocumented dead symbols are not acceptable — every WARN needs a resolution.

   **Why WARN not BLOCK:** Some interfaces are designed ahead of their callers
   (e.g., Phase 4 archival functions). Blocking would force premature wiring.
   But the agent must make an explicit decision, not silently ship dead code.

   Include integration trace results in the review artifact under `"integration_trace"`.

5. **Replace/Move Parity Check** — when code is **moved or replaced** (not just added):

   | Check | What to verify | Example |
   |-------|---------------|---------|
   | Feature parity | Every capability of old code exists in new code | Old `_recall_knowledge` had TranscriptStore; new `_recall_for_query` must too |
   | Dead orphan detection | After removing a call site, grep old function — if 0 callers remain, flag as dead code | `_recall_knowledge` still defined after its only caller was removed |
   | Argument validity | Mock attributes must exist on the real class | `unit.working_directory` doesn't exist on SessionUnit |

   This check exists because PE review of the RecallEngine activation found 2 HIGH
   bugs: (1) replaced function dropped a capability (TranscriptStore), (2) test mock
   hid a missing attribute. Both would have been caught by feature parity diff.

6. **UX Review** — **only when changeset includes frontend files** (`.tsx`, `.jsx`, `.css`,
   `.html`, `.svelte`, `.vue`). Skip entirely for backend-only changesets.

   Walk through every new/changed user-facing interaction and check:

   | # | Check | What to verify | Example failure |
   |---|-------|---------------|-----------------|
   | UX1 | **Discoverability** | How does the user discover this feature? Is there a hint, tooltip, or visual affordance? | Diff lines became clickable but no visual cue existed |
   | UX2 | **Feedback** | New interactive elements have hover, active, and disabled states? | Clickable rows missing hover highlight |
   | UX3 | **Behavioral contracts** | Reused components — are reactive props actually reactive? (values that must update on scroll/resize/state change) | CommentPopover `topOffset` passed as DOM snapshot instead of React state |
   | UX4 | **Escape / click-outside** | Escape and click-outside behave correctly in all contexts: modal, panel, portal? Does Escape propagate unexpectedly? | Escape in portal CommentPopover also closed the parent editor |
   | UX5 | **Scroll tracking** | Positioned elements (popover, tooltip, dropdown) — do they follow when the container scrolls? | Popover stays in place while diff content scrolls away |

   **Action on findings:**
   - Each finding → **auto-fix** (these are always bugs, not taste decisions)
   - Include UX review results in the review artifact under `"ux_review"`

   **Why this exists:** Pipeline run_6455a707 shipped with 10/10 confidence and 44/44
   tests, but E2E user walkthrough found 3 bugs in 5 minutes (scroll tracking, no
   discoverability hint, Escape propagation). Engineering-complete ≠ user-complete.

7. **Runtime Pattern Checklist** — scan the changeset for known bug patterns.

   These are recurring production bugs that code review and unit tests
   consistently miss. For each pattern that applies to the changeset,
   explicitly verify the fix is in place. Do NOT skip patterns — a "no"
   answer is fine, but silence means unchecked.

   | # | Pattern | Trigger (when to check) | What to verify | Example bug |
   |---|---------|------------------------|----------------|-------------|
   | RP1 | **subprocess timeout orphan** | `create_subprocess_exec` or `subprocess.Popen` | `asyncio.wait_for` timeout path has `proc.kill()` + `await proc.wait()` BEFORE re-raise | ffmpeg runs forever after 30s timeout (run_c2881d2f) |
   | RP2 | **subprocess missing binary** | `create_subprocess_exec("some_binary", ...)` | `FileNotFoundError` caught → user-friendly error message | ffmpeg not installed → raw 500 instead of "install ffmpeg" (run_c2881d2f) |
   | RP3 | **React hook cleanup** | new `use*` hook with refs or subscriptions | `useEffect` return releases all resources (streams, timers, listeners) | Mic never released on tab close (run_c2881d2f) |
   | RP4 | **stale closure** | callback passed to hook/async uses component state | use `useRef` to hold latest value, read `.current` in callback | transcribed text overwrites user's typing (run_c2881d2f) |
   | RP5 | **FormData Content-Type** | `new FormData()` sent via axios/fetch | NO explicit `Content-Type` header — browser must add boundary | Multipart broken, backend gets 400 (run_c2881d2f) |
   | RP6 | **setTimeout leak** | `setTimeout` in component/hook | timer ID stored in ref, cleared in cleanup effect | setState on unmounted component (run_c2881d2f) |
   | RP7 | **error msg ↔ constant mismatch** | error messages containing numeric values | message matches the actual threshold/constant in code | "need 0.5s" but constant is 1.0s (run_c2881d2f) |
   | RP8 | **hardcoded env assumption** | region, URL, port, path as string literal | configurable via env var or parameter with sensible default | us-east-1 hardcoded, user in us-west-2 (run_c2881d2f) |

   **Output format:** For each applicable pattern, one line:
   ```
   RP1: ✅ proc.kill() in timeout handler + finally (voice_transcribe.py:92,112)
   RP3: ✅ useEffect cleanup releases stream + recorder (useVoiceRecorder.ts:58-68)
   RP5: N/A — no FormData in this changeset
   ```

   Include checklist results in the review artifact under `"runtime_patterns"`.

   **Why this exists:** Voice Input (run_c2881d2f) passed 8 pipeline stages
   with 10/10 confidence and 14 green tests. E2E review found 12 issues.
   8 of 12 were instances of these 8 patterns. The patterns are NOT novel
   — they're the same bugs every project hits. A 30-second checklist
   catches what unit tests structurally cannot.

8. **Cross-Boundary Wire Test** — **only when changeset includes BOTH
   frontend API calls AND backend endpoints** (e.g., new `.ts` service
   function + new `@router.post`). Skip for single-layer changes.

   For each frontend→backend boundary in the changeset, explicitly answer:

   | # | Question | How to verify | Example failure |
   |---|----------|--------------|-----------------|
   | WR1 | **Content-Type match?** | Frontend sends X → backend parser expects X | Axios sends `application/json` default, backend expects `multipart/form-data` |
   | WR2 | **Field names match?** | Frontend `form.append('audio', ...)` → backend `form.get("audio")` | Frontend sends `audioFile`, backend reads `audio` → None |
   | WR3 | **Response shape match?** | Backend returns `{"transcript": ...}` → frontend types `TranscribeResult` has `transcript` | Backend returns `text`, frontend reads `transcript` → undefined |
   | WR4 | **Error shape match?** | Backend raises `HTTPException(400, detail=...)` → frontend error handler expects `response.data.detail` | Backend returns `{"message": ...}`, frontend reads `detail` |

   **Output format:**
   ```
   Wire: POST /api/chat/transcribe
     WR1: ✅ FormData (auto Content-Type) → request.form() (multipart parser)
     WR2: ✅ "audio" field name matches both sides
     WR3: ✅ {transcript, language, duration_ms} matches TranscribeResult
     WR4: ✅ HTTPException detail → axios error.response.data.detail
   ```

   This is code-level trace only — no live requests needed. Read the
   frontend service function and the backend endpoint side by side.

   Include wire test results in the review artifact under `"wire_test"`.

   **Why this exists:** Voice Input (run_c2881d2f) had an explicit
   `Content-Type: multipart/form-data` header that broke the Axios
   boundary string — voice input would have been completely non-functional.
   Integration trace verified "symbols are connected" but not "the data
   format crossing the wire is correct." This check fills that gap.

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type review --producer s_autonomous-pipeline \
  --summary "Review: <N findings>, <M auto-fixed>, <K integration warnings>, <J ux findings>, <P runtime patterns>, <W wire tests>" \
  --data '{"findings":[...],"approved":true/false,"security_findings":[],"integration_trace":{"checked":N,"connected":M,"warnings":[...]},"ux_review":{"triggered":true/false,"checks":5,"findings":[...]},"runtime_patterns":{"checked":N,"passed":M,"findings":[...]},"wire_test":{"boundaries":N,"verified":M,"findings":[...]}}'
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
6. Run changed + related test files after all fixes (NOT full suite)

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type test_report --producer s_autonomous-pipeline \
  --summary "Tests: <passed>/<total> pass, <fixed> bugs fixed" \
  --data '{"passed":N,"failed":M,"fixed":K,"skipped":J,"bugs":[...],"coverage":"..."}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state deliver
```

#### DELIVER

**Run the Delivery Gate first** (see Step 4 below), then:

1. **Confidence scoring** — assess how confident you are the delivery matches the requirement:
   ```
   confidence_score (1-10):
     +3 if all acceptance criteria have passing tests
     +2 if review found 0 critical issues
     +2 if TDD red-green cycle completed cleanly
     +1 if no taste decisions were overridden
     +1 if zero regressions on existing tests
     +1 if design_doc was available (not just evaluation)
     -2 if any acceptance criterion lacks a test
     -2 if WTF gate triggered (even if resolved)
     -2 if smoke_tests == 0 and files_changed > 1 (runtime crashes likely hidden)
     -2 if user_path_traces == 0 and files_changed > 1 (real data flow unverified)
     -1 if integration_trace.checked == 0 (wiring unverified)
     -1 if frontend files changed but ux_review.triggered == false (UX unverified)
     -1 if runtime_patterns.checked == 0 and applicable patterns exist (known bugs unchecked)
     -2 if frontend+backend changed but wire_test.boundaries == 0 (cross-layer contract unverified)
     -1 per unresolved warning from validator
   ```
   If confidence < 7 → flag for human review even without judgment decisions.

2. **Generate pipeline report** as markdown in the project's artifacts directory.
   Save to: `Projects/<PROJECT>/.artifacts/runs/<RUN_ID>/REPORT.md`

   The report follows this template (every run produces one):

   ```markdown
   # Autonomous Pipeline Report: <title>

   **Run ID:** run_<id> | **Project:** <PROJECT> | **Profile:** <profile>
   **Date:** <ISO date> | **Confidence:** <score>/10

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
   - **TDD cycle:** RED (<N> tests generated, all failed) → GREEN (code until pass) → VERIFY (full suite)

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
   | Confidence | X/10 |

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

3. Generate PR description (if changeset exists)
4. Update PROJECT.md with delivery entry
5. Check for unresolved issues from upstream stages

Publish artifact:
```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type delivery --producer s_autonomous-pipeline \
  --summary "Delivery: <feature title> (confidence: <N>/10)" \
  --data '{"title":"...","summary":"...","decisions":[...],"quality":{...},"attention_flags":[],"confidence_score":N,"confidence_breakdown":{...},"report_path":"runs/<RUN_ID>/REPORT.md"}'
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
2. **TDD is mandatory in BUILD.** Generate tests from acceptance criteria FIRST.
   Run them RED (all fail). Write code until GREEN (all pass). Verify FULL suite
   (zero regressions). Fix code, not tests. Changing tests = changing the spec.
3. **Classify every decision.** No unclassified decisions. If unsure, default
   to "taste" (surface at delivery gate rather than block or ignore).
4. **Verify before advancing.** Run pipeline_validator.py after every stage.
   Never skip verification. Garbage in one stage becomes garbage in all downstream.
5. **Completeness bias.** When the complete implementation costs minutes more
   than the shortcut, do the complete thing. (gstack "Boil the Lake" principle.)
6. **Atomic commits.** One commit per logical change in BUILD and TEST stages.
   This enables rollback if a fix breaks something.
7. **Never loop forever.** Respect max_retries. Checkpoint on exhaustion.
   Three attempts at the same stage is enough.
8. **Taste decisions batch at delivery.** Don't interrupt the user mid-pipeline
   for taste decisions. Accumulate them, present once at the delivery gate.
9. **Judgment decisions block immediately.** Don't continue past a judgment
   decision. The whole point is that the agent genuinely doesn't know.
10. **Pipeline state is the artifact registry.** Use artifact_cli for ALL state
    operations. No separate state store.
11. **DEFER/REJECT at evaluate ends the pipeline.** Don't continue stages after
    the evaluate stage says stop.
12. **Always generate REPORT.md.** Every pipeline run produces a markdown report
    at `.artifacts/runs/<RUN_ID>/REPORT.md`. This is the permanent record.
13. **Confidence score at delivery.** Score 1-10 based on TDD coverage, review
    results, and validator output. Below 7 → flag for human review.

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
