# PLAN Stage

Pipeline-owned stage (no sibling skill).

## Methodology

1. Take the recommended (or user-chosen) alternative
2. **Spike Read** — verify feasibility against actual code (see below)
3. Produce a design document covering ALL of these sections:
   - Architecture/approach description
   - Data model or API contract (if applicable)
   - Acceptance criteria (carry forward from evaluate + refine)
   - Edge cases and error handling
   - Estimated files to change
   - **Boundaries** (required — see below)
   - **Success criteria** (required — see below)
   - **Test strategy** (required — see below)
4. If design requires uncommitted dependencies or API changes -- taste/judgment decision

### Exhaustive File Discovery (Required — Before Writing Design Doc)

**Find ALL affected files BEFORE writing the design — not just the ones you
plan to change.** The design must account for callers, consumers, importers,
and adjacent code. Missing one caller = surprise regression in BUILD.

Inspired by Sweep's scored file localization loop: "search exhaustively,
then plan" — not "plan, then hope you didn't miss a file."

**Process (search → expand → verify):**

1. **Seed**: List the files the chosen approach will obviously touch
2. **Expand**: For EACH seed file, search for ALL code that references it:
   ```bash
   grep -rn "from <module>\|import <module>\|<ClassName>\|<function_name>" \
     --include="*.py" --include="*.ts" --include="*.rs" .
   ```
3. **Categorize** every discovered file:
   - **MODIFY** — needs code changes to implement the feature
   - **TEST** — needs new/updated tests (test files that import MODIFY files)
   - **VERIFY** — won't change but must be checked for compatibility
   - **IRRELEVANT** — references exist but unaffected by this change
4. **Read** all MODIFY + VERIFY files. For each, answer:
   - Does the current interface support the planned change?
   - What patterns must the new code match? (error style, async/sync, naming)
   - Are there existing abstractions to reuse instead of creating new ones?
5. **Update approach** if discovery reveals:
   - An existing utility already does 80% of what's needed → extend, don't duplicate
   - Interface doesn't support the plan → revise approach
   - More callers than expected → scope is larger, adjust effort estimate

**Output (in design doc):**
```markdown
## File Discovery
| File | Category | Key Finding |
|------|----------|-------------|
| `session_router.py` | MODIFY | Has `compute_max_tabs()` — extend with new param |
| `lifecycle_manager.py` | VERIFY | Calls `compute_max_tabs()` — must stay compatible |
| `test_session.py` | TEST | 12 tests import session_router — run after changes |
| `frontend/services/chat.ts` | MODIFY | Needs new field in response type |
```

**If discovery reveals approach won't work:** Go back to THINK and either
choose a different alternative or modify the approach. Do NOT proceed with
a plan that contradicts what the code actually looks like.

**Why this exists:** Multiple pipeline runs hit BUILD only to discover the
planned interface doesn't exist, there are 5 more callers than expected, or
an existing utility already handles the need. Exhaustive discovery in PLAN
costs 3 minutes; discovering gaps in BUILD costs 20 minutes of backtracking.

### Change Spec (Required — Ordered Atomic Sub-Changes)

**Decompose the requirement into topologically-sorted atomic code changes.**
Each AC tells the user "what done looks like" (outcome). The Change Spec tells
BUILD "what to do, in what order" (action). Without this, BUILD has to figure
out the sequencing itself — burning TDD time on logistics instead of code.

Inspired by Sweep's "Issue Sub-Request Decomposition" — each sub-change maps
to a specific file and function, and they're ordered by dependency.

**Framing Principle: Write for an implementer with ZERO project context.**
Assume the BUILD agent has never seen this codebase. It cannot infer file
locations, naming conventions, or architectural patterns. Every sub-change
must be executable by reading ONLY this spec — no ambient knowledge assumed.
If BUILD has to grep to find a file you referenced, the spec is incomplete.

