# EVALUATE ref — Subsystem Health, Cross-Boundary, Complexity, Drift, Anti-Repetition
Read when: the requirement touches an existing subsystem, you classify `cross_boundary`, a prior
completed run exists for the project (drift), the project has a `code_intel.db`, or
IMPROVEMENT.md has "What Failed" entries. The card (`stages/evaluate.md` steps 2-3) has the
core procedure.
### Subsystem Health Audit (P1) `[MUST]` <!-- R-evaluate-005 -->
**Before scoring, if the requirement touches an existing subsystem** (not a greenfield
feature), run a 5-minute E2E audit of that subsystem:
1. **Identify the subsystem**: what directory/module does this requirement live in?
2. **List all public operations**: every API endpoint, CLI command, or user action the
   subsystem supports (e.g. deploy, stop, start, update, delete, reset-password).
3. **For each operation, check the 8 operational invariants** (from `OPERATIONAL_PATTERNS.md`):
   OP1 concurrency guard? · OP2 rollback path? · OP3 data backup? · OP4 access control? ·
   OP5 health unauthenticated? · OP6 fail-loud placeholders? · OP7 single update path? ·
   OP8 config consistency?
4. **For each missing invariant**, add it to the acceptance criteria.
This turns a "fix X" requirement into "fix X + harden the neighborhood". The audit typically
finds 3-10× more gaps than the original requirement.
**When to skip:** greenfield features (no existing subsystem to audit), trivial one-line
fixes, or when the user explicitly says "just fix this one thing".
### Cross-Boundary Classification (P1) `[GATE·artifact_cli]` — produces the `cross_boundary` flag <!-- R-evaluate-006 -->
The flag is code-consumed downstream: the `artifact_cli.py` `cross_boundary_e2e` gate reads it
from the published EVALUATE artifact and `sys.exit(1)`s if a `cross_boundary=true` change
reaches DELIVER without a passing TEST-Layer-4 E2E record. REVIEW check 15 also rejects a
`false` with no `ruled_out`.
**One question, asked on every run:** does this change cross a CONTRACT BOUNDARY, a seam where
each side is a separate unit that a unit test can pass in isolation while the seam between
them silently breaks? The answer (a `cross_boundary` object: `.value` boolean + the `.kinds`
that fire when true) is consumed by TEST Layer 4 and the REVIEW exit checklist. It is the
sibling of the Subsystem Health Audit: that audit hardens a subsystem's *operations*; this
classifies the change's *seams*.
**`cross_boundary = true` if the change touches ANY of these boundary kinds:**
| Boundary kind | Concrete signal |
|---|---|
| **event-bus / window-event** | dispatch/listen a `CustomEvent`/`swarm:*` event; add/rename/retire an event name; move a surface off/onto an event mechanism |
| **IPC / SSE / wire** | a serialized payload crosses a process/network hop (SSE event shape, Tauri command, an `editor_context`/`ui_command` field) |
| **data / schema migration** | a column/field/dir/contract renames or moves and existing writers OR **readers** must follow (R27 "grep ALL consumers incl. reads") |
| **multi-subsystem shared path** | ≥2 coupled subsystems share one critical path (resume, spawn, streaming, auth): the R16 blast-radius case |
| **frontend↔backend contract** | a backend allowlist/enum and a frontend table must stay in lockstep (e.g. `ui_action` UI_COMMAND_ALLOWLIST ↔ `ALL_SHOW_EVENTS`) |
| **ACT / SENSE proprioception** | the agent's ability to ACT on a surface (a command that dispatches an event) OR SENSE its state (a payload field read from a store) rides the mechanism being changed |
**`cross_boundary = false` (EXEMPT, no E2E ceremony tax)** when the change is a pure-logic
function (inputs→outputs, no seam), a docs-only edit, a single-file config value, or a
cosmetic/style change. **Do NOT inflate the classification:** if you cannot name a SPECIFIC
boundary kind from the table with a file:line, it is `false`. Marking everything
"cross-boundary" re-creates the ceremony tax this gate exists to avoid.
**⚠️ The `false` branch requires a NEGATIVE ATTESTATION.** The self-exemption temptation fires
hardest on the run most likely to break a seam: a migration where "every unit passes". So
`false` is not "leave the field blank"; you MUST record `ruled_out`, a one-line statement that
you checked the 6 kinds and none fire, naming what the change touches. A `false` with no
`ruled_out` is an INVALID EVALUATE artifact (an unjustified skip), and REVIEW check 15 rejects
it. The classification must be a decision on record, not a silent default.
**Record it in the EVALUATE artifact:** `true` carries the firing `kinds` + the `seam`; `false`
carries `ruled_out`:
```json
"cross_boundary": { "value": true, "kinds": ["event-bus", "frontend-backend-contract", "act-sense"], "seam": "swarm:show-<id> dispatch ↔ OverlayContext listener ↔ backend allowlist" }
// or, for an exempt run:
"cross_boundary": { "value": false, "ruled_out": "pure-logic change to score_confidence() — no event/wire/migration/shared-path/fe-be/act-sense seam; single file, callers in same module" }
```
**Profile consistency (silent-escape guard):** `docs` and `research` have NO TEST/REVIEW stage,
so `cross_boundary.value == true` under either is a SCOPE CONTRADICTION: the flag would have no
stage enforcing Layer 4. The profile is wrong, not the classification: re-route to
`bugfix`/`full`/`goal`. Only exception: a docs-only edit *describing* a boundary
(`cross_boundary=false`, `ruled_out: "docs describe the seam, no code crosses it"`).
### Codebase Complexity Assessment (if `code_intel.db` exists)
After DDD doc scoring, if the project has a `code_intel.db`, read the codebase summary to
enrich the feasibility score:
```python
from core.code_intel import load_project_graph
g = load_project_graph("PROJECT_NAME")
if g:
    summary = g.get_codebase_summary()
    # Use: modules affected (keyword → symbol search), dead code in target
    # modules (cleanup overhead), most-connected nodes (fragility indicator)
```
Adjust **Feasibility**:
- Target module has >5 dead code symbols → -0.5 (cleanup overhead)
- Target module's top node has >50 callers → -0.5 (high fragility)
- Change crosses 3+ modules → -0.5 (coordination cost)
**Skip** when no `code_intel.db` exists or the requirement is research-only.
### Drift Detection (P2 — Warning, Non-Blocking) `[GUIDE]`
**Before scoring, check whether code has drifted from design docs since the last pipeline
delivery.** If code changed but design docs didn't, the pipeline may work from stale
assumptions.
1. Find the last completed pipeline run for this project:
   ```bash
   python backend/scripts/artifact_cli.py run-get --project <PROJECT> 2>/dev/null | python3 -c "
   import sys,json
   runs = json.load(sys.stdin).get('runs',[])
   completed = [r for r in runs if r.get('status')=='completed']
   if completed: print(completed[-1].get('completed_at',''))
   " 2>/dev/null
   ```
