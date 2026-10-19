# DELIVER — delivery audits (P6, P6.5, Completion Audit)

Read when the card points here (step 1). These run BEFORE Gate-2: the push-ready gate
asks "did we pass every quality gate", the audits ask "did we build the right thing, and
can a real user use it".

## Fresh User Audit (P6) — only if user-facing infrastructure changed

Applies when the changeset touches deploy, onboarding, config, CLI, or API endpoints.
Answer: "Could a new user, starting from zero, successfully use this feature without
modifying source code?" Check:
1. **No stale values** — hardcoded IPs, usernames, paths, URLs that only work for the
   developer.
2. **No hidden prerequisites** — tools, keys, permissions, env vars that are not
   documented or auto-provisioned.
3. **No placeholder that passes silently** — config values that look valid but are not
   (e.g. a bcrypt-hash placeholder a proxy accepts but that always rejects auth).
4. **Clear error messages** — a missing prerequisite says what is wrong and how to fix
   it, not a raw stack trace.
5. **Single documented path** — when several ways exist, only one is documented; the
   others are deprecated or removed.

Output: one attention flag per friction point for a new user. Tests run in the
developer's environment, so these gaps are invisible to them.

## User Path Latency Trace (P6.5) — every profile, when a user-facing path changed

Pick 2-3 representative user scenarios. For each, walk the **exact runtime code path**
from user action to visible response, asking at every step:
1. What does the user SEE at this moment? (loading, nothing, stale content)
2. What does the user WAIT for? (network, sleep, lock, background task)
3. Is there unnecessary latency? (a sleep with no data dependency, sequential calls that
   could be parallel, a lock held across I/O)
4. Is there invisible silent failure? (return value ignored, a parameter the callee
   ignores, a fallback that looks like success)

Format each trace as a numbered path ending in the total user-visible latency, e.g.
`1. Slack event → adapter._handle_event() → gateway.handle_inbound()` …
`Total user-visible latency: ~4s ← ACCEPTABLE for "hi"`.

Red flags: `asyncio.sleep(N)` with N > 0 on a user-facing path without justification; a
parameter ignored by its callee (text passed, hardcoded string used); `try/except: pass`
on a path that decides what the user sees; redundant API calls (post → update → post
again = flicker). **Any red flag: fix it NOW in this iteration.** Do not log it as a
known limitation or future work if the fix takes < 30 minutes. Mock-based tests accept
any call, so these bugs pass TDD and review and only show up when the real path is walked.

## Completion Audit — verify before declaring done

1. **RESTATE** the deliverables: copy the original requirement verbatim and list every
   acceptance criterion from EVALUATE (`stage-start` returns them).
2. **CHECKLIST** per deliverable: `| # | Acceptance Criterion | Evidence | Status |`
   (evidence = test that passes, file created; `[no evidence found]` → ❌).
3. **INDEPENDENT AC VERIFICATION** (pre-flight for Gate-2). TEST confirmed the tests
   PASS; this step confirms they VERIFY THE RIGHT THING. A test can pass while testing
   the wrong behavior. For each AC → test mapping claimed in step 2:
   a. READ the test file and function body fresh (not from memory).
   b. VERIFY it exercises the AC: does the input match what the AC describes, does the
      assertion check the AC's expected output, could it pass while the AC is NOT
      satisfied? (yes → necessary but not sufficient → ⚠️)
   c. TRACE one level downstream: does the implementation connect to the tested
      interface, is there a gap between "unit passes" and "feature works end to end"?
   Output the matrix `| # | AC | Test | Verifies AC? | E2E Connected? |`. Any ⚠️ or ❌ →
   fix before proceeding (write the integration test, fix the assertion), then
   re-verify only the fixed rows.
4. **INSPECT** the evidence exists: cite the file, test name or output for each ✅;
   confirm each cited test really tests its criterion (not just a similar name).
5. **GAPS**: every ❌; anything the requirement implies but no AC captured; grep the
   changed files for `TODO`, `FIXME`, `placeholder`.