**Format** *(the paths/commands below are illustrative — they use SwarmAI's own
Python/FastAPI/Tauri stack; for another project substitute its real paths and its
test/build commands from TECH.md `## Dev Commands`/`## Stack`)*:
```markdown
## Change Spec (ordered)
1. `backend/routers/chat.py` → Add `POST /api/chat/transcribe` endpoint
   - Depends on: nothing (new endpoint)
   - AC: AC1 (returns 200 with transcript)
   - Current: file has 12 routes, `transcribe` does not exist
   - Target: new route handler, ~20 lines, returns `{"text": str}`
   - Verify: `curl -X POST localhost:18321/api/chat/transcribe` returns 200

2. `backend/core/transcribe.py` → Create `transcribe_audio()` function
   - Depends on: #1 (endpoint calls this)
   - AC: AC1, AC2 (handles timeout)
   - Current: file does not exist
   - Target: new module, single public function, timeout param
   - Verify: `pytest tests/test_transcribe.py -x` passes

3. `desktop/src/services/chat.ts` → Add `transcribeAudio()` client method
   - Depends on: #1 (needs endpoint contract)
   - AC: AC3 (frontend integration)
   - Current: `chat.ts` has `sendMessage()`, `getMessages()` etc.
   - Target: new export, matches existing `post()` pattern
   - Verify: TypeScript compiles without error
```

**Required fields per sub-change:**
- `Depends on:` — explicit dependency (BUILD processes in order)
- `AC:` — which acceptance criteria this satisfies (traceability)
- `Current:` — what the file/function looks like NOW (1-2 lines)
- `Target:` — what it should look like AFTER (shape, not full code)
- `Verify:` — command or check that proves this sub-change worked

For **trivial profile** only: Current/Target/Verify fields optional when the
sub-change is self-explanatory (rename a constant, fix a typo).

**Rules:**
- Each sub-change maps to ONE file + ONE function/class/endpoint
- Dependencies are explicit — BUILD processes them in order
- Each sub-change links to the AC it satisfies (traceability)
- No vague items ("improve error handling") — be specific about WHAT changes
- If a sub-change can't be expressed concretely → the AC is too vague, rewrite it

**Why this exists:** BUILD's TDD loop works best with clear "what to do next."
Without a change spec, the agent has to re-derive the sequencing every time,
often picking the wrong order (implementing a consumer before the provider).

### Boundaries (Required)

Every design document MUST include a three-tier boundary system. This prevents
the most expensive class of bugs: building the wrong thing because the agent
made an assumption the user didn't intend.

```markdown
## Boundaries

### Always (non-negotiable — agent auto-enforces)
- [Things that must happen regardless, e.g., "every declared state must have a code path"]
- [Quality gates that are never optional, e.g., "run tests before committing"]

### Ask First (agent pauses and confirms with user)
- [Things that need human judgment, e.g., "adding new dependencies"]
- [Scope-expanding decisions, e.g., "changing the public API"]

### Never (hard constraints — agent refuses)
- [Things that must not happen, e.g., "don't mock resource management code"]
- [Anti-patterns for this specific feature, e.g., "don't use setTimeout for state"]
```

**Populate from:**
- IMPROVEMENT.md "What Failed" section → past failures become "Never" items
- TECH.md conventions → existing patterns become "Always" items
- PRODUCT.md non-goals → off-scope becomes "Never" items
- Pre-mortem risks from EVALUATE → risk mitigations become "Always" items

### Security Boundaries (Required WHEN `cross_boundary.value == true`)

This is the pipeline's ONLY design-level security checkpoint. Every other security
review is code-level (REVIEW security specialist / Gate-2 adversarial) — i.e. AFTER
code is written. A design-level miss (e.g. an endpoint whose auth is *assumed* but
never enforced) sails through those and surfaces only at runtime. This block catches
it at PLAN.