2. If a prior run exists, count changes since then (scope to the swarmai repo, not the
   workspace):
   ```bash
   # From the swarmai repo root: code commits touching backend/ or desktop/
   git log --oneline --after="<completed_at>" -- backend/ desktop/ | wc -l
   # Design doc commits for this project
   git -C <WORKSPACE> log --oneline --after="<completed_at>" -- Projects/<PROJECT>/TECH.md Projects/<PROJECT>/PRODUCT.md Projects/<PROJECT>/IMPROVEMENT.md | wc -l
   ```
3. **Drift signal:** code_commits > 0 AND design_commits == 0.
**Output (in the evaluation artifact, only if drift is detected):**
```json
{"drift_detection": {"last_pipeline": "<pipeline id>", "code_commits_since": 7,
  "design_doc_commits_since": 0, "verdict": "DRIFT_WARNING"}}
```
**Behavior:**
- Drift detected → warn in the stage landmark:
  `⚠️ Drift: {N} code commits since last pipeline, 0 design doc updates`.
- P2 signal: continue if the changes are mechanical (config, deps, formatting); pause and
  propose a design update if they are architectural (new module, changed API, new state).
- **Never auto-update design docs from code**: that inverts the direction of truth.
- No prior run → skip silently (first pipeline for this project, no baseline).
### Anti-Repetition Check `[MUST]` <!-- R-evaluate-007 -->
> ⚠️ **doc-code drift:** this section reads "(BLOCKING)" but NO code enforces it — <!-- R-evaluate-008 -->
> `grep anti_repetition backend/scripts/{pipeline_validator,artifact_cli}.py` = 0 hits.
> It is agent-discipline (`[MUST]`), NOT a code gate. Flagged in the drift table (AC4).
**Before producing the final GO/DEFER recommendation, cross-reference IMPROVEMENT.md "What
Failed" for structurally similar approaches.** Failed experiments actively prevent dead-end
exploration instead of only being archived.
1. Read the project's IMPROVEMENT.md "What Failed" section.
2. For each `[pitfall]` entry: does the current requirement's proposed approach structurally
   resemble it?
   - Same module/subsystem targeted
   - Same technique (e.g. "big-bang refactor", "shared mutex", "brute-force replay")
   - Same architectural pattern (e.g. "multi-writer", "polling loop", "silent fallback")
3. If a match is found:
   a. **Cite the specific failed entry** (date + first line).
   b. **Explain why this attempt is structurally different**: what changed since the failure?
      Different constraints, scope, new capabilities?
   c. If you CANNOT articulate a structural difference → **REJECT** with
      `"REJECT: structurally similar to failed approach [date]: [summary]"`.
**Output (in the evaluation artifact):**
```json
{
  "anti_repetition_check": {
    "entries_scanned": 12,
    "matches_found": 1,
    "matches": [
      {
        "entry": "2026-04-01: pytest-xdist — 12 commits, 8 days, 970 lines...",
        "similarity": "shared conftest approach for test isolation",
        "verdict": "PROCEED — different scope: this adds a hook, not conftest rewrite",
        "structural_difference": "Pure additive hook vs modifying shared infrastructure"
      }
    ]
  }
}
```
- **0 matches:** still output the result with the `entries_scanned` count; it proves the check
  ran, not that it was skipped.
- **IMPROVEMENT.md missing or no "What Failed" section:** output `entries_scanned: 0,
  matches_found: 0` and proceed. The check is satisfied (nothing to match). Do NOT skip or error.