6. **VERDICT**: all green → `completion_audit.all_green = true`; any gap → fix (loop back
   to BUILD/TEST); an unfixable gap → attention flag with the explanation.

Record in the deliver artifact:
- `ac_verification`: `{"status": ..., "detail": "AC1 → test_x; ..."}` (a `matrix` list
  of `{ac, test, verifies_ac, e2e_connected}` is welcome). `verified` = every row
  ✅/✅ → gate passes; `partial` = tests exist but are not confirmed (step skipped) →
  warning; `unverified` = a fix was attempted and ❌ persists → NOT-PUSH-READY (known
  gap in spec satisfaction). The field is required for strict profiles at publish AND
  at completion.
- `completion_audit`: `{"all_green": true, "requirements_met": N, "requirements_total":
  N, "gaps": 0, "unfixable_gaps": 0}`. `gaps > 0` (unfixed) → NOT-PUSH-READY;
  `unfixable_gaps > 0` → WARNING (note in the report, the user decides).

<!-- R-deliver-010 -->
## User-Value Probe (BLOCKING) — [MUST] (⚠️ prose-only, see drift note)

<!-- R-deliver-011 -->
> ⚠️ **Doc-code drift (surfaced by the standalone annotation audit — flagged, NOT fixed
> here):** this section is labeled "(BLOCKING)" and its verdict rules say "0 value-add
> claims → NOT-PUSH-READY", but NO code enforces it — `user_value_probe` appears in
> neither `pipeline_validator.py` nor `artifact_cli.py`'s completion gate. So its true
> level is [MUST] (agent discipline), not a [GATE]. Handed to the user to decide: add a
> validator check for `user_value_probe.verdict != "FAIL"`, or downgrade the "BLOCKING"
> wording to [MUST].

After the meta-review, before the Doc Sync Check. Applies to every profile that produces
user-facing output (full, bugfix); skip for research. It catches "the code is
correct but the output is useless": adversarial review validates code quality,
meta-review validates operational correctness, neither asks whether a real user benefits.

**The ONE question:** "If I'm the user who requested this, and I receive this output —
can I point to at least 3 things in it that are ONLY knowable by the work this pipeline
did, things I could not have gotten in 2 minutes myself?"

**How to verify (mechanical, not vibes):**
1. Pick 3 claims from the output (a convention in TECH.md, an edge in code-intel.json, a
   gotcha in IMPROVEMENT.md, a test result).
2. For each: "Could this be produced WITHOUT this pipeline's work?" — without reading
   source, running tests, analyzing git history, or asking the user.
3. YES for all 3 → the output is derivable from public surface info →
   **NOT-PUSH-READY: output adds no value over what the user could do in 2 minutes.**

| Claim in output | Derivable without the work? | Verdict |
|---|---|---|
| "Uses Python 3.9+" | YES — visible in pyproject.toml | ❌ not value-add |
| "`mine_lock` is a threading.Lock, not async-safe — concurrent mine from MCP + CLI deadlocks" | NO — needs the source line plus the server's concurrency model | ✅ value-add |
| "Always go through `get_collection()`, never instantiate the client directly (seen in 3 files)" | NO — needs reading several files and spotting the pattern | ✅ value-add |
| "Architecture: 12 modules" | YES — `ls` gives this | ❌ not value-add |

**Verdict rules:** 3+ value-add claims → PASS; 1-2 → WARNING (thin, suggest
enrichment); 0 → **NOT-PUSH-READY** (a paraphrase, not analysis).

Record `user_value_probe`: `{"verdict": "PASS|WARNING|FAIL", "claims_checked": 3,
"value_add_count": N, "evidence": [{"claim": "...", "derivable_without_work": false,
"source": "file:line"}]}`.

<!-- R-deliver-012 -->
**Why this is BLOCKING (not advisory):** the pipeline's entire value proposition is "I
did the work so you don't have to." If the output contains nothing that required the
work, the pipeline consumed tokens and produced noise. A user who receives a
"score 7.8/10" report full of README-derivable facts will never trust the tool again.
First impressions are permanent. One garbage output destroys credibility for all future
outputs.