**When to write it:** ONLY when EVALUATE set `cross_boundary.value == true` (a new
external input surface / endpoint / auth / credential / tool·delegation / agent
capability — the same boundary kinds EVALUATE already classifies). When
`cross_boundary.value == false`, OMIT this section entirely — zero ceremony on
non-security changes. **The completion-time validator enforces this conditionally**
(it reads the evaluation artifact's `cross_boundary` and BLOCKS a `true` plan that
lacks the block; it no-ops on `false`).

**It is assumption-VERIFICATION, NOT threat-ENUMERATION.** Do not brainstorm every
possible attack against an unwritten design (that hallucinates). Instead, for each
trust boundary the design introduces: name it → state the trust ASSUMPTION → **cite
the code locus (`path:line`) that ENFORCES it** — after actually reading/grepping that
locus to confirm it enforces what you claim. If you cannot verify an assumption is
code-enforced, set `escalate: true` and hand it to the reviewer. The defense must be a
DETERMINISTIC boundary in code (allowlist, scoped credential, verified principal,
egress jail) — never a prompt instruction or a client-side toggle.

```markdown
## Security Boundaries
- **Boundary:** frontend↔backend API `POST /api/workspace/write`
  - **Trust assumption:** caller is authenticated before reaching the handler
  - **Enforcement:** `backend/middleware/hive_auth.py:62`  ← grep it; if it only fires
    in one mode/config, the assumption is FALSE and this is the design-level catch
- **Boundary:** a newly-registered agent tool that writes files
  - **Trust assumption:** the write path is confined to the workspace tree
  - **Escalate:** true — reason: no code-enforced confinement exists yet, needs review
```

**Validator contract (`_check_security_boundaries`, completion-time):** each entry is
VALID iff it has a real enforcement locus (`path:line` whose file EXISTS on disk) OR
`escalate: true`. A cited locus whose file does not exist → BLOCK (fabricated/stale
locus). The validator verifies the locus is REAL; you + the DELIVER security specialist
verify it is CORRECT (semantics are beyond a validator).

### Success Criteria (Required)

Reframe vague requirements into specific, testable conditions. These become
the exit conditions for the DELIVER stage.

```markdown
## Success Criteria
- [Criterion 1 — specific, measurable, testable]
- [Criterion 2]
- [Criterion 3]
```

**Format rule:** Each criterion must be verifiable by a test, a command output,
or a visual check. "Works correctly" is not a success criterion. "Returns 200
with valid JSON body containing `transcript` field" is.

### Test Strategy (Required)

**Bridge AC → TDD.** Each acceptance criterion needs a concrete test approach
BEFORE BUILD starts. Without this, BUILD spends half its time figuring out
test setup instead of writing implementation.

For each acceptance criterion, specify *(the harness/mock cells below use SwarmAI's
Python stack — httpx/pytest/ASGI — as illustration; use the project's own test
harness from TECH.md `## Stack`, e.g. Go `httptest`, JUnit `MockMvc`, vitest)*:

```markdown
## Test Strategy
| # | AC | How to Test | Mock Boundary | Input Construction |
|---|----|-----------|--------------|--------------------|
| 1 | "Returns 200 with transcript" | httpx AsyncClient through ASGI | Mock: Transcribe API response | WAV fixture bytes from test_assets/ |
| 2 | "Handles timeout gracefully" | pytest.raises + mock slow response | Mock: asyncio.sleep in API call | Same fixture, timeout=0.1 |
| 3 | "≥90% accounts have owner" | Query test DB, assert ratio | Stand-in: in-memory SQLite with 20 rows | Factory fixtures with/without owner |
```

**Column definitions:**
- **How to Test:** Test harness approach (unit, integration, ASGI client, CLI subprocess)
- **Mock Boundary:** What external dependency gets mocked, and at what level (leaf SDK call, adapter interface, or nothing)
- **Input Construction:** Where test inputs come from (factory, fixture file, inline, production sample)

