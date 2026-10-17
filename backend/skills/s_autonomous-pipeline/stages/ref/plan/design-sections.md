# PLAN ref — design-doc sections

Read when: you need the template or rules for card steps 2–6 (file discovery, change spec,
test strategy, boundaries, success criteria), or the project has a `code_intel.db` (impact
projection). The `[MUST]` lines and their enforcement level are on the card.

The design doc covers ALL of: architecture/approach, data model or API contract (if
applicable), acceptance criteria (carried from EVALUATE and refined), edge cases and error
handling, estimated files to change, boundaries, success criteria, test strategy, required
experts.

## File Discovery (before writing the design)

Find ALL affected files, not just the ones you plan to change: callers, consumers, importers,
adjacent code. Missing one caller = a surprise regression in BUILD.

1. **Seed**: list the files the chosen approach obviously touches.
2. **Expand**: for EACH seed, search for everything that references it:
   ```bash
   grep -rn "from <module>\|import <module>\|<ClassName>\|<function_name>" \
     --include="*.py" --include="*.ts" --include="*.rs" .
   ```
3. **Categorize** every hit:
   - **MODIFY**: needs code changes to implement the feature
   - **TEST**: needs new/updated tests (test files that import MODIFY files)
   - **VERIFY**: won't change but must be checked for compatibility
   - **IRRELEVANT**: references exist but are unaffected
4. **Read** all MODIFY + VERIFY files. For each: does the current interface support the
   change? What patterns must new code match (error style, async/sync, naming)? Is there an
   existing abstraction to reuse instead of a new one?
