# REVIEW Stage

## Base Methodology

> **Reference:** `backend/skills/s_code-review/INSTRUCTIONS.md`
>
> Follow the base code review methodology defined there for structured review findings.

## Pipeline-Specific Checks

The REVIEW stage extends the base code review with 9 pipeline-specific checks:

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

---

### 4. Replace/Move Parity Check

When code is **moved or replaced** (not just added):

| Check | What to verify | Example |
|-------|---------------|---------|
| Feature parity | Every capability of old code exists in new code | Old `_recall_knowledge` had TranscriptStore; new `_recall_for_query` must too |
| Dead orphan detection | After removing a call site, grep old function -- if 0 callers remain, flag as dead code | `_recall_knowledge` still defined after its only caller was removed |
| Argument validity | Mock attributes must exist on the real class | `unit.working_directory` doesn't exist on SessionUnit |

This check exists because PE review of the RecallEngine activation found 2 HIGH bugs: (1) replaced function dropped a capability (TranscriptStore), (2) test mock hid a missing attribute. Both would have been caught by feature parity diff.

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

**BLOCKING: Read `backend/skills/s_autonomous-pipeline/REVIEW_PATTERNS.md` and apply RP1-RP19.**

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

### 8. Anti-Rationalization Gate

Before concluding REVIEW, reject these shortcuts:

| Agent Shortcut | Required Response |
|---|---|
| "Changeset is small, skip integration trace" | Small changes with unwired symbols are the #1 silent failure. Trace every new symbol. |
| "Security scan isn't needed for internal code" | Internal code with injection paths gets exploited via MCP tools and API calls. Scan it. |
| "Runtime pattern checklist doesn't apply here" | Check every pattern. Write N/A explicitly. Silence = unchecked. |
| "Wire test is overkill -- the types match" | Types matching != serialization matching. Content-Type bugs are invisible to type checkers. |
| "UX review isn't needed -- the UI change is trivial" | Trivial UI changes cause scroll breaks and accessibility regressions. If UI files changed, check UX. |
| "Review is clean, marking confidence 10/10" | Confidence without evidence is fiction. Score against the checklist, not gut feel. |

---

### 9. Exit Evidence Checklist

Confirm each before publishing:
- [ ] Integration trace output present (`N symbols checked, M connected, K warnings`)
- [ ] Runtime pattern checklist complete (every applicable RP has pass or N/A)
- [ ] Security scan ran with confidence scores (or "no security-relevant changes" stated)
- [ ] Wire test results shown (or "single-layer change, N/A" stated)
- [ ] UX review completed (or "no frontend files, N/A" stated)

---

## Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type review --producer s_autonomous-pipeline \
  --summary "Review: <N findings>, <M auto-fixed>, <K integration warnings>, <J ux findings>, <P runtime patterns>, <W wire tests>" \
  --data '{"findings":[...],"approved":true/false,"security_findings":[],"integration_trace":{"checked":N,"connected":M,"warnings":[...]},"ux_review":{"triggered":true/false,"checks":5,"findings":[...]},"runtime_patterns":{"checked":N,"passed":M,"findings":[...]},"wire_test":{"boundaries":N,"verified":M,"findings":[...]}}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state test
```