**Rules:**
- Every AC MUST have a row. No AC without a test approach.
- If you can't specify "How to Test" for an AC, the AC is too vague — rewrite it.
- Prefer integration tests (real ASGI, real DB) over unit tests with mocks.
- If spike read revealed existing test utilities (factories, fixtures, helpers),
  reference them here — don't reinvent in BUILD.

**Why this exists:** BUILD's TDD RED phase is supposed to be "write test, watch fail."
In practice it's often "spend 15min figuring out fixture setup, then write test."
Moving the HOW to PLAN means BUILD can start writing tests immediately.

### Impact Projection (if code_intel.db exists)

After design decisions are made, use code_intel to project the blast radius
of the planned changeset:

```python
from core.code_intel import load_project_graph
g = load_project_graph("PROJECT_NAME")
if g:
    for file_path in planned_files:
        callers = g.find_dependents(file_path, max_hops=2)
        # List ALL files that will need testing even if not directly modified
```

Output: `"Impact projection: 5 files to change, 12 files to test, crosses core→hooks→channels"`

Add the impact projection to the design_doc artifact under `"impact_projection"`.
This gives BUILD a testing roadmap beyond just the changed files.

**Skip** when no `code_intel.db` exists for the project.

## Artifact Publish

The design_doc artifact MUST include `boundaries`, `success_criteria`, `file_discovery`,
`change_spec`, and `test_strategy` fields. Pipeline validator will check for their presence.
**Additionally, when the run's `cross_boundary.value == true`, the artifact MUST include a
non-empty `security_boundaries` list** (each entry: `boundary`, `trust_assumption`, and
either `enforcement` = `"path:line"` OR `escalate: true` + `reason`). The completion-time
validator BLOCKS a cross_boundary=true plan that lacks it or cites a non-existent locus;
OMIT the field when cross_boundary=false (no-op).

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> --run-id <RUN_ID> \
  --type design_doc --producer s_autonomous-pipeline \
  --summary "Design: <approach> for <requirement>" --stage plan \
  --data '{"approach":"...","acceptance_criteria":[...],"boundaries":{"always":[...],"ask_first":[...],"never":[...]},"success_criteria":[...],"file_discovery":[{"file":"...","category":"MODIFY|TEST|VERIFY","finding":"..."}],"change_spec":[{"order":1,"file":"...","change":"...","depends_on":[],"ac":"AC1"}],"test_strategy":[{"ac":"...","how":"...","mock_boundary":"...","input":"..."}],"data_model":"...","api_contract":"...","files_to_change":[...],"security_boundaries":[{"boundary":"...","trust_assumption":"...","enforcement":"path:line"}]}'
# NOTE: security_boundaries is REQUIRED only when the run's cross_boundary.value==true; OMIT it otherwise.
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state build --run-id <RUN_ID>
```

---

## Common Rationalizations

| Rationalization | Reality | Source |
|---|---|---|
| "I can figure out the files as I build" | Missing one caller = surprise regression in BUILD. C020: extracted function + added new caller in one commit; missed 2 bugs only visible in the new calling context. File discovery BEFORE planning forces you to see ALL affected surfaces. | C020 |
| "Boundaries are implicit — everyone knows not to touch X" | Implicit boundaries = silent drift. "Never" boundaries caught 3 unauthorized scope expansions in prior runs. If it's not written, it's not enforced. | Pipeline history |
| "Acceptance criteria are the same as the requirement" | Requirements describe WHAT. ACs describe WHEN DONE (testable, binary, per-scenario). A requirement "add validation" has 5+ ACs (each invalid input × expected response). Without ACs, you build until you "feel done." | C009 |
| "Test strategy is obvious — just test the happy path" | LL15: "Design doc 里每个 change X 必须有对应的 test." No test strategy in plan = untested edge cases in BUILD = bugs found in REVIEW = rework. Strategy upfront costs 2 minutes; rework costs 30. | LL15 |