5. **Update the approach** when discovery shows an existing utility already does 80% (extend,
   don't duplicate), the interface doesn't support the plan (revise), or there are more
   callers than expected (scope is larger, adjust the effort estimate).

If discovery shows the approach won't work, go back to THINK and choose or modify an
alternative. Never proceed with a plan that contradicts what the code looks like.

Output in the design doc:

```markdown
## File Discovery
| File | Category | Key Finding |
|------|----------|-------------|
| `session_router.py` | MODIFY | Has `compute_max_tabs()` — extend with new param |
| `lifecycle_manager.py` | VERIFY | Calls `compute_max_tabs()` — must stay compatible |
| `test_session.py` | TEST | 12 tests import session_router — run after changes |
| `frontend/services/chat.ts` | MODIFY | Needs new field in response type |
```

## Change Spec (ordered atomic sub-changes)

ACs say "what done looks like" (outcome); the change spec tells BUILD "what to do, in what
order" (action), topologically sorted by dependency.

**Write for an implementer with ZERO project context.** BUILD cannot infer file locations,
naming conventions or architectural patterns. Every sub-change must be executable from this
spec alone; if BUILD has to grep to find a file you referenced, the spec is incomplete.

Format (paths/commands are illustrative, from SwarmAI's Python/FastAPI/Tauri stack; for
another project use its real paths and the test/build commands from TECH.md
`## Dev Commands`/`## Stack`):

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
   - Verify: the sub-change's test passes (run via pipeline_test.py)

3. `desktop/src/services/chat.ts` → Add `transcribeAudio()` client method
   - Depends on: #1 (needs endpoint contract)
   - AC: AC3 (frontend integration)
   - Current: `chat.ts` has `sendMessage()`, `getMessages()` etc.
   - Target: new export, matches existing `post()` pattern
   - Verify: TypeScript compiles without error
```

Required fields per sub-change:
- `Depends on:` explicit dependency (BUILD processes in order)
- `AC:` which acceptance criteria it satisfies (traceability)
- `Current:` what the file/function looks like NOW (1–2 lines)
- `Target:` what it looks like AFTER (shape, not full code)
- `Verify:` command or check that proves the sub-change worked

**Trivial profile only:** Current/Target/Verify are optional when the sub-change is
self-explanatory (rename a constant, fix a typo).

Rules:
- Each sub-change maps to ONE file + ONE function/class/endpoint.
- Dependencies are explicit; BUILD processes them in order (provider before consumer).
- Each sub-change links to the AC it satisfies.
- No vague items ("improve error handling"); say exactly WHAT changes.
- A sub-change that can't be expressed concretely means the AC is too vague: rewrite the AC.

## Test Strategy (bridge AC → TDD)

Each AC gets a concrete test approach BEFORE BUILD starts, so BUILD's RED phase can start
writing tests immediately. The harness/mock cells below use SwarmAI's Python stack
(httpx/pytest/ASGI) as illustration; use the project's own harness from TECH.md `## Stack`
(e.g. Go `httptest`, JUnit `MockMvc`, vitest).

```markdown
## Test Strategy
| # | AC | How to Test | Mock Boundary | Input Construction |
|---|----|-----------|--------------|--------------------|
| 1 | "Returns 200 with transcript" | httpx AsyncClient through ASGI | Mock: Transcribe API response | WAV fixture bytes from test_assets/ |
| 2 | "Handles timeout gracefully" | pytest.raises + mock slow response | Mock: asyncio.sleep in API call | Same fixture, timeout=0.1 |
| 3 | "≥90% accounts have owner" | Query test DB, assert ratio | Stand-in: in-memory SQLite with 20 rows | Factory fixtures with/without owner |
```

Columns:
- **How to Test**: harness approach (unit, integration, ASGI client, CLI subprocess)
- **Mock Boundary**: which external dependency is mocked and at what level (leaf SDK call,
  adapter interface, or nothing)
- **Input Construction**: where inputs come from (factory, fixture file, inline, production
  sample)

Rules:
- Every AC MUST have a row. No AC without a test approach.
- Can't fill "How to Test" for an AC → the AC is too vague; rewrite it.
- Prefer integration tests (real ASGI, real DB) over unit tests with mocks.
- If the spike read found existing test utilities (factories, fixtures, helpers), name them
  here so BUILD doesn't reinvent them.

## Boundaries (three tiers)

```markdown
## Boundaries

### Always (non-negotiable — agent auto-enforces)
- [e.g. "every declared state must have a code path"]
- [quality gates never optional, e.g. "run tests before committing"]

### Ask First (agent pauses and confirms with user)
- [needs human judgment, e.g. "adding new dependencies"]
- [scope-expanding, e.g. "changing the public API"]

### Never (hard constraints — agent refuses)
- [e.g. "don't mock resource management code"]
- [feature-specific anti-patterns, e.g. "don't use setTimeout for state"]
```

Populate from:
- IMPROVEMENT.md "What Failed" → past failures become **Never** items
- TECH.md conventions → existing patterns become **Always** items
- PRODUCT.md non-goals → off-scope becomes **Never** items
- EVALUATE pre-mortem risks → mitigations become **Always** items

## Success Criteria

Reframe vague requirements into specific, testable conditions; they become DELIVER's exit
conditions.

```markdown
## Success Criteria
- [Criterion 1 — specific, measurable, testable]
- [Criterion 2]
```

Each criterion must be verifiable by a test, a command output, or a visual check. "Works
correctly" is not a success criterion; "Returns 200 with valid JSON body containing a
`transcript` field" is.

## Impact Projection (only if `code_intel.db` exists)

After the design decisions, project the blast radius of the planned changeset:

```python
from core.code_intel import load_project_graph
g = load_project_graph("PROJECT_NAME")
if g:
    for file_path in planned_files:
        callers = g.find_dependents(file_path, max_hops=2)
        # List ALL files that will need testing even if not directly modified
```

Output: `"Impact projection: 5 files to change, 12 files to test, crosses core→hooks→channels"`.
Add it to the design_doc artifact under `"impact_projection"`; it gives BUILD a testing
roadmap beyond the changed files. Skip when the project has no `code_intel.db`.
