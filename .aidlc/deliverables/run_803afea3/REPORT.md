# # Detect drift between a seeded context file and the product template it was co… · Audit through DELIVER

Delivered: # Detect drift between a seeded context file and the product template it was copied from \*\*Evidence (2026-11-15).\*\* A Hive still injects the August version of \`SELF.md\` into every prompt: it names \`\_e. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 43m 28s wall · 43m 28s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 1 red at base · 6/6 findings closed (2 test-closed, confirmed by review) |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 564k comparable · 441k input · 124k output · 21.8M cache reads, not counted · 22.4M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Knowledge: requirement recalled · 7 · design recalled · 7 · cited 0 (+5 found on demand) · searched the DDD 1×

## Decisions

- D1 · Which files are tracked → every CONTEXT\_FILES entry with user\_customized=True (SELF, USER, STEERING, TOOLS, MEMORY, EVOLUTION, KNOWLEDGE, TIMELINE). Why: these are the copy-if-missing seeds; system-owned files are already overwritten every startup (DESIGN\_SPEC, agent)
- D2 · When may apply write → only when the pending drift is untouched AND the live file still hashes to the recorded baseline AND the template still hashes to the pending one, re-checked under md\_lock of the live file. Why: an edited or unrecorded file is never overwritten: for USER/STEERING/TOOLS/MEMORY that would destroy the person's content; the person merges and runs resolve (DESIGN\_SPEC, agent)
- D3 · What clears an item → apply; resolve (keep mine / merged: new template becomes the baseline); the live file becoming byte-equal to the new template; or the template returning to the baseline. Why: each is an observable state, so the same drift cannot come back after it was settled (DESIGN\_SPEC, agent)
- D4 · How a diff reaches the item without cutting it → the template-change diff is inlined whole when it is at most 4000 characters; otherwise the message names its length and the show command that prints it whole. Live-file content is never inlined. Why: R23 whole-or-named; the live file may hold private text (DESIGN\_SPEC, agent)
- D5 · An unparseable state file → isolate it as .template-seeds.json.corrupt-&lt;ts&gt; and start empty. Why: recovery preserves; starting empty only re-raises unrecorded items once, never writes a context file (DESIGN\_SPEC, agent)
- D6 · Source label in the Need-You overlay → en 'Context file', zh '上下文文件'. Why: the label test requires one per backend source (BUILD report · swarmai, agent)
- D7 · Should the pre-existing inline ensure\_directory at startup move off the loop → yes, step 3 now runs ensure\_directory + check together on the io pool. Why: the step it lives in is the one this change extends; it was blocking the event loop (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Store an attention row in the database and add an /api/attention act endpoint that applies the template on click. Not taken: the attention channel is deliberately read-aggregation with no store and no act endpoint (attention\_authority docstring); a row would dual-write against the file state and drift
- A3 · Run the drift check inside ensure\_directory on every prompt build. Not taken: ensure\_directory runs per session spawn (prompt\_builder); templates change only with an upgrade, which restarts the backend, so a per-prompt check adds reads to the spawn path for nothing
- A4 · Record only the hash and derive the old template from git history for the three-way view. Not taken: installed builds (bundles, Hives) have no git history of backend/context; without the old text an edited file can only get a two-way view

Requirements analysis:

- missing\_edge (R2.4): the person edits the file while an untouched drift is pending; handled: the pending kind becomes edited under the same item id; apply re-checks the live hash under the file lock and refuses → confirmed
- missing\_edge (R2.6): the template changes again while a drift is pending; handled: the pending record is replaced, so a file never has more than one item → confirmed
- missing\_edge (R2.2): the person makes the live file equal to the new template by hand; handled: the next check records the new hash as baseline and clears the item → confirmed
- missing\_edge (R2.7): MEMORY.md is also written by CDF under md\_lock; handled: apply writes under md\_lock of the live file so it serialises with CDF → confirmed
- missing\_edge (R2.1): the startup refresh calls ensure\_directory inline in an async function; handled: the startup step runs ensure\_directory and the drift check on the io pool; the per-prompt ensure\_directory path does no drift check → confirmed

## Review coverage (from each expert's transcript)

- correctness · review r1: read 100% of its prompt · 7/26 tool calls
- security · review r1: read 100% of its prompt · 9/25 tool calls
- correctness · review r2: read 100% of its prompt · 10/25 tool calls
- security · review r2: read 100% of its prompt · 9/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 0964d2840c61: 7 pass, 0 fail, 0 not run
- test · swarmai @ 0c463caa4c98: 11 pass, 0 fail, 0 not run
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

TEST · swarmai:
- PYTHONPATH=. python -m pytest tests/test\_context\_template\_drift.py -q --timeout=60 → pass (20 tests; the change's own acceptance)
- python scripts/smoke\_import.py → pass
- python scripts/lint\_problem\_values.py → pass
- python scripts/security\_scan.py → pass
- python scripts/lint\_skills.py → pass
- python scripts/lint\_experts.py → pass
- bash scripts/sync-version.sh check → pass
- bash scripts/run\_bvt.sh → pre\_existing (857 of 860 pass with maxfail lifted; the 3 failures are tests/test\_precommit\_uv\_lock.py, which needs a uv binary this machine does not have on PATH (shutil.which('uv') is None) and fails the same way )
- npx tsc --noEmit → pass
- npm run lint:contract → pass
- 2 more in run_audit.json

## Red at the run's base too (the agent's report, not checked)

- swarmai · bash scripts/run\_bvt.sh: 857 of 860 pass with maxfail lifted; the 3 failures are tests/test\_precommit\_uv\_lock.py, which needs a uv binary this machine does not have on PATH (shutil.which('uv') is None) and fails the same way on the base tree

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/core/context\_template\_drift.py
- swarmai: backend/tests/test\_context\_template\_drift.py
