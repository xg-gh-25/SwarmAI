# ## Goal: checks a Hive run records with \`scripts.task\_checks run\` show their re… · Audit through DELIVER

Delivered: ## Goal: checks a Hive run records with \`scripts.task\_checks run\` show their result on the host's run card instead of \`not\_seen\` On the person's machine, three runs on the Elastic Hive (carrier agentc. Landing checks passed.

## Run Audit

| Dimension | Result | This run |
|---|---|---|
| Run Isolation | PASS | isolated · 1/1 repos · 0 contamination |
| Speed | N/A | 17m 58s wall · 17m 58s active · no baseline yet (0/5 runs) |
| Quality | WARN | 1/1 verification · 1 not run · 0/1 findings closed · 1 fail-to-pass |
| Autonomy | PASS | 0 answers · 0 rescues · 0 required approvals (approvals are oversight, not interventions) |
| Token Cost | N/A | 301k comparable · 242k input · 59k output · 11.3M cache reads, not counted · 11.6M billed · host-session responses in the run window · no baseline yet (0/5 runs) |

Project Agent: SwarmAI · code-change · 11 checks · 3 rules · 2 watches

Knowledge: requirement recalled · 6 · design recalled · 7 · cited 1 (+1 found on demand) · searched the DDD 1×

## Decisions

- D1 · Which order of checks.json is the dispatch's? → the one whose work\_id equals the dispatch id; none matching -&gt; None (keeps what was kept). Why: core/swarm\_out.\_dispatch\_round sends the work order as work\_id = dispatch\_id when checks\_by\_id; selecting by that id, never by position, follows the producer-assigned identity (AGENT R3 identity audit) (DESIGN\_SPEC, agent)
- D2 · Should checks of earlier AgentCore rounds be merged into a later round's? → no: the current round's checks.json is what is read, as before. Why: Standing decision a727be55: an AgentCore later round is read on its own (readers see only the current round); a later round gets the task's checks again (DESIGN\_SPEC, agent)
- D3 · Show passed/failed as the task text says? → keep the product's statuses ran (exit 0) / errored (non-zero) / not\_seen. Why: swarm\_out\_results.\_CHECK\_STATUSES and its design say never 'passed': the record is the Hive's word, not proof. The card already renders these (DESIGN\_SPEC, agent)
- D4 · Commit by hand in the worktree? → no: left uncommitted for land. Why: the worktree gate refuses hand commits; land makes the one commit (BUILD report · swarmai, agent)

## Findings

Considered and not taken:

- A2 · Change the Hive writer core/hive\_agentcore.\_checks\_body to write {'checks': \[...\]} for the one order of the microVM. Not taken: The writer's shape is the documented one (hive\_agentcore module doc) and keeps the work\_id key; flattening it drops the key the reader must select by, and a Hive package already deployed writes orders, so the host must read that shape anyway.
- A3 · Make the reader accept both shapes. Not taken: No producer has ever written {'checks'} to S3; accepting an invented shape keeps the dead branch and the misleading doc that caused the bug.

Requirements analysis:

- missing\_edge (R1.4): checks.json holds a list of orders keyed by work\_id; a microVM runs one order, but the reader must still select by the producer-assigned id (the dispatch id) rather than take whichever order is first → rewritten
- missing\_edge (R1.1): a later AgentCore round is a fresh microVM with its own checks.json; per the standing decision that readers see only the current round (a727be55), the round's own record is what is shown, and a round that wrote no checks.json yet keeps what was kept → rewritten
- missing\_edge (R1.1, R1.2): the task text says passed/failed; the product's statuses for a recorded check are ran/errored (swarm\_out\_results.\_CHECK\_STATUSES, design: never 'passed', the record is the Hive's word, not proof). The fix keeps that vocabulary: ran = recorded exit 0, errored = recorded non-zero exit → rewritten

## Review coverage (from each expert's transcript)

- gate0-skeptic · evaluate r1: read 100% of its prompt · 4/25 tool calls
- security · review r1: read 100% of its prompt · 3/25 tool calls
- correctness · review r1: read 100% of its prompt · 10/25 tool calls

Full machine-readable audit is stored with this run.

## Tests (reported by the agent, not rerun by the pipeline; review experts read these reports)

- build · swarmai @ 2554b988bd71: 3 pass, 0 fail, 0 not run
- test · swarmai @ 2554b988bd71: 5 pass, 0 fail, 1 not run
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
  - backend-bvt: pass
  - frontend-run-frontend-tests: not run

## Feature checks

**swarmai**:
- the feature checks could not be decided: features.yaml: schema: is 2, newer than this SwarmAI reads (1); update SwarmAI
- no feature is reached by this change


TEST · swarmai:
- perl -e 'alarm 110; exec @ARGV' python -m pytest tests/test\_check\_record\_reaches\_host.py tests/test\_carriers\_agentcore.py -v --timeout=60 --maxfail=1000 → pass (33 passed on this tree (unchanged since BUILD); the AgentCore tests fail on the base)
- python scripts/smoke\_import.py → pass (exit 0)
- python scripts/lint\_problem\_values.py → pass (exit 0)
- python scripts/security\_scan.py → pass (exit 0)
- bash scripts/run\_bvt.sh → pass (exit 0, 1363 tests, with the PATH the task setup recorded (uv on it))
- python -m scripts.feature\_map replay --affected --run run\_8dde5e65 → not\_run (environment: the one reached walk (api-health-q56c, backend health) was not run because this Hive has no swarm-replay user (hive/replay-host.sh sets it up on the next update); the installed SwarmAI CL)

## Not run (the agent's reason, not checked)

- swarmai · python -m scripts.feature\_map replay --affected --run run\_8dde5e65: environment: the one reached walk (api-health-q56c, backend health) was not run because this Hive has no swarm-replay user (hive/replay-host.sh sets it up on the next update); the installed SwarmAI CLI cannot read the repo's features.yaml schema 2

## Files delivered without being added to git

These files were untracked in the run's worktree (not ignored, never added to git), so they are in this delivery. Check that they belong before pushing: build or test output here usually means the repo's ignore rules do not cover it.

- swarmai: backend/tests/test\_check\_record\_reaches\_host.py
