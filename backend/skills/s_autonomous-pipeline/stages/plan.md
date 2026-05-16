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

### Spike Read (Required — Before Writing Design Doc)

**Read every file you plan to change BEFORE writing the design.** Design
decisions made without reading the target code produce plans that don't fit
reality — BUILD then either hacks around the mismatch or backtracks to PLAN.

**Process:**
1. List the files the chosen approach will touch (from THINK output or inference)
2. `Read` each file — focus on: interfaces, existing patterns, constraints
3. For each planned change, answer:
   - Does the current interface support this? (method signatures, types, exports)
   - What existing patterns must I match? (error handling style, naming, async/sync)
   - What adjacent code might break? (callers, imports, shared state)
   - Are there existing abstractions I should use instead of building new ones?
4. Update the approach if code reality differs from design assumption

**Output (in design doc):**
```markdown
## Spike Read Findings
- `file_a.py`: Interface supports planned change. Uses async pattern X.
- `file_b.py`: Existing `process()` already does 80% of what we need — extend, don't duplicate.
- `file_c.ts`: Frontend expects camelCase — need toCamelCase() update for new fields.
```

**If spike reveals the approach won't work:** Go back to THINK recommendation
and either choose a different alternative or modify the approach. Do NOT
proceed with a plan that contradicts what the code actually looks like.

**Why this exists:** Multiple pipeline runs hit BUILD only to discover the
planned interface doesn't exist, the function signature is different, or an
existing utility already handles the need. Reading code in PLAN costs 2 minutes;
discovering the mismatch in BUILD costs 20 minutes of backtracking.

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

For each acceptance criterion, specify:

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

The design_doc artifact MUST include `boundaries`, `success_criteria`, `spike_read`,
and `test_strategy` fields. Pipeline validator will check for their presence.

```bash
python backend/scripts/artifact_cli.py publish --project <PROJECT> \
  --type design_doc --producer s_autonomous-pipeline \
  --summary "Design: <approach> for <requirement>" --stage plan \
  --data '{"approach":"...","acceptance_criteria":[...],"boundaries":{"always":[...],"ask_first":[...],"never":[...]},"success_criteria":[...],"spike_read":[{"file":"...","finding":"..."}],"test_strategy":[{"ac":"...","how":"...","mock_boundary":"...","input":"..."}],"data_model":"...","api_contract":"...","files_to_change":[...]}'
python backend/scripts/artifact_cli.py advance --project <PROJECT> --state build
```
