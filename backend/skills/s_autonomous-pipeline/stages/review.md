# REVIEW Stage

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

The REVIEW stage extends the base code review with 12 pipeline-specific checks:

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

**BLOCKING: Read `backend/skills/s_autonomous-pipeline/REVIEW_PATTERNS.md` and apply RP1-RP25.**

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

**Why this exists:** run_19129544 (unified release pipeline) passed 8 stages with 9/10 confidence. DevOps E2E audit found 2 HIGH + 3 MED in 5 minutes — all outside the diff, all inside the system lifecycle. Pipeline REVIEW reads the diff; it doesn't trace the system. For infra/release code, the system lifecycle IS the feature. (2026-04-29)

---

### 10. Anti-Rationalization Gate

Before concluding REVIEW, reject these shortcuts:

| Agent Shortcut | Required Response |
|---|---|
| "Changeset is small, skip integration trace" | Small changes with unwired symbols are the #1 silent failure. Trace every new symbol. |
| "Security scan isn't needed for internal code" | Internal code with injection paths gets exploited via MCP tools and API calls. Scan it. |
| "Runtime pattern checklist doesn't apply here" | Check every pattern. Write N/A explicitly. Silence = unchecked. |
| "Wire test is overkill -- the types match" | Types matching != serialization matching. Content-Type bugs are invisible to type checkers. |
| "UX review isn't needed -- the UI change is trivial" | Trivial UI changes cause scroll breaks and accessibility regressions. If UI files changed, check UX. |
| "Review is clean, marking confidence 10/10" | Confidence without evidence is fiction. Score against the checklist, not gut feel. |
| "Blast radius trace not needed -- I only changed scripts" | Infra/release bugs are invisible in the diff and break the system. If it touches build/deploy/CI, trace the lifecycle. |

---

### 11. Exit Evidence Checklist

Confirm each before publishing:
- [ ] Integration trace output present (`N symbols checked, M connected, K warnings`)
- [ ] Runtime pattern checklist complete (every applicable RP has pass or N/A)
- [ ] Security scan ran with confidence scores (or "no security-relevant changes" stated)
- [ ] Wire test results shown (or "single-layer change, N/A" stated)
- [ ] Depth & seam analysis completed for new files (or "no new files, N/A" stated)
- [ ] UX review completed (or "no frontend files, N/A" stated)
- [ ] Blast radius trace completed (or "no infra/release/deploy files, N/A" stated)

---

## Artifact Publish

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type review --producer s_autonomous-pipeline \
  --summary "Review: <N findings>, <M auto-fixed>, <K integration warnings>, <J ux findings>, <P runtime patterns>, <W wire tests>" \
  --data '{"findings":[...],"approved":true/false,"security_findings":[],"integration_trace":{"checked":N,"connected":M,"warnings":[...]},"depth_analysis":{"modules_checked":N,"deep":M,"shallow":K,"findings":[...]},"seam_audit":{"new_interfaces":N,"real_seams":M,"hypothetical_seams":K,"findings":[...]},"ux_review":{"triggered":true/false,"checks":5,"findings":[...]},"runtime_patterns":{"checked":N,"passed":M,"findings":[...]},"wire_test":{"boundaries":N,"verified":M,"findings":[...]}}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state test
```
