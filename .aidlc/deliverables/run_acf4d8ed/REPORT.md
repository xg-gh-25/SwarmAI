# Two corrections before this can land: · Audit through DELIVER

Delivered: Two corrections before this can land: 1. Scope. Drift detection must cover only seeded files whose content the product owns, where the template IS the content: today that is SELF.md (runtime-owned, se. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 15m 2s wall · 15m 2s active · no baseline yet (2/5 runs) |
| Quality | WARN | 1/1 verification · 2 not run · 1 red at base · 5/5 findings closed (1 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 231k comparable · 182k input · 49k output · 12.8M cache reads, not counted · 13.0M billed · host-session responses in the run window · no baseline yet (2/5 runs) |

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 0 · searched the DDD 0×

## Decisions

- D1 · Record seeds for files outside the covered set → no. Why: the state then holds only what the check reads; a file flagged later starts as unrecorded, which is the designed path (DESIGN\_SPEC, agent)
- D2 · Where the mechanism is described → the module docstring of core/context\_template\_drift.py; AGENTS.md reverted to its base text. Why: AGENTS.md changes are the person's to land; the repo has no TECH doc under docs/ with a context-files section (DESIGN\_SPEC, agent)
- D3 · Existing state entries for files no longer covered → ignored (never read for items); not deleted. Why: pending\_drifts iterates the covered set only; deleting is a write with no reader benefit (DESIGN\_SPEC, agent)

## Findings

Considered and not taken:

- A2 · A module constant TRACKED = ('SELF.md',) in context\_template\_drift. Not taken: ownership of each context file is already declared on its spec in CONTEXT\_FILES; a second list in another module can drift from it when a file is added or renamed
- A3 · Keep all seeded files but compare only a header region for MEMORY/EVOLUTION/. Not taken: those templates are skeletons the content replaces by design; a header item still asks the person to merge for nothing

Requirements analysis:

- missing\_edge (R1.2): should seeding still record hashes for non-covered files; handled: no, record\_seed runs only for covered files, so the state holds only what the check reads → confirmed
- missing\_edge (R2.1): the repository has no TECH doc under docs/ with a context-files section; handled: the docstring is the only description → confirmed

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 5/25 tool calls
- correctness · review r1: read 100% of its prompt · 8/25 tool calls
- security · review r2: read 100% of its prompt · 7/25 tool calls
- correctness · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 4cec378ee061: 3 pass, 0 fail, 0 not run
- test · swarmai @ 89491f95dfc7: 4 pass, 0 fail, 2 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: not run
  - experts-lint: not run
  - desktop-typecheck: not run
  - desktop-contract-lint: not run
  - desktop-build: not run
  - version-check: not run
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: not run

TEST · swarmai:
- PYTHONPATH=. python -m pytest tests/test\_context\_template\_drift.py -q --timeout=60 → pass (25 tests)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- bash scripts/run\_bvt.sh → pre\_existing (857 of 860 pass with maxfail lifted; the 3 failures are tests/test\_precommit\_uv\_lock.py, which needs a uv binary not on this shell's PATH and fails the same way on the base tree (the recorded task che)
- python scripts/lint\_skills.py; python scripts/lint\_experts.py; bash scripts/sync-version.sh check → not\_run (this correction touches no skill, expert or version file; all three passed on the previous run's tree and run again as recorded task checks after landing)
- npx tsc --noEmit; npm run lint:contract; npm run build; npx vitest run → not\_run (this correction changes no desktop file (backend and AGENTS.md only); they run again as recorded task checks after landing)

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 857 of 860 pass with maxfail lifted; the 3 failures are tests/test\_precommit\_uv\_lock.py, which needs a uv binary not on this shell's PATH and fails the same way on the base tree (the recorded task check, whose environment has uv, passed on the previous landing)

## Not run (the agent's reason, not checked)

- swarmai · python scripts/lint\_skills.py; python scripts/lint\_experts.py; bash scripts/sync-version.sh check: this correction touches no skill, expert or version file; all three passed on the previous run's tree and run again as recorded task checks after landing
- swarmai · npx tsc --noEmit; npm run lint:contract; npm run build; npx vitest run: this correction changes no desktop file (backend and AGENTS.md only); they run again as recorded task checks after landing
