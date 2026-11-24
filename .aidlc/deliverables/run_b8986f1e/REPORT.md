# ## Goal: each EC2 Hive has its own idle stop, one of them can be always on · Audit through DELIVER

Delivered: ## Goal: each EC2 Hive has its own idle stop, one of them can be always on Today one app-config value, \`hive\_idle\_stop\_minutes\` (backend/core/app\_config\_manager.py \~:136, default 120, 0 = off), decide. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 29m 44s wall · 29m 44s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 1 not run · 1 red at base · 2/3 findings closed (2 test-closed, confirmed by review) · CF-85d26b6d: no mutation check (a deletion of unused code: there is no behaviour to take out; the tests that call the remaining helpers changed with it) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 485k comparable · 388k input · 97k output · 22.6M cache reads, not counted · 23.1M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 4 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 3 (+2 found on demand) · searched the DDD 2×

## Decisions

- D1 · What range does the per-Hive route accept? → null, 0, or a whole number 30..480 (StrictInt; strings, bools and fractions refused with 422). Why: the task says the same range the existing selector offers (30, 60, 120, 240, 480) plus 0 and null (DESIGN\_SPEC, agent)
- D2 · Which rows may be set? → only rows whose carrier is ec2 and whose carrier has the idle\_stop duty (registry.with\_duty); Elastic Workers keeps its ELASTIC\_NOT\_EC2 409, any other carrier gets a 409 saying the Host never stops it. Why: the same EC2-only check the delete/replace routes use (\_is\_elastic), widened to the duty so the SSH carrier rows are refused when they arrive (DESIGN\_SPEC, agent)
- D3 · What does the list report for a carrier without idle\_stop? → idle\_stop\_settable false, idle\_stop\_effective 0 (never stopped by the Host), own value null; the UI shows Always on and no selector. Why: the task allows Always on or nothing; Always on is the truth for a machine the Host never stops (DESIGN\_SPEC, agent)
- D4 · Does a replacement Hive keep the old Hive's own value? → yes: fleet.replace copies idle\_stop\_minutes when it is set. Why: a replaced always-on Hive silently falling back to the default would stop a machine the person chose to keep up (DESIGN\_SPEC, agent)
- D5 · How is an unreadable own value handled? → anything that is not None, 0 or a positive number counts as NULL (follow the default) and is logged. Why: same stance as idle\_after\_s for the default: a bad value never turns stopping off silently (DESIGN\_SPEC, agent)
- D6 · Where does the default's label say it is the default? → the Row label becomes a new key settings.idleDefault (Default: stop when idle for) with a note that it applies to new Hives and Hives without their own setting; rulesLead is reworded. Why: task decision 2 (DESIGN\_SPEC, agent)
- D7 · What status does a bad body get? → 400 VALIDATION\_FAILED, the app's own validation handler (main.py maps every RequestValidationError), not 422. Why: the requirement said 422 by FastAPI's default; the app answers every validation error this way and the route keeps that contract (BUILD report · swarmai, agent)
- D8 · Is the new route the agent's? → person-only (core/swarm\_out\_ops hive.idle\_stop, weight change), so the run-owner gate refuses an agent's command naming it. Why: always on keeps paying for a machine: money is the person's call, like stop and create (BUILD report · swarmai, agent)
- D9 · Does setting the value touch updated\_at? → no: only the column changes, the idle time keeps counting. Why: updated\_at marks status changes the idle count starts from; a policy change is not one (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Keep a map {hive\_id: minutes} inside app config (hive\_idle\_stop\_by\_hive). Not taken: the person decided the value lives on the Hive row; a config map outlives deleted Hives, is not copied with a replacement and goes through the generic settings route, which cannot refuse non-EC2 rows
- A3 · Store the value in the existing seed\_data/shared\_content JSON text on the row. Not taken: those columns carry provisioning payloads with their own readers; mixing a policy into them is a second meaning per column and is not queryable in \_running\_hives

Requirements analysis:

- missing\_edge (R2.3): the default may be 0 (never) while one Hive has its own N: the own value wins, so that Hive still stops after N → rewritten
- missing\_edge (R1.1): a replacement Hive (fleet.replace) would lose the old Hive's own value and silently fall back to the default → rewritten
- missing\_edge (R4.1): an own value set to a number not in the selector's choices (e.g. 45 written by an API call) must still be shown selected, not snap to another option → rewritten
- missing\_edge (R2.1): an unreadable or non-number own value in the row is treated as NULL (follow the default), never as off → rewritten

## Review coverage (from each expert's transcript)

- security · review r1: read 100% of its prompt · 7/26 tool calls
- correctness · review r1: read 100% of its prompt · 8/26 tool calls
- correctness · review r2: read 100% of its prompt · 6/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 05a141fa30a4: 6 pass, 0 fail, 0 not run
- test · swarmai @ 9aba47f26f4d: 11 pass, 0 fail, 1 not run
- the Brain's checks (code-change), as the TEST reports name them:
  - backend-import-smoke: pass
  - backend-problem-values: pass
  - backend-security-scan: pass
  - skills-lint: pass
  - experts-lint: pass
  - desktop-typecheck: pass
  - desktop-contract-lint: pass
  - desktop-build: pass
  - version-check: pass
  - backend-bvt: pre_existing
  - frontend-run-frontend-tests: pass

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- .venv/bin/python -m pytest tests/test\_hive\_idle\_stop.py tests/test\_hive\_auto\_update.py tests/test\_hive\_idle\_per\_hive.py tests/test\_swarm\_out\_ops.py tests/test\_hive\_fleet.py tests/test\_carriers\_agentco → pass (the change's tests and its consumers' (run in BUILD/REVIEW on this reviewed tree: 80 + 362 passed))
- python scripts/smoke\_import.py → pass (exit 0)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (exit 0)
- python scripts/lint\_skills.py → pass (exit 0)
- python scripts/lint\_experts.py → pass (exit 0)
- bash scripts/sync-version.sh check → pass (exit 0)
- bash scripts/run\_bvt.sh → pre\_existing (1360 of 1363 pass; the 3 failures are tests/test\_precommit\_uv\_lock.py, which calls \`uv\` and this machine has no uv on PATH (shutil.which('uv') is None -&gt; TypeError); the same 3 fail identically on the)
- npx tsc --noEmit → pass (exit 0)
- npm run lint:contract → pass (exit 0)
- 3 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 1360 of 1363 pass; the 3 failures are tests/test\_precommit\_uv\_lock.py, which calls \`uv\` and this machine has no uv on PATH (shutil.which('uv') is None -&gt; TypeError); the same 3 fail identically on the run's base tree

## Not run (the agent's reason, not checked)

- swarmai · .venv/bin/python -m scripts.feature\_map replay --affected --run run\_b8986f1e: all 16 reached walks not\_run for environment reasons on this Hive: the pinned Chromium lacks system libraries (libX11, libnss3, ...) and the machine has no swarm-replay user; the installed SwarmAI's own feature\_map refuses the repo's features.yaml schema 2

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_hive\_idle\_per\_hive.py
- swarmai: desktop/src/components/swarm-out/\_\_tests\_\_/SwarmOutIdlePerHive.test.tsx
- swarmai: desktop/src/components/swarm-out/idlePolicy.ts
